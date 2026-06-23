# 仓库指南

## 产品方向

Auto Reign 是本地优先、单用户的 AI 面试学习工作台。当前代码库是一个可运行的 v1，实现使用 FastAPI、Next.js、MySQL 和 Qdrant。下一开发周期的权威目标设计是：

- `docs/superpowers/specs/2026-06-22-filesystem-first-interview-workbench-design.md`

修改产品行为、存储、入库、面试流程、记忆、检索或主界面之前，必须先阅读该规格。`README.md` 描述当前可运行实现；目标设计会有意取代当前行为中的一部分。

目标设计不要求兼容现有 MySQL 记录、Qdrant points 或运行时文件。不要新增双读、双写、数据复制或旧 prompt 分支。绝不能自动删除本地用户数据；破坏性重置命令必须保持显式执行。

## 项目结构

- `backend/app/`：FastAPI 应用、API、services、repositories、schemas、数据库 models 和 prompts。
- `backend/alembic/`：MySQL schema 迁移。
- `backend/tests/`：后端单元测试和集成测试。
- `frontend/src/`：Next.js 应用、components、i18n resources 和 tests。
- `scripts/`：`start.sh` 使用的仓库生命周期工具。
- `docs/superpowers/specs/`：已批准的产品和架构规格。
- `docs/superpowers/plans/`：从已批准规格拆出的临时实施计划，仅在对应阶段开发期间保留。
- `data/`：本地运行数据，不是源代码，必须保持 ignored。

不要在仓库根目录新增平行的 `src/` 目录。遵循现有后端和前端组织方式。

## 文档语言

长期仓库文档默认使用简体中文。同一篇文档内，标题、正文、表格和 Mermaid 流程图节点应保持中文。产品名、配置项、路径、API 名称、代码标识符和常见技术术语可以保留英文，例如 FastAPI、Next.js、MySQL、Qdrant、chunk、embedding、RAG 和 SSE。

除非仓库明确采用 `docs/zh/` 与 `docs/en/` 这类镜像双语结构，否则不要在同一份长期文档里混写中文和英文正文。过程计划是临时产物，不应演变成第二套文档系统。

## 开发工作流

针对文件系统优先的工作台重构：

1. 检查当前实现和权威设计。
2. 修改应用代码前，先在 `docs/superpowers/plans/` 编写分阶段实施计划。
3. 将每个任务映射到精确文件、测试、迁移影响和验证命令。
4. 一次实现一个可独立验证的阶段。
5. 在每个阶段边界保持应用可运行，并保持测试通过。

不要把整个重构做成一次不可审查的大改。目标边界内能复用现有代码就复用；替代方案完成后，应删除过时代码。

实施计划是过程产物，不是长期产品文档。PR 准备好之前，删除已经完成的一次性计划，或把其中仍然有效的决策沉淀到 `README.md`、`docs/README.md`、`docs/superpowers/specs/` 或 `docs/` 下的专题文档。不要仅仅为了保留历史而留下过时计划。

修改行为时，同一个 PR 必须更新对应权威文档：`README.md` 描述可运行行为，`docs/superpowers/specs/` 描述目标架构，`docs/*.md` 专题文档描述当前操作流程。避免在多份文档中重复同一事实；优先链接到权威来源。

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
