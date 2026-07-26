# Auto Reign

Auto Reign 是一个可自部署、多账号隔离的 Agent 聊天与知识工作平台。它把 Agent、模型、文件上下文和 Knowledge 组织在同一个工作台中，支持个人使用，也支持管理员维护公共 Agent 和普通用户。

## 当前能力

| 能力 | 说明 |
| --- | --- |
| Agent 聊天 | 基于 LangGraph 的多轮对话与工具调用，支持流式响应 |
| Agent 管理 | 用户可创建 private Agent，管理员可维护 global Agent |
| Knowledge | 管理 Collection 和 Document，支持 Elasticsearch/Qdrant 检索 |
| Agent Home | 为 Agent 提供可持续维护的文件空间 |
| 文件上下文 | 聊天附件绑定当前输入，不会自动写入 Agent Home 或 Knowledge |
| 多账号 | 固定 `admin` 一次性初始化，管理员创建、启停和重置普通用户 |

## 快速开始

本地开发需要 Docker Compose v2、Python 3.12+、`uv`、Node.js 22+ 和 pnpm 11.7.0。

```sh
cp .env.example .env
./start.sh
```

启动后访问：

- Web：<http://127.0.0.1:3100>
- API 健康检查：<http://127.0.0.1:8300/api/health>

至少配置一个模型 Provider API Key，例如 `QWEN_API_KEY`。首次启动后访问 `/setup` 设置一次性 `admin` 密码，再由管理员在 `/admin/users` 创建普通用户。系统不提供公开注册。完整步骤见[快速开始](docs/getting-started/quick-start.md)。

常用命令：

```sh
./start.sh --status
./start.sh --restart
./start.sh --stop
```

## 生产部署

生产环境使用 GitHub Actions 发布明确版本的 backend/frontend 镜像，管理员在 ECS 上手工更新。普通 PR 合并到 `main` 只运行 CI；发布时在 `Actions -> Publish Release` 输入 SemVer，例如 `0.1.0`。

ECS 使用发布 Tag 对应的仓库配置和镜像：

```sh
cd /opt/auto-reign
git fetch --force --tags origin
git switch --detach v0.1.0
AUTO_REIGN_ENV_FILE=/etc/auto-reign/auto-reign.env \
  ./deploy/deploy.sh 0.1.0
```

完整的 ACR、OSS、ECS、Nginx、备份和升级说明见[生产部署与运维](docs/operations/production.md)；常见部署问题见[故障排查](docs/operations/troubleshooting.md)。

## 技术栈

- Backend：FastAPI、SQLAlchemy 2、Alembic、LangGraph、Socket.IO
- Frontend：Next.js App Router、React、TypeScript
- 持久化：MySQL、S3-compatible ObjectStore
- 实时与检索：Redis、Elasticsearch、Qdrant
- 交付：Docker Compose、GitHub Actions、阿里云 ACR

生产当前只支持单个 FastAPI 进程和单个 Uvicorn 进程。Redis 保存活跃流状态，MySQL 保存业务与聊天历史，Knowledge 检索索引和 ObjectStore 内容都必须按生产文档配置。

## 开发检查

```sh
cd backend
uv run pytest -v
uv run ruff check .

cd ../frontend
pnpm run lint
pnpm run typecheck
pnpm test
pnpm run build

cd ..
docker compose config
```

本地数据重置必须显式执行：

```sh
./reset-data.sh --dry-run
./reset-data.sh --yes
```

注意：该命令不会删除远端 S3/OSS 对象。

## 文档

- [文档地图](docs/README.md)：按入门、概念、运维、开发和参考分类。
- [快速开始](docs/getting-started/quick-start.md)：本地启动、首次管理员设置和常用命令。
- [平台架构](docs/architecture/platform.md)：资源、Task/Subtask、Runtime、权限和存储边界。
- [Knowledge 架构](docs/architecture/knowledge.md)：文档入库、generation、切分和检索。
- [生产部署与运维](docs/operations/production.md)：发布、ACR、ECS、备份、日志和扩展边界。
- [故障排查](docs/operations/troubleshooting.md)：本地启动、镜像拉取和生产部署问题。
