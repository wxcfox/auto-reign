# 文档

本目录只保留当前有效的产品、架构和运维说明。

## 文档地图

- [项目说明](../README.md)：当前能力、快速开始、配置、数据和开发检查。
- [通用 Agent 平台架构](workbench-architecture.md)：资源、Runtime、Agent Home、权限和 LLM 边界。
- [Knowledge Collection 数据流](knowledge-data-flow.md)：Document 原文、generation、Elasticsearch/Qdrant 投影与多路检索。
- [生产部署](production-deployment.md)：版本发布、S3-compatible 配置、账号初始化、备份、日志和单实例边界。

## 维护原则

- 长期文档默认使用简体中文；路径、配置项、API 名称和技术术语可以保留英文。
- 同一事实只保留在一个权威文档中，其他文档通过链接引用。
- 行为变更必须同步更新对应文档。
- 临时实施计划只在开发期间保留，完成后应删除或把有效决策沉淀到权威文档。
