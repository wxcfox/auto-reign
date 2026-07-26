# 故障排查

先确认命令是在正确的仓库目录、正确的版本 Tag 和正确的环境文件下执行。不要用破坏性重置掩盖配置或网络问题。

## 本地服务未启动

```sh
./start.sh --status
docker compose ps
docker compose logs --tail=200 backend frontend
```

检查 `.env` 是否存在、模型 Provider API Key 是否填写，以及 3100、8300、13306、16379、16333 和 19200 是否被其他进程占用。需要重启时使用 `./start.sh --restart`；不要同时从多个 checkout 启动同一组本地服务。

## 生产 Compose 校验失败

生产 Compose 依赖外部环境文件，不能直接运行不带 `--env-file` 的 `docker compose config`：

```sh
export AUTO_REIGN_ENV_FILE=/etc/auto-reign/auto-reign.env
docker compose --env-file "$AUTO_REIGN_ENV_FILE" \
  --file deploy/compose.prod.yml config --quiet
```

如果提示 `Set ACR_REGISTRY`、`Set ACR_NAMESPACE` 或 `Set AUTO_REIGN_VERSION`，说明环境文件缺少发布拓扑配置。不要把这些值写进 `compose.prod.yml` 或提交 Secret。

## Docker Hub 拉取超时

如果 `docker pull redis:7.4-alpine` 或 Compose 报 `registry-1.docker.io` timeout，先区分 Docker daemon 网络问题和应用配置问题：

```sh
curl -I --max-time 20 https://registry-1.docker.io/v2/
docker info | sed -n '/Registry Mirrors:/,/Live Restore Enabled/p'
```

根据 ECS 所在网络和组织策略配置可用的 Docker Registry mirror 或出口代理，然后重启 Docker daemon，并重复 `docker info` 和单镜像拉取验证。不要把临时公共 mirror 地址固化进仓库文档；生产更稳定的做法是将 Redis、MySQL、Elasticsearch 和 Qdrant 基础镜像同步到 ACR，并通过 `*_IMAGE` 配置引用。

## ACR 拉取失败

确认 ECS 使用的是 ACR VPC Endpoint，而不是 GitHub Actions 的公网 Endpoint，并使用 ECS 专用 Pull 凭据登录。`docker login`、`docker compose` 和 `deploy.sh` 必须由同一个 OS 用户执行，否则 Docker credential store 可能不一致：

```sh
docker login <ACR_VPC_ENDPOINT>
docker pull <ACR_VPC_ENDPOINT>/<ACR_NAMESPACE>/auto-reign-backend:0.1.1
```

再检查 `/etc/auto-reign/auto-reign.env` 中的 `ACR_REGISTRY`、`ACR_NAMESPACE` 和 `AUTO_REIGN_VERSION` 是否与目标 Release 一致。GitHub 推送账号和 ECS 拉取账号不应复用。

## 部署后健康检查失败

```sh
docker compose --env-file "$AUTO_REIGN_ENV_FILE" \
  --file deploy/compose.prod.yml ps
docker compose --env-file "$AUTO_REIGN_ENV_FILE" \
  --file deploy/compose.prod.yml logs --tail=200 backend frontend
curl -fsS http://127.0.0.1:18300/api/health
```

内部健康检查会校验应用状态和发布版本。确认 `AUTO_REIGN_VERSION` 与部署命令参数相同，数据库 migration 已完成，MySQL、Redis、Elasticsearch 和 Qdrant 都处于 healthy。公网健康检查只有在域名和 HTTPS 可用时才填写 `DEPLOY_HEALTHCHECK_URL`。

## 版本升级或回滚

每次部署前都拉取 Tag；不需要重新构建应用，也不需要在服务器上 `git pull` 未发布的分支：

```sh
git fetch --force --tags origin
git switch --detach v0.1.1
AUTO_REIGN_ENV_FILE=/etc/auto-reign/auto-reign.env \
  ./deploy/deploy.sh 0.1.1
```

回滚只切换到已经验证过的兼容版本并执行 `deploy/rollback.sh`。脚本不会自动执行 Alembic downgrade；无法确认 schema 兼容性时，应停止写入并按生产文档从备份恢复。
