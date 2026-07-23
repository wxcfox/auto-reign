# 文档

本目录只保留当前有效的产品、架构、工程规范和运维说明。

## 文档地图

- [项目说明](../README.md)：当前能力、快速开始、配置、数据和开发检查。
- [通用 Agent 平台架构](workbench-architecture.md)：Task/Subtask 历史、MySQL Context、Socket.IO room、Redis 临时态、Runtime 与权限边界。
- [Knowledge Collection 数据流](knowledge-data-flow.md)：Document 原文、确定性 splitter、generation、Elasticsearch/Qdrant 投影与多路检索。
- [生产部署](production-deployment.md)：版本发布、Redis、Nginx Socket.IO Upgrade、S3-compatible 配置、备份、日志和单实例边界。

### 工程规范

- [Spec 编写规范](engineering/spec-writing-standard.md)：实现证据、行为契约、状态机、失败恢复、测试矩阵和分阶段实施。
- [测试规范](engineering/testing-standard.md)：单元、组件、集成、端到端、test double 边界和真实基础设施验收。
- [数据库迁移规范](engineering/database-migration-standard.md)：单一 Alembic head、真实 MySQL、数据保留、显式重置和回滚。
- [前端契约规范](engineering/frontend-contract-standard.md)：跨层类型、i18n、Task room 单一事实源、可测试性和主要用户流。

## 维护原则

- 长期文档默认使用简体中文；路径、配置项、API 名称和技术术语可以保留英文。
- 同一事实只保留在一个权威文档中，其他文档通过链接引用。
- 行为变更必须同步更新对应文档。
- 较大行为变更先按 Spec 编写规范完成并批准设计，再拆分临时实施计划。
- 临时实施计划只在开发期间保留，完成后应删除或把有效决策沉淀到权威文档。
