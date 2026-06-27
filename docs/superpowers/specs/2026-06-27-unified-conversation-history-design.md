# 统一对话历史设计

## 背景

当前“新面试”和“新学习”在前端交互上都是聊天流：用户输入、模型流式回复、结果写入本地工作区。差异在于后端持久化边界：

- 面试使用 `interview_sessions`、`interview_turns` 和 `reports`，侧边栏历史只读取面试会话。
- 学习只把结果写入 `inbox/`、`knowledge/` 和 `review/status.md`，前端聊天消息保存在内存里，刷新后不能从历史继续。

目标是让新面试和新学习都进入历史对话。历史侧边栏不显示“已完成”“处理中”之类状态标记；面试的特殊性只体现在达到指定轮次后，在最后一次回答或追问评价之后自动给出整体评价。

## 目标

1. 侧边栏历史改为“对话历史”，同时展示面试和学习。
2. 新学习提交后创建或更新学习对话，刷新后可从历史打开并继续追加学习笔记。
3. 新面试仍按当前面试状态机推进：出题、回答、追问、反馈、自动整体评价和报告归档保持现有行为。
4. 历史列表不暴露 `completed`、`resumable` 或类似状态标签。
5. 抽出统一对话接口，避免前端和后端继续把“历史”写死为 interview-only。
6. 不复制面试 transcript，不新增同一份数据的双写；面试历史仍以现有 interview 表为权威来源。

## 非目标

- 本阶段不把 `interview_sessions` 和 `interview_turns` 全量迁移为通用 message 表。
- 本阶段不删除现有 `/api/interview-sessions/*` 面试 API。
- 本阶段不改变上传资料和真实面试复盘入口。
- 本阶段不把旧本地历史数据迁入新学习对话；本地 MySQL 历史元数据可按新 schema 重新开始。

## 推荐架构

采用 Adapter 方式建立统一对话边界：

- `ConversationAdapter`：后端协议，负责把某一类业务会话投影为统一历史项和详情。
- `InterviewConversationAdapter`：读取现有 `InterviewService`、`interview_sessions` 和 `interview_turns`，把面试投影为对话历史。它不写通用消息表，避免复制面试记录。
- `LearningConversationService`：管理学习会话和学习消息。学习是目前缺失持久化的一侧，因此新增数据库表保存聊天流。
- `ConversationService`：聚合 adapters，提供统一 API 给前端。

这种结构让“历史对话”依赖统一接口，而不是依赖某个具体业务表。之后如果要把面试也迁到通用 message 表，可以替换 `InterviewConversationAdapter` 的内部实现，不需要再次改侧边栏契约。

## 数据模型

新增学习对话表：

```text
learning_sessions
  id
  title
  language
  chat_model_provider
  chat_model
  started_at
  updated_at

learning_messages
  id
  session_id
  role              # user, assistant, system
  message_type      # learning_input, learning_summary, error
  content
  artifact_id
  artifact_path
  metadata          # JSON，保存 summary/source 等结构化轻量引用
  created_at
```

学习会话的用户输入和助手总结以 `learning_messages` 为权威聊天记录；长期学习资产仍以工作区 Markdown 为权威来源。

统一对话 API response 不直接暴露内部表名：

```text
ConversationHistoryItem
  id
  kind              # interview, learning
  title
  href
  started_at
  updated_at
  last_message

ConversationDetail
  id
  kind
  title
  messages

ConversationMessage
  id
  role              # user, assistant, system
  message_type
  content
  created_at
  metadata
```

历史列表按 `updated_at` 倒序合并学习和面试。面试的 `updated_at` 使用 `ended_at`、最后一轮 `created_at` 或 `started_at` 中最新可用值；学习的 `updated_at` 来自 `learning_sessions.updated_at`。

## API 设计

新增：

- `GET /api/conversations`
  - 返回混合历史。
  - 不包含状态标签字段。
  - `href` 由后端给出，前端无需判断路由规则。
- `GET /api/conversations/{conversation_id}`
  - 返回统一详情。
  - 学习详情从 `learning_messages` 读取。
  - 面试详情由现有 `interview_turns` 投影为通用消息；专业面试页面仍可继续调用 `/api/interview-sessions/{id}` 获取完整状态机字段。
  - 前端历史跳转优先使用列表里的 `href`，不需要根据 kind 拼路由。

调整：

- `POST /api/workspace/learning-notes/stream`
  - request 新增可选 `conversation_id`。
  - response 增加 `conversation_id`。
  - 未传 `conversation_id` 时创建学习会话；传入时追加到已有学习会话。

保留：

- `/api/interview-sessions/*` 继续承担面试状态机。
- `/api/reports/*` 继续承担报告读取。

## 前端设计

侧边栏：

- `AppShell` 从 `listInterviewSessions` 改为 `listConversations`。
- 历史项显示标题和最后消息摘要，不再显示 completed/working/unavailable。
- 历史项直接使用后端 `href`，面试进入 `/interview?session=...`，学习进入 `/learn?session=...`。
- 事件名从 interview-only 扩展为 conversation changed。可以保留旧事件常量别名以减少改动，但新代码使用 `notifyConversationsChanged`。

学习页：

- `learn/page.tsx` 读取 `session` query，传给 `LearningWorkspace`。
- `LearningWorkspace` 支持 `sessionId`：
  - 无 `sessionId` 时是新学习。
  - 有 `sessionId` 时加载学习消息并恢复聊天流。
  - 提交学习笔记时把当前 `conversation_id` 传给后端。
  - 首次提交成功后记住返回的 `conversation_id`，并通知侧边栏刷新。

面试页：

- 继续使用 `InterviewWorkspace` 和现有面试 API。
- 历史侧边栏不显示状态；已产生整体评价的面试打开后仍显示完整对话和整体评价。

## 复用边界

本阶段复用的是“对话历史和聊天消息展示契约”，不是强行复用面试状态机。

可抽出的前端复用：

- 历史事件：统一为 conversation changed。
- 可选的小工具函数：把学习 response 转 Markdown、从消息构建聊天项。

暂不抽出的部分：

- `InterviewWorkspace` 的轮次、追问和整体评价流程。
- `LearningWorkspace` 的学习笔记整理流程。

原因是面试和学习的编排状态差异明显，过早合并成一个大组件会让控制流更难读。通过 adapter 统一外部接口即可满足当前需求。

## 迁移策略

新增 Alembic 迁移创建 `learning_sessions` 和 `learning_messages`。不迁移旧学习记录；旧的 `inbox/`、`knowledge/` 和 `review/status.md` 文件继续保留为长期资产。

因为用户允许本地历史元数据和数据库数据不保留，迁移不做复杂旧数据回填。迁移不得删除 `DATA_DIR/workspace` 下的用户文件。

## 错误处理

- 学习内容已经写入 workspace 但学习消息写入失败时，API 返回明确错误；不删除已保存的 Markdown 资产。
- 加载某个学习会话不存在时返回 404。
- `conversation_id` 类型不匹配时返回 404 或 400，不把学习写到面试会话。
- 侧边栏历史加载失败时显示空历史，不阻塞主页面。

## 测试计划

后端：

- 学习笔记首次提交会创建学习会话、两条消息，并返回 `conversation_id`。
- 同一 `conversation_id` 再次提交会追加消息并更新 `updated_at`。
- `GET /api/conversations` 同时返回面试和学习，按更新时间倒序，不返回状态标签。
- `GET /api/conversations/{id}` 可以加载学习详情。
- 面试达到目标轮次后仍在最后一次反馈后生成整体评价。
- Alembic schema 测试覆盖新增表。

前端：

- `AppShell` 使用 conversation 历史，展示面试和学习，且不显示 completed/working 状态。
- `LearningWorkspace` 首次提交后通知历史刷新。
- 从 `/learn?session=...` 打开学习历史后可看到旧消息并继续追加。
- `InterviewWorkspace` 现有自动整体评价测试保持通过。

验证命令：

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
