# 快速开始

本文用于本地开发和功能体验。生产环境不要使用本地 `.env`、本地 ObjectStore 或 `./start.sh`，请阅读[生产部署与运维](../operations/production.md)。

## 前置条件

- Docker Engine 和 Docker Compose v2；
- Python 3.12+、`uv`；
- Node.js 22+、pnpm 11.7.0；
- 至少一个模型 Provider API Key，例如 `QWEN_API_KEY`。

## 启动

在仓库根目录执行：

```sh
cp .env.example .env
./start.sh
```

默认地址：

| 入口 | 地址 |
| --- | --- |
| Web | <http://127.0.0.1:3100> |
| API 健康检查 | <http://127.0.0.1:8300/api/health> |

`./start.sh` 会启动本地依赖、backend 和 frontend，并把运行状态和日志保存在仓库的 `data/` 目录。不要把该目录提交到 Git。

## 首次登录

1. 打开 Web 地址。
2. 按页面提示访问 `/setup`，设置固定 `admin` 用户的密码。
3. 使用 `admin` 登录。
4. 在 `/admin/users` 创建普通用户。
5. 在模型配置中确认至少一个 Provider 可用，再进入 `/chat` 创建 Task。

`/setup` 只允许完成一次初始化，不是公开注册入口。管理员密码不会从环境变量自动生成。

## 常用命令

```sh
./start.sh --status
./start.sh --restart
./start.sh --stop
```

需要清空本地数据时必须显式执行，并先确认当前目录和数据范围：

```sh
./reset-data.sh --dry-run
./reset-data.sh --yes
```

该命令不读取或删除远端 S3/OSS 数据。

## 下一步

- [概念导航](../concepts/README.md)：理解 Agent、Workspace、Knowledge 和 Task 的关系。
- [平台架构](../architecture/platform.md)：理解权限、历史和存储边界。
- [开发者导航](../developer-guide/README.md)：运行测试、修改数据库和前端契约。
- [故障排查](../operations/troubleshooting.md)：启动失败或端口、镜像问题。
