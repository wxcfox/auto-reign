# Auto Reign 上下文治理与压缩设计

## 文档状态

- 日期：2026-07-22
- 状态：已批准，待实施
- 范围：历史重建、当前轮 Context 处理、每次模型调用前治理、摘要压缩、紧急降级和可观测状态
- 前置依赖：`2026-07-22-task-subtask-chat-history-design.md`
- 设计基线：Task/Subtask 历史权威、统一上下文预算、摘要压缩和 `messages_chain` 序列化契约

本文定义第二阶段的模型可见上下文治理。MySQL 保存完整原始历史；治理只改变某次模型调用实际看到的消息，不删除或改写原始 User/Assistant Subtask。

## 目标

1. 按本规格定义的规则从 Task/Subtask/SubtaskContext 重建模型历史。
2. 当前轮统一处理附件、Knowledge RAG 和 selected documents。
3. 在 Agent 每次调用模型之前重新计算真实 token 用量，而不是一轮只裁剪一次。
4. 采用来源级压缩、摘要压缩和紧急压缩三阶段机制。
5. 在 Assistant `result.messages_chain` 和 `result.context_compactions` 中保存必要的压缩痕迹。
6. 通过 WebSocket 向 UI 暴露上下文预算与压缩生命周期。

## 非目标

- 不删除、覆盖或合并 MySQL 中的旧 Subtask。
- 不把摘要单独保存为新的 Subtask。
- 不在后续轮重新注入 selected-document 正文。
- 不使用固定“最近 N 条消息”替代 token 治理。
- 不按字符近似代替消息级 token counter。
- 不让压缩模型直接写数据库。
- 不增加独立长期摘要表或第二套记忆系统。

## 实现边界

历史装配、token 计数、压缩决策、Runtime 执行和结果序列化必须保持独立接口。MySQL 原始历史是唯一权威，任何治理步骤只能生成单次模型调用使用的派生状态。

## 历史重建

每个 Assistant Subtask 启动前，按 `message_id` 升序读取当前 Task 中状态为 `COMPLETED` 或 `FAILED` 的历史 Subtask。

### User Subtask

1. 从 `prompt` 还原原始用户输入。
2. 一次查询取得该 User Subtask 下所有 `ready` 的 attachment 和 knowledge_base Context。
3. attachment 优先：
   - 图片从 `binary_data` 重新编码为 `data:<mime>;base64,...`；
   - 文档使用 `extracted_text`；
   - 视频能力不在当前 Auto Reign 范围内。
4. 文档内容包装为独立 `<attachment>...</attachment>` block。
5. Knowledge 检索内容包装为独立 `<knowledge_base>...</knowledge_base>` block。
6. attachment 具有绝对优先级，不在 history loader 中为了 Knowledge 主动截断；Knowledge 只使用默认 100,000 字符文本预算扣除 attachment 长度后的剩余空间，必要时截断或完全跳过。
7. selected_documents 不参与历史查询，因此不会在后续轮重复注入。

SubtaskContext 是 Context 内容的权威源；不能从旧 prompt 中的嵌入副本恢复附件或 Knowledge。

### Assistant Subtask

- `FAILED`：只返回非空 `result.value`，不展开 `messages_chain`。
- `COMPLETED` 且有 `messages_chain`：返回完整 assistant/tool 序列。
- 无 chain：退回到单条 `result.value`。
- chain 中保留 tool call、tool result、reasoning、模型信息和压缩标记。

## 当前轮 Context

当前 User Subtask 在 Agent 执行前处理：

### Attachment

- 从 MySQL SubtaskContext 读取二进制、Base64 和解析文本。
- 图片以模型多模态 block 注入；文本附件使用带来源信息的 attachment block。
- 附件内容中的指令视为不可信用户来源，不能覆盖 System Prompt 或工具权限。

### Knowledge Base

- 根据当前用户输入和 Agent Knowledge scope 执行 RAG。
- 检索结果包含查询、正文、来源、chunk 数量和检索模式。
- 结果 best-effort upsert 为当前 User Subtask 的 `knowledge_base` Context。
- Context 持久化失败记录安全日志，但不让已经可继续的主回答失败。

### Selected Documents

- `type_data` 只包含 Knowledge ID 与 Document ID。
- 当前轮加载相应 Document 的解析文本。
- 估算内容能放入直接注入预算时直接注入。
- 超过预算时转为 RAG，不强行塞入完整原文。
- 后续历史不重新读取这些引用。

## 模型前治理边界

等价的 `UnifiedContextGuard` 作为 LangGraph Agent 的 `pre_model_hook` 接入。它在每次模型调用前运行，包括：

- 首次回答前；
- 每次工具结果返回、准备再次调用模型前；
- 同一 Assistant Subtask 中的任意后续模型循环。

治理依据是该时刻模型实际可见的完整消息列表，而不是数据库 Subtask 数量或本轮开始时的估算。

## Token 预算

模型配置提供 context window、可选可信 output limit 和可选 auto-compact hard limit。

```text
ratio_reserve = context_window × 10%
reserved_output = clamp(ratio_reserve, 16_000, 48_000)

若 output limit 明确可信：
reserved_output = min(reserved_output, output_limit)

reserved_output = min(reserved_output, context_window / 2)
available_input = context_window - reserved_output
trigger_limit = available_input × 90%
target_limit = available_input × 70%
```

如果配置了 auto-compact hard limit：

- `trigger_limit` 取上述值与 hard limit 的较小值；
- `target_limit` 不能大于 `trigger_limit - 1`。

未知模型使用明确的保守默认值：context window 为 128,000，trigger 为 available input 的 85%，target 为 65%，output cap 视为不可信。所有用量由 provider-aware 消息 token counter 计算，包含 role、content、tool calls 和必要协议开销。

## 三阶段治理

### 阶段一：来源级压缩

每次 guard 执行时都运行。每种大内容来源通过独立 adapter 判断、压缩和标记：

- attachment/Knowledge 文本；
- tool output；
- 后续可能注册的其他明确来源。

普通策略跳过已带 `additional_kwargs.compacted=true` 的消息，避免重复包裹压缩结果。来源还可以把不可安全截断的内容标记为 request-compaction bypass。

### 阶段二：请求级摘要压缩

当前用量超过 `trigger_limit` 时，优先调用当前模型生成摘要。摘要请求固定要求输出：

```text
Current objective
Key completed work
Important findings
Next step
```

压缩任务输入包括当前模型可见消息，并先清理孤立 ToolMessage 和没有对应结果的未完成 tool call。

如果压缩任务本身超限：

1. 保留当前用户消息。
2. 按 `remove_oldest` 顺序移除最旧历史项。
3. 每次重新计算压缩请求 token。
4. 达到不可再缩小的 floor 后仍超限，则判定摘要不适用。

成功后的替代历史由以下内容组成：

- 初始 System Context；
- 最近的真实 User 消息，总计最多约 20,000 tokens；
- 一条 `[COMPACT SUMMARY]` HumanMessage。

摘要消息标记：

```json
{
  "compacted": true,
  "summary_compacted": true,
  "summary_compact_version": 1
}
```

### 阶段二 fallback

摘要失败、不适用或执行后仍超限时，运行请求级紧急压缩策略：

- 历史裁剪；
- attachment/Knowledge 截断；
- tool result 截断。

该 fallback 是保护路径，不取代摘要作为首选方案。

### 阶段三：紧急压缩

如果仍超过 `trigger_limit`：

1. 按 token 体积从大到小处理来源拥有的消息。
2. 使用各来源更严格的 emergency policy。
3. 每次缩减后重新计数，降到阈值下即停止。
4. 已被标记为 bypass-protected 的 payload 不做不安全截断。

如果最终仍超限且 protected payload 阻止进一步安全压缩，抛出明确的 context-guard fail-fast 错误，要求缩小请求或避免直接注入。本次模型调用不得继续发送。

## 压缩持久化

压缩只改变当前 LangGraph live state。原始 Task/Subtask/SubtaskContext 不删除、不改写。

当前 Assistant Subtask 完成时：

- `result.messages_chain` 保存本轮生成的 Assistant/Tool 消息；
- 如果 live state 中存在摘要产物，同时保存带 compact markers 的摘要 User/System 消息；
- `result.context_compactions` 保存本轮压缩事件。

每条摘要事件至少包含：

```text
type = summary_compact
status = started | completed | fallback
before_tokens
after_tokens?
trigger_limit
target_limit
used_legacy_fallback
failure_reason?
summary_message_id?
removed_history_items?
created_at
```

图片原始 Base64 不写入 messages_chain；历史需要图片时从 SubtaskContext 二进制重建。

## 实时状态与 UI

每个主要阶段通过 `chat:status_updated` 向 Task room 推送：

- `context_window`；
- `reserved_output_tokens`；
- `available_input_tokens`；
- `used_input_tokens`；
- `remaining_input_tokens` 和百分比；
- UI 展示口径的剩余 tokens 和百分比；
- `trigger_limit`、`target_limit`、`is_over_trigger`；
- 可选的 summary compact started/completed/fallback 事件。

最新 snapshot 同步缓存到 Redis。客户端刷新或重连时，`task:join` ACK 返回该 snapshot。UI 只展示后端计算结果，不在浏览器重复实现 token 预算算法。

## 错误处理

- 历史中的单个无效 Context 不得造成越权读取；不可见 Context 按错误处理，不能静默读取其他用户数据。
- RAG Context 持久化失败不影响主回答，RAG 查询本身失败则按 Agent 工具失败进入 chain。
- 摘要调用失败进入 fallback，并记录不含敏感 prompt 的 failure reason。
- tool call/result 链接无效时，Assistant result 序列化 fail-fast，不能保存不可重建 chain。
- guard fail-fast 时 Assistant 进入 `FAILED`，Task 进入 `FAILED`，通过 `chat:error` 返回安全错误。

## 验收标准

至少覆盖：

- User/Assistant 历史重建及状态过滤；
- attachment 优先、Knowledge 剩余空间和图片二进制重建；
- selected documents 当前轮 direct/RAG 分流与历史不重放；
- 每次模型调用前都执行 guard；
- output reserve、trigger、target、hard limit 边界；
- provider-aware token counter；
- 来源级普通压缩及 compacted 跳过；
- 摘要成功、空摘要、模型异常、压缩任务超限和 remove-oldest floor；
- legacy fallback 和 emergency 最大来源优先；
- protected payload fail-fast；
- compact markers、messages_chain 和 context_compactions 持久化；
- `chat:status_updated` 和 `task:join` snapshot 恢复；
- 不记录完整敏感 prompt、附件正文或工具结果到日志。

集成测试使用确定性模型与 token counter test double，并补充真实 MySQL、Redis 和 WebSocket 流程；最终运行后端、前端及 compose 权威检查。

## 实施边界

本规格在 Task/Subtask 历史权威稳定后实施。Context loader、current-turn preparer、token counter、source adapter、summary compactor、guard 和 metrics tracker 必须保持独立边界，避免重新堆回单个 GenerationService。
