# Claude Code Instructions

## 必读上下文

实现 Auto Reign 的较大开发任务前，按顺序阅读：

1. `AGENTS.md`
2. `README.md`
3. `docs/workbench-architecture.md`
4. `docs/knowledge-data-flow.md`
5. 与当前任务相关的后端、前端、Alembic 迁移、启动脚本和测试。

`README.md` 描述当前可运行实现；`docs/workbench-architecture.md` 是长期产品与架构边界；资料入库、索引和检索细节以 `docs/knowledge-data-flow.md` 为准。

## 实施约束

涉及工作台架构、存储、入库、检索、面试流程或主界面的较大变更时，先在 `docs/superpowers/plans/` 写分阶段实施计划。计划是临时工作产物，不是长期文档。计划应列出精确文件、测试、迁移影响、数据重置影响和验证命令，并保持每个阶段结束时应用可运行。

实现时按计划逐步推进。准备 PR 前，删除已完成的一次性计划，或把仍然有效的决策沉淀到 `README.md`、`docs/workbench-architecture.md`、`docs/knowledge-data-flow.md` 或其他专题文档。不要把过时计划留作历史归档。

## 产品规则

- 用户应专注于上传真实资料和练习回答，而不是维护标签、文档角色、trust level、chunk 或索引。
- 原始资料和真实回答是不可变证据；修正、整理和 AI 生成内容是单独 artifact。
- 上传笔记只表示用户准备过什么，不能当作通用正确答案。
- 只有真实练习证据可以改变 mastery 状态。
- 文件是长期学习资产；MySQL 保存运行状态和可重建投影；Qdrant 是可重建检索索引。
- LLM 只返回经过校验的结构化建议；文件系统、数据库和向量写入由确定性应用代码执行。
- 用户可见 Markdown 按 artifact 语义决定是否可编辑，保存后重新投影和索引。
- 当前复习重点不超过三个。
- 不为已替代的旧行为保留兼容分支。
- 绝不能静默删除本地用户数据。

## 工程纪律

- 优先采用满足当前设计的最简单实现。
- 对行为变更先添加聚焦测试，再改实现。
- 改动范围只覆盖当前任务，删除确实已被当前任务淘汰的代码。
- 使用 `AGENTS.md` 中的权威命令做提交前验证。
- 不要只凭代码检查声称完成；报告最新验证命令和无法运行的检查。
