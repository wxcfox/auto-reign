# Auto Reign Task/Subtask 聊天历史与 WebSocket 设计

## 文档状态

- 日期：2026-07-22
- 状态：已批准，待实施
- 范围：以 Task、Subtask、SubtaskContext 替换 Conversation、Message、Attachment，并将网页聊天切换为 WebSocket Task room
- 数据兼容：不兼容旧聊天表、旧 API、旧前端类型和旧附件路径；用户已显式执行 `./reset-data.sh --yes`
- 设计基线：Task/Subtask 历史权威、原地重试、SubtaskContext、Socket.IO Task room 和 block 事件契约

本文定义第一阶段的历史权威与网页聊天协议。目标是建立适合 Auto Reign 单 Agent 聊天的完整行为契约，只保留实际使用的字段和能力。

## 目标

1. 数据库、后端、API 和前端统一使用 Task/Subtask 命名。
2. 一个 Task 表示一个持续聊天任务；一次用户输入和一次完整 Agent 回合分别保存为 User Subtask 与 Assistant Subtask。
3. Assistant 的工具调用、工具结果、最终回答、模型信息和压缩标记完整保存在同一个 `result.messages_chain` 中。
4. 聊天附件、图片、解析文本和 Knowledge 检索内容以 MySQL `subtask_contexts` 为权威源。
5. 网页聊天使用 Socket.IO `/chat` 命名空间和 `task:{task_id}` room，不再使用 SSE 主链路。
6. 实时 UI 以 text chunk 和 block created/updated 事件增量渲染，刷新后能从 MySQL 与 Redis 恢复。
7. 失败回合原地重试，不新增重试 Subtask。

## 非目标

- 不迁移 `conversations`、`messages` 或 `attachments` 中的数据。
- 不保留旧 API、旧前端类型、双读、双写或兼容别名。
- 不引入 Team、Bot、Group Chat、Executor、Pipeline、Subscription、Device 或 CRD 字段。
- 不为 `(task_id, message_id)` 增加唯一约束。
- 不为同一 Task 的发送增加数据库行锁或额外并发门闩。
- 不把工具调用和工具结果拆成独立 Subtask 或独立工具执行表。
- 不把 selected-document 正文复制进 `subtask_contexts`。

## 实现边界

数据库、后端 schema、HTTP/Socket.IO payload、前端类型和 UI 状态必须使用同一套 Task/Subtask 术语与状态。旧 Conversation/Message/Attachment 结构不作为新实现的推导来源或兼容入口。

## 数据模型

### Task

Task 替换 Conversation，使用 MySQL BIGINT 主键。核心字段：

```text
tasks
├── id                 BIGINT
├── user_id
├── agent_id
├── name
├── status             PENDING | RUNNING | COMPLETED | FAILED | CANCELLED
├── model_override_json
├── is_active
├── created_at
└── updated_at
```

约束与语义：

- `agent_id` 仍引用 Auto Reign Agent 资源；Task 不保存 Agent 配置快照。
- Agent 配置修改后，已有 Task 的下一轮继续解析最新配置。
- `model_override_json` 保存 Task 级用户模型覆盖。
- 不再使用 `idle/generating`。
- 后端在接收新输入前读取 Task 状态；`PENDING/RUNNING` 时拒绝普通发送，前端同步禁用输入。
- 发送流程不通过 `SELECT ... FOR UPDATE` 串行化。

### Subtask

```text
subtasks
├── id                 BIGINT
├── user_id
├── task_id
├── role               USER | ASSISTANT
├── message_id
├── parent_id
├── title
├── prompt             LONGTEXT
├── status             PENDING | RUNNING | COMPLETED | FAILED | CANCELLED
├── progress
├── result             JSON
├── error_message      TEXT
├── created_at
├── updated_at
└── completed_at
```

语义：

- `message_id` 是 Task 内的消息顺序号，不是 Subtask 主键。
- `parent_id` 指向上一条消息的 `message_id`，不指向 Subtask 主键。
- User Subtask 的原始输入保存在 `prompt`。
- Assistant Subtask 的执行结果保存在 `result`。
- 数据库不声明 `(task_id, message_id)` 唯一约束；顺序由服务层在创建成对 Subtask 时分配。
- 不保留 `team_id`、`bot_ids`、executor、group、sender 或 reply 字段。

Assistant `result` 的核心结构：

```json
{
  "value": "最终回答或已保存的部分回答",
  "messages_chain": [],
  "blocks": [],
  "context_compactions": [],
  "sources": [],
  "termination_reason": null
}
```

`messages_chain` 按 OpenAI-compatible message 结构保存本轮所有 Assistant/Tool 消息，并保留：

- assistant content；
- tool call ID、名称和参数；
- tool result 与 tool call ID 的关联；
- reasoning content；
- 每条 Assistant 消息的 provider/model 信息；
- `compacted`、`summary_compacted` 和版本标记。

写入前必须校验 tool call 与 tool result 链接完整。工具结果中的临时图片 Base64 不重复写入 `messages_chain`；图片原始数据由 SubtaskContext 保存。

### SubtaskContext

```text
subtask_contexts
├── id                 BIGINT
├── user_id
├── subtask_id         0 表示发送前草稿
├── context_type       attachment | knowledge_base | selected_documents
├── name
├── status             pending | uploading | parsing | ready | empty | failed
├── error_message
├── binary_data        LONGBLOB
├── image_base64       LONGTEXT
├── extracted_text     LONGTEXT
├── text_length
├── mime_type
├── file_extension
├── file_size
├── type_data          JSON
├── created_at
└── updated_at
```

约束与语义：

- `subtask_id=0` 是未绑定草稿的 sentinel，因此该列不声明外键。
- attachment 同时允许保存原始二进制、图片 Base64 和解析文本。
- `knowledge_base` 保存本轮 RAG 查询、检索正文、来源与模式等结果。
- `selected_documents.type_data` 只保存 Knowledge ID 和 Document ID 列表；正文在当前轮读取，不做快照。
- 删除旧 `attachments` 表及聊天附件 ObjectStore 读写路径。
- Knowledge Document 和 Agent Home 的 ObjectStore 权威结构不受本设计影响。

## 创建一轮聊天

对于已有 Task：

1. 校验用户权限、Agent 可用性和 Task 状态。
2. 查询 Task 当前最大 `message_id`。
3. 创建 User Subtask，`message_id=N`，`parent_id` 指向上一条消息。
4. User Subtask 作为已接收的用户输入直接置为 `COMPLETED`。
5. 创建 Assistant Subtask，`message_id=N+1`，`parent_id=N`，状态为 `PENDING`。
6. 将当前用户拥有且 `subtask_id=0` 的草稿 Context 绑定到 User Subtask。
7. 将 Task 置为 `PENDING` 并提交事务。
8. 返回发送 ACK 后异步启动 Agent。

对于新 Task，`chat:send.task_id` 为空；服务端在同一流程中创建 Task 和首对 Subtask。

不添加 Task 行锁、数据库 advisory lock、外部调度器或 message ID 唯一约束。并发保护使用 Task 状态检查和前端禁用行为。

## Context 绑定规则

- attachment ID 必须属于当前用户、处于 `ready`，且仍为 `subtask_id=0`。
- Knowledge Context 在发送事务中创建并绑定。
- selected documents 只保存引用，Agent 当前轮按引用加载直接原文或执行 RAG。
- 任一用户提供的 Context 不合法时，整次发送失败，不创建半套 Subtask。
- RAG 结果 Context 的持久化是 best-effort；检索结果写入失败不应让已成功的主回答失败。

## 历史读取

Task 详情和 Agent 历史都以 Subtask 为持久化单位，按 `message_id` 升序读取。

User Subtask：

- 返回 `prompt`、状态和 Context brief；
- 模型历史只重新注入 `ready` 的 attachment 与 knowledge_base Context；
- selected documents 不在后续轮重新注入。

Assistant Subtask：

- `COMPLETED` 且有 `messages_chain` 时展开完整 Assistant/Tool 链；
- 无 chain 时使用 `result.value`；
- `FAILED` 只使用已有 `result.value`，不展开失败工具链；
- `PENDING/RUNNING` 不进入已完成模型历史。

前端 Task 详情仍显示一条 User Subtask 和一条 Assistant Subtask，但把 Assistant 的 `messages_chain` 或 `blocks` 展开为结构化工具调用、工具结果与最终回答。

## WebSocket 协议

### 连接与 Task room

- Socket.IO 命名空间：`/chat`
- Socket.IO path：`/socket.io`
- Task room：`task:{task_id}`
- 客户端事件：`task:join`、`task:leave`、`chat:send`、`chat:cancel`、`chat:retry`
- 服务端事件：`chat:start`、`chat:chunk`、`chat:block_created`、`chat:block_updated`、`chat:done`、`chat:error`、`chat:cancelled`、`chat:status_updated`、`task:created`、`task:status`

`task:join` 请求：

```json
{
  "task_id": 123,
  "after_message_id": 8
}
```

首次加入返回全部 Subtask；重连可用 `after_message_id` 增量同步。若 Assistant 正在生成，ACK 还返回：

- `subtask_id`；
- 当前文本和 offset；
- 当前 blocks；
- 开始与最后活动时间；
- 最新上下文预算和压缩状态。

### chat:send 时序

`chat:send` 的 ACK 必须先于 `chat:start`：

```json
{
  "task_id": 123,
  "subtask_id": 456,
  "message_id": 9
}
```

其中 `subtask_id` 是已落库的 User Subtask ID。服务端在返回 ACK 后通过后台协程启动 Assistant，避免前端收到流事件时仍没有稳定持久化 ID。

### Block 协议

核心 block：

```text
TextBlock
├── id
├── type = text
├── content
├── status = streaming | done
└── timestamp

ToolBlock
├── id
├── type = tool
├── tool_use_id
├── tool_name
├── tool_input
├── tool_output
├── status = generating_arguments | pending | done | error
└── timestamp
```

事件行为：

1. 工具调用开始：`chat:block_created`。
2. 工具参数增量或完成：`chat:block_updated`。
3. 工具执行返回：`chat:block_updated` 写入 output，并置为 `done/error`。
4. 文本内容主要通过携带 `block_id`、`block_offset` 和全局 offset 的 `chat:chunk` 追加。
5. `chat:done` 使用最终持久化 result 收敛前端状态。

前端按 block ID 和 offset 幂等合并，不能用整段 Markdown 覆盖当前 Assistant 消息。

## Redis 运行态

Redis 只保存可丢失的运行态：

- Socket.IO manager 和 Task room 分发；
- Task 当前活动 Assistant Subtask；
- 流式文本、offset 和 blocks；
- 取消标记；
- 最新上下文预算与压缩事件。

MySQL 始终是历史权威。Redis 丢失最多导致未完成回合无法恢复增量，不能损坏已完成历史。

Socket.IO 使用 `AsyncRedisManager`。Redis 初始化失败时可以记录警告并退化为单进程内存 manager；健康检查必须暴露降级状态。开发与生产运行栈都应配置 Redis，以获得完整刷新恢复行为。

## 状态、失败、取消与重试

- Agent 开始时：Assistant 和 Task 进入 `RUNNING`。
- 成功时：保存完整 result，Assistant 和 Task 进入 `COMPLETED`，再发出 `chat:done`。
- 失败时：保存已有部分 `value` 和安全错误，Assistant 与 Task 进入 `FAILED`，发出 `chat:error`。
- 取消时：停止当前执行，保存允许保留的部分输出，将 Assistant 与 Task 置为 `CANCELLED`，清理 Redis 活跃态并发出 `chat:cancelled`。
- 断线不取消执行；客户端重连后重新 `task:join`。
- 进程中断留下的运行中回合必须转为可诊断的失败状态，不能伪造回答。

重试采用以下原地恢复契约：

1. 校验目标是当前用户可访问的失败 Assistant Subtask。
2. 将同一行重置为 `PENDING`。
3. 清空旧 `result` 和 `error_message`。
4. 重新执行，不创建新 Subtask。

## API 与前端边界

- REST 负责 Task 列表、Task 管理、Context 上传与非流式管理操作。
- 网页聊天发送、生成、取消、重试和实时状态走 WebSocket。
- 后端 schema、OpenAPI、前端 API 类型、状态容器和组件统一改为 Task/Subtask。
- 删除 Conversation/Message 的兼容导出与路由。
- 前端保留 loading、empty、error、cancelled、reconnecting 状态和 i18n。

## Schema 基线

用户已经显式执行 `./reset-data.sh --yes`。本次以空数据库为前提整理新的 Alembic 基线：

- 直接创建 `tasks`、`subtasks`、`subtask_contexts`；
- 不实现旧聊天表转换；
- 不为旧 schema 增加专项保护或过渡代码；
- 删除 Conversation/Message/Attachment 相关运行时代码与迁移定义。

## 验收标准

后端至少覆盖：

- Task 与成对 Subtask 创建、顺序和 parent 关系；
- 草稿 Context 绑定、权限、状态和失败回滚；
- MySQL 二进制、图片 Base64、解析文本及历史还原；
- selected documents 仅当前轮注入；
- 完整 `messages_chain` 及 tool call/result 链接校验；
- 失败历史不展开 chain；
- 原地重试；
- Task 运行状态拒绝重复发送；
- WebSocket 鉴权、room 隔离、ACK 时序和事件 payload；
- 全量加入、增量重连、活跃流恢复和 Redis 降级；
- 完成、失败、取消与进程中断恢复。

前端至少覆盖：

- 新 Task 临时状态收敛到真实 ID；
- ACK 绑定 User Subtask；
- block 创建、参数更新、结果和错误；
- 基于 block ID/offset 的幂等文本合并；
- 页面刷新和 Socket 重连；
- Task 运行期禁用发送；
- 失败回合原地重试；
- 历史与实时流渲染一致。

最终验证执行仓库权威命令，并增加真实 MySQL、Redis 和 Socket.IO 集成测试。

## 与后续规格的关系

- 上下文组装、模型前预算和摘要压缩见 `2026-07-22-context-governance-compression-design.md`。
- Knowledge Document 的 chunk 策略与父子检索见 `2026-07-22-knowledge-splitter-design.md`。
- 实施顺序先完成本规格，使 Task/Subtask 和 `messages_chain` 成为稳定权威，再接入上下文治理；Knowledge Splitter 可在历史模型稳定后独立实施。
