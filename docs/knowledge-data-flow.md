# 资料库数据流

本文描述 Auto Reign 当前资料库从上传资料、学习记录、工作区投影、切块、embedding、向量索引到面试检索的完整流程。

```mermaid
flowchart TD
  A["用户上传资料或记录学习笔记"] --> B{"输入类型"}
  B -->|Markdown 或 TXT| C["原始文件保存到 DATA_DIR/sources/documents"]
  B -->|PDF 或 DOCX| D["原始文件保存到 DATA_DIR/sources/documents"]
  D --> E["可解析时提取文本到 sources/extracted"]
  B -->|学习笔记| F["把原始笔记保存为来源资料"]
  F --> G["LLM 总结为受管知识 Markdown 文件"]
  C --> H["整理到候选人画像、目标画像或知识分类"]
  E --> H
  G --> I["重建工作区投影"]
  H --> I
  I --> J["扫描工作区文件和 sidecar 元数据"]
  J --> K["把 artifact 元数据写入 MySQL"]
  K --> L["重建向量索引"]
  L --> M["读取可索引文本：原文、提取文本、知识和练习记录"]
  M --> N["切分为带重叠窗口的 chunk"]
  N --> O["使用配置的 embedding provider 生成向量"]
  O --> P["把 chunk 向量和元数据写入 Qdrant"]
  P --> Q["在工作区设置中记录活跃向量 collection"]
  Q --> R["面试服务根据当前上下文构造检索 query"]
  R --> S["对检索 query 生成 embedding"]
  S --> T["查询活跃 Qdrant collection"]
  T --> U["把检索片段注入出题、点评或总结 prompt"]
```

## 当前存储职责

- `DATA_DIR/sources/documents/` 保存用户上传的原始文件和学习笔记原文。来源文件会在元数据中保留用户的原始文件名，并在资料库中展示该名称。
- `DATA_DIR/sources/extracted/` 保存 PDF 和 DOCX 输入可解析出的文本。
- `DATA_DIR/knowledge/`、`DATA_DIR/profile/`、`DATA_DIR/practice/`、`DATA_DIR/state/` 和 `DATA_DIR/reports/` 保存系统管理的 Markdown 资产。
- MySQL 保存工作区 artifact 投影、处理状态、索引状态、修订版本、会话和报告元数据。
- Qdrant 保存可检索的 chunk 向量。活跃 Qdrant collection 可以从文件工作区和 MySQL artifact 投影重新构建。

## 索引规则

- Markdown 和 TXT 来源文件直接从原始文件索引。
- PDF 和 DOCX 来源文件不直接索引；解析成功后索引对应的提取文本 Markdown artifact。
- 知识和练习 Markdown artifact 从正文内容索引。
- 候选人画像、目标画像、计划、报告和掌握状态会展示在资料库中，但当前不进入向量索引。
- 删除资料库 artifact 时，系统删除对应工作区文件并重建投影。随后索引重建会发布新的活跃 collection，从而移除陈旧向量内容。
