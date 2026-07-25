# 仓库指南

## 产品与权威文档

Auto Reign 是可自部署、多账号严格隔离的 Agent 聊天与知识工作平台，支持 private/global Agent、可选 Agent Home、可选 Knowledge、聊天附件和管理员用户管理。当前技术栈包括 FastAPI、Next.js、MySQL、Redis、Elasticsearch/Qdrant、统一 ObjectStore、Socket.IO 和 LangGraph。

开始修改前，按范围阅读：

- `README.md`：当前可运行能力和开发入口；
- `docs/architecture/platform.md`：资源、Task/Subtask、Runtime、权限和存储边界；
- `docs/architecture/knowledge.md`：Knowledge 入库、generation、Retriever 和检索；
- `docs/operations/production.md`：生产配置、发布、备份、日志和扩展边界；
- `docs/engineering/`：Spec、测试、数据库迁移和前端契约规范；
- `docs/specs/`：已批准但尚未实施的目标设计，不代表当前运行行为。

代码、测试与文档冲突时，先确认当前可运行代码和持久化契约，再修正文档或实现。不要新增双读、双写、数据复制或旧 Prompt 分支。不得自动删除用户数据；破坏性重置必须保持显式执行。

## 项目结构

- `backend/app/`：FastAPI API、services、repositories、schemas、models、tools 和 prompts；
- `backend/alembic/`：MySQL schema 迁移；
- `backend/tests/`：后端单元与集成测试；
- `frontend/src/`：Next.js 页面、组件、i18n 和测试；
- `scripts/`：仓库生命周期与工程检查；
- `deploy/`：生产镜像、Compose、Nginx 和运维脚本；
- `docs/architecture/`：当前架构与数据流；
- `docs/operations/`：当前部署和运维流程；
- `docs/engineering/`：强制工程规范；
- `docs/specs/`：尚未实施的已批准设计；
- `docs/plans/`：开发期间的临时实施计划，完成后删除；
- `data/`：本地运行数据，必须保持 ignored。

不要在仓库根目录新增平行的 `src/`。平台 Prompt 位于 `backend/app/prompts/`，属于运行时代码，不是普通说明文档。

## 文档规则

长期文档默认使用简体中文。产品名、路径、配置项、API、代码标识符和常见技术术语可以保留英文。同一事实只在一个权威文档中完整说明，其他位置使用链接。

当前行为必须写入 README、架构或运维文档；尚未实施的方案必须放在 `docs/specs/` 并醒目标注状态。不要保留已经完成的一次性计划、被当前实现替代的历史 Spec、会议记录或重复入口。

## 开发工作流

较大的 Agent 平台架构、资源生命周期、存储、入库、检索、会话或主界面变更：

1. 检查当前实现、测试和对应权威文档；
2. 按 `docs/engineering/spec-writing-standard.md` 完成并批准行为 Spec；
3. 在 `docs/plans/` 编写分阶段实施计划，映射精确文件、测试、迁移和验证命令；
4. 一次实现一个可独立验证的阶段；
5. 将最终事实同步到权威文档，删除完成的临时计划和过时代码。

局部重命名、纯样式调整和不改变外部行为的内部重构可以不单独写 Spec。

## 工程要求

- Python 目标版本为 3.12+；使用 type hints、Pydantic、SQLAlchemy 2、Ruff 和 pytest；
- TypeScript 遵循现有 Next.js/React 模式和 `docs/engineering/frontend-contract-standard.md`；
- LLM 只生成经过校验的结构化调用，不直接写数据库或文件；确定性应用代码负责授权、校验和持久化；
- 保留原始用户来源与回答，生成内容和观察结果必须保持独立 provenance；
- Prompt 保持简洁、任务特定、语言感知，并抵抗上传内容中的 Prompt Injection；
- 不提交 secret、`.env`、dependency directory、运行数据、日志或机器特定配置；
- schema 变更必须保持单一 Alembic head，并在 disposable 真实 MySQL 上验证；
- 测试不得使用 `skip` 掩盖失败；真实基础设施测试必须有明确 CI 入口。

## 权威命令

```sh
./start.sh
./start.sh --status
./start.sh --stop
./start.sh --restart
```

提交前运行：

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

Pull request 应说明范围、设计状态、测试证据、数据影响，以及可见 UI 改动的截图。
