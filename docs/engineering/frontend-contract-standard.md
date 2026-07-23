# 前端契约规范

本文规定 Auto Reign 前端在类型、国际化、Socket.IO 状态、可测试性和用户状态方面的长期约束。前端不是后端数据的第二权威，也不能用本地拼接掩盖跨层契约错误。

## 跨层类型

- HTTP schema、Socket.IO payload、ACK 和错误码必须与后端公开契约逐项对应。
- `task_id`、`subtask_id`、`message_id`、`block_id`、generation 和 UTF-16 offset 不得互相替代。
- 禁止用 `any`、宽泛对象或多套旧字段名同时兼容未知 payload。
- 新增字段时明确必填性、默认值、旧客户端行为和运行时校验边界。
- 数据库、Pydantic、API、Socket.IO、TypeScript 和 UI 使用统一领域术语；当前聊天持久化单位是 Task/Subtask/SubtaskContext。

类型定义只能证明编译期一致，关键 payload 仍需通过后端集成测试和前端流程测试验证。

## i18n namespace 所有权

面向用户的文案必须来自 `frontend/src/i18n/locales/{language}/`，不能在组件中硬编码单一语言。

namespace 按功能所有权划分：

| namespace | 所有内容 |
| --- | --- |
| `common` | 登录、通用导航、真正跨功能的按钮与状态 |
| `chat` | Task、Subtask、模型、Context、工具 block 和聊天错误 |
| `agents` | Agent 列表、表单、绑定与 Agent Home 选择 |
| `workspaces` | Workspace 与 Agent Home 浏览和编辑 |
| `knowledge` | Collection、Document、入库和 Retriever |
| `admin` | 管理员用户与管理专属操作 |

功能文案归属其功能 namespace，不能为了省事塞进 `common`。新增 key 必须同时更新 `zh-CN` 和 `en`，保持相同层级与参数；测试不得依赖 fallback 掩盖缺失翻译。技术标识符和后端错误码不能直接作为用户文案。

## Task room 单一事实源

网页聊天的实时入口是 `SocketProvider` 管理的 `/chat` namespace 和服务端控制的 Task room。前端状态按以下权威顺序收敛：

1. `task:join` ACK 返回的 MySQL Subtask 历史是持久基线；
2. ACK 中仍存活的 Redis active snapshot 补充当前未完成流；
3. `chat:block_created`、`chat:block_updated` 和终止事件推进当前回合；
4. 最终重新加入或刷新时，以服务端历史覆盖本地临时态。

组件不得各自维护另一份聊天历史、从旧 REST 消息接口拼接快照，或把 Redis active 内容当作持久记录。

所有生成事件先使用 `task_id + subtask_id + generation_id` 隔离当前执行世代，旧 generation 的晚到事件不得覆盖失败重试或下一轮执行。不同事件再按各自契约合并：

- `chat:send` ACK 只建立持久 User Subtask 的 `subtask_id/message_id`；Assistant Subtask 身份来自 `chat:start`，或缺失 start 时来自 `done/error/cancelled` 和后续 join 历史；
- `chat:block_created` 以 `block.id` 建立完整 block，重复 created 不重复追加；
- `chat:block_updated` 以 `block_id` 更新结构化字段，它没有 offset，不能套用文本增量规则；
- 只有 `chat:chunk` 使用 `block_id + block_offset` 合并文本块，并使用可见回答 `offset` 校验整体游标；两者都是 JavaScript UTF-16 code-unit offset，晚到或较小 offset 不覆盖较新内容；
- 服务端正常顺序是 `chat:send` ACK 后开始该轮 stream event；前端仍须隔离 ACK 返回前已排队的事件，避免调度和重连竞态；
- 重连使用最后持久 `message_id`，并能合并 active snapshot；
- `done/error/cancelled` 即使缺少 `chat:start` 也能让 UI 收敛；
- 失败重试复用原 Assistant Subtask ID，不能在前端伪造新的重试消息。

Task 状态决定输入区、模型修改、取消和重试是否可用。组件不能仅凭“是否正在显示 spinner”推断后端状态。

## loading、empty、error 与恢复

每个异步页面或组件都必须显式设计：

- 首次 loading：防止空内容闪烁和重复提交；
- empty：说明当前没有什么，并提供权限允许的下一步；
- error：展示本地化、可行动的错误，不泄露服务端内部堆栈；
- retry：只对幂等或后端明确支持的操作提供；
- disabled：说明运行中、权限不足或资源不可用的原因；
- stale：重连或切换路由时不把旧 Task 数据显示到新 Task。

错误码由 typed client 映射为功能 namespace 文案。未知错误使用稳定通用文案并保留安全的诊断关联信息；不能把原始异常直接插入页面。

## `data-testid` 规则

优先使用可访问角色、label、placeholder 和可见文案查询。以下情况可以增加稳定 `data-testid`：

- 跨 Socket.IO 事件的动态 block 或 active stream；
- 没有语义角色的可视化容器；
- 同类重复元素需要稳定领域身份；
- 主要端到端流程需要不受文案翻译影响的锚点。

命名采用小写 kebab-case，并表达领域含义，例如：

```text
task-composer
assistant-subtask-42
tool-block-7
knowledge-retriever-select
```

不要使用 CSS 类名、DOM 层级、随机值或实现组件名。修改或删除已用于主要流程的 test id 时，必须在同一变更更新组件和端到端测试；不得悄悄破坏自动化契约。

`data-testid` 不是可访问性替代品。按钮、输入框和对话框仍必须具有正确元素、label、名称和键盘行为。

## 响应式与可访问性

主要用户流至少验证桌面与窄屏布局：

- 输入区、发送、取消、重试和导航不会被遮挡；
- 长文件名、tool 参数、代码块和错误文本可换行或滚动；
- 弹窗和抽屉不会超出视口；
- loading 不引起主要布局跳动；
- 键盘可到达所有交互控件，焦点关闭后回到合理位置；
- 颜色不是状态的唯一表达；
- 动态流更新不反复抢夺输入焦点。

Spec 应为有明显布局影响的功能给出目标断点或验收视口，不要依赖开发者主观拖动窗口。

## 主要用户流测试

前端行为变更不能只测试孤立展示组件。按影响范围覆盖：

- 页面路由、权限守卫和数据加载；
- 表单提交、重复提交保护、成功收敛和错误恢复；
- 列表到详情、空状态到创建完成；
- 中英文资源与插值；
- Task join、发送 ACK、tool block、最终回答、失败、重试、取消和重连；
- Knowledge 上传、处理状态与可用性；
- global/private 资源可见性和不可操作状态。

组件测试使用 typed fake 模拟不在当前目标内的网络边界；跨 Next.js、FastAPI 和基础设施的关键路径按[测试规范](testing-standard.md)补充端到端或真实集成验收。

## 性能与事件生命周期

- effect 注册的 Socket.IO、window、timer 和订阅必须在清理函数中解除。
- 重渲染不能重复加入 room、重复注册 handler 或重复发送请求。
- 切换 Task 时先隔离旧 Task 事件，再 hydrate 新 Task。
- 大历史和流式 block 更新应保持稳定 key，避免整棵消息树重建。
- optimistic state 必须能由 ACK 失败、断线或最终服务端历史确定性回滚或覆盖。

## 前端提交检查

```sh
cd frontend
npm test
npm run lint
npm run build
```

Pull request 同时确认：

- [ ] TypeScript 类型与后端契约一致。
- [ ] 新文案同时存在于 `zh-CN` 和 `en` 的正确 namespace。
- [ ] loading、empty、error、disabled 与 retry 状态已覆盖。
- [ ] 主要用户流和失败路径有测试。
- [ ] 稳定 test id 与测试同步。
- [ ] 窄屏、键盘和动态内容可用。
- [ ] Socket handler、timer 和请求在卸载时正确清理。
- [ ] 没有新增旧消息源、双状态或 `any` 兼容层。
