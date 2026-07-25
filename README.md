# Auto Reign

Auto Reign 是可自部署、多账号隔离的 Agent 聊天与知识工作平台。用户可以创建 private Agent，管理员可以维护 global Agent；每个 Agent 组合模型、系统提示词、可选 Agent Home 文件工具和可选 Knowledge 检索能力。

所有聊天都使用同一套 Task、Subtask 和 Agent Runtime。普通问答、学习辅助或其他用途由 Agent 配置与用户输入决定，不需要平台级专用会话类型。

## 核心能力

| 能力 | 当前实现 |
| --- | --- |
| Agent 聊天 | LangGraph `create_react_agent` 驱动 ReAct loop，按本轮权限绑定工具 |
| 实时交互 | Socket.IO `/chat` namespace 与 Task room，支持 text/tool block 增量事件 |
| 历史记录 | MySQL 保存 Task、User/Assistant Subtask、工具消息链和生成审计 |
| Agent Home | ObjectStore 中可写、可演进的长期文件，通过受控文件工具访问 |
| Knowledge | 显式维护 Collection 和 Document；Elasticsearch 支持 vector、keyword、hybrid，Qdrant 支持 vector |
| 文件上下文 | 聊天附件绑定 User Subtask，不会自动写入 Agent Home 或 Knowledge |
| 账号管理 | 固定 `admin` 一次性初始化；管理员创建、启停和重置普通用户 |

## 快速开始

需要 Docker Compose v2、Python 3.12+、`uv`、Node.js 22+ 和 pnpm 11.7。

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

至少配置一个模型 Provider API Key，例如 `QWEN_API_KEY`。`DEFAULT_CHAT_PROVIDER` 指定系统默认 Provider；默认模型取该 Provider 在 `*_CHAT_MODELS` 中的第一个模型。Provider 或模型不可用时会明确失败，不会静默切换。

空库启动会创建固定用户名 `admin`，但不会生成默认密码。首次访问 `/setup` 完成一次性管理员密码设置，之后由管理员在 `/admin/users` 创建普通用户。系统不提供公开注册。

完整配置由 `backend/app/core/config.py` 的 `Settings` 定义，开发示例见 [.env.example](.env.example)，生产示例见 [deploy/auto-reign.env.example](deploy/auto-reign.env.example)。

## 核心数据模型

平台共有六张业务表：

- `users`：账号与角色；
- `resources`：Agent、Agent Home 和 Knowledge Collection；
- `knowledge_documents`：Knowledge Document 状态与 generation；
- `tasks`：持续聊天任务；
- `subtasks`：User 输入和完整 Assistant 回合；
- `subtask_contexts`：附件、解析内容和选中文档快照。

一次用户输入保存为一条 User Subtask，整个 Agent 回合保存为一条 Assistant Subtask。Assistant `result.messages_chain` 保存模型消息、tool call、tool result、最终回答和模型信息。失败重试复用原 Assistant Subtask，不创建另一条重试记录。

## 上下文与存储

| 数据 | 权威源 | 说明 |
| --- | --- | --- |
| 用户、资源、聊天历史、附件、Document 状态 | MySQL | 持久业务权威 |
| Agent Home、Knowledge 原文与完整解析文本 | ObjectStore | 开发环境可用本地目录；生产必须使用 S3-compatible backend |
| Knowledge chunk | Elasticsearch 或 Qdrant | 可从 MySQL 与 ObjectStore 重建的检索投影 |
| 活跃流、block、offset、取消标记 | Redis | 带 TTL 的临时运行态，不是历史或备份权威 |

聊天附件、Agent Home 和 Knowledge 是三个独立来源：

- 聊天附件只随所属 User Subtask 进入有界历史；
- Agent Home 是模型通过文件工具维护的长期工作目录；
- Knowledge 是只读参考资料，只有 Knowledge Document 会进入 Retriever。

每轮由 Tool Registry 冻结可用能力，再通过 LangGraph 执行受工具轮次和上下文预算约束的 ReAct loop。当前上下文治理保留最新完整 Turn，并在每次模型调用前复核预算；摘要压缩仍属于待实施设计。

## 产品入口

- `/chat?task={task_id}`：创建或继续 Task；
- `/agents`、`/workspaces`、`/knowledge`：用户私有资源管理；
- `/admin/agents`、`/admin/workspaces`、`/admin/knowledge`：管理员全局资源管理；
- `/admin/users`：普通用户管理；
- `/setup`、`/login`：管理员初始化和账号登录。

新 Task 在发送第一条输入前可选择 Agent、模型和 Context。首条输入发送后 Agent 锁定；模型可在 Task 非运行态时切换或清除覆盖。Agent 已不可用时，已有 Task 仍可查看，但不能继续生成。

## 开发检查

较大行为变更先阅读[平台架构](docs/architecture/platform.md)与对应专题文档，并遵守[工程规范](docs/README.md#工程规范)。

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

远端 S3/OSS 对象不会被该命令清理。

## 文档

- [文档地图](docs/README.md)
- [平台架构](docs/architecture/platform.md)
- [Knowledge 架构](docs/architecture/knowledge.md)
- [生产部署与运维](docs/operations/production.md)
- [工程规范](docs/README.md#工程规范)
- [待实施设计](docs/README.md#待实施设计)

## 当前扩展边界

当前生产部署只支持单个 FastAPI 进程。Redis 已承载 Socket.IO manager 和实时流状态，但 Task 执行、Knowledge Worker、进程内取消和 ObjectStore 同 Key 串行化仍缺少多实例领取与故障转移协议。增加 backend replica 前必须先设计幂等任务领取、跨进程取消和副作用协调。
