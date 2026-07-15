# 仓库指南

## 产品方向

Auto Reign 是本地优先、多账号严格隔离的通用 Agent 聊天平台，支持 private/global Agent、可选 Agent Home、可选 Knowledge RAG、消息附件和管理员用户管理。当前代码库使用 FastAPI、Next.js、MySQL、Qdrant 和统一 ObjectStore。长期产品与架构边界记录在：

- `docs/workbench-architecture.md`
- `docs/knowledge-data-flow.md`：Knowledge Document 入库、generation、Qdrant 投影和检索。
- `docs/production-deployment.md`：生产 S3-compatible 存储、账号初始化、日志、备份和单实例边界。

修改产品行为、资源生命周期、存储、入库、会话流程、记忆、检索或主界面之前，必须先阅读 `docs/workbench-architecture.md`，并按变更范围阅读对应专题文档。`README.md` 描述当前可运行实现，专题流程文档放在 `docs/` 下。

不要新增双读、双写、数据复制或旧 prompt 分支。绝不能自动删除本地用户数据；破坏性重置命令必须保持显式执行。

## 项目结构

- `backend/app/`：FastAPI 应用、API、services、repositories、schemas、数据库 models 和 prompts。
- `backend/alembic/`：MySQL schema 迁移。
- `backend/tests/`：后端单元测试和集成测试。
- `frontend/src/`：Next.js 应用、components、i18n resources 和 tests。
- `scripts/`：`start.sh` 使用的仓库生命周期工具。
- `docs/superpowers/plans/`：从已批准规格拆出的临时实施计划，仅在对应阶段开发期间保留。
- `docs/workbench-architecture.md`：通用 Agent 平台的当前核心与长期架构边界。
- `docs/knowledge-data-flow.md`：Knowledge 当前入库、索引和检索数据流。
- `docs/production-deployment.md`：生产配置、发布、备份、日志和扩展边界。
- `data/`：本地运行数据，不是源代码，必须保持 ignored。

不要在仓库根目录新增平行的 `src/` 目录。遵循现有后端和前端组织方式。

## 文档语言

长期仓库文档默认使用简体中文。同一篇文档内，标题、正文、表格和 Mermaid 流程图节点应保持中文。产品名、配置项、路径、API 名称、代码标识符和常见技术术语可以保留英文，例如 FastAPI、Next.js、MySQL、Qdrant、chunk、embedding、RAG 和 SSE。

除非仓库明确采用 `docs/zh/` 与 `docs/en/` 这类镜像双语结构，否则不要在同一份长期文档里混写中文和英文正文。过程计划是临时产物，不应演变成第二套文档系统。

## 开发工作流

针对 Agent 平台架构、资源生命周期、存储、入库、检索、会话流程或主界面的较大变更：

1. 检查当前实现和 `docs/workbench-architecture.md`。
2. 修改应用代码前，先在 `docs/superpowers/plans/` 编写分阶段实施计划。
3. 将每个任务映射到精确文件、测试、迁移影响和验证命令。
4. 一次实现一个可独立验证的阶段。
5. 在每个阶段边界保持应用可运行，并保持测试通过。

不要把整个重构做成一次不可审查的大改。目标边界内能复用现有代码就复用；替代方案完成后，应删除过时代码。

实施计划是过程产物，不是长期产品文档。PR 准备好之前，删除已经完成的一次性计划，或把其中仍然有效的决策沉淀到 `README.md`、`docs/README.md`、`docs/workbench-architecture.md` 或 `docs/` 下的专题文档。不要仅仅为了保留历史而留下过时计划。

修改行为时，同一个 PR 必须更新对应权威文档：`README.md` 描述可运行行为，`docs/workbench-architecture.md` 描述长期架构，`docs/*.md` 专题文档描述当前操作流程。避免在多份文档中重复同一事实；优先链接到权威来源。

## 权威命令

从仓库根目录启动或管理本地栈：

```sh
./start.sh
./start.sh --status
./start.sh --stop
./start.sh --restart
```

提交前运行仓库检查：

```sh
cd backend
uv run pytest -v
uv run ruff check .

cd ../frontend
npm test
npm run build

cd ..
docker compose config
```

优先使用这些命令，而不是临时拼凑的替代命令。行为变更应先添加聚焦测试，再运行更宽的相关测试套件。

## 编码约定

- Python 目标版本为 3.12+，使用 type hints、Pydantic models、SQLAlchemy 2、Ruff 和 pytest。保持 service 聚焦，并把 provider 或 persistence 细节放在明确接口之后。
- TypeScript 遵循现有 Next.js 和 React 模式。面向用户的改动必须保留 i18n、loading、empty 和 error states。
- LLM 调用返回经过校验的结构化输出。LLM 不直接写文件或数据库；由确定性应用代码应用变更。
- 保留原始用户来源和回答。生成内容、个人事实和观察到的练习证据必须保留不同 provenance。
- Prompt 应简洁、任务特定、语言感知，并抵抗上传内容中的 prompt injection。
- 不要提交 secrets、`.env`、dependency directories、运行数据、logs 或机器特定配置。

## 测试期望

每个行为变更都需要覆盖预期行为、边界情况和失败路径的测试。除非测试明确是集成检查，否则使用确定性模型和向量 test double。存储变更必须覆盖 MySQL 迁移、文件系统失败行为和 Qdrant 恢复。前端变更必须覆盖主要用户流程，而不只是孤立展示组件。

Pull request 应说明范围、设计阶段、测试证据、数据重置影响，以及可见 UI 改动的截图。
