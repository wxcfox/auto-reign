# 数据库迁移规范

本文规定 Auto Reign 的 MySQL 与 Alembic schema 演进方式。目标是在保持一个明确 schema 历史的同时，防止普通启动、测试或迁移静默破坏用户数据。

## 核心不变量

- MySQL 是用户、资源、Knowledge Document 状态、Task、Subtask 和 SubtaskContext 的持久权威。
- 仓库在任一可合并状态必须恰好只有一个 Alembic head。
- 普通启动和 `alembic upgrade head` 不得静默删除、截断或覆盖用户数据。
- 破坏性本地重置只能由操作者显式执行 `./reset-data.sh --yes`。
- migration 必须在真实 MySQL 上验证；SQLite 不能证明 MySQL 的 DDL、索引、外键、JSON、锁或事务行为。
- 不新增永久双读、双写、影子表或“以后再删”的兼容分支。

## 何时需要 migration

以下变化必须通过 Alembic 表达：

- 表、列、索引、唯一约束、外键或默认值变化；
- 持久枚举或状态表示变化；
- 数据库级 owner、顺序、幂等或引用完整性约束变化；
- 需要数据回填才能满足新 schema 的变化。

仅修改 Pydantic 或 TypeScript 类型不能代替 migration；仅修改 migration 也不能代替 SQLAlchemy model 与 schema 更新。

## 设计前检查

写 migration 前必须：

1. 阅读相关 Spec、`docs/workbench-architecture.md` 和领域专题文档；
2. 检查当前 SQLAlchemy model、repository、schema 与测试；
3. 运行 `uv run alembic heads`，确认只有一个 head；
4. 判断已有库能否无损升级；
5. 列出数据量、锁表风险、回填方式和失败恢复；
6. 明确是否需要用户执行显式 reset。

若需求已经批准不兼容旧数据，应把破坏性变化放在显式 reset 流程，而不是让应用启动或 Alembic 猜测并删除旧数据。

## Migration 编写要求

每个 revision 必须：

- 使用唯一、可读的 revision 标识和准确说明；
- 普通 revision 的 `down_revision` 指向当前唯一 head，禁止无意创建分支；只有为收敛已评审的并行分支而创建 merge revision 时，才允许显式引用多个 heads；
- 显式命名重要索引、唯一约束和外键；
- 与当前 MySQL 8.4 支持的类型和 DDL 语义一致；
- 将 schema 变化与必要的数据回填按安全顺序拆分；
- 对 nullable 到 non-null、默认值和唯一约束说明已有行如何满足；
- 保持 SQLAlchemy model、repository、Pydantic schema、API 和文档同步；
- 不读取应用运行时配置来决定生成不同 schema；
- 不依赖应用服务、LLM、ObjectStore、Redis 或 Retriever 才能完成。

大表回填或可能长时间持锁的变更必须在 Spec 中单独设计阶段、批次、观测和中止策略，不能隐藏在一次不可控的启动迁移中。

## 数据保留与破坏性操作

禁止在普通 migration 中无提示执行：

- 删除仍可能包含用户数据的表或列；
- 用空值或默认值覆盖已有内容；
- 因解析失败而丢弃行；
- 自动清空 MySQL、Redis、ObjectStore 或 Retriever；
- 在无法识别旧 schema 时“恢复出厂设置”。

确需删除旧结构时，Spec 必须说明数据兼容结论。若不保留旧数据，则要求操作者先备份并显式执行重置；若保留数据，则提供可验证的转换和失败回滚方案。任何破坏性命令都必须显示目标、支持 dry-run（适用时）并要求明确确认。

`./reset-data.sh --yes` 只用于已确认的本地开发重置。它不应成为生产升级步骤，也不能让远端 S3/OSS 对象被隐式删除。

## 单一 Alembic head

提交前必须满足：

```sh
cd backend
uv run alembic heads
```

输出应恰好包含一个 `head`。并行开发产生多个 head 时，开发者必须基于最新主线重排或创建经过评审的 merge revision；不能让 CI 或启动脚本任意选择分支。

CI 应把“恰好一个 head”作为独立门禁，而不是只依赖 `upgrade head` 偶然发现问题。

## 真实 MySQL 验证

每个 schema 变更至少验证：

1. 专用空数据库从 base 执行 `alembic upgrade head`；
2. 再次执行 `alembic upgrade head` 保持幂等；
3. SQLAlchemy metadata 与实际表、列、索引、唯一约束和外键一致；
4. 受影响 repository 能在真实 MySQL 上读写；
5. 约束拒绝非法 owner、重复顺序或悬空引用；
6. migration 失败不会触发测试目标之外的数据清理；
7. 测试结束后专用数据库无表或已删除。

如果支持从前一发布升级，还必须准备代表前一发布的 schema 与数据，执行 upgrade 后验证数据值、引用和新代码行为。不能只验证最终空库建表。

集成测试 URL 必须显式传入，并验证数据库名称属于约定的 disposable 后缀或白名单。禁止回退到 `.env` 中的日常 `DATABASE_URL`。

## 空库、已有库与启动

- 空库：由 Alembic 唯一 head 创建当前 schema，再执行确定性的 create-only bootstrap。
- 已有兼容库：只运行已评审的向前 migration，不重复 bootstrap 用户数据。
- 未知或不兼容库：明确失败并提示备份、迁移或显式 reset；不得自动删除。
- 启动顺序：数据库和必要基础设施健康后执行 migration，再启动应用。

应用运行时不能自行创建、修改或删除业务表来绕过 Alembic。

## 回滚

每份 Spec 必须选择并记录一种恢复方式：

- 可证明安全的 `alembic downgrade`；
- 回退应用版本但保留向前兼容 schema；
- 从已验证备份恢复数据库；
- 在显式维护窗口执行补偿 migration。

如果 downgrade 会丢数据，应拒绝提供虚假的可逆实现，并明确要求备份恢复。生产回滚不能依赖开发环境的 `reset-data.sh`。

## generation 与异步写入

涉及 Knowledge 或其他异步投影时，schema migration 不能把数据库迁移版本当作业务 generation。业务 worker 必须：

- 领取时记录目标资源、状态和 generation；
- 外部处理完成后重新读取权威行；
- 最终写入前校验 owner、状态、generation 和当前引用；
- 丢弃 stale 结果，不覆盖较新 generation；
- 使 Elasticsearch/Qdrant 等投影可从权威源重建。

锁可以降低竞争，但不能替代最终 generation 校验。

## 提交检查清单

- [ ] Spec 说明 schema、数据兼容、重置与回滚。
- [ ] `uv run alembic heads` 只有一个 head。
- [ ] 空 MySQL 8.4 数据库可 `upgrade head`。
- [ ] 重复 upgrade 不改变结果。
- [ ] SQLAlchemy model 与实际 schema 一致。
- [ ] 受影响读写、约束、失败和并发有测试。
- [ ] 测试使用可证明 disposable 的数据库。
- [ ] 普通启动和 migration 不删除用户数据。
- [ ] README、架构或专题文档已同步。
