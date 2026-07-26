# 文档地图

Auto Reign 的文档按读者和任务组织。当前运行行为以代码和对应权威文档为准；`docs/specs/` 只描述已批准但尚未实现的目标，不应当当作产品功能说明。

## 入门

- [快速开始](getting-started/quick-start.md)：安装前置条件、本地启动、首次管理员设置和常用命令。
- [入门导航](getting-started/README.md)：本地开发、生产部署和下一步阅读路径。

## 概念与架构

- [概念导航](concepts/README.md)：从产品边界、资源模型和存储边界开始阅读。
- [平台架构](architecture/platform.md)：Agent、Workspace、Knowledge、Task/Subtask、Context、Runtime、权限和扩展边界。
- [Knowledge 架构](architecture/knowledge.md)：Document 入库、generation、splitter、Retriever 投影和检索。

## 用户操作

当前用户操作主要通过 Web 界面完成，入口与权限以实际页面为准：

- `/chat`：创建和继续 Task，进行流式 Agent 对话。
- `/agents`：管理个人 Agent；管理员还可以维护 global Agent。
- `/workspaces`：管理 Agent Home 文件空间。
- `/knowledge`：管理 Knowledge Collection 和 Document。
- `/admin/users`：管理员创建、停用、启用和重置普通用户。

资源权限和生命周期见[平台架构](architecture/platform.md)，不要在用户指南中重复维护一份权限表。

## 部署与运维

- [生产部署与运维](operations/production.md)：发布、ACR、ECS、Nginx、配置、备份、日志和单实例边界。
- [故障排查](operations/troubleshooting.md)：本地 Compose、Docker Registry、ACR、健康检查和版本升级问题。
- [运维导航](operations/README.md)：部署前、部署中和部署后的操作入口。

## 开发者

- [开发者导航](developer-guide/README.md)：开发环境、测试、数据库迁移、前端契约和 Spec 工作流。
- [工程规范](engineering/README.md)：所有工程规范的索引。

## 参考与待实施设计

- [参考导航](reference/README.md)：配置文件、API 文档和仓库目录入口。
- [上下文治理与压缩](specs/context-governance.md)：已批准但尚未实施。
- [Knowledge Splitter](specs/knowledge-splitter.md)：已批准但尚未实施。

## 文档维护原则

- 同一事实只保留在一个权威文档，其他位置只做链接或简短摘要。
- 当前行为变化必须同步更新 README、架构或运维文档；不能只更新 Spec。
- 较大行为变更先按 [Spec 编写规范](engineering/spec-writing-standard.md) 固化契约，再编写临时实施计划。
- 实施完成后删除计划和被当前代码替代的历史说明；不要把临时设计当作长期文档。
- 平台 Prompt 位于 `backend/app/prompts/`，属于运行时代码，不复制到普通说明文档。
