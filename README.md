# Auto Reign

Auto Reign 是本地优先的通用 Agent 聊天平台。用户在新 Task 发送第一条输入前选择 Agent；Agent 配置决定系统提示词、默认模型，以及是否获得 Agent Home 文件工具和 Knowledge 检索工具。聊天附件绑定一条 User Subtask，只作为不可信上下文使用，不会自动写入 Agent Home、Knowledge 或 Retriever。

## 核心能力

- 单一 `/chat?task=...` 入口；网页聊天通过 Socket.IO `/chat` namespace 加入 Task room，并按 text/tool block 事件更新界面。
- 一次用户输入持久化为一条 User Subtask；整次 Agent 回合持久化为一条 Assistant Subtask，`result.messages_chain` 保存本轮 assistant 消息、tool call、tool result、最终回答和模型信息。
- 失败重试复用原 Assistant Subtask：清空旧 `result/error`、重置为 `PENDING` 后重新执行，不创建新的 retry Subtask。
- global/private Agent、Workspace、Knowledge Collection；入口固定 scope，表单不能修改 owner。
- 可选 Agent Home：ObjectStore 中的文件和根 `AGENTS.md` 是长期权威源，通过精确文件工具访问，不进入 Knowledge Retriever。
- 可选 Knowledge：显式上传 Document；Elasticsearch 支持 vector、BM25 keyword 和线性融合 hybrid，Qdrant 支持 vector。
- 固定 `admin` 一次性密码设置；管理员在 `/admin/users` 创建、启停和重置普通用户。系统没有公开注册。
- User/Assistant Subtask 在模型执行前一起提交。Provider、Runtime、Redis 或连接失败不会删除已经提交的用户输入。

## 快速开始

依赖 Docker Compose v2、Python 3.12+、`uv`、Node.js 22+ 和 `npm`。

```sh
cp .env.example .env
./start.sh
./start.sh --status
```

启动后访问：

- Web：<http://127.0.0.1:3100>
- API 健康检查：<http://127.0.0.1:8300/api/health>

常用命令：

```sh
./start.sh --restart
./start.sh --stop
```

至少配置一个模型 Provider API Key，例如 `QWEN_API_KEY`。`DEFAULT_CHAT_PROVIDER` 指定系统默认 Provider；默认模型取该 Provider 在 `*_CHAT_MODELS` 中的第一个模型。Provider 或模型不可用时明确失败，不静默降级。

后端配置由 `backend/app/core/config.py` 的 `Settings` 管理。`.env.example` 和生产 env 示例列出可覆盖值，包括 Redis chat stream TTL、Socket.IO ping、上下文预算、Knowledge 查询上限、模型请求超时和工具轮次上限。

空库启动后会创建固定用户名 `admin`，但不会生成默认密码。首次访问 `/setup` 完成一次性管理员密码设置；随后由管理员创建普通用户。

## 当前页面

- `/setup`：固定 `admin` 一次性密码设置。
- `/login`：本地账号登录。
- `/chat?task={task_id}`：新 Task 与已有 Task 共用的聊天入口。
- `/agents`：当前用户的 private Agent 管理。
- `/workspaces`：当前用户的 Agent Home 管理，并可查看 global Workspace。
- `/knowledge`：当前用户的 Collection 与 Document 管理，并可查看 global Collection。
- `/admin/agents`、`/admin/workspaces`、`/admin/knowledge`：管理员全局资源管理。
- `/admin/users`：管理员普通用户管理。

新 Task 的输入区提供 Agent、模型和 Context 选择。第一条输入发送后 Agent 锁定，模型可在 Task 非运行态时切换或清除覆盖。历史支持重命名和 soft delete；Agent 已不可用时不能继续生成。

## 三类上下文来源

| 来源 | 权威与访问方式 | 是否进入 Knowledge Retriever |
| --- | --- | --- |
| 聊天附件 | `subtask_contexts` 保存二进制、图片 Base64、解析文本和元数据；草稿使用 `subtask_id=0`，发送后绑定 User Subtask | 否 |
| Agent Home | 可写、可演进的 ObjectStore 文件；根 `AGENTS.md` 管理导航，模型使用精确文件工具 | 否 |
| Knowledge | MySQL 保存 Document 状态，ObjectStore 保存原文和解析文本，模型按需调用 `search_knowledge` | 只有 Knowledge Document 的 chunk 投影进入 |

selected-document Context 在发送时把选中的 Knowledge 身份保存到 User Subtask；只有当前轮会把该选择投影为检索范围。普通附件的解析文本和图片可以随所属 User Subtask 进入有界历史，二进制只作为下载权威，不直接注入模型。

每轮通过统一 Tool Registry 绑定可用工具，再由 LangGraph `create_react_agent` 执行受工具轮次与上下文预算约束的 ReAct loop。当前上下文治理确定性地保留最新完整 Turn，并在每次模型调用前复核预算；已批准的摘要压缩方案尚未在本阶段启用。Assistant result 已保留 `messages_chain` 和 `context_compactions` 契约，不能据此推断实际发生了摘要压缩。

Knowledge 的入库、切分、generation、原文与 RAG 规则见 [Knowledge Collection 数据流](docs/knowledge-data-flow.md)。

## 数据与存储

| 数据 | 权威源 | 说明 |
| --- | --- | --- |
| 用户、资源、Task、Subtask、聊天 Context、Document 状态 | MySQL | 聊天正文、工具消息链、附件二进制、图片 Base64、解析文本和 selected-document 快照的持久权威 |
| Agent Home、Knowledge 原文与完整解析文本 | ObjectStore | development 为本地目录，production 为单一 S3-compatible backend；不保存聊天附件 |
| Knowledge chunk | Elasticsearch 或 Qdrant | 从 Knowledge 权威源重建，不保存 Agent Home、聊天附件或聊天历史 |
| 活跃流、block、UTF-16 offset、取消标记 | Redis | 有 TTL 的临时运行态，可丢失；不是历史、正文或备份权威 |

平台共有六张业务表：`users`、`resources`、`knowledge_documents`、`tasks`、`subtasks` 和 `subtask_contexts`。

development 默认使用本地 ObjectStore；production 必须显式配置 `APP_ENV=production`、JWT、S3-compatible ObjectStore、Redis 和单个 FastAPI 进程。完整配置、备份与 Nginx Socket.IO Upgrade 见 [生产部署](docs/production-deployment.md)。

## 重置本地数据

```sh
./reset-data.sh --dry-run
./reset-data.sh --yes
```

`./reset-data.sh --yes` 是显式破坏性本地操作，只清理本地 MySQL、Redis、Elasticsearch、Qdrant volume 和仓库内 runtime data。远端 S3/OSS 对象不随该命令清空；应用也不会自动删除用户数据。

## 运行与发布

| 模式 | 启动方式 | 用途 |
| --- | --- | --- |
| 开发模式 | `./start.sh` | Redis、MySQL、Elasticsearch、Qdrant 在 Docker 中运行，FastAPI、Next.js 在宿主机运行 |
| 生产模式 | ACR 版本镜像和 `deploy/compose.prod.yml` | 单机、单 FastAPI 进程，Redis 保存临时流状态，文件权威源为 S3-compatible ObjectStore |

代码合并到 `main` 只运行 CI，不自动部署。版本发布、Tag、ACR、ECS、Nginx、备份、回滚、结构化日志和 orphan audit 见 [生产部署](docs/production-deployment.md)。

## 开发检查

共同开发先阅读[工程规范](docs/README.md#工程规范)。较大行为变更应先形成 Spec，再拆分临时实施计划；数据库、前端和测试改动分别遵守对应专题规范。

```sh
cd backend
uv run pytest -v
uv run ruff check .

cd ../frontend
npm run lint
npm test
npm run build

cd ..
docker compose config
```

## 文档

- [文档地图](docs/README.md)
- [通用 Agent 平台架构](docs/workbench-architecture.md)
- [Knowledge Collection 数据流](docs/knowledge-data-flow.md)
- [生产部署](docs/production-deployment.md)
- [Spec 编写规范](docs/engineering/spec-writing-standard.md)
- [测试规范](docs/engineering/testing-standard.md)
- [数据库迁移规范](docs/engineering/database-migration-standard.md)
- [前端契约规范](docs/engineering/frontend-contract-standard.md)

## 当前扩展边界

当前版本只支持单 FastAPI 进程。Redis 为 Socket.IO manager 和 chat stream 提供跨连接临时状态，但 Task 执行、Knowledge Worker 和进程内取消仍没有多实例领取协议；增加 backend replica 前必须单独设计幂等任务领取、故障转移和跨进程副作用协调。未来代码能力使用独立的 Git/POSIX execution Workspace，不会把 Agent Home 或 ObjectStore 当作活跃代码工作树。
