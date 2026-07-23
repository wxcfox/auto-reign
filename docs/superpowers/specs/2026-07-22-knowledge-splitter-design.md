# Auto Reign Knowledge Splitter 设计

## 文档状态

- 日期：2026-07-22
- 状态：已批准，待实施
- 范围：规范化 splitter 配置、文件类型路由、Markdown 增强、层级索引与检索、Semantic Splitter
- 数据兼容：不兼容 Auto Reign 旧 splitter 配置或旧 chunk 投影；用户已显式执行 `./reset-data.sh --yes`
- 设计基线：可持久化的 splitter 配置、确定性文件路由、层级检索和 generation 原子切换

本文定义第三阶段的 Knowledge chunk 行为。目标不是把所有文件按固定字符数硬切，而是在语义完整、检索精准和返回上下文充足之间建立可验证的平衡。

## 目标

1. 使用一个严格、可持久化、可在后端与索引运行时共享的规范化 splitter 配置。
2. 默认采用 `flat + file_aware + 1024/50`，按文件类型选择真实 parser。
3. Markdown 先保留标题结构并合并低信息章节。
4. 支持 flat、hierarchical 和 semantic 三种策略。
5. Hierarchical 实现“检索 Child、返回 Parent、同 Parent 去重”。
6. Splitter 与 Auto Reign 当前 Elasticsearch/Qdrant Retriever 选择解耦，并保留 generation 原子切换。

## 非目标

- 不保留旧 `sentence`、`smart`、`semantic` legacy payload 兼容入口。
- 不把所有 splitter 的 `chunk_size` 强制统一为字符单位。
- 不新增 LLM 自动切块、LLM rerank、HyDE 或 MultiQuery。
- 不改变 Knowledge Document 原文和解析文本的 ObjectStore 权威结构。
- 不允许客户端自行实现与后端不同的默认配置。

## 实现边界

配置归一化、文件路由、node 构建、metadata enrich、Retriever storage 和 Parent merge 必须保持独立接口。前后端只共享规范化配置契约，不各自维护另一套默认值或推导规则。

## 规范化配置

每个 Knowledge Document 保存其实际生效的规范化 `splitter_config` JSON：

```text
NormalizedSplitterConfig
├── chunk_strategy       flat | hierarchical | semantic
├── format_enhancement   none | file_aware
├── flat_config?
├── hierarchical_config?
├── semantic_config?
└── markdown_enhancement
```

所有配置使用 Pydantic `extra=forbid`。只有当前 strategy 对应的 config 生效，其他 config 在规范化后为空。

### FlatConfig

```text
chunk_size       默认 1024，范围 128..8192
chunk_overlap    默认 200，范围 0..2048，且小于 chunk_size
separator        默认 "\n\n"
```

### HierarchicalConfig

```text
parent_chunk_size      默认 2048，范围 256..16384
child_chunk_size       默认 512，范围 128..8192
child_chunk_overlap    默认 64，范围 0..2048
parent_separator       默认 "\n\n"
child_separator        默认 "\n"
```

约束：

- `child_chunk_overlap < child_chunk_size`
- `child_chunk_size < parent_chunk_size`

### SemanticConfig

```text
buffer_size                       默认 1，范围 1..10
breakpoint_percentile_threshold   默认 95，范围 50..100
```

### MarkdownEnhancementConfig

```text
enabled   默认 false
```

## 运行时默认值

配置类的一般 Flat 默认是 `1024/200`，但文档入库不能直接使用该静态默认。空配置必须统一经过 runtime normalization：

```json
{
  "chunk_strategy": "flat",
  "format_enhancement": "file_aware",
  "flat_config": {
    "chunk_size": 1024,
    "chunk_overlap": 50,
    "separator": "\n\n"
  },
  "markdown_enhancement": {
    "enabled": true
  }
}
```

API 创建、后台索引、重新索引和测试必须复用同一 `normalize_runtime_splitter_config`，不能各自维护默认值。

## Flat 与 file-aware 路由

```text
.md          markdown_sentence
.txt         sentence
.pdf         recursive_character
.doc         recursive_character
.docx        recursive_character
其他         recursive_character fallback
```

### Markdown

1. 使用 MarkdownNodeParser 按标题结构生成初始 node。
2. 开启 markdown enhancement 时执行弱章节合并。
3. 把增强后的 node 转为带 metadata 的 Document。
4. 再使用 LlamaIndex SentenceSplitter 按 `chunk_size/overlap` 切分。

弱章节统一按以下规则判定：

- 空内容；
- 只有 `#` 到 `######` 标题；
- 去掉标题与 Markdown 符号后，有效正文少于 24 个字符。

连续弱章节累计为 prefix，合并到下一个有效章节；文档结尾只有弱章节时保留合并后的尾 node。

### TXT

使用 LlamaIndex SentenceSplitter，优先保持句子和段落边界。

### PDF、DOC、DOCX 与 fallback

使用 LangChain RecursiveCharacterTextSplitter，分隔符顺序固定为：

```text
"\n\n" → "\n" → " " → ""
```

## 实际计量单位

虽然配置字段常以 characters 描述，实际运行单位取决于底层 parser：

- LlamaIndex SentenceSplitter：tokenizer 计数；
- Markdown 第二阶段：tokenizer 计数；
- Hierarchical Parent/Child SentenceSplitter：tokenizer 计数；
- LangChain RecursiveCharacterTextSplitter：默认字符计数；
- Semantic Splitter：embedding 距离，不使用固定 chunk size。

Auto Reign 必须使用上述底层库与参数实现这些计量语义，不能额外写一个统一字符切分器。前端帮助文案应说明策略含义，不虚假承诺所有文件使用同一单位。

## Hierarchical 策略

### 入库

1. Parent SentenceSplitter 使用 `parent_chunk_size`、`parent_separator`，parent overlap 固定为 0。
2. 每个 Parent 内部使用 Child SentenceSplitter，参数为 `child_chunk_size`、`child_chunk_overlap` 和 `child_separator`。
3. Parent metadata：

```json
{
  "chunk_strategy": "hierarchical",
  "node_role": "parent"
}
```

4. Child metadata：

```json
{
  "chunk_strategy": "hierarchical",
  "node_role": "child",
  "parent_node_id": "..."
}
```

5. Child 是向量/关键词检索的 index node；Parent 作为可批量回查的内容节点保存。
6. Markdown hierarchical 先执行 Markdown parsing 与可选弱章节增强，再生成 Parent/Child；其他格式不额外套 file-aware recursive splitter。

### 检索

1. 正常检索 Child records。
2. 按首次出现顺序收集唯一 `parent_node_id`。
3. 从当前 Retriever backend 批量读取 Parent records。
4. 用 Parent 的 content、title 和 metadata 替换 Child 展示内容。
5. 同一 Parent 的多个命中只返回一条。
6. 保留对应 Child score 最高的那条 Parent 结果，同时保持该 Parent 首次出现的位置。
7. 非 hierarchical record 或 Parent 缺失时保留原 record，不误删结果。

Elasticsearch 与 Qdrant backend 都必须实现 Parent 保存和批量读取契约，确保 splitter 行为不随 Retriever 类型变化。

## Semantic 策略

使用当前 Knowledge embedding provider 构造 LlamaIndex `SemanticSplitterNodeParser`：

- `buffer_size=1`；
- `breakpoint_percentile_threshold=95`；
- 以相邻句子窗口 embedding 差异的相应百分位作为断点。

Semantic 入库不得临时选择另一套 embedding 模型，否则切分、索引向量和查询空间会产生未经配置的差异。

## 入库与 metadata

每次上传或重新索引：

1. 规范化 splitter config，并保存为 Document 本次实际配置。
2. 从 ObjectStore 读取或生成解析文本。
3. 根据文件扩展名和 strategy 运行 splitter。
4. 为所有 node 补齐稳定 metadata：
   - owner、Collection/Knowledge ID、Document ID；
   - index generation；
   - chunk strategy；
   - parser subtype；
   - node role；
   - parent node ID；
   - Markdown header path 与其他来源信息。
5. 对 index nodes 生成 embedding。
6. 写入 Collection 当前配置的 Elasticsearch 或 Qdrant 投影。
7. 新 generation 全部成功后原子切换 Document 当前有效 generation。
8. 失败时保留旧有效 generation，并记录安全、可重试的错误状态。

旧 splitter config 和旧 chunk 投影不迁移。重新索引以新的规范化配置生成完整新 generation。

## API 与前端

Knowledge Document 创建和重新索引请求接受规范化配置；Document 详情返回实际生效配置。Collection 可以提供 UI 默认值，但每个 Document 必须保存自身实际采用的 snapshot，避免日后无法解释既有索引。

前端提供：

- Flat、Hierarchical、Semantic 策略选择；
- file-aware 开关；
- Markdown enhancement 开关；
- 当前 strategy 对应参数和校验提示；
- runtime 默认值；
- 重新索引会创建新 generation 的提示。

不显示已废弃的 `smart/sentence` 类型，也不允许同时提交多个 strategy config。

## 错误与恢复

- 配置校验失败在创建索引任务前返回，不产生半成品 generation。
- parser 或 embedding 失败将当前 attempt 标记为失败，不切换 active generation。
- Parent 写入不完整时 hierarchical generation 不能标记 ready。
- 查询时单个 Parent 缺失不会丢弃原 Child record。
- Qdrant/Elasticsearch 是可重建投影；MySQL Document 状态、splitter config 与 ObjectStore 原文/解析文本是重建依据。

## 验收标准

至少覆盖：

- 空配置得到 runtime `flat/file_aware/1024/50/markdown enabled`；
- extra field、范围、overlap 和 parent/child 关系校验；
- `.md/.txt/.pdf/.doc/.docx/unknown` 路由；
- Markdown 标题、连续弱章节、24 字符边界和文末弱章节；
- SentenceSplitter 与 RecursiveCharacterTextSplitter 的实际单位；
- Hierarchical Parent/Child metadata、parent overlap 0 和 child 关联；
- 只检索 Child，批量回查 Parent；
- 同 Parent 去重、最高分替换、顺序稳定和 Parent 缺失 fallback；
- Semantic 默认参数、断点行为与 embedding provider 复用；
- Document splitter config snapshot；
- Elasticsearch/Qdrant Parent backend 契约；
- generation 成功切换、失败保留旧版本和重新索引；
- 前端表单、校验、i18n、loading 和 error state。

聚焦单元测试使用确定性文本和 embedding test double；存储契约补充真实 Qdrant、Elasticsearch 与 MySQL 集成测试。最终执行仓库权威后端、前端和 compose 检查。

## 实施边界

Splitter config、文件路由、node 构建、metadata enrich、Retriever storage 和 Parent merge 必须保持独立接口。实施时按本规格逐项验收，不保留旧 payload 兼容层。
