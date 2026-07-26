# 开发者指南

本文档面向修改 Auto Reign 代码、数据库、前端契约或运行时行为的开发者。

## 开始开发

- [快速开始](../getting-started/quick-start.md)：启动本地依赖和前后端。
- [仓库指南](../../AGENTS.md)：代码边界、文档规则和提交前命令。
- [平台架构](../architecture/platform.md)：先理解资源、权限、Runtime 和存储权威。
- [Knowledge 架构](../architecture/knowledge.md)：涉及入库或检索时必读。

## 工程规范

- [工程规范导航](../engineering/README.md)
- [测试规范](../engineering/testing-standard.md)
- [数据库迁移规范](../engineering/database-migration-standard.md)
- [前端契约规范](../engineering/frontend-contract-standard.md)
- [Spec 编写规范](../engineering/spec-writing-standard.md)

## 修改行为的工作流

1. 先阅读当前代码、测试和对应权威文档。
2. 跨模块行为变更先按 Spec 规范记录不变量、失败恢复、权限和测试矩阵。
3. 数据库变更保持单一 Alembic head，并在真实 MySQL 上验证。
4. 完成实现后同步 README、架构或运维文档，删除完成的临时计划和过时说明。
5. 提交前运行仓库指南中的后端、前端和 Compose 检查。

平台 Prompt 位于 `backend/app/prompts/`，是运行时代码。上传内容、Knowledge 原文和 ToolResult 都是不可信输入，不能通过文档或 Prompt 绕过确定性授权、路径和持久化边界。
