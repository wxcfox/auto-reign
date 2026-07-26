# 参考文档

## 配置

- [开发环境模板](../../.env.example)：本地端口、依赖服务和开发默认值。
- [生产环境模板](../../deploy/auto-reign.env.example)：生产配置名和部署拓扑，不包含 Secret。
- `backend/app/core/config.py`：运行时 Settings 的最终字段定义。

## API 与运行时入口

- 本地启动后访问 <http://127.0.0.1:8300/docs> 查看 FastAPI OpenAPI 文档。
- `/api/health`：应用健康检查和版本信息。
- `/api/health/retrievers`：Knowledge Retriever 健康检查。
- `/setup`：空库首次管理员密码设置，一次性使用。
- `/admin/users`：管理员管理普通用户。

## 仓库目录

| 目录 | 职责 |
| --- | --- |
| `backend/app/` | FastAPI API、服务、仓储、Runtime、工具和 Prompt |
| `frontend/src/` | Next.js 页面、组件、i18n 和前端测试 |
| `backend/alembic/` | MySQL schema migration |
| `deploy/` | 生产 Compose、镜像和运维脚本 |
| `scripts/` | 本地启动、重置和诊断工具 |
| `docs/specs/` | 已批准但尚未实施的目标设计 |
