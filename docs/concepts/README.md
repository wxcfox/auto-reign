# 概念导航

Auto Reign 将 Agent、模型、文件上下文和 Knowledge 组织在一个多账号隔离的工作台中。平台不为“问答、面试或学习”建立不同的会话类型；这些用途由 Agent 配置和用户输入决定。

## 核心概念

| 概念 | 含义 |
| --- | --- |
| Agent | 可复用的系统提示词、默认模型、Agent Home 和 Knowledge 引用。可以是 global 或 private。 |
| Workspace | Agent Home 的定义和文件空间。定义可以共享，文件实例仍按调用用户隔离。 |
| Knowledge Collection | 显式管理的参考资料集合，包含 Document 和检索配置。 |
| Task | 一段可继续的聊天历史和运行容器。首轮发送后 Agent 引用固定。 |
| Subtask | Task 中的一条 User 输入或一个 Assistant 回合。原始历史不会因上下文裁剪而删除。 |
| Context | 当前 User Subtask 的附件和 selected documents；它不会自动写入 Agent Home 或 Knowledge。 |

## 阅读架构

- [平台架构](../architecture/platform.md)：资源生命周期、租户隔离、Task/Subtask、Socket.IO、Runtime 和存储权威。
- [Knowledge 架构](../architecture/knowledge.md)：Document 入库、generation、切分、索引和检索。

## 当前边界

- MySQL 是业务、聊天历史和 Document 状态的权威存储。
- ObjectStore 保存 Agent Home 和 Knowledge 文件；聊天附件保存在 MySQL Context。
- Redis 只保存带 TTL 的实时流状态，不是聊天备份。
- Elasticsearch/Qdrant 是可重建的 Knowledge 检索投影。
- 生产只支持一个 backend/Uvicorn 进程；不要仅因为已经使用 Redis 就增加 replica。
