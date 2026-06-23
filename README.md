# Auto Reign

*自回归式问答：每一个 token 都是通往掌控面试的一步。*

**本地优先的 AI 模拟面试工作台，基于你自己的资料库出题、追问、点评，并自动沉淀薄弱项和复盘报告。**

## 架构基线

本文档描述当前可运行实现。工作台的长期产品与架构边界见
[Auto Reign 工作台架构](docs/workbench-architecture.md)。

当前架构把用户可见的 Markdown 文件作为长期学习资产，自动完成资料整理和学习状态维护，并将 MySQL 与 Qdrant 视为运行状态或可重建基础设施。修改存储、入库、检索、面试流程或主界面时，应同步更新对应架构或流程文档。

文档地图和生命周期规则见 [docs/README.md](docs/README.md)。已完成的一次性实施计划不应继续留在仓库中，除非其中长期有效的决策已经沉淀到权威文档。

Auto Reign 可以把 Markdown、TXT、PDF、DOCX、自由文本学习笔记和真实面试记录写入本地工作区，在 Qdrant 中索引可检索 chunk，以聊天式流程进行书面模拟面试，并在本地保存面试历史、题库、高频问题和复盘记忆。自由文本学习笔记会先保存到 `inbox/`，再整理为面试短卡片；真实面试记录会保存到 `raw/`，并更新高频问题和未来 1-3 天计划。

工作台首页会读取 `state/plan.md`，展示最多 3 个当前准备任务，并提供开始抽检和查看计划入口。完成一轮有效面试后，系统会把本轮证据沉淀为练习记录；如果点评暴露缺失点或薄弱点，会同步创建或更新 `questions/` 中的题库条目，并用真实练习证据更新当前计划。

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

`./reset-data.sh --yes` 是破坏性命令：它会停止本地 Auto Reign 进程，删除 MySQL 和 Qdrant Docker volume，并删除 `data/`、`.pids/`、`logs/` 等本地运行数据。它不会删除源代码、依赖目录或 `.env` 等本地配置文件。

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

- MySQL：工作区 artifact 投影、面试、报告和记忆元数据
- Qdrant：索引后的 chunk 向量和检索 payload
- `DATA_DIR`：工作区来源文件、提取文本、生成的 Markdown、报告、修订版本和本地工作数据

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
| `QDRANT_COLLECTION` | 默认 Qdrant collection 名称。 |
| `DATA_DIR` | 工作区来源文件、提取文本、生成报告、修订版本和本地文件的根目录。 |
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
| `DETERMINISTIC_MODEL_FALLBACK` | 在测试或离线演示中使用本地确定性聊天和向量。 |
| `NEXT_PUBLIC_API_BASE_URL` | 浏览器访问后端 API 的公开 URL。 |

Provider key 只从后端环境变量读取。API 只返回 provider 是否可用和配置的模型名称，绝不返回 key 值。前端不接收 key，也不会把 key 写入 MySQL、Qdrant、报告或记忆文件。

OpenAI 使用标准 API endpoint。DeepSeek 和 Qwen 使用各自的 OpenAI-compatible endpoint。默认配置使用 Qwen 同时提供聊天和 embedding，因此有效的 `QWEN_API_KEY` 足以在本地运行文档索引、检索和面试。OpenAI 仍然支持聊天和 embedding；使用 OpenAI embedding 时设置 `EMBEDDING_PROVIDER=openai` 和 `EMBEDDING_MODEL=text-embedding-3-small`。`DETERMINISTIC_MODEL_FALLBACK=true` 只应用于自动化测试或明确的离线演示；它会绕过 provider 调用，使用稳定的本地响应和 hash 向量。

## 资料库

上传支持 `.md`、`.txt`、`.pdf` 和 `.docx` 文件。上传原始文件保存到 `DATA_DIR/workspace/sources/documents`；“新学习”自由文本原文保存到 `DATA_DIR/workspace/inbox`，并按主题合并生成使用「我的理解 / 修正/补充 / 30 秒面试说法 / 易混点 / 追问」格式的 `knowledge` 短卡片。PDF 和 DOCX 可解析文本会保存到 `DATA_DIR/workspace/sources/extracted`。真实面试粘贴记录保存到 `DATA_DIR/workspace/raw`，并更新 `review/high-frequency.md` 和 `state/plan.md`。工作区投影保存到 MySQL，可索引的来源、提取文本、知识、题库、项目、真实面试、高频复盘和练习内容会被切块并以向量形式保存到 Qdrant。当前数据路径见 [资料库数据流](docs/knowledge-data-flow.md)。

面试出题、回答点评和追问点评都会结合候选人画像、目标画像、掌握状态、当前计划、项目材料和资料库检索片段。点评结果会包含更好的面试说法、掌握状态变化、是否写入薄弱点、是否写入高频题和本题考察点。检索片段只作为不可信的个人来源材料使用，不会覆盖系统指令，也不会被当作新的用户事实自动写回。

`POST /api/workspace/rebuild-index` 保留为诊断 API，用于手工重建 Qdrant 索引；资料库主界面不再把它作为日常操作展示。

## 冒烟验证

1. 运行 `./start.sh`。
2. 打开工作台，确认本地服务栈可访问。
3. 打开资料库，上传 Markdown、TXT、PDF 或 DOCX 文件。
4. 确认文件以原始文件名展示，并显示分类、创建时间、更新时间以及编辑/删除操作。
5. 在 `.env` 中配置 `QWEN_API_KEY`，然后运行 `./start.sh --restart`。
6. 打开新面试，可以在聊天输入框中描述公司、岗位或 JD，然后开始在对话流里回答。
7. 持续回答直到配置的面试完成，并确认总结出现在聊天流中。
8. 打开复盘页，粘贴一段真实面试记录，确认系统抽取问题、暴露问题，并更新当前计划。
9. 从侧边栏打开历史，确认已结束面试可见但不能继续对话。

## 当前不包含的能力

当前版本不包含认证、授权、多用户隔离、Redis、对象存储、远程 RAG runtime、扫描图片 OCR、语音/视频面试、数字评分或前端 API key 输入。它面向单个用户在本地运行。
