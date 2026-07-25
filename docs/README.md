# 文档

本目录按职责组织当前架构、运维说明、工程规范和待实施设计。当前运行行为以代码为最终准据，README 和架构文档必须与其保持一致；`docs/specs/` 只描述尚未实施的目标契约。

## 架构

- [平台架构](architecture/platform.md)：资源、Task/Subtask 历史、Context、Socket.IO、Redis、Agent Runtime、权限和预算边界。
- [Knowledge 架构](architecture/knowledge.md)：Document 原文、splitter、generation、Elasticsearch/Qdrant 投影和检索。

## 运维

- [生产部署与运维](operations/production.md)：版本发布、ACR、ECS、Nginx、S3-compatible ObjectStore、备份、日志和单实例边界。

## 工程规范

- [Spec 编写规范](engineering/spec-writing-standard.md)：当前实现证据、行为契约、失败恢复、测试矩阵和分阶段实施。
- [测试规范](engineering/testing-standard.md)：测试层级、test double 边界和真实基础设施验收。
- [数据库迁移规范](engineering/database-migration-standard.md)：单一 Alembic head、真实 MySQL、数据保留和显式重置。
- [前端契约规范](engineering/frontend-contract-standard.md)：跨层类型、i18n、Task room 单一事实源和主要用户流。

## 待实施设计

以下文档已批准但尚未实现，不是当前行为说明：

- [上下文治理与压缩](specs/context-governance.md)
- [Knowledge Splitter](specs/knowledge-splitter.md)

## 维护原则

- 同一事实只保留在一个权威文档，其他位置通过链接引用。
- 当前行为变化必须同步更新对应架构或运维文档。
- 较大行为变化先形成 Spec，再在 `docs/plans/` 拆分临时实施计划。
- 已完成计划和被当前代码替代的历史设计应删除，不长期归档在主仓库。
- 平台 Prompt 位于 `backend/app/prompts/`，属于运行时代码，不纳入普通文档目录。
