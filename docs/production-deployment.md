# 生产部署

Auto Reign 使用 GitHub Actions 发布版本，生产服务器由管理员手工更新。仓库不保存服务器 SSH 凭据，也不从 GitHub Actions 直接部署主机。

```text
Pull Request -> main -> Publish Release -> Git Tag + GitHub Release + ACR 镜像
                                                        |
                                                        v
                                         管理员登录 ECS 手工部署
```

`./start.sh` 只用于本地开发。生产服务器不安装 Python、Node.js 依赖，也不从源码构建应用。

## 部署拓扑

```text
公网 80/443
    |
宿主机 Nginx
    |-- /api/* -> FastAPI 127.0.0.1:18300
    `-- 其他请求 -> Next.js 127.0.0.1:13100

FastAPI -> MySQL
        -> Qdrant
        -> /srv/auto-reign/data
```

MySQL 和 Qdrant 仅在 Compose 内部网络中可见。FastAPI 和 Next.js 只映射宿主机回环地址。

## 准备 ACR

当前项目使用杭州个人版 ACR：

```text
公网 Endpoint：crpi-xlp0v6pbunrharin.cn-hangzhou.personal.cr.aliyuncs.com
VPC Endpoint：crpi-xlp0v6pbunrharin-vpc.cn-hangzhou.personal.cr.aliyuncs.com
Namespace：auto-reign
```

仓库：

```text
auto-reign-backend
auto-reign-frontend
```

GitHub-hosted runner 使用公网 Endpoint 推送；杭州 ECS 使用 VPC Endpoint 拉取。为 GitHub 和 ECS 分别创建最小权限凭据，不复用管理员密码。

在 GitHub `Settings -> Secrets and variables -> Actions` 中配置：

Repository variables：

```text
ACR_REGISTRY=crpi-xlp0v6pbunrharin.cn-hangzhou.personal.cr.aliyuncs.com
ACR_NAMESPACE=auto-reign
```

Repository secrets：

```text
ACR_USERNAME=<推送账号>
ACR_PASSWORD=<推送密码>
```

## 初始化 ECS

要求：Linux x86_64、Git、Docker Engine 和 Docker Compose v2.24+。建议使用独立部署用户，以下以 `deploy` 为例：

```sh
sudo adduser deploy
sudo usermod -aG docker deploy
sudo install -d -m 0755 -o deploy -g deploy /opt/auto-reign
sudo install -d -m 0700 -o deploy -g deploy /etc/auto-reign
sudo install -d -m 0750 -o deploy -g deploy /srv/auto-reign
sudo install -d -m 0750 -o deploy -g deploy \
  /srv/auto-reign/data \
  /srv/auto-reign/mysql \
  /srv/auto-reign/qdrant \
  /srv/auto-reign/backups
sudo chown 10001:deploy /srv/auto-reign/data
sudo chmod 0770 /srv/auto-reign/data
sudo chown -R 1000:1000 /srv/auto-reign/qdrant
```

重新登录使 Docker 用户组生效，然后克隆仓库：

```sh
git clone https://github.com/wxcfox/auto-reign.git /opt/auto-reign
cd /opt/auto-reign
```

服务器只使用仓库中的 `deploy/` 文件，不执行源码构建。

### 生产配置

```sh
sudo cp deploy/auto-reign.env.example /etc/auto-reign/auto-reign.env
sudo chown deploy:deploy /etc/auto-reign/auto-reign.env
sudo chmod 600 /etc/auto-reign/auto-reign.env
```

生成独立密码和 JWT 密钥：

```sh
openssl rand -hex 32
openssl rand -hex 32
openssl rand -base64 48
```

编辑 `/etc/auto-reign/auto-reign.env`，至少配置：

```text
ACR_REGISTRY=crpi-xlp0v6pbunrharin-vpc.cn-hangzhou.personal.cr.aliyuncs.com
ACR_NAMESPACE=auto-reign
MYSQL_PASSWORD=<随机密码>
MYSQL_ROOT_PASSWORD=<随机密码>
JWT_SECRET_KEY=<随机密钥>
DEPLOY_HEALTHCHECK_URL=https://auto-reign.agdoer.com
```

同时填写实际使用的模型 API Key。创建第一个账号前暂时设置 `REGISTRATION_ENABLED=true`，创建完成后改回 `false` 并重新执行当前版本部署。

使用 ECS 拉取凭据登录 ACR：

```sh
docker login crpi-xlp0v6pbunrharin-vpc.cn-hangzhou.personal.cr.aliyuncs.com
```

### Nginx 与安全组

ECS 安全组只开放：

| 端口 | 来源 | 用途 |
| --- | --- | --- |
| `22/tcp` | 固定管理 IP | SSH |
| `80/tcp` | 公网 | HTTP 和证书签发 |
| `443/tcp` | 公网 | HTTPS |

不要开放 `13100`、`18300`、MySQL 或 Qdrant 端口。

安装仓库提供的 Nginx 配置：

```sh
sudo cp deploy/nginx/auto-reign.conf /etc/nginx/conf.d/auto-reign.conf
sudo nginx -t
sudo systemctl reload nginx
```

配置文件默认域名为 `auto-reign.agdoer.com`。证书应由服务器现有的 Certbot 或其他证书管理方式维护。

## 发布版本

普通 PR 合并到 `main` 后只运行 CI，不自动创建 Tag。

需要发布时：

1. 确认目标代码已经合并到 `main`，并且 CI 通过。
2. 打开 GitHub `Actions -> Publish Release -> Run workflow`。
3. 输入明确版本号，例如 `0.1.0`。

Workflow 固定读取当时的 `main` HEAD，重复运行后端和前端检查，构建 `linux/amd64` 镜像并推送：

```text
auto-reign-backend:0.1.0
auto-reign-frontend:0.1.0
auto-reign-backend:sha-<commit>
auto-reign-frontend:sha-<commit>
```

全部镜像成功后，Workflow 才创建 `v0.1.0` annotated Tag 和 GitHub Release。版本号和 Tag 不允许覆盖，生产环境不使用 `latest`。如果 Tag 已创建但 GitHub Release 因临时故障失败，可以重跑同一版本；只有 Tag 仍指向本次 `main` 提交时才允许补建 Release。

## 手工部署

登录 ECS，选择已经存在 GitHub Release 的版本：

```sh
cd /opt/auto-reign
git fetch --force --tags origin
git switch --detach v0.1.0
AUTO_REIGN_ENV_FILE=/etc/auto-reign/auto-reign.env \
  ./deploy/deploy.sh 0.1.0
```

Tag 中的 Compose、迁移和部署脚本与同版本镜像保持一致。部署脚本按顺序执行：

1. 校验配置并创建部署锁。
2. 备份 MySQL 和文件工作区。
3. 拉取指定版本镜像。
4. 启动 MySQL、Qdrant 并执行 Alembic migration。
5. 更新 backend、frontend。
6. 执行内部和可选公网健康检查。
7. 写入 `/srv/auto-reign/deployed-version`。

查看状态：

```sh
cd /opt/auto-reign
export AUTO_REIGN_ENV_FILE=/etc/auto-reign/auto-reign.env
docker compose --env-file "$AUTO_REIGN_ENV_FILE" -f deploy/compose.prod.yml ps
docker compose --env-file "$AUTO_REIGN_ENV_FILE" -f deploy/compose.prod.yml \
  logs -f --tail=200 backend frontend
curl -fsS https://auto-reign.agdoer.com/api/health
```

## 备份与回滚

每次部署前，`deploy/backup.sh` 会备份 MySQL 和 `AUTO_REIGN_DATA_DIR`，并写入版本与校验信息。Qdrant 是可重建索引，不在默认部署备份中。生产备份应定期加密同步到 OSS，并执行恢复演练。

应用回滚前必须确认旧版本与当前数据库 schema 兼容：

```sh
cd /opt/auto-reign
git fetch --force --tags origin
git switch --detach v0.1.0
AUTO_REIGN_ENV_FILE=/etc/auto-reign/auto-reign.env \
  ./deploy/rollback.sh 0.1.0 --yes
```

回滚只切换应用镜像，不自动执行 Alembic downgrade。不能确认兼容时，应停止写入并从部署前备份恢复。

## 扩展边界

当前 `deploy/compose.prod.yml` 面向单台服务器。MySQL、Qdrant 和 `DATA_DIR` 都是有状态组件，不能仅复制 Compose 到多台机器就实现水平扩展。

后续迁移 Kubernetes 时可以继续使用同一套 ACR 版本镜像和 Git Tag，在独立运维仓库中维护 Helm chart、Secret、存储和发布策略；不需要把集群部署逻辑加入 Auto Reign 应用代码。
