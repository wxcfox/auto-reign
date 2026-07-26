# 运维导航

生产部署按“发布版本、准备主机、配置依赖、执行升级、验证和恢复”组织。应用镜像由 GitHub Actions 发布到 ACR，ECS 只拉取明确版本，不在服务器上构建应用。

## 常用入口

- [生产部署与运维](production.md)：完整的 ACR、ECS、Nginx、配置、备份、日志和扩展边界。
- [故障排查](troubleshooting.md)：部署时最常见的网络、镜像、配置和健康检查问题。
- [生产 Compose](../../deploy/compose.prod.yml)：服务、镜像和健康检查定义。
- [生产环境模板](../../deploy/auto-reign.env.example)：配置名和默认拓扑，不包含 Secret。

## 发布者

普通 PR 合并到 `main` 只运行 CI。需要发布时，在 GitHub Actions 的 `Publish Release` workflow 中输入明确 SemVer，例如 `0.1.0`。workflow 会从当时的 `main` HEAD 重新检查、构建 `linux/amd64` backend/frontend 镜像，并创建对应 Tag 和 Release。

## 部署者

服务器只需要目标版本的 `deploy/` 和运维脚本。每次升级先拉取 Tag，再执行：

```sh
cd /opt/auto-reign
git fetch --force --tags origin
git switch --detach v0.1.1
AUTO_REIGN_ENV_FILE=/etc/auto-reign/auto-reign.env \
  ./deploy/deploy.sh 0.1.1
```

`AUTO_REIGN_ENV_FILE` 可以写在当前 shell 的环境中，也可以像上面一样只对本次命令设置；后续检查 Compose 时需要显式传入同一 env 文件。
