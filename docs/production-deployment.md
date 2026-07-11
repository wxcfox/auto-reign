# Auto Reign 生产部署

本文是 Auto Reign 在单台阿里云 ECS 上进行版本化生产部署的权威说明。生产链路为：

```text
Git Tag -> GitHub Actions -> 阿里云 ACR -> ECS Docker Compose
```

生产服务器只拉取指定版本镜像，不在服务器安装 Python、Node.js 依赖或构建应用。`./start.sh` 仍只用于本地开发。

## 部署拓扑

```text
公网 80/443
    |
宿主机 Nginx
    |-- /api/* -> FastAPI
    `-- 其他请求 -> Next.js

FastAPI -> MySQL
        -> Qdrant
        -> DATA_DIR
```

Nginx 由 ECS 宿主机和 systemd 管理。生产 Compose 只把 FastAPI 与 Next.js 分别映射到 `127.0.0.1:18300` 和 `127.0.0.1:13100`，公网不能绕过 Nginx 访问；MySQL 和 Qdrant 不映射宿主机端口。

## 一、准备阿里云资源

建议准备：

- 一台 Linux ECS，推荐至少 2 核 4 GB；资料解析或并发增加后再扩容。
- 一块有快照策略的数据盘，挂载后用于 `/srv/auto-reign`。
- 与 ECS 同地域的阿里云容器镜像服务 ACR。正式生产推荐企业版；个人版无 SLA，更适合个人试用和低风险部署。
- 一个解析到 ECS 公网 IP 的域名。

ECS 安全组入方向只保留：

| 端口 | 来源 | 用途 |
| --- | --- | --- |
| `22/tcp` | 固定管理 IP | SSH |
| `80/tcp` | 公网 | HTTP 和证书签发 |
| `443/tcp` | 公网 | HTTPS |
| `443/udp` | 公网，可选 | HTTP/3 |

不要开放 `3100`、`8300`、`13306`、`16333`、MySQL `3306` 或 Qdrant `6333/6334`。

## 二、创建 ACR 仓库

在 ACR 中创建一个命名空间，例如 `auto-reign`，再创建两个私有仓库：

- `auto-reign-backend`
- `auto-reign-frontend`

记录实例实际显示的公网和 VPC Endpoint。2024 年 9 月 9 日后新建的个人版实例通常使用独立域名，不能根据地域手工拼接地址，例如：

```text
crpi-xxxx.cn-hangzhou.personal.cr.aliyuncs.com
crpi-xxxx-vpc.cn-hangzhou.personal.cr.aliyuncs.com
```

为 GitHub Actions 创建仅具有这两个仓库推送权限的 ACR/RAM 凭据。为 ECS 创建仅具有拉取权限的凭据，不要复用管理员账号。

## 三、配置 GitHub

在 GitHub 仓库的 `Settings -> Secrets and variables -> Actions` 中配置：

Repository variables：

```text
ACR_REGISTRY=<ACR 公网 Endpoint，供 GitHub-hosted runner 推送>
ACR_NAMESPACE=auto-reign
```

Repository secrets：

```text
ACR_USERNAME=<ACR 推送账号>
ACR_PASSWORD=<ACR 推送密码>
```

GitHub Actions 使用三条职责分离的 workflow：

- `.github/workflows/ci.yml`：在 PR 和 `main` push 上运行 Ruff、pytest、前端测试、生产构建、依赖审计、Compose 校验和镜像构建。
- `.github/workflows/release.yml`：在推送 `vMAJOR.MINOR.PATCH` Tag 后重复关键校验，构建 `linux/amd64` backend/frontend 镜像，推送版本标签和 commit SHA 标签，最后创建 GitHub Release。发布流程不生成供生产部署使用的 `latest`。
- `.github/workflows/deploy-production.yml`：只部署已经存在的 GitHub Release，进入 `production` Environment 后等待人工审批，再通过 SSH 调用 ECS 部署脚本。

## 四、初始化 ECS

在 ECS 上安装 Git、Docker Engine 和 Docker Compose v2.24 或更高版本，然后创建专用部署用户。以下命令以 `deploy` 用户为例：

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

重新登录使 Docker 用户组生效。为 ECS 配置 GitHub 只读 Deploy Key，然后克隆仓库：

```sh
git clone git@github.com:wxcfox/auto-reign.git /opt/auto-reign
cd /opt/auto-reign
```

复制生产配置并限制权限：

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

编辑 `/etc/auto-reign/auto-reign.env`，至少替换：

- `ACR_REGISTRY`、`ACR_NAMESPACE`；ECS 配置优先使用同 VPC 可达的 VPC Endpoint
- `AUTO_REIGN_BACKEND_PORT=18300`、`AUTO_REIGN_FRONTEND_PORT=13100`；只能保留回环地址映射
- `DEPLOY_HEALTHCHECK_URL=https://auto-reign.agdoer.com`
- `MYSQL_PASSWORD`、`MYSQL_ROOT_PASSWORD`、`JWT_SECRET_KEY`
- 实际使用的模型 API Key

使用 ECS 只读凭据登录 ACR：

```sh
docker login <ACR VPC Endpoint>
```

## 五、配置宿主机 Nginx

仓库提供 `deploy/nginx/auto-reign.conf`，其中已经包含 `/api/` 的 SSE 长连接参数、上传大小、转发请求头，以及 frontend/backend 的回环地址 upstream。安装配置前先确认现有 Nginx 会加载 `/etc/nginx/conf.d/*.conf`：

```sh
nginx -T 2>/dev/null | grep -F 'include /etc/nginx/conf.d/*.conf'
```

确认 include 后安装站点配置：

```sh
sudo cp /opt/auto-reign/deploy/nginx/auto-reign.conf /etc/nginx/conf.d/auto-reign.conf
sudo nginx -t
sudo systemctl reload nginx
```

模板先提供 HTTP 服务，便于接入现有证书工具。使用 Certbot 时可以执行：

```sh
sudo certbot --nginx -d auto-reign.agdoer.com
sudo nginx -t
sudo systemctl reload nginx
```

如果服务器已有其他证书管理方式，应由现有方式为 `auto-reign.agdoer.com` 配置证书和 HTTP 到 HTTPS 跳转，不要同时让两套工具修改同一个 Nginx server block。完成后确认只有 Nginx 监听公网端口，而应用端口只监听回环地址：

```sh
ss -lntp | grep -E ':(80|443|13100|18300)\b'
```

## 六、创建第一个版本

在本地确认主分支 CI 通过后创建带注释 Tag：

```sh
git switch main
git pull --ff-only
git tag -a v0.1.0 -m "Auto Reign v0.1.0"
git push origin v0.1.0
```

等待 GitHub Actions 的 `Release` workflow 完成，并在 ACR 中确认两个 `0.1.0` 镜像存在。不要在镜像完成前部署。

## 七、首次部署

首次部署需要创建账号时，暂时设置：

```text
REGISTRATION_ENABLED=true
```

然后在 ECS 执行：

```sh
cd /opt/auto-reign
git fetch --tags origin
git switch --detach v0.1.0
AUTO_REIGN_ENV_FILE=/etc/auto-reign/auto-reign.env ./deploy/deploy.sh 0.1.0
```

访问域名注册第一个账号。创建完成后把 `REGISTRATION_ENABLED` 改为 `false`，再次执行同版本部署。关闭注册只阻止创建新账号，不影响已有账号登录。

部署脚本会按顺序执行：配置校验、备份、拉取镜像、启动存储、Alembic migration、更新应用、内部健康检查、可选公网健康检查和版本记录。

当前版本记录在：

```text
/srv/auto-reign/deployed-version
```

## 八、配置 GitHub 一键部署

创建 GitHub Environment `production`，并配置 Required reviewers，确保生产部署需要人工批准。

Environment secrets：

```text
PROD_SSH_HOST=<ECS 公网 IP 或管理域名>
PROD_SSH_USER=deploy
PROD_SSH_PRIVATE_KEY=<专用部署私钥>
PROD_SSH_KNOWN_HOSTS=<人工核验过的 ECS host key>
```

Environment variables：

```text
PROD_SSH_PORT=22
PROD_APP_DIR=/opt/auto-reign
```

`PROD_SSH_KNOWN_HOSTS` 可以用 `ssh-keyscan` 获取候选值，但必须通过阿里云控制台或其他可信通道核对指纹后再保存，不能未经核验直接信任。

之后在 GitHub Actions 手工运行 `Deploy Production`，输入已经发布的版本号，例如 `0.1.1`。Workflow 会先确认对应 GitHub Release 存在，再登录 ECS、切换到对应 Tag 并运行部署脚本。

## 九、日常发布

每次发布遵循：

1. PR 合并到 `main`，等待 CI 通过。
2. 根据变更创建新的 SemVer Tag。
3. 等待 Release workflow 构建镜像并创建 GitHub Release。
4. 从 `Deploy Production` 选择该版本，人工审批后部署。
5. 检查页面、`/api/health`、日志和 `/srv/auto-reign/deployed-version`。

查看运行状态和日志：

```sh
cd /opt/auto-reign
export AUTO_REIGN_ENV_FILE=/etc/auto-reign/auto-reign.env
docker compose --env-file "$AUTO_REIGN_ENV_FILE" -f deploy/compose.prod.yml ps
docker compose --env-file "$AUTO_REIGN_ENV_FILE" -f deploy/compose.prod.yml logs -f --tail=200 backend frontend
sudo tail -f /var/log/nginx/access.log /var/log/nginx/error.log
curl -fsS https://auto-reign.agdoer.com/api/health
```

## 十、备份和恢复边界

每次部署前，`deploy/backup.sh` 会：

- 使用 `mysqldump --single-transaction` 备份 MySQL。
- 归档 `AUTO_REIGN_DATA_DIR` 中的用户原始资料、工作区和报告。
- 写入版本和校验信息。
- 保留所有历史备份，不自动清理。

Qdrant 是可重建索引，默认不进入部署前备份。备份应通过 `ossutil` 或其他受控任务加密同步到 OSS，并为 OSS Bucket 配置版本控制和生命周期规则。至少每季度做一次恢复演练；未验证恢复的文件不能视为有效备份。

## 十一、应用回滚

确认目标版本与当前数据库 schema 向后兼容后执行：

```sh
AUTO_REIGN_ENV_FILE=/etc/auto-reign/auto-reign.env \
  ./deploy/rollback.sh 0.1.0 --yes
```

回滚脚本会再次备份，然后只切换应用镜像，不执行 Alembic downgrade。数据库变更应使用 expand-contract 策略，至少保证相邻版本应用能够短期共用 schema。不能确认兼容时，应先停止写入并从部署前备份恢复，不能盲目执行数据库降级。

## 十二、从旧版 `git pull && ./start.sh` 迁移

旧方式中的长期数据通常位于仓库 `data/`、Docker MySQL volume 和 Qdrant volume。迁移前先停止应用，但不要执行 `docker compose down -v` 或 `reset-data.sh`：

```sh
./start.sh --stop
```

将原仓库 `data/` 完整复制到 `/srv/auto-reign/data`，再执行 `sudo chown -R 10001:deploy /srv/auto-reign/data`，确保非 root backend 用户可以继续写入。MySQL 使用逻辑导出和导入迁移；Qdrant 可以在完全停止后复制 volume，或者在部署后从工作区重建索引。迁移过程中保留原 volume 和额外离线备份，验证新环境资料、账号、会话和报告完整后再决定是否清理旧环境。

由于旧服务器的 Compose project 名、volume 名和 MySQL 密码可能不同，执行数据迁移前先运行：

```sh
docker volume ls
docker compose config
```

根据实际输出确定 volume 和凭据。不要把示例名称直接用于生产数据操作。
