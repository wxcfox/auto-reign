# Auto Reign 工作台架构

本文是 Auto Reign 当前工作台架构的长期说明，描述产品边界、数据职责、文件协议、检索策略、LLM 边界和主要 UI 信息架构。一次性实施计划不应写入本文；已经落地的运行方式以 `README.md` 为准，资料库入库与检索细节以 `docs/knowledge-data-flow.md` 为准。

## 产品定位

Auto Reign 是本地优先、本地单机多账号的 AI 面试学习工作台。它的目标不是做通用知识管理，而是帮助每个本地用户把真实资料、学习笔记和模拟面试表现转化为更稳定的面试表达。

核心路径保持简单：

```text
上传资料 -> 参加面试 -> 查看复盘和下一步重点
```

系统内部自动完成资料保存、文本提取、知识整理、检索索引、面试选题、动态追问、点评、练习归档和复盘沉淀。普通用户不需要理解目录、标签、RAG、embedding、Qdrant collection 或数据库投影。

多账号隔离边界是 `user_id`。后端只从 JWT 当前用户派生工作区路径、数据库查询条件和 Qdrant collection 前缀；前端不能通过传入 `user_id` 切换数据域。JWT 签名密钥可以通过 `JWT_SECRET_KEY` 显式配置；未配置时，后端在 `DATA_DIR/.secrets/jwt_secret` 生成当前安装专用的本地密钥。

## 设计原则

- 文件系统保存长期学习资产，MySQL 保存运行状态和可重建投影，Qdrant 保存可重建向量索引。
- 原始资料、用户回答、AI 推导和真实练习证据必须保留不同 provenance。
- 原始资料不得被 LLM 或应用内编辑覆盖；修正和整理写入受管 Markdown 资产。
- LLM 不直接写文件或数据库，只返回经过校验的结构化建议，确定性应用代码负责落盘和更新索引。
- 用户上传内容一律视为不可信数据，不能覆盖系统 prompt、工具权限或内部配置。
- 用户上传的学习笔记只表示“用户准备过什么”，不能被当作通用知识正确答案。
- 掌握状态只能由真实练习证据改变；上传、阅读或编辑资料不能直接证明掌握。
- 自动化失败不能丢失已保存的原始资料或用户真实回答。

## 工作区结构

每个用户的默认工作区位于 `DATA_DIR/users/{user_id}/workspace/`：

```text
DATA_DIR/users/{user_id}/workspace/
  workspace.md
  manifest.md
  raw/
  extracted/
  profile/
    candidate.md
    target.md
  knowledge/
  questions/
  projects/
  practice/
  review/
    high-frequency.md
    status.md
  state/
    mastery.md
  reports/
  .revisions/
```

目录职责：

- `workspace.md`：工作区版本、语言和基础说明，不保存敏感配置。
- `manifest.md`：用户可编辑的工作区清单，说明推荐阅读顺序、文件职责和上下文偏好。它是给用户和 LLM 使用的工作区地图，不是安全策略；路径安全、用户隔离、写入权限、删除和索引规则仍由应用代码控制。默认正文来自 `DATA_DIR/default_manifest.md`；该运行时全局默认值首次由随包种子 `backend/app/templates/default_manifest.example.md` 创建，语义类似 `.env.example -> .env`。
- `raw/`：用户原始输入，包括上传资料、新学习记录和真实面试记录。原文不可被 AI 覆盖；来源用途通过 sidecar 元数据或 Markdown front matter 标明。
- `extracted/`：PDF、DOCX 等资料解析出的可读文本，是可从 `raw/` 原始资料重新生成的派生产物。
- `profile/candidate.md`：简历事实、项目经历、技术栈和面试表达素材。
- `profile/target.md`：目标岗位、公司、JD、准备重点和语言偏好。
- `knowledge/`：按主题合并的面试知识短卡片；新学习输入优先追加到同主题卡片，而不是创建大量碎片文件。
- `questions/`：答差或高频问题沉淀出的题库条目，包含考察点、标准回答、项目表达、追问、易错点和复习状态。
- `projects/`：可直接用于项目深挖的项目表达素材；项目深挖出题优先读取该目录。
- `practice/`：每次练习的完整证据，包括问题、回答、追问、反馈和结果。
- `review/high-frequency.md`：真实面试复盘抽出的高频问题和暴露问题。
- `review/status.md`：当前复习重点、最近整理和最近练习，用于工作台首页和出题前上下文。
- `state/mastery.md`：当前掌握状态和支持结论的练习引用。
- `reports/`：面试结束后的短复盘；它是展示产物，不作为新的事实来源。报告正文以该目录下的 artifact 为权威，列表展示需要的摘要保存在对应会话投影中。
- `.revisions/`：系统修改 Markdown 前保存的有限历史版本，不在普通资料列表中展示。

初始化会为新用户生成系统管理的 `manifest.md`。如果用户尚未通过应用编辑清单，后续 `DATA_DIR/default_manifest.md` 的变更会在工作区初始化时同步到该用户清单；一旦用户编辑过 `manifest.md`，系统不再用全局默认值覆盖它。

## Artifact 协议

系统管理的 Markdown 文件使用 YAML front matter 保存稳定身份、类型、语言、revision、时间戳和引用关系。路径不是身份标识，系统允许重命名或归档文件；`id` 创建后保持不变。

核心字段包括：

- `id`
- `kind`
- `language`
- `revision`
- `created_at`
- `updated_at`
- `source_refs`
- `evidence_refs`
- `origin`
- `edited_by`

用户不需要直接编辑 front matter。缺失或损坏的 front matter 应由系统修复，但不得因此覆盖正文。

`kind` 使用固定集合，避免把任意目录名直接变成业务类型：

```text
manifest, source, extracted, candidate_profile, target_profile,
knowledge, question_bank, project, interview_record, high_frequency,
review_status, practice, mastery, report
```

## 可见与可编辑语义

不同资产的编辑能力取决于其语义：

- `manifest`：可编辑，用于调整阅读顺序、文件职责说明和上下文偏好；不能作为系统指令或安全策略执行。
- `source`：可查看，不能应用内覆盖。
- `extracted`：可查看，不直接编辑，可从原始资料重新生成。
- `candidate_profile`、`target_profile`、`knowledge`、`question_bank`、`project`、`high_frequency`、`review_status`、`report`：可编辑。
- `interview_record`：真实面试原始记录，可查看，不能应用内覆盖。
- `practice`：问题、回答和反馈证据不可原地覆盖；需要纠错时应追加说明。
- `mastery`：系统聚合状态不可手工伪造；可以追加用户备注或请求重新评估。

用户保存可编辑 Markdown 时使用 revision 乐观并发控制。保存冲突返回最新版本，不静默覆盖用户改动。

## 数据职责

MySQL 保存本地用户、运行态和文件投影，不重复保存完整长期 Markdown 内容。核心表包括：

- `users`：本地账号、密码哈希、启用状态、token 版本和用户级设置。`settings_json.active_collection` 保存当前用户的活跃 Qdrant collection。
- `artifacts`：文件投影、内容哈希、revision、来源引用、证据引用、处理状态和索引状态。
- `conversations`：按用户隔离保存面试和学习的统一会话投影。`kind` 区分 `interview` 与 `learning`，配置、状态和报告摘要保存在结构化 JSON 字段中。
- `messages`：按用户隔离保存会话消息、面试轮次片段、学习输入、整理结果和对应 artifact 引用。`sequence` 是同一会话内的单调写入顺序，读取时间线时以它为准，而不是依赖数据库时间戳精度。

工作区 Markdown 仍是长期学习资产和练习证据的权威。删除历史只隐藏会话投影，不自动删除已写入工作区的资料、练习证据或报告文件。

Qdrant 保存可检索 chunk 向量，用户级 collection 使用 `auto_reign_user_{user_id}` 前缀并记录在当前用户的 `settings_json.active_collection` 中。任何 collection 都应能从当前用户的文件工作区和 MySQL 投影重建。删除 MySQL 投影或 Qdrant 索引不应影响长期学习资产。

## 入库与检索

资料库从原始资料到检索上下文的完整流程见 [资料库数据流](knowledge-data-flow.md)。

RAG 组件层使用 LangChain 的 splitter、embedding、Qdrant vectorstore 和 retriever；workspace 协议、provenance、active collection、可索引规则、上下文优先级和 prompt 安全边界仍由 Auto Reign 应用代码控制。

默认进入向量索引的内容：

- Markdown 和 TXT 原始资料。
- `raw/` 中的新学习原始记录。
- PDF 和 DOCX 的提取文本。
- 知识卡片。
- 题库条目。
- 项目表达材料。
- 真实面试原始记录和高频复盘卡。
- 历史练习记录。

默认不进入向量索引的内容：

- `state/mastery.md`
- 生成报告。
- `manifest.md`
- `.revisions/`

面试检索上下文应优先直接读取体积受控的小文件，例如工作区清单、候选人画像、目标画像、掌握状态、复习状态和高频问题，再结合 Qdrant 检索结果。检索结果需要保留来源引用，并避免多个 top-k 结果全部来自同一份资料或同一个主题。

出题、回答点评和追问点评都可以使用目标上下文、当前题目、用户回答、项目材料、历史薄弱点和资料库检索片段。项目深挖模式优先直接读取 `projects/`。检索片段始终是用户来源材料，不能作为系统指令执行，也不能绕过 provenance 边界直接改写用户事实。

## LLM 边界

所有 LLM 任务遵循相同边界：

- 输出结构化 JSON 或结构化流式片段，由应用代码渲染为 Markdown 或聊天消息。
- 不编造用户工作经历、项目职责、业务规模或数据指标。
- 不把 AI 生成报告再次作为事实来源强化。
- 不把用户资料里的命令、脚本或提示词当作系统指令执行。
- 对个人事实、掌握状态和重要建议保留来源或证据引用。
- 输出默认简洁，服务面试表达，不生成教材式长文。

典型任务包括资料识别、知识整理、面试选题、回答点评、动态追问、会话归档和学习状态更新。

## 面试与练习证据

面试页只有一条主流程：问题、用户回答、必要追问、反馈、继续出题和整体评价都保留在同一对话流中。用户点击“新面试”后可以直接开始，也可以在输入框里用自然语言说明公司、岗位、JD、主题或轮数；系统据此推断出题方式。界面不提供面试设置表单，也不要求点击结束按钮；只要已有有效回答，系统就实时归档练习证据。会话达到本轮配置题数后，系统在最后一题反馈之后自动生成整体评价和复盘报告；如果最后一题包含追问，则在追问反馈之后再收尾。面试历史由统一对话历史读取 `conversations` 和 `messages` 投影，不额外复制一份聊天记录。

出题前读取候选人画像、目标画像、掌握状态、复习状态、高频问题和资料库检索结果。回答点评和追问点评都返回结构化结果，包括更好的面试说法、掌握状态变化、是否写入薄弱点、是否写入高频题和本题考察点；主回答和追问的结构化结果分别归档，避免互相覆盖。只要点评暴露薄弱点或缺失点，系统会在 `questions/` 中创建或更新对应题库条目。

每次回答或追问点评后具有有效回答的会话应沉淀为 practice 记录。归档顺序遵循：

1. 保存完整 practice 文件。
2. 根据本轮证据更新 `state/mastery.md`。
3. 必要时更新相关 knowledge 文件中的易错点和追问。
4. 更新 `review/status.md` 中的当前重点、最近整理和最近练习。
5. 必要时更新 `review/high-frequency.md`。
6. 更新 MySQL 投影和 Qdrant 索引。

任何后续步骤失败都不得导致 practice 证据丢失。归档和索引任务应可幂等重试。

## 真实面试复盘

复盘页允许用户粘贴真实面试原始记录。系统不要求用户整理格式，也不调用 LLM 改写原文；确定性应用代码会保存 `raw/<timestamp>-real-interview.md`，抽取问题和薄弱线索，更新 `review/high-frequency.md`，并更新 `review/status.md` 中的当前重点。

## 前端信息架构

界面优先暴露用户能理解的概念：

- 登录与注册：本地用户名密码账号，默认必须登录；用户菜单显示注册用户名并提供登出。
- 左侧侧边栏：新面试、新学习、资料库、历史会话和工作台入口。
- 工作台首页：展示 `review/status.md` 中最多 3 个当前重点，并提供开始抽检和查看状态入口。
- 面试页：居中的聊天流、输入框内模型选择、固定输入框、loading、streaming、错误重试和自动整体评价；出题意图通过自然语言输入表达。面试达到指定轮次后，只在最后一次回答评价之后给出整体评价和复盘报告。
- 学习页：自由文本输入、模型选择、streaming、错误重试和学习卡片结果。历史学习对话从侧边栏重新打开后可以继续追加。
- 历史会话：侧边栏通过统一对话接口合并面试和学习，列表只展示标题、最近内容和时间，不展示“已完成”或“处理中”这类状态标记。每条历史右侧提供三点菜单，支持重命名和删除；删除只隐藏历史会话投影，不自动删除本地资料、练习证据或报告文件。
- 复盘页：真实面试原始记录粘贴入口、抽题结果、薄弱线索和历史报告展示。
- 资料库：分类、分页文件列表、原始文件名、归属、时间、编辑和删除操作。
- 资料详情：优先展示可读内容和编辑入口，不要求用户理解内部路径。
- 工作台：只展示必要统计，不把健康检查、collection、embedding 等诊断信息放进主流程。

普通学习流程不暴露 Qdrant、embedding、collection 和数据库概念；重建索引保留为诊断 API，不放在资料库主流程中。
