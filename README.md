# Auto Reign

*Autoregressive Q&A — every token is a step to the throne.*

**本地优先的 AI 模拟面试工作台，基于你自己的资料库出题、追问、点评，并自动沉淀薄弱项和复盘报告。**

## 架构基线

本文档描述当前可运行实现。工作台的长期产品与架构边界见
[Auto Reign 工作台架构](docs/workbench-architecture.md)。

当前架构把用户可见的 Markdown 文件作为长期学习资产，自动完成资料整理和学习状态维护，并将 MySQL 与 Qdrant 视为运行状态或可重建基础设施。修改存储、入库、检索、面试流程或主界面时，应同步更新对应架构或流程文档。

文档地图和生命周期规则见 [docs/README.md](docs/README.md)。已完成的一次性实施计划不应继续留在仓库中，除非其中长期有效的决策已经沉淀到权威文档。

Auto Reign 可以把 Markdown、TXT、PDF、DOCX、自由文本学习笔记和真实面试记录写入本地工作区，在 Qdrant 中索引可检索 chunk，以聊天式流程进行书面模拟面试，并在本地保存历史对话、题库、高频问题、复习状态和复盘报告。自由文本学习笔记会追加到 `raw/YYYY-MM-DD-learning-notes.md`，再整理为面试短卡片；真实面试记录会保存到 `raw/`，并更新高频问题和复习状态。

当前版本默认必须登录。用户通过本地用户名和密码注册账号；密码只保存哈希，不保存明文。每个账号拥有独立的本地工作区、MySQL 投影和 Qdrant collection。用户文件位于 `DATA_DIR/users/{user_id}/workspace`。JWT 签名密钥优先读取 `JWT_SECRET_KEY`；未配置时，后端会在 `DATA_DIR/.secrets/jwt_secret` 生成并复用当前安装的本地密钥。

旧版单用户 `DATA_DIR/workspace` 数据不会自动迁移或自动删除。切换到多账号版本前，如需清理本地数据，请显式运行：

```sh
./reset-data.sh --yes
```

工作台首页展示当前登录用户 `DATA_DIR/users/{user_id}/workspace` 下的真实目录和文件，左侧直接列出 workspace 下的一级文件夹和根目录文件，不显示 workspace 根节点，也不默认展开子目录；点击文件夹后右侧用资料库式表格展示该目录的直接子文件夹和文件，点击右侧子文件夹可逐层进入。能匹配到 artifact 投影的文件可跳转到资料详情页并使用与资料库一致的编辑、删除操作，普通 UTF-8 文本文件可只读预览，非文本或过大文件不会在首页展开。健康检查、collection、embedding 和抽检任务不放在首页主流程。模拟面试没有设置表单和结束按钮；用户点击新面试后可以直接开始，也可以在输入框里用自然语言说明公司、岗位、JD、主题或轮数。每次有效回答都会实时沉淀为练习记录；如果点评暴露缺失点或薄弱点，会同步创建或更新 `questions/` 中的题库条目，并用真实练习证据更新复习状态。会话达到本轮配置题数后，会在最后一题反馈之后自动生成整体评价和复盘报告；如果最后一题包含追问，则在追问反馈之后再收尾。侧边栏历史对话同时包含面试和学习，不展示“已完成”或“处理中”状态；每条历史右侧的三点菜单支持重命名和删除。学习对话可以继续追加，面试对话重新打开后由面试流程自身决定是否还能继续作答。删除历史只隐藏对应会话投影，不删除工作区里的资料、练习证据或报告文件。

## 快速开始

依赖：

- Docker with Compose v2
- Python 3.12+
- `uv`
- Node.js 22+
- `npm`

从仓库根目录启动本地开发栈：

```sh
./start.sh
```

该脚本会在需要时把 `.env.example` 复制为 `.env`，用 Docker 启动 MySQL 和 Qdrant，执行 Alembic 迁移，安装缺失的前端依赖，并以宿主机进程启动后端和前端。

常用生命周期命令：

```sh
./start.sh --status
./start.sh --stop
./start.sh --restart
./start.sh --help
./reset-data.sh --dry-run
./reset-data.sh --yes
```

`./reset-data.sh --yes` 是破坏性命令：它会停止本地 Auto Reign 进程，删除 MySQL 和 Qdrant Docker volume，并删除 `data/`、`.pids/`、`logs/` 等本地运行数据，包括 `DATA_DIR/users` 下的本地用户数据和旧版 `DATA_DIR/workspace`。它不会删除源代码、依赖目录或 `.env` 等本地配置文件。

默认宿主机端口：

- `3100`：前端
- `8300`：后端
- `13306`：MySQL
- `16333`：Qdrant HTTP

默认依赖镜像：

- `MYSQL_IMAGE=mysql:8.4`
- `QDRANT_IMAGE=qdrant/qdrant:v1.17.0`

至少一个后端 provider key 非空时，对应 provider 和模型才会出现在面试模型选择器中。默认本地路径针对单个 `QWEN_API_KEY` 做了优化：聊天默认使用 `qwen3.7-plus`，RAG embedding 通过 DashScope OpenAI-compatible endpoint 使用 `text-embedding-v4`。`QWEN_CHAT_MODELS` 只控制面试聊天模型选择器；embedding 由 `EMBEDDING_PROVIDER` 和 `EMBEDDING_MODEL` 单独控制。`.env` 只保存在本地，并已被 Git 忽略。

如果你的环境访问 Docker Hub 不稳定，可以先在 `.env` 中把 `MYSQL_IMAGE` 和 `QDRANT_IMAGE` 改成可访问的镜像源，再重新运行 `./start.sh`。

## 运行模式

### 宿主机进程开发模式

`./start.sh` 会启动：

- Docker 中的 MySQL，用于关系型元数据
- Docker 中的 Qdrant，用于向量和检索
- 宿主机上的 FastAPI
- 宿主机上的 Next.js

日常开发优先使用该模式。运行状态分别保存到：

- MySQL：本地用户、用户级 artifact 投影、学习和面试统一对话、消息与报告摘要
- Qdrant：按用户 collection 隔离的 chunk 向量和检索 payload
- `DATA_DIR`：按用户隔离的来源文件、提取文本、生成的 Markdown、复盘报告、修订版本和本地工作数据

### 全容器模式

如果希望四个服务都运行在 Docker 中，可以直接使用 Compose：

```sh
cp .env.example .env
docker compose config
docker compose up --build -d
```

前端地址是 <http://127.0.0.1:3100>。后端健康检查地址是 <http://127.0.0.1:8300/api/health>。

停止容器栈：

```sh
docker compose down
```

持久化数据会保留在 MySQL、Qdrant 命名 volume 和 `./data` 中。

### 版本化生产部署

生产环境不使用 `./start.sh`，也不在服务器执行 `docker compose up --build`。正式发布通过 SemVer Git Tag 触发 GitHub Actions，在阿里云 ACR 生成 backend/frontend 版本镜像，再由 ECS 使用 `deploy/compose.prod.yml` 部署指定版本。

完整的 ACR、GitHub、ECS、域名、HTTPS、备份、回滚和旧环境迁移说明见 [生产部署](docs/production-deployment.md)。生产栈只公开 Caddy 的 `80/443`，MySQL、Qdrant、后端和前端端口不对公网映射。

## 权威检查命令

提交前运行仓库检查：

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

## 配置

| 变量 | 说明 |
| --- | --- |
| `BACKEND_HOST` | 容器模式下的后端监听 host。 |
| `APP_VERSION` | 当前运行版本；生产部署由发布版本注入并通过 `/api/health` 返回。 |
| `REGISTRATION_ENABLED` | 是否允许创建新本地账号；本地开发默认开启，生产创建首个账号后应关闭。 |
| `BACKEND_PORT` | 后端进程或容器映射使用的首选宿主机端口。 |
| `FRONTEND_PORT` | 前端进程或容器映射使用的首选宿主机端口。 |
| `MYSQL_PORT` | 映射到 MySQL 容器 `3306` 的宿主机端口。 |
| `QDRANT_HTTP_PORT` | 映射到 Qdrant HTTP `6333` 的宿主机端口。 |
| `QDRANT_GRPC_PORT` | 映射到 Qdrant gRPC `6334` 的宿主机端口。 |
| `MYSQL_IMAGE` | 本地 MySQL 依赖的容器镜像。 |
| `QDRANT_IMAGE` | 本地 Qdrant 依赖的容器镜像。 |
| `MYSQL_DATABASE` | MySQL 数据库名称。 |
| `MYSQL_USER` | MySQL 应用用户。 |
| `MYSQL_PASSWORD` | MySQL 应用用户密码。 |
| `MYSQL_ROOT_PASSWORD` | 本地容器初始化使用的 MySQL root 密码。 |
| `DATABASE_URL` | 后端和 Alembic 使用的 SQLAlchemy 数据库 URL。 |
| `QDRANT_URL` | 后端访问 Qdrant 的 URL。 |
| `QDRANT_COLLECTION` | 诊断和兼容路径使用的默认 Qdrant collection 名称；用户级索引使用 `auto_reign_user_{user_id}` 前缀。 |
| `DATA_DIR` | 用户目录、工作区来源文件、提取文本、生成报告、修订版本和本地文件的根目录。 |
| `JWT_SECRET_KEY` | 可选 JWT 签名密钥。留空时会在 `DATA_DIR/.secrets/jwt_secret` 自动生成本机密钥。 |
| `EMBEDDING_PROVIDER` | Embedding provider 标识，默认是 `qwen`。 |
| `EMBEDDING_MODEL` | Embedding 模型标识，默认是 `text-embedding-v4`。 |
| `OPENAI_API_KEY` | 启用 OpenAI 模型目录，仅后端读取。 |
| `DEEPSEEK_API_KEY` | 启用 DeepSeek 模型目录，仅后端读取。 |
| `QWEN_API_KEY` | 启用 Qwen 模型目录，仅后端读取。 |
| `OPENAI_CHAT_MODELS` | OpenAI 聊天模型白名单，逗号分隔。 |
| `DEEPSEEK_CHAT_MODELS` | DeepSeek 聊天模型白名单，逗号分隔。 |
| `QWEN_CHAT_MODELS` | Qwen 聊天模型白名单，逗号分隔。 |
| `DEEPSEEK_BASE_URL` | DeepSeek OpenAI-compatible API base URL。 |
| `QWEN_BASE_URL` | Qwen OpenAI-compatible 区域 API base URL。 |
| `NEXT_PUBLIC_API_BASE_URL` | 浏览器访问后端 API 的公开 URL。 |

Provider key 只从后端环境变量读取。API 只返回 provider 是否可用和配置的模型名称，绝不返回 key 值。前端不接收 key，也不会把 key 写入 MySQL、Qdrant、报告或工作区文件。

OpenAI 使用标准 API endpoint。DeepSeek 和 Qwen 使用各自的 OpenAI-compatible endpoint。默认配置使用 Qwen 同时提供聊天和 embedding，因此有效的 `QWEN_API_KEY` 足以在本地运行文档索引、检索和面试。OpenAI 仍然支持聊天和 embedding；使用 OpenAI embedding 时设置 `EMBEDDING_PROVIDER=openai` 和 `EMBEDDING_MODEL=text-embedding-3-small`。

## 资料库

上传支持 `.md`、`.txt`、`.pdf` 和 `.docx` 文件。工作区会生成用户可编辑的 `manifest.md`，用于说明推荐阅读顺序、文件职责和上下文偏好；它不是权限或安全策略。默认清单采用类似 `.env.example -> .env` 的初始化方式：仓库随包提供 `backend/app/templates/default_manifest.example.md`，服务首次启动时把它种子写入 `DATA_DIR/default_manifest.md`；管理员后续修改运行时默认值后，尚未自定义 `manifest.md` 的用户会在下次工作区初始化时同步，已经通过应用编辑过清单的用户不会被覆盖。上传原始文件保存到 `DATA_DIR/users/{user_id}/workspace/raw`；“新学习”自由文本原文追加到 `raw/YYYY-MM-DD-learning-notes.md`，并按主题合并生成使用「我的理解 / 修正/补充 / 30 秒面试说法 / 易混点 / 追问」格式的 `knowledge` 短卡片，同时把用户输入和整理结果写入学习对话。继续学习时，前端会把历史会话的 `conversation_id` 传给 `POST /api/workspace/learning-notes/stream` 追加消息；侧边栏历史列表通过 `GET /api/conversations` 合并面试和学习，并通过 `PATCH /api/conversations/{id}` 和 `DELETE /api/conversations/{id}` 重命名或隐藏会话。PDF 和 DOCX 可解析文本会保存到 `DATA_DIR/users/{user_id}/workspace/extracted`。真实面试粘贴记录保存到 `raw/`，并更新 `review/high-frequency.md` 和 `review/status.md`。资料入库统一通过 workspace API 完成：上传资料使用 `POST /api/workspace/materials/upload`，学习笔记使用 `POST /api/workspace/learning-notes/stream`，真实面试记录使用 `POST /api/workspace/real-interview-records`。工作区投影保存到 MySQL，可索引的来源、提取文本、知识、题库、项目、真实面试、高频复盘和练习内容会被切块并以向量形式保存到当前用户的 Qdrant collection。当前数据路径见 [资料库数据流](docs/knowledge-data-flow.md)。

索引和检索只读取当前登录用户 workspace artifact 的 active collection。Markdown/递归切块、embedding、Qdrant vectorstore 和 retriever 由 LangChain 组件处理；workspace 协议、provenance、可索引规则、active collection 发布、检索后处理、上下文预算和 prompt 安全边界由 Auto Reign 应用代码控制。

面试出题、回答点评和追问点评都会结合候选人画像、目标画像、掌握状态、复习状态、高频问题、项目材料和资料库检索片段。点评结果会包含更好的面试说法、掌握状态变化、是否写入薄弱点、是否写入高频题和本题考察点。检索片段只作为不可信的个人来源材料使用，不会覆盖系统指令，也不会被当作新的用户事实自动写回。

`POST /api/workspace/rebuild-index` 保留为诊断 API，用于手工重建 Qdrant 索引；资料库主界面不再把它作为日常操作展示。

## 冒烟验证

1. 运行 `./start.sh`。
2. 未登录访问 `/`，确认会跳转到 `/login`。
3. 注册本地账号并进入工作台。
4. 打开资料库，上传 Markdown、TXT、PDF 或 DOCX 文件。
5. 确认文件以原始文件名展示，并显示分类、创建时间、更新时间以及编辑/删除操作。
6. 在 `.env` 中配置 `QWEN_API_KEY`，然后运行 `./start.sh --restart`。
7. 打开新面试，可以不输入直接开始，也可以在聊天输入框中描述公司、岗位、JD、主题或轮数。
8. 回答问题并观察反馈，确认练习记录、题库和复习状态会实时入库；达到本轮配置题数后，确认最后一题反馈之后会自动展示整体评价。
9. 打开复盘页，粘贴一段真实面试记录，确认系统抽取问题、暴露问题，并更新高频问题和复习状态。
10. 从侧边栏打开历史，确认历史面试和历史学习都可以重新打开，学习对话可以继续追加，并确认历史条目右侧三点菜单可以重命名和删除会话。
11. 登出后注册第二个本地账号，确认资料库和历史为空，且无法通过第一个账号的 artifact URL 查看其资料。

## 当前不包含的能力

当前版本不包含 Redis、对象存储、远程 RAG runtime、扫描图片 OCR、语音/视频面试、数字评分、邮箱/微信/OIDC 登录或前端 API key 输入。它面向本地单机多账号运行。
