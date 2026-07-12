# Auto Reign

Auto Reign 是本地优先的通用 Agent 聊天平台。用户在新会话发送第一条消息前选择 Agent；Agent 配置决定系统提示词、默认模型，以及是否获得 Agent Home 文件工具和 Knowledge 检索工具。聊天附件绑定一条用户消息，并在该消息仍进入有界历史窗口时作为不可信上下文使用，不会自动写入 Agent Home、资料库或 Qdrant。

## 核心能力

- 单一 `/chat?session=...` 聊天入口，支持动态模型选择、SSE、失败恢复、统一历史和消息附件。
- 用户私有资源与管理员全局 Agent、Workspace、Knowledge Collection；入口固定 scope，表单不能修改 owner。
- Conversation 在首轮后固定引用 Agent，但不保存 Agent 配置快照。已有会话的每个新轮次都解析最新 Agent 配置；管理员修改 global Agent 后也会影响已有会话的后续轮次。
- 用户可在首条消息前选择模型，也可在会话空闲时切换或清除覆盖。优先级为“会话覆盖 > 最新 Agent 默认 > 系统默认”，不可用时明确失败，不静默降级。
- Agent 停用或删除后不再用于新会话；历史 Conversation 和 Message 仍可读取，但已有会话不能继续生成。
- 可选 Agent Home：ObjectStore 中的文件和根 `AGENTS.md` 是长期权威源，通过精确文件工具访问，不进入 Qdrant。
- 可选 Knowledge Collection：用户或管理员显式上传 Document；Qdrant 只保存从权威解析文本生成、可重建的原文 chunk 检索投影。
- 固定 `admin` 一次性密码设置；管理员在 `/admin/users` 创建、启停和重置普通用户。系统没有公开注册页或注册 API。
- MySQL 在调用模型前提交 User Message 和 pending Assistant；流式过程中持续 checkpoint，Provider、Runtime、附件读取或取消失败不会丢失已经提交的用户输入和已知审计。

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

至少配置一个模型 Provider API Key，例如 `QWEN_API_KEY`，对应模型才会出现在模型选择器中。`DEFAULT_CHAT_PROVIDER` 指定系统默认 Provider；系统默认模型取该 Provider 在 `*_CHAT_MODELS` 中配置的第一个模型。该 Provider 缺少 API Key 或可用模型时，系统不会自动切换到其他 Provider。

后端运行时配置统一由 `backend/app/core/config.py` 的 `Settings` 管理；`.env.example` 和生产 env 示例列出可覆盖的默认值。上下文预算、Knowledge 查询上限、模型请求超时和工具轮次上限都通过环境变量配置，服务端 schema 仍负责请求和资源边界校验。

空库启动后会创建固定用户名 `admin`，但不会生成可用的默认密码。首次打开页面时访问 `/setup` 完成一次性管理员密码设置，再使用普通登录流程；随后由管理员在 `/admin/users` 创建普通用户。

## 当前页面

- `/setup`：固定 `admin` 一次性密码设置。
- `/login`：本地账号登录。
- `/chat?session={conversation_id}`：新聊天和已有会话共用的聊天入口。
- `/agents`：当前用户的 private Agent 管理；`/agents?create=1` 直接打开新建表单。
- `/workspaces`：当前用户的 Agent Home 管理，并可查看可见的 global Workspace。
- `/knowledge`：当前用户的 Collection 与 Document 管理，并可查看可见的 global Collection。
- `/admin/agents`、`/admin/workspaces`、`/admin/knowledge`：管理员全局资源管理。
- `/admin/users`：管理员普通用户管理。

新会话的输入区提供 Agent、模型和附件选择。第一条消息发送后 Agent 锁定，模型仍可在会话空闲时切换。历史支持重命名和 soft delete；Agent 已不可用时输入区会禁用。

## 三类上下文来源

| 来源 | 权威与访问方式 | 是否进入 Qdrant |
| --- | --- | --- |
| 聊天附件 | 绑定一条 User Message；原文与可选解析文本存于 ObjectStore，在该消息进入有界历史窗口时加载 | 否 |
| Agent Home | 可写、可演进的 ObjectStore 文件；根 `AGENTS.md` 管理导航，模型使用 list/read/create/write 精确工具 | 否 |
| Knowledge | 用户或管理员显式维护的只读 Document；ObjectStore 保存原文和解析文本，模型按需调用 `search_knowledge` | 只有 Knowledge Document 的 chunk 投影进入 |

同一个 Agent 可以同时绑定 Agent Home 和一个或多个 Knowledge Collection。主聊天 LLM 决定是否调用已经授予的工具；用户隔离、路径、ETag、Collection/Document 范围、Qdrant filter、预算和持久化均由服务端确定性代码执行。

Knowledge 的入库、generation、直接原文与 RAG 检索规则见 [Knowledge Collection 数据流](docs/knowledge-data-flow.md)。

## 数据与存储

| 数据 | 权威源 | 说明 |
| --- | --- | --- |
| 用户、资源、会话、消息、附件元数据、Document 状态 | MySQL | 可审计业务状态；Message 是聊天请求与回复的权威记录 |
| Agent Home、聊天附件、Knowledge 原文与解析文本 | ObjectStore | development 为本地目录，production 为单一 S3-compatible backend |
| Knowledge chunk | Qdrant | 可删除并从权威源重建，不保存 Agent Home、聊天附件或日志 |

Agent、Workspace 和 Knowledge Collection 共用 `resources` 表的 owner 与生命周期列，但对外使用各自的类型化 API。新平台共有六张业务表：`users`、`resources`、`knowledge_documents`、`conversations`、`messages` 和 `attachments`。

development 默认使用 `OBJECT_STORE_BACKEND=local`；`OBJECT_STORE_LOCAL_ROOT` 为空时对象保存在 `DATA_DIR/objects`。production 必须显式配置 `APP_ENV=production`、JWT、单一 S3-compatible ObjectStore 和单个 FastAPI 进程，不会回退到 Local。完整配置、阿里云 OSS 兼容边界和备份要求见 [生产部署](docs/production-deployment.md)。

平台当前不依赖 Redis、Elasticsearch、Kibana、Celery 或其他消息队列。Qdrant 只服务 Knowledge 投影，不保存请求日志或 Message 权威数据。

## 重置本地数据

```sh
./reset-data.sh --dry-run
./reset-data.sh --yes
```

`./reset-data.sh --yes` 是显式破坏性本地操作，只清理本地 MySQL/Qdrant volume 和仓库内本地 runtime data。远端 S3/OSS 对象永不随该命令清空；应用也不会自动删除本地或远端用户数据。

## 运行与发布

| 模式 | 启动方式 | 用途 |
| --- | --- | --- |
| 开发模式 | `./start.sh` | MySQL、Qdrant 在 Docker 中运行，FastAPI、Next.js 在宿主机运行 |
| 生产模式 | ACR 版本镜像和 `deploy/compose.prod.yml` | 单机、单 FastAPI 进程，文件权威源为 S3-compatible ObjectStore |

代码合并到 `main` 只运行 CI，不自动创建版本或部署服务器。版本发布、Tag、ACR、ECS、Nginx、备份、回滚、结构化日志和 orphan audit 见 [生产部署](docs/production-deployment.md)。

## 开发检查

提交前从仓库根目录运行：

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

## 文档

- [文档地图](docs/README.md)
- [通用 Agent 平台架构](docs/workbench-architecture.md)
- [Knowledge Collection 数据流](docs/knowledge-data-flow.md)
- [生产部署](docs/production-deployment.md)

## 当前扩展边界

当前版本只支持单 FastAPI 进程。Agent Home、Knowledge 索引 Worker、SSE 和取消协调都在该进程中；增加 backend replica 前必须先设计跨进程流、锁和任务协调。未来代码能力使用独立的 Git/POSIX execution Workspace，不会把 Agent Home 或 ObjectStore 当作活跃代码工作树。
