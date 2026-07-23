# 生产部署

Auto Reign 使用 GitHub Actions 发布明确版本，生产服务器由管理员手工更新。仓库不保存服务器 SSH 凭据，也不从 GitHub Actions 直接部署主机。生产只支持单个 FastAPI service、单个 Uvicorn 进程和单一 S3-compatible ObjectStore。

```text
Pull Request -> main -> Publish Release -> Git Tag + GitHub Release + ACR 镜像
                                                        |
                                                        v
                                         管理员登录 ECS 手工部署
```

`./start.sh` 只用于本地开发。生产服务器不从源码构建应用。

## 部署拓扑

```text
公网 Nginx
    |-- /api/* -> 单 FastAPI 进程
    |-- /socket.io/* -> 单 FastAPI 进程（Socket.IO Upgrade）
    `-- 其他请求 -> Next.js

FastAPI -> MySQL（业务与生成审计权威状态）
        -> Redis（活跃流、block、offset、取消和 Socket.IO 临时状态）
        -> Elasticsearch（Knowledge vector/BM25/hybrid 可重建投影）
        -> Qdrant（Knowledge 可重建投影）
        -> S3-compatible/阿里云 OSS（文件权威源）
```

Redis、MySQL、Elasticsearch 和 Qdrant 只在 Compose 内部网络中可见。FastAPI 和 Next.js 只映射宿主机回环地址；安全组和 Nginx 不应公开 backend、frontend 或数据服务的内部端口。

生产 Compose 包含 Redis 7.4、MySQL、Elasticsearch、Qdrant、一次性 migrate、backend 和 frontend。Redis 只保存带 TTL 的实时运行态，不是 Task/Subtask 历史或备份权威；当前不部署 Kibana、Celery 或独立 Knowledge worker container。Elasticsearch 只是 Knowledge Retriever，不是日志栈。

## 发布镜像

当前项目使用阿里云 ACR。GitHub-hosted runner 使用公网 Endpoint 推送，杭州 ECS 使用 VPC Endpoint 拉取；为两端分别创建最小权限凭据，不复用管理员密码。

GitHub `Settings -> Secrets and variables -> Actions` 至少配置：

```text
ACR_REGISTRY=<公网 ACR Endpoint>
ACR_NAMESPACE=<ACR Namespace>
ACR_USERNAME=<推送账号>
ACR_PASSWORD=<推送密码>
```

普通 PR 合并到 `main` 后只运行 CI，不自动创建 Tag。需要发布时在 `Actions -> Publish Release` 输入明确 SemVer，例如 `0.1.0`。Workflow 从当时的 `main` HEAD 重跑检查，构建 `linux/amd64` backend/frontend 镜像，并推送版本和 commit 标签：

```text
auto-reign-backend:0.1.0
auto-reign-frontend:0.1.0
auto-reign-backend:sha-<commit>
auto-reign-frontend:sha-<commit>
```

全部镜像成功后才创建 `v0.1.0` annotated Tag 和 GitHub Release。版本号和 Tag 不允许覆盖，生产不使用 `latest`。

## 初始化 ECS

要求 Linux x86_64、Git、Docker Engine 和 Docker Compose v2.24+。建议使用独立部署用户：

```sh
sudo adduser deploy
sudo usermod -aG docker deploy
sudo install -d -m 0755 -o deploy -g deploy /opt/auto-reign
sudo install -d -m 0700 -o deploy -g deploy /etc/auto-reign
sudo install -d -m 0750 -o deploy -g deploy \
  /srv/auto-reign/data \
  /srv/auto-reign/redis \
  /srv/auto-reign/mysql \
  /srv/auto-reign/elasticsearch \
  /srv/auto-reign/qdrant \
  /srv/auto-reign/backups
sudo chown 10001:deploy /srv/auto-reign/data
sudo chmod 0770 /srv/auto-reign/data
sudo chown -R 1000:1000 /srv/auto-reign/qdrant
sudo chown -R 1000:1000 /srv/auto-reign/elasticsearch
```

`/srv/auto-reign/data` 只是容器本地 runtime 目录。聊天附件二进制、图片 Base64 和解析文本保存在 MySQL；Agent Home 与 Knowledge 文件保存在远端 S3-compatible ObjectStore。`/srv/auto-reign/redis` 即使持久挂载也只承载可丢失的实时状态。

克隆仓库只为使用版本对应的 `deploy/`、迁移和运维文件：

```sh
git clone https://github.com/wxcfox/auto-reign.git /opt/auto-reign
cd /opt/auto-reign
```

## 生产配置

```sh
sudo cp deploy/auto-reign.env.example /etc/auto-reign/auto-reign.env
sudo chown deploy:deploy /etc/auto-reign/auto-reign.env
sudo chmod 600 /etc/auto-reign/auto-reign.env
```

使用 `openssl rand` 或等价密码管理工具生成独立 MySQL 密码和 JWT Secret。生产必须显式配置非空 `JWT_SECRET_KEY`；development 默认值和旧 sentinel `auto-reign-local-dev-secret-change-me` 都会被拒绝，不会生成或回退到本地 JWT 文件。

### 必需运行边界

```dotenv
APP_ENV=production
BACKEND_INSTANCE_COUNT=1
LOG_LEVEL=INFO
JWT_SECRET_KEY=<独立随机密钥>
REDIS_URL=redis://redis:6379/0
CHAT_STREAM_TTL_SECONDS=3600
CHAT_STREAM_KEY_PREFIX=auto_reign:chat
SOCKETIO_PING_INTERVAL_SECONDS=25
SOCKETIO_PING_TIMEOUT_SECONDS=20

OBJECT_STORE_BACKEND=s3
OBJECT_STORE_MAX_READ_BYTES=33554432
S3_BUCKET=<私有 bucket>
S3_ENDPOINT_URL=https://oss-cn-hangzhou.aliyuncs.com
S3_REGION=cn-hangzhou
S3_ACCESS_KEY_ID=<最小权限 AccessKey>
S3_SECRET_ACCESS_KEY=<最小权限 Secret>
S3_SESSION_TOKEN=
S3_KEY_PREFIX=auto-reign-production
S3_NAMESPACE_APP_EXCLUSIVE=true
S3_ADDRESSING_STYLE=virtual
```

同时填写 MySQL、ACR、Elasticsearch 密码和实际使用的模型或 Embedding Provider 配置。Elasticsearch 与 Qdrant 的地址、认证和索引配置只由部署者维护，不通过 Collection API 暴露。Secret 只保存在权限为 `0600` 的生产 env 或外部 Secret 管理系统中，不能提交仓库、写入前端或输出日志。

应用运行时的对象大小、上下文预算、Knowledge 检索、Worker、模型超时和工具轮次上限也应显式填写；这些配置由 `Settings` 统一读取，完整示例见 `deploy/auto-reign.env.example`。Compose 固定把 backend 的 `REDIS_URL` 指向内部 `redis:6379/0`，并注入容器路径、数据库 URL、Retriever URL 和发布版本等拓扑值。

production validator 固定要求：

- `APP_ENV=production`；
- 显式安全 JWT；
- `OBJECT_STORE_BACKEND=s3`，没有 Local fallback；
- `BACKEND_INSTANCE_COUNT=1`；
- bucket、endpoint、access key 和 secret 完整；
- `S3_NAMESPACE_APP_EXCLUSIVE=true`；
- `S3_ADDRESSING_STYLE=virtual`。

`S3_NAMESPACE_APP_EXCLUSIVE=true` 表示 `(bucket, key_prefix)` 组合下没有其他 writer。`S3_KEY_PREFIX` 非空时只要求该 prefix 独占；为空时才表示整个 bucket 必须由当前应用独占。

阿里云 OSS 只实现 S3 API 子集，并要求 virtual-hosted style。参考：

- [Alibaba Cloud OSS S3 compatibility](https://www.alibabacloud.com/help/en/oss/developer-reference/compatibility-with-amazon-s3)
- [OSS PutObject](https://www.alibabacloud.com/help/en/oss/developer-reference/putobject)
- [AWS conditional writes](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html)

AWS 文档只说明 AWS S3 合约，不能据此假定 OSS 支持相同 conditional PUT。v1 的同 Key 串行化使用进程内锁，不把 AWS conditional write 当作 OSS 正确性前提，因此不能增加第二个 FastAPI 进程或 replica。

## 首次管理员设置

空库启动会创建 fixed `admin`，但不会创建默认密码。`credential_bootstrap_status=pending` 时，`/setup` 是一次性、未认证的管理员密码 claim；这不是公开注册功能。

部署者必须在本机、SSH 隧道或受信管理网络中完成 `/setup`，确认管理员登录和 `/admin/users` 可用后，再向公网开放 Nginx。不要让 pending `/setup` 在未知公网访问者可达时长期运行。

完成后 bootstrap 状态单向变为 `completed`，再次调用 endpoint 永久返回 409。重启、环境变量或配置开关都不能重新开放。系统没有注册页面、注册 API 或临时注册开关；普通用户只能由管理员在 `/admin/users` 创建、启停和重置密码，fixed admin 不受该页面管理。

## Nginx 与网络

ECS 安全组只开放：

| 端口 | 来源 | 用途 |
| --- | --- | --- |
| `22/tcp` | 固定管理 IP | SSH |
| `80/tcp` | 公网 | HTTP 和证书签发 |
| `443/tcp` | 公网 | HTTPS |

不要开放 backend/frontend loopback 端口、Redis、MySQL、Elasticsearch 或 Qdrant。安装并检查仓库 Nginx 配置：

```sh
sudo cp deploy/nginx/auto-reign.conf /etc/nginx/conf.d/auto-reign.conf
sudo nginx -t
sudo systemctl reload nginx
```

证书由 Certbot 或现有证书管理系统维护。Nginx 必须在前端 `/` location 前匹配 `/socket.io/`，使用 HTTP/1.1、转发 `Upgrade`/`Connection`、关闭 buffering 并设置长读写 timeout。网页客户端连接 Engine.IO path `/socket.io` 后加入 Socket.IO namespace `/chat`；不要把 `/chat` 配成 backend HTTP location。REST 继续通过同域 `/api` 暴露。

## 手工部署

登录 ECS，切换到已经存在 GitHub Release 的版本：

```sh
cd /opt/auto-reign
git fetch --force --tags origin
git switch --detach v0.1.0
AUTO_REIGN_ENV_FILE=/etc/auto-reign/auto-reign.env \
  ./deploy/deploy.sh 0.1.0
```

部署脚本按顺序：

1. 校验配置并创建部署锁；
2. 备份 MySQL 和本地 runtime 目录；
3. 拉取明确版本镜像；
4. 启动 Redis、MySQL、Elasticsearch、Qdrant 并执行 Alembic migration；
5. 更新单 backend 和 frontend；
6. 执行内部与可选公网健康检查；
7. 写入 deployed version。

查看状态：

```sh
cd /opt/auto-reign
export AUTO_REIGN_ENV_FILE=/etc/auto-reign/auto-reign.env
docker compose --env-file "$AUTO_REIGN_ENV_FILE" -f deploy/compose.prod.yml ps
docker compose --env-file "$AUTO_REIGN_ENV_FILE" -f deploy/compose.prod.yml \
  logs -f --tail=200 backend frontend
curl -fsS https://auto-reign.example.com/api/health
curl -fsS https://auto-reign.example.com/api/health/retrievers
```

## 请求 ID 与结构化日志

应用 stdout 默认一行一个 JSON object。每个 HTTP 响应包含 `X-Request-ID`：安全的调用方 ID 会保留，非法、空白、换行、Unicode 或超长值会替换为 UUID。Socket.IO 鉴权和事件使用独立的结构化 allowlist 日志，不记录 payload 正文或 access token。

HTTP 完成事件只记录 method、路由模板、status、duration 和 request ID，不记录原始动态路径、query、header 或 body。默认也不记录：

- User 或 Assistant 正文；
- 附件、Agent Home 文件或 Knowledge 原文；
- RAG chunk、Prompt 或 Tool arguments；
- Secret、credential、第三方 URL/header/body；
- exception message 或 stack string。

只有 `app`/`app.*` 的稳定 event 和 allowlist extra 可以透传。第三方 SDK 日志统一收敛为 `third_party_log`，只保留安全 logger、level 和 exception type。日志轮转由 Compose 的 Docker `json-file` 设置和宿主机日志平台负责；Knowledge Elasticsearch 不接收 stdout 或聊天审计数据，stdout 也不复制到 MySQL。

## 生成与工具审计

生成审计保存在 MySQL Assistant Subtask result：

- Task、Subtask 和安全 request ID；
- tool loop 中每次 Provider 调用的 `call_index`、provider、model；
- 只从 SDK response/header 结构化字段取得的安全 Provider request ID；
- Provider 返回的 input/output token usage；
- 首个非空正文 delta 的 latency、每次调用 duration/status 和本轮总 duration；
- Provider 未提供字段对应的 `null` 与 `unavailable_fields`。

平台不自行估算 Provider usage，也不能用 completion chunk ID 冒充 Provider request ID。纯 Tool Call 没有正文，首正文 latency 保持 unavailable。成功、Provider 失败、Runtime 失败和取消都会尽力保存已经观察到的 metrics；聚合 `token_usage.incomplete` 明确表示存在未知值。

Agent Home Tool audit 只保存 tool、call、status、规范化 relative path 的 SHA-256 和 opaque ETag，不保存路径原文、内容或 Tool arguments。Knowledge Tool audit 只保存有界来源身份，不保存 query 或 chunk 正文。最终 Provider audit merge 必须保留已经即时写入的 Tool audit。

## 备份与恢复

每次部署前，`deploy/backup.sh` 备份 MySQL 和 `AUTO_REIGN_DATA_DIR`，并写入版本与校验信息。MySQL 备份包含用户、资源、Task、Subtask、SubtaskContext 二进制/解析内容、Document 状态和生成审计；本地 runtime archive 不包含远端 ObjectStore 权威文件。

Redis 目录不属于历史备份集合。恢复后活跃流、cached block、offset 和取消标记可以消失；已完成聊天必须完全从 MySQL 恢复，不能从 Redis 反向重建。远端 ObjectStore 备份只需覆盖 Agent Home 与 Knowledge，不包含聊天附件。

Elasticsearch 和 Qdrant 都是可重建投影，不在默认部署备份中。恢复时从 MySQL 当前 Document generation 与 ObjectStore 权威解析文本重新索引。

远端 bucket 的 versioning、lifecycle、服务端加密、跨区域复制、访问审计和恢复演练由对象存储运维策略承担。必须把 MySQL 备份与相容时间点的 ObjectStore version 一起纳入恢复演练。

回滚只切换应用镜像，不自动执行 Alembic downgrade：

```sh
cd /opt/auto-reign
git fetch --force --tags origin
git switch --detach v0.1.0
AUTO_REIGN_ENV_FILE=/etc/auto-reign/auto-reign.env \
  ./deploy/rollback.sh 0.1.0 --yes
```

不能确认旧版本兼容当前 schema 时，应停止写入并从部署前备份恢复。

## reset 安全边界

普通 `./reset-data.sh --yes` 只面向本地开发：它只识别仓库内 `DATA_DIR`、`OBJECT_STORE_LOCAL_ROOT` 和本地 Docker Redis/MySQL/Elasticsearch/Qdrant volumes。路径在仓库外会被拒绝。

该命令不读取 S3 配置，不构建远端 client，也不 list 或 purge S3/OSS。远端对象永远不是普通 reset 候选；生产清理不能复用该命令。

## ObjectStore orphan audit

`scripts/audit_object_orphans.py` 是显式、report-only 的诊断工具，不由应用启动、后台 worker、cron 或 reset 调用。它只比较 Agent Home/Knowledge 的 MySQL 引用与 ObjectStore list snapshot；聊天 Context 全量保存在 MySQL，不参与对象分类。报告区分：

- 可能没有业务引用的 candidate orphan；
- MySQL 引用但对象缺失；
- Knowledge cleanup pending/failed 仍需通过领域 DELETE 重试的对象。

默认只输出 counts；`--show-keys` 才在受控管理员终端显示 logical key。S3/OSS 即使只读也必须显式传入 `--allow-remote-read`，否则在建立 DB/ObjectStore client 或 list 前拒绝。CLI 没有 delete mode、`--delete-orphans` 或 `--yes`，也不会调用 ObjectStore delete。

进程被强制终止可能留下 orphan，审计期间并发上传或清理也可能产生短暂假阳性。应在静默窗口重复执行并人工核对，不能依据单次报告自动删除。未来若设计删除，必须使用独立命令，至少同时要求 `--delete-orphans --yes`，并默认拒绝远端；不能给当前 audit 增加隐式删除。

## 扩展边界

当前只能运行单个 FastAPI 进程。Redis 已承载 Socket.IO manager、活跃流、offset 和取消标记，但 Task 执行注册表、Knowledge worker、Agent Home 同 Key 串行化和 shutdown drain 仍依赖当前进程。没有完整领取与故障转移协议前，不得增加 Uvicorn worker、backend replica 或独立 worker container。

如果未来需要多实例，必须先设计：

- Task 执行领取、跨进程取消和幂等副作用；
- 持久任务领取和幂等发布；
- ObjectStore 条件写与分布式锁；
- 健康检查、滚动升级和故障恢复。

现有 Redis 只能作为可丢失协调层，不能成为 Subtask、Context 或文件权威。Knowledge 使用的 Elasticsearch 不是日志或审计依赖。未来迁移 Kubernetes 时可以继续使用相同 ACR 版本镜像和 Git Tag，但 Helm、Secret 和存储策略应由独立运维设计承担。
