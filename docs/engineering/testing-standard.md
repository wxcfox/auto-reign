# 测试规范

本文规定 Auto Reign 行为变更的测试层级、外部系统边界和提交前验证要求。测试的目标是证明行为契约、故障收敛和租户隔离，而不是只提高覆盖率数字。

## 基本原则

- 每个行为变更都覆盖正常路径、边界条件和失败路径。
- 修复缺陷时先添加能稳定复现问题的聚焦测试，再修复实现。
- 测试通过公开契约观察结果；只有无法通过公开边界验证的不变量才直接检查内部状态。
- 测试必须确定性地控制时间、模型输出、并发门闩和外部失败，不能依赖真实公网服务。
- 不能用 `skip`、放宽断言、吞掉异常或无限重试掩盖失败。
- 测试数据必须属于可证明的临时命名空间，清理不能触及开发者的正常数据库、Redis key 或对象。

## 测试层级

### 单元测试

用于不依赖真实基础设施的确定性逻辑，例如：

- schema 校验、权限谓词、状态转换和错误映射；
- token 预算、chunk range、generation 判定和幂等合并；
- repository 查询条件构造；
- Prompt 输入组装和工具参数校验。

单元测试应快速、无网络、无共享状态。LLM、embedding、时钟、随机数和 provider 使用受控 test double。

### 前端组件测试

使用 Vitest、Testing Library 和 jsdom 验证：

- 用户可见文本、可访问角色和交互结果；
- loading、empty、error、disabled 与重试状态；
- i18n namespace 和中英文资源；
- typed API 或 Socket.IO 事件驱动的状态变化；
- 响应式布局中关键控件仍可使用。

优先从用户视角查询元素。`data-testid` 只用于没有稳定语义角色、跨多层事件流或需要长期兼容的元素，规则见[前端契约规范](frontend-contract-standard.md)。

### 后端集成测试

用于证明应用与真实基础设施之间的契约。以下行为不能只由 SQLite 或 mock 证明：

- Alembic 在真实 MySQL 上的 schema、约束、索引和升级；
- MySQL 事务、外键、唯一约束、行级并发和 Context 原子绑定；
- Redis TTL、active stream、UTF-16 offset、重连快照和清理；
- 真实 Socket.IO 客户端与服务器之间的认证、ACK、room 隔离和事件顺序；
- Elasticsearch 的 mapping、generation filter、vector、BM25 和 hybrid 查询；
- Qdrant 的 collection、filter 和恢复语义当前由官方 `QdrantClient` in-memory engine 验证；若修改远端 Qdrant 服务、鉴权、网络或部署契约，必须增加真实 Qdrant service 集成。

集成测试必须使用显式环境变量和可证明可丢弃的测试资源。测试夹具应在连接前校验数据库名称或 key 前缀，结束后验证无表、无 key 或无测试投影残留。

### 端到端测试

端到端测试覆盖浏览器、Next.js、FastAPI 与所需基础设施组成的主要用户流。至少在下列变更引入或更新对应端到端场景：

- 登录、权限或管理员账号流程；
- 新建 Task、发送消息、工具 block、失败重试和断线重连；
- Agent、Workspace 或 Knowledge 的关键创建与绑定流程；
- 上传、索引、检索和删除等跨服务生命周期；
- 无法由组件测试证明的路由、浏览器 API 或生产代理行为。

当前仓库的权威前端命令是 `npm test` 和 `npm run build`。在引入浏览器端到端框架前，Spec 必须同时定义稳定启动、测试数据隔离、截图或 trace 产物和 CI 命令，不能用临时手工步骤冒充持续验收。

## Test double 边界

默认允许替换：

- LLM 与 embedding Provider；
- 当前测试目标之外的 Retriever；
- 时钟、UUID、随机数和故障注入；
- 浏览器组件测试中的 typed HTTP/Socket.IO 客户端；
- 不属于本测试契约的 ObjectStore。

不得用 test double 替代本次要证明的边界。例如：

- 测 MySQL 事务时不能换成 SQLite；
- 测 Task room 时不能只调用 handler，必须至少有真实 Socket.IO 客户端集成；
- 测 Redis 重连快照时不能只用进程内 store；
- 测 Elasticsearch mapping 和 filter 时不能只断言请求参数；
- 测前后端 payload 演进时，不能分别维护两份互不校验的手写假数据。

test double 必须实现最小公开接口并确定性返回，不应复制生产实现的大段算法。若 fake 与真实系统存在重要差异，测试名称或注释必须明确边界，并补充真实集成测试。

## `skip` 与环境门控

禁止因为测试不稳定、实现未完成、依赖难启动或 CI 太慢而新增无期限 `skip`。

真实基础设施测试可以在默认本地套件中使用显式环境门控，但必须同时满足：

- 缺少开关或一次性测试 URL 时只表示“未选择运行该集成套件”；
- 测试在使用资源前验证其为专用、可丢弃目标；
- CI 有独立步骤设置开关并运行该文件；
- CI 中依赖不可用、配置错误或断言失败必须失败，不能转为 `skip`；
- PR 测试证据报告实际通过数量和预期门控数量。

条件门控不能包住普通单元测试，也不能让关键集成场景只在开发者机器上偶尔运行。发现现有 `skip` 没有对应 CI 执行入口时，应删除、补齐入口或在 Spec 中明确限期处理。

## 行为测试矩阵

每个复杂功能至少检查：

| 维度 | 必测内容 |
| --- | --- |
| 正常路径 | 输入、持久化、响应、UI 最终状态 |
| 输入边界 | 空值、最大值、重复值、非法类型与超限 |
| 状态机 | 所有允许转换和主要非法转换 |
| 失败 | 事务前后、Provider、存储、连接、超时与中断 |
| 重试与恢复 | 是否复用身份、清理旧结果、是否安全重放 |
| 幂等与乱序 | 重复请求、重复事件、游标、offset、晚到结果 |
| 并发 | 同资源竞争、唯一约束、generation 变化和最终写入校验 |
| 权限 | 未认证、停用用户、跨 owner、global/private 和管理员边界 |
| 数据清理 | 测试资源、临时 key、对象和进程均被回收 |
| 可观测性 | 稳定错误码、关键日志字段且无敏感内容 |

## 前端验收

用户可见改动必须覆盖：

- 初始 loading 与防止重复操作；
- 空数据引导；
- 可恢复与不可恢复错误；
- 成功后的列表、详情或聊天状态；
- 中英文资源一致；
- 键盘和可访问名称；
- 小屏幕下主要操作不消失；
- 真实主要用户流，而不只是孤立展示组件。

Task room 改动还必须覆盖初次 join、ACK 后乐观 User Subtask、block created/updated、done/error/cancelled、重复事件、重连游标、active snapshot 和失败 Assistant 原地重试。

## 后端与基础设施验收

涉及相应领域时，至少运行：

```sh
cd backend
uv run pytest -v
uv run ruff check .
```

真实集成套件必须按 CI 中的专用环境变量执行，不能指向日常开发数据库。当前 CI 使用：

- MySQL 8.4 验证 Alembic、schema、SubtaskContext、资源竞态、Knowledge generation 和 Task room；
- Redis 7.4 database 15 验证 stream 原子性与 Task room 临时态；
- Elasticsearch 8.19 验证 Knowledge Retriever；
- 官方 `QdrantClient` in-memory engine 验证当前 Qdrant collection、filter 和恢复语义；
- 真实 `python-socketio` AsyncClient 验证 Socket.IO 契约。

前端运行：

```sh
cd frontend
npm test
npm run lint
npm run build
```

容器配置运行：

```sh
docker compose config
```

生产 Compose 或镜像发生变化时，还要执行 `.github/workflows/ci.yml` 中对应的 production config 与 build 检查。

## 测试证据

Pull request 或交付说明应记录：

- 运行的精确命令；
- 通过、失败和环境门控数量；
- 使用的真实基础设施及版本；
- 数据库、Redis 和其他临时资源的清理结果；
- 未运行的场景及明确原因；
- UI 改动的截图或端到端证据。

“全量测试通过”必须能对应到具体命令和结果，不能把默认套件里被门控的集成测试算作已经执行。
