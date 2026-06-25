# RAG 轻量增强设计：LangChain 组件化接入

## 背景

Auto Reign 当前 RAG 主流程已经从旧 `documents/document_chunks` 路径迁移到 workspace artifact 路径：长期资产保存在 `DATA_DIR/workspace/`，MySQL 保存 artifact 投影和 active Qdrant collection，Qdrant 保存可重建向量索引。面试流程在出题、回答点评和追问点评前读取候选人画像、目标画像、掌握状态、复习状态、高频问题，再结合 workspace 检索片段生成上下文。

当前短板主要集中在 RAG 质量层：

- 切块使用固定字符窗口，不能利用 Markdown 标题、题卡结构和项目段落。
- query 直接由自然语言意图、题目和回答拼接生成，缺少可解释的 query plan。
- Qdrant 检索只做 dense top-k，缺少 metadata filter、score threshold、去重、多样性和上下文预算。
- 旧 `/api/documents/*` 与 `/api/rag/search` 仍代表 legacy RAG 入口，和 workspace artifact 体系分叉。
- 依赖中已有 LangChain，但实际业务代码未使用，RAG 组件能力没有沉淀成可替换接口。

本设计采用轻量增强路线：Auto Reign 继续负责数据和业务语义，LangChain 负责 splitter、embedding、vectorstore、retriever 组件。

## 目标

1. 统一 RAG 到 workspace artifact 体系，删除旧 documents/RAG API 和相关模型。
2. 用 LangChain 组件替换自研的基础 RAG 胶水：切块、embedding、Qdrant vectorstore、retriever。
3. 保留 Auto Reign 对文件协议、provenance、active collection、索引生命周期和面试语义的控制。
4. 提升面试出题与点评场景的召回质量、上下文可解释性和失败降级能力。
5. 为后续 hybrid retrieval、rerank 和离线 RAG 评测留下清晰接口。

## 非目标

- 不引入独立 RAG runtime。
- 不引入 Elasticsearch、Milvus 或新的持久存储。
- 不把面试流程改成 LangChain Agent。
- 不让 LangChain 直接写文件、数据库或 workspace artifact。
- 不兼容 legacy `documents`、`document_chunks` 数据；旧数据不需要迁移。
- 不自动删除用户本地 `data/` 目录，破坏性数据重置仍必须显式执行。

## 总体边界

Auto Reign 负责：

- workspace 目录协议、artifact front matter、source/evidence provenance。
- artifact 投影、processing/index status、active Qdrant collection 切换。
- 哪些 artifact 可索引、哪些不可索引。
- 面试业务 query 的语义解释、上下文优先级和 prompt 注入边界。
- practice、mastery、review/status 和 high-frequency 的更新规则。

LangChain 负责：

- 把 artifact 正文切成带 metadata 的 `Document` chunk。
- 通过 `Embeddings` 接口生成向量。
- 通过 Qdrant vectorstore 写入和检索向量。
- 暴露 retriever 接口，供 Auto Reign 的 query planner 和 context assembler 调用。

## 架构设计

新增或调整以下后端模块：

- `EmbeddingService`：封装 LangChain embedding。支持 OpenAI 和 Qwen 的 OpenAI-compatible 配置，并保留 deterministic test embedding。
- `ArtifactDocumentBuilder`：把 workspace artifact 转成 LangChain `Document`，metadata 包含 `artifact_id`、`artifact_kind`、`relative_path`、`source_refs`、`evidence_refs`、`revision` 和标题层级。
- `ArtifactTextSplitter`：先按 Markdown 标题切分，再按递归字符策略二次切分。无法解析 Markdown 结构时退回递归字符切分。
- `WorkspaceVectorStore`：封装 LangChain Qdrant vectorstore，仍复用现有 `QdrantClient` 和 active collection 命名策略。
- `RetrievalQueryPlanner`：根据出题、回答点评、追问点评场景生成 `RetrievalQueryPlan`，包含 semantic query、keywords、artifact kind filter、path prefix filter、limit、score threshold 和 purpose。
- `WorkspaceRetriever`：根据 query plan 调用 LangChain retriever，传入 metadata filter，返回标准化 `RetrievedContext`。
- `RetrievalPostProcessor`：执行 score threshold、同一 artifact 去重、来源多样性、内容长度裁剪和诊断统计。
- `ContextAssembler`：把直接读取的小文件上下文、项目材料和检索片段按预算合并为 prompt context。

`IndexService` 继续负责 collection 重建和 active collection 切换，但内部用 LangChain splitter、embedding 和 Qdrant vectorstore 完成 chunk 写入。`WorkspaceRetrievalService` 继续是面试服务的唯一检索入口，但内部委托给 query planner、retriever 和 postprocessor。

## 数据流

索引流程：

1. `WorkspaceService.rebuild_projection` 扫描 workspace 文件和 sidecar 元数据，更新 `artifacts` 投影。
2. `IndexService.rebuild_index` 创建新的 rolling collection。
3. 对每个可索引 artifact，`ArtifactDocumentBuilder` 读取正文并生成 LangChain `Document`。
4. `ArtifactTextSplitter` 生成 chunk，并继承 artifact metadata 与 Markdown 标题 metadata。
5. `EmbeddingService` 使用 LangChain embedding 生成向量。
6. `WorkspaceVectorStore` 写入 Qdrant。
7. 所有可处理 artifact 完成后切换 `workspace_settings.active_collection`。
8. 旧 collection 和 orphan collection 仍按现有逻辑清理。

检索流程：

1. 面试服务根据场景调用 `WorkspaceRetrievalService.search`。
2. `RetrievalQueryPlanner` 生成 query plan。
3. `WorkspaceRetriever` 根据 active collection、semantic query 和 metadata filter 调用 LangChain retriever。
4. `RetrievalPostProcessor` 过滤低分结果、去重、增加来源多样性并记录诊断信息。
5. `ContextAssembler` 把直接读取的小文件、项目材料和检索片段按预算合并。
6. Prompt 中的检索片段继续标注来源，并明确视为不可信用户材料。

## Legacy 删除

本变更删除以下 legacy 路径：

- `/api/documents/*`
- `/api/rag/search`
- `DocumentService`
- legacy `RagService.index_document`
- legacy `RagService.search`
- legacy `Document`、`DocumentChunk` SQLAlchemy model
- 对应的 `documents`、`document_chunks` 表迁移保留历史文件，但新增 migration 可直接 drop 表。

`RagService` 不再作为 RAG 服务存在。若保留文件名，会改造成短期兼容导出；更推荐拆分为 `EmbeddingService` 并删除旧类名。

## LangChain 使用方式

建议依赖：

- `langchain`
- `langchain-openai`
- `langchain-text-splitters`
- `langchain-qdrant`

embedding：

- OpenAI：`OpenAIEmbeddings(model=..., api_key=...)`
- Qwen：使用 OpenAI-compatible `base_url`、`api_key` 和 `model`
- 测试：实现 LangChain `Embeddings` 兼容的 deterministic embedding

splitter：

- Markdown artifact 优先使用 Markdown header splitter 保留标题层级。
- 二次切分使用 recursive character splitter 控制 chunk 大小和 overlap。
- 对 source/extracted/plain text 使用 recursive character splitter。

vectorstore/retriever：

- 通过 `QdrantVectorStore` 接入现有 Qdrant。
- 第一阶段使用 dense retrieval。
- retriever 必须支持 metadata filter、top_k 和 score threshold。
- hybrid retrieval 仅预留接口，不在第一阶段实现。

## 检索策略

第一阶段 query plan 使用确定性规则：

- 项目深挖：优先过滤 `artifact_kind=project`，必要时放宽到 `knowledge` 和 `practice`。
- 回答点评：查询由目标岗位、JD、当前题目、用户回答和考察点组成，过滤到 `knowledge`、`question_bank`、`project`、`high_frequency`、`practice`。
- 出题：查询由用户自然语言意图、岗位、JD、轮次和当前薄弱点组成，优先 `question_bank`、`knowledge`、`project`、`high_frequency`。
- 追问点评：查询包含原题、追问、追问回答和缺失点，优先 `question_bank`、`practice`、`knowledge`。

后处理规则：

- 默认返回候选结果多于最终注入数量，例如检索 12 条、最终保留 4 条。
- 同一 artifact 默认最多保留 1-2 条。
- 同一 `artifact_kind` 不应垄断全部结果，除非 query plan 明确只查某一种 kind。
- 低于阈值的结果不进入 prompt。
- prompt context 按预算裁剪，优先保留直接小文件、项目材料、题库/知识卡和高分检索片段。

## 错误处理与降级

- active collection 不存在或为空：返回空检索结果，不阻塞面试。
- embedding provider 不可用：面试流程可以继续，但记录诊断日志；诊断 API 可展示 index stale 或 retrieval failed。
- Qdrant 查询失败：返回空结果并记录错误，不把底层错误暴露给普通用户。
- 索引重建失败：保留旧 active collection，不切换到部分新 collection。
- 单个 artifact 切块或读取失败：标记为 stale，不影响其他 artifact。

## 测试策略

后端测试覆盖：

- legacy `/api/documents/*` 和 `/api/rag/search` 已删除。
- migration 删除 legacy 表。
- Markdown 标题切块保留标题 metadata。
- 不同 artifact kind 使用正确索引规则。
- Qwen/OpenAI embedding 通过 LangChain wrapper 调用正确参数。
- deterministic embedding 支持测试稳定性。
- Qdrant vectorstore upsert/search 使用 artifact metadata。
- query planner 针对出题、回答点评、项目深挖和追问点评生成正确 filter。
- postprocessor 执行 score threshold、去重和来源多样性。
- 面试服务仍能读取直接 workspace context，并把检索片段注入 prompt。
- Qdrant 或 embedding 失败时面试流程降级继续。

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

## 迁移影响

新增 migration 直接删除 legacy `documents` 和 `document_chunks` 表。无需数据兼容和迁移。已有本地 `data/` 文件不会自动删除；用户如果需要清空旧数据，仍需显式运行仓库提供的重置命令。

`workspace_settings.embedding_config` 后续可以记录 LangChain embedding provider、model、base_url 标识和 embedding dimension，用于检测 collection 与 embedding 配置不匹配。第一阶段至少在日志和诊断中记录 provider/model/collection。

## 文档影响

需要更新：

- `README.md`：删除 `/api/documents` 和 `/api/rag/search` 的旧说明，说明资料入库统一走 workspace API。
- `docs/workbench-architecture.md`：补充 LangChain 只负责 RAG 组件，Auto Reign 负责数据语义。
- `docs/knowledge-data-flow.md`：更新切块、embedding、vectorstore 和 retriever 的实现描述。

## 阶段划分

阶段 1：删除 legacy RAG 入口和模型，保持 workspace RAG 现有行为通过测试。

阶段 2：引入 LangChain splitter 和 embedding wrapper，保持 Qdrant 写入和检索结果结构不变。

阶段 3：引入 LangChain Qdrant vectorstore/retriever，支持 metadata filter、top_k 和 score threshold。

阶段 4：加入 query planner、postprocessor 和 context budget，优化面试出题与点评召回质量。

阶段 5：补充 RAG 诊断日志和 golden set 召回测试。
