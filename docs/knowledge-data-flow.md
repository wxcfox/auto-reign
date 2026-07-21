# Knowledge Collection 数据流

聊天附件、Agent Home 和 Knowledge 是三种不同来源：聊天附件绑定一条 User Message，并在该消息进入有界历史窗口时作为消息上下文；Agent Home 是可写、可演进的长期文件权威源，通过精确文件工具访问；Knowledge 是用户或管理员显式维护的只读参考资料，只有 Knowledge Document 会进入 Elasticsearch 或 Qdrant Retriever。

本文是 Knowledge Collection、Knowledge Document、ObjectStore、索引 Worker、共享 Retriever 与聊天 Runtime 的当前数据流权威。通用资源、权限、Runtime 和预算边界见[通用 Agent 平台架构](workbench-architecture.md)。

## 当前范围

当前实现包括：

- `resources` 表中的 `knowledge_collection` typed resource；
- `/api/knowledge-collections` 和管理员领域 API；
- `/knowledge` 与 `/admin/knowledge` 管理页面；
- Agent `knowledge_scopes` 的整库或 Document 子集绑定；
- `knowledge_documents` 状态、对象引用、内容 hash 和 `index_generation`；
- 显式 Document 上传、预览、下载、重新索引和删除；
- 只从持久状态领取工作的进程内索引 Worker；
- generation 专属解析对象和 Retriever chunk；
- 绑定 Knowledge 后才向主聊天 LLM 暴露的 `search_knowledge(query)`。

同一个 Agent 可以同时绑定 Agent Home 和多个 Knowledge Collection。两类 Capability 互不复制，主 LLM 按当前问题决定是否及以何种顺序调用。

## 数据权威边界

| 存储 | 权威职责 |
| --- | --- |
| ObjectStore | Knowledge Document 原始文件和 generation 专属完整解析文本 |
| MySQL | Collection/Document owner、Retriever 配置、对象 Key、内容 hash、状态、当前 `index_generation`、错误和时间戳 |
| Elasticsearch | 可重建的原文 chunk、dense vector、BM25 字段和 metadata 投影 |
| Qdrant | 可重建的原文 chunk dense vector 和 metadata 投影 |

Elasticsearch 和 Qdrant 都不保存业务权威数据。删除索引、索引损坏或服务不可用，都不能删除或改写原始文件和完整解析文本。恢复索引只能从 ObjectStore 与 MySQL 当前 generation 重建，不能从 Retriever、聊天回答或生成式摘要反推来源。

## Collection 检索配置

每个 Knowledge Collection 固定保存以下配置：

- `retriever_type`：`elasticsearch` 或 `qdrant`，默认 `elasticsearch`；
- `retrieval_mode`：`vector`、`keyword` 或 `hybrid`，默认 `vector`；
- `top_k`：默认 5，范围 1～10；
- `score_threshold`：默认 0.5，范围 0～1；
- `vector_weight` 和 `keyword_weight`：默认 0.7/0.3，混合检索时先按总和归一化；
- `chunk_size` 和 `chunk_overlap`。

Elasticsearch 支持三种检索模式，Qdrant 只支持 `vector`。创建表单切到 Qdrant 时会把 `keyword` 或 `hybrid` 明确重置为 `vector`，后端仍会独立拒绝非法组合，不做静默降级。Retriever 是创建时确定的不可变配置：创建后编辑界面只读展示，更新 API 即使继续携带当前值也会拒绝任何类型变化。Retriever 是平台级共享基础设施；连接地址、认证和索引名称来自部署配置，Collection 只保存类型，不保存凭证。

## 入库与重新索引

```mermaid
flowchart LR
    A[用户显式上传 Document] --> B[ObjectStore 保存原文]
    B --> C[MySQL 写入 uploaded/queued 与 generation]
    C --> D[确定性解析原文]
    D --> E[ObjectStore 保存 generation 专属解析文本]
    E --> F[按原文切 chunk]
    F --> G[Embedding]
    G --> H{Collection Retriever}
    H -->|Elasticsearch| I[写入原文、dense vector 与 metadata]
    H -->|Qdrant| J[写入 dense vector 与 metadata]
    I --> K[MySQL 条件更新 ready]
    J --> K
```

Document 状态主线为：

```text
uploaded -> queued -> processing -> ready
                            \-> failed
```

规则如下：

1. 上传必须针对明确 Collection；聊天附件不会隐式创建 Knowledge Document。
2. 接口先把原始文件写入 ObjectStore，再在 MySQL 创建可追踪 Document；任一步失败都不能留下伪装成 ready 的记录。
3. 文档解析和 chunk 由确定性应用代码完成。LLM 不改写原文或决定持久化格式，Embedding 模型只生成向量。
4. 每次上传或重新索引使用新的 `index_generation`；完整解析文本和 point 都属于该 generation。
5. Worker 只有在任务 generation 仍是 Document 当前 generation 时，才能把 MySQL 状态条件更新为 `ready`。
6. 迟到旧任务不能覆盖当前状态；其对象或索引记录即使清理失败残留，也不能进入直接原文或 Retriever 检索。
7. 新 generation 发布后再 best-effort 清理同一 Retriever 中的旧解析对象和索引记录。清理失败不改变当前可见内容，也不删除 source object。
8. `failed` 不无限自动重试；用户通过管理界面显式重新索引。启动时只恢复持久化 queued 或超时 processing 工作。

Embedding 请求由 provider-compatible wrapper 逐个发送 chunk，每个 HTTP 请求只包含一个文本。这样不限制文档可以生成的 chunk 数量，同时兼容 Qwen 等对批量输入有严格上限的服务。网络错误、超时、429 和 5xx 只在请求层做有限指数退避；确定性的 4xx 不重复请求。连续失败后当前 generation 进入 `failed`，不会无限重放整个索引任务。

显式重新索引遵循 generation 去重：文档处于 `queued` 或未超时的 `processing` 时，重复请求返回当前任务，不创建新的 generation；processing 超过 `KNOWLEDGE_WORKER_PROCESSING_TIMEOUT_SECONDS` 后才允许新的 generation 接管。修改 chunk size 或 overlap 会为 active Document 创建新 generation 并排队重建；检索模式、Top K、阈值和 hybrid 权重只影响查询，不触发向量重建。Retriever 创建后不可修改。新的 generation 发布前，旧任务不能写入或发布其解析对象和 Retriever 投影。

`index_generation` 同时隔离 MySQL 状态、parsed object 和 Retriever 记录，不只是一个展示字段。

`20260720_0005` 不兼容历史 Collection 检索配置或旧 Qdrant-only 投影。迁移会把历史 Collection 配置直接重置为 Elasticsearch、vector、默认 Top K、阈值、hybrid 权重和分块参数，并把所有历史 active Document 固定绑定到 Elasticsearch，清空旧 generation 的 parsed pointer、索引时间、失败与 attempt 状态，递增 `index_generation` 并改为 `queued`。Worker 随后从仍保留的 source object 重新解析并建立新投影；完成前 Document 不能以虚假的 `ready` 进入直接原文或 RAG。系统不双读旧配置或旧 Qdrant generation，也不把旧索引当作迁移来源。

## 统一 Retriever 投影

Factory 根据 Collection 的 `retriever_type` 返回 `ElasticsearchRetriever` 或 `QdrantRetriever`。业务层只调用统一接口完成 generation upsert、retrieve、generation/Document 删除、Collection purge、连接测试和能力查询，不直接依赖 SDK。

每个索引记录的稳定 ID、正文和 metadata 都必须能由服务端校验，metadata 至少包含：

- owner；
- Collection ID；
- Document ID；
- `index_generation`；
- 内容 hash；
- chunk 起止位置和顺序；
- 文件名等来源标识；
- 确定性切分得到的原文片段。

Elasticsearch 保存原始 chunk 文本与 dense vector，因此同一份投影可执行 vector、BM25 和 hybrid；Qdrant 保存 dense vector。查询 filter 由应用根据认证用户、本轮冻结的 Agent scope、active Document 和当前 generation 强制添加。模型只能提交 `query`，不能提交 owner、Collection、Document、generation、Retriever、mode 或 filter 来扩大范围。

旧 generation 记录可以在清理失败时残留，但任何查询都必须同时匹配有效 scope、active/ready 状态和当前 generation。Retriever 返回的 payload 不能直接信任；应用会回读权威解析对象并逐项验证 owner、Collection、Document、generation、hash、字符范围和逐字内容。

## Agent 绑定语义

Agent `knowledge_scopes` 支持多个 Collection：

- `document_ids=null` 表示整库；以后新增并进入 ready 的 Document 自动进入有效范围；
- 非空 `document_ids` 表示精确子集；
- 空数组没有语义，拒绝保存；
- global Agent 只能绑定 global Collection；
- private Agent 可以绑定自己的 private Collection 或 global Collection；
- 保存 Agent 时在同一事务中校验 owner、可见性、active、tombstone 和 Document 归属。

每个新轮次解析最新 Agent 配置并冻结该轮有效 Knowledge scope。已经开始的工具循环不会因管理员中途修改 Agent 而改变范围，下一轮才读取新配置。

## 检索：直接原文与 RAG

主聊天 LLM 根据用户问题和 Agent Prompt 决定是否调用 `search_knowledge(query)`。平台不会在主模型前固定运行 Retrieval Agent，也不使用关键词规则强制每轮检索。

`search_knowledge` 使用确定性的 `auto` 路由：

1. 服务端根据用户、本轮 Agent scope、Collection/Document 状态和 generation 解析有效来源。
2. 若全部有效来源的完整解析文本能放入本轮剩余预算，直接读取并返回权威 parsed object。
3. 若完整原文超出预算，使用主 LLM 提供的 query，按每个 Collection 固定保存的 Retriever 和 mode 检索当前范围、当前 generation 的候选 chunk。
4. 应用回读权威解析对象，验证候选 metadata、hash、字符范围和逐字内容。
5. ToolResult 返回有界原文、Collection、Document、文件名、chunk 位置、score 和稳定引用。

只有第 3 步属于 RAG。直接原文与 Retriever 路径共享完全相同的权限、Document 状态、来源引用和上下文预算，不能因使用 RAG 扩大范围。

Retriever 不可用，或者候选 metadata、score、generation、hash、范围、parsed object 不合法时，工具 fail-closed 返回稳定的 `knowledge_unavailable`，不能伪装成“没有结果”。直接原文路径发现来源损坏时也不能回退到二手投影掩盖故障。

### vector、keyword 与 hybrid

- `vector`：Elasticsearch 和 Qdrant 都使用 query embedding 执行 dense vector 召回，并统一把 cosine 按 `(cosine + 1) / 2` 映射为闭区间 `0～1`；越界或非有限分数 fail-closed。随后应用 Collection 的 `score_threshold` 和 `top_k`，因此不同 Retriever 的候选可以按同一 score contract 确定性合并排序。
- `keyword`：只在 Elasticsearch 使用 BM25。当本批最高 BM25 score 大于 1 时，所有 score 除以该最高值；否则保留原 score。
- `hybrid`：Elasticsearch 分别召回最多 `top_k` 个 vector 候选和 `top_k` 个归一化 BM25 候选，以 chunk 稳定身份去重，缺失路分数按 0 计算。权重先按两者总和归一化，然后线性融合：`fused_score = normalized_vector_weight * vector_score + normalized_keyword_weight * keyword_score`。最后按 `fused_score`、稳定 chunk 身份确定性排序，应用 threshold，返回最多 `top_k`。

结果保留 `retrieval_mode`、`vector_score`、`keyword_score` 和 `fused_score`。多个 Collection 同时参与时，应用把各库结果合并后按 score 与稳定来源身份统一排序，再受工具最终结果数和上下文 token budget 限制。模型不能选择 Retriever、mode、权重或 filter，平台也不根据 query 动态切换 mode。

RAG 返回确定性切分得到的原文片段，不返回生成式摘要。当前不启用：

- query rewriting；
- 独立 Retrieval Agent；
- MultiQuery 或 HyDE；
- LLM rerank；
- 生成式摘要检索；
- Agent Home 文件索引。

## LLM 与确定性代码分工

| 环节 | 主聊天 LLM | Embedding 模型 | 确定性应用代码 |
| --- | --- | --- | --- |
| 判断是否需要 Knowledge | 决定是否调用工具 | 否 | 只提供已授权能力 |
| 生成检索 query | 是 | 否 | 校验 schema、长度和预算 |
| 解析 Agent 绑定范围 | 否 | 否 | 校验用户、Collection、Document、active 和 generation |
| 选择直接原文或 Retriever | 否 | 否 | 根据完整文本大小与剩余预算决定 |
| 文档解析与 chunk | 否 | 否 | 是 |
| 生成向量 | 否 | 是 | 调度并写入投影 |
| Retriever、mode、权重与 filter | 否 | 否 | 从 Collection 读取并执行 |
| 验证并回读权威原文 | 否 | 否 | 是 |
| 根据来源生成回答 | 是 | 否 | 提供片段和引用 |

LLM 没有 DB Session、ObjectStore client 或 Retriever client。它只能提出 schema 校验后的工具调用；权限、范围收窄、预算、状态机和持久化由应用代码执行。

Knowledge Tool audit 可以保存有界的 Collection、Document、文件名、generation、内容 hash、chunk 位置和 score 等来源身份，但不保存 Knowledge 正文、Object Key、query、Provider payload 或模型返回的任意 metadata。审计不是下一轮内容权威。

## 生命周期与删除

- private Collection/Document 使用实际用户 ID，只对 owner 可管理；
- global Collection/Document 使用 owner sentinel `0`，它不代表可登录用户；
- 只有 `ready + active + current generation` 的 Document 可进入检索；
- 被 active Agent 引用的 Collection 不能停用或删除；
- 被精确 `document_ids` 引用的 Document 不能删除；
- 整库绑定不阻止删除其中单个 Document。

允许删除的 Document 先在 MySQL 把 `is_active` 设为 false，并以 cleanup-pending 状态从检索范围隔离，再清理其固定 Retriever 的全部 generation 投影、parsed object 和 source object。外部清理失败时保留 `knowledge_cleanup_failed` 和 HTTP `202 cleanup_pending`，由管理者显式重试；系统不能静默改写 Agent 配置或把失败伪装成删除完成。

原文解析、Embedding 或 Retriever 写入失败不会删除 source object。用户可以在修复配置或外部服务后显式重新索引。

## 与附件和 Agent Home 的关系

```text
聊天附件 -> 所属 User Message 与有界历史窗口
聊天附件 -X-> Agent Home
聊天附件 -X-> Knowledge Document
聊天附件 -X-> Retriever

Agent Home -> ObjectStore 权威文件与精确文件工具
Agent Home -X-> Knowledge Document
Agent Home -X-> Retriever

Knowledge Document -> ObjectStore 权威原文/解析文本
Knowledge Document -> Elasticsearch 或 Qdrant 可重建 chunk 投影
```

用户上传内容、解析文本和检索 chunk 都是不可信来源，不能覆盖平台 Prompt、Agent Prompt、工具 schema、用户隔离或持久化协议。

## Worker 与恢复边界

索引 Worker 只把 `knowledge_documents` 持久状态作为领取、恢复与发布的权威。当前 Worker 与 SSE/取消运行在同一 FastAPI 进程，不使用内存队列、额外 Job 表、Redis、Celery 或独立 worker container。

进程退出不会丢失队列语义；重启从 MySQL 恢复安全工作。Elasticsearch 和 Qdrant 都可从 ObjectStore 与 MySQL 重建，不能成为恢复原文的来源。
