# Auto Reign

*Autoregressive Q&A - every token is a step to the throne.*

Auto Reign 是本地优先的 AI 面试学习工作台。它围绕用户自己的简历、项目、学习资料和真实面试记录组织学习，通过资料检索生成问题、追问和反馈，并持续沉淀题库、练习证据、掌握状态和复盘报告。

## 核心能力

- **资料工作台**：管理 Markdown、TXT、PDF 和 DOCX，保留原始来源并生成可编辑的学习资产。
- **模拟面试**：通过自然语言说明岗位、公司、JD、主题或轮数，在同一条对话中完成出题、追问、点评和整体评价。
- **学习整理**：把自由文本笔记整理为面试短卡片，并保留原始输入和历史对话。
- **真实面试复盘**：保存原始面试记录，提取高频问题和薄弱项，更新后续复习重点。
- **本地数据隔离**：使用本地用户名和密码登录；每个账号拥有独立的文件工作区、MySQL 投影和 Qdrant collection。

## 快速开始

依赖：

- Docker with Compose v2
- Python 3.12+
- `uv`
- Node.js 22+
- `npm`

从仓库根目录启动开发环境：

```sh
./start.sh
```

启动后访问：

- Web：<http://127.0.0.1:3100>
- API 健康检查：<http://127.0.0.1:8300/api/health>

常用命令：

```sh
./start.sh --status
./start.sh --restart
./start.sh --stop
```

首次启动会从 `.env.example` 创建本地 `.env`。至少配置一个模型 Provider API Key，例如 `QWEN_API_KEY`，对应模型才会出现在面试模型选择器中。

### 重置本地数据

```sh
./reset-data.sh --dry-run
./reset-data.sh --yes
```

`./reset-data.sh --yes` 是显式破坏性操作，会删除本地账号、工作区、MySQL 和 Qdrant 数据。应用不会自动删除或迁移旧数据。

## 运行模式

| 模式 | 启动方式 | 用途 |
| --- | --- | --- |
| 开发模式 | `./start.sh` | MySQL、Qdrant 运行在 Docker，FastAPI、Next.js 运行在宿主机 |
| 全容器模式 | `docker compose up --build -d` | 本地集成验证，数据保存在 Docker named volumes |
| 生产模式 | ACR 版本镜像 + `deploy/compose.prod.yml` | 单机服务器长期运行，按明确版本发布和回滚 |

全容器模式：

```sh
cp .env.example .env
docker compose config
docker compose up --build -d
```

`app_data`、`mysql_data` 和 `qdrant_data` named volumes 会在 `docker compose down` 后保留。全容器模式的 `app_data` 与开发模式的 `./data` 相互独立，不会自动共享账号和工作区。

## 版本发布

代码合并到 `main` 只运行 CI，不自动创建版本，也不自动部署服务器。

需要发布时，在 GitHub Actions 手动运行 **Publish Release**，输入明确的 SemVer，例如 `0.1.0`。Workflow 会从 `main` 构建并推送 backend/frontend ACR 镜像；全部成功后创建 `v0.1.0` Git Tag 和 GitHub Release。

生产服务器由管理员手工登录，切换到对应 Tag 后运行部署脚本：

```sh
git fetch --tags origin
git switch --detach v0.1.0
AUTO_REIGN_ENV_FILE=/etc/auto-reign/auto-reign.env ./deploy/deploy.sh 0.1.0
```

发布流程不生成供生产部署使用的 `latest`。服务器只使用明确版本镜像。阿里云 ACR、ECS、Nginx、备份和回滚配置见 [生产部署](docs/production-deployment.md)。

## 数据与架构

```text
Next.js -> FastAPI -> MySQL
                  -> Qdrant
                  -> DATA_DIR/users/{user_id}/workspace
```

- **文件工作区**保存原始资料、知识卡片、题库、练习证据和报告，是长期学习资产。
- **MySQL**保存账号、文件投影、会话、消息和运行状态。
- **Qdrant**保存可从文件工作区和 MySQL 投影重建的检索索引。

用户文件位于：

```text
DATA_DIR/users/{user_id}/workspace/
```

JWT 签名密钥优先读取 `JWT_SECRET_KEY`；未配置时会在 `DATA_DIR/.secrets/jwt_secret` 生成并复用本机密钥。Provider Key 只由后端读取，不会返回前端或写入用户资料。

完整产品、存储、检索、面试和 LLM 边界见 [工作台架构](docs/workbench-architecture.md)。资料入库和检索过程见 [资料库数据流](docs/knowledge-data-flow.md)。

## 开发检查

提交前从仓库根目录运行：

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

CI 会在 Pull Request 和 `main` push 上执行后端检查、前端测试与构建、依赖审计、Compose 校验和容器构建。

## 文档

- [文档地图](docs/README.md)
- [工作台架构](docs/workbench-architecture.md)
- [资料库数据流](docs/knowledge-data-flow.md)
- [生产部署](docs/production-deployment.md)

## 当前边界

当前版本面向本地或可信服务器运行，不包含对象存储、远程 RAG runtime、扫描图片 OCR、语音/视频面试、数字评分、邮箱/微信/OIDC 登录或前端 API Key 输入。
