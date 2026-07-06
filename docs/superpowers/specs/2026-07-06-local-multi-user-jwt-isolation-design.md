# 本地多账号 JWT 鉴权与用户级隔离设计

## 背景

Auto Reign 当前产品边界是本地优先、单用户工作台。现有实现把 `DATA_DIR/workspace`、MySQL 投影和 Qdrant active collection 视为全局状态。要引入本地单机多账号，必须把认证身份、文件工作区、数据库记录和向量索引统一绑定到当前登录用户。

本设计参考 wegent 的两个做法：

- 后端通过 JWT 解析当前用户，并在业务入口统一得到 `current_user`。
- 资源查询必须带所有权边界，不能只按资源 id、名称或路径查全局数据。

Auto Reign 不照搬 wegent 的 group、organization、share、namespace 权限体系。当前目标是本地单机多账号硬隔离，`user_id` 就是隔离边界。

## 目标

- 默认必须登录；除注册、登录、健康检查等少量端点外，工作台 API 必须携带 JWT。
- 支持本地开放注册：用户用用户名和密码创建本地账号。
- 密码只保存哈希，不保存明文。
- 支持已登录用户修改自己的密码。
- 支持本机命令行管理员重置用户密码。
- 所有 Auto Reign 本地文件都从当前认证用户的 `user_id` 派生路径。
- MySQL 所有业务记录都以 `user_id` 为查询边界。
- Qdrant collection 按用户隔离。
- 不兼容旧 `DATA_DIR/workspace` 数据，不做旧数据回填，也不在应用启动或迁移中自动删除旧数据。

## 非目标

- 不做邮箱登录、邮箱验证、短信验证、微信扫码、OIDC 或 OAuth。
- 不做网页管理员后台。
- 不做组织、团队、共享或跨用户协作。
- 不做 refresh token、服务端 session 或 token 黑名单。
- 不做旧单用户数据自动迁移、自动接管或自动删除。

## 总体架构

认证采用本地用户名密码加 JWT。用户注册或登录成功后，后端签发 access token。前端后续请求统一携带：

```text
Authorization: Bearer <token>
```

后端在请求入口通过统一依赖解析当前用户，并构造 `UserScope`：

```text
UserScope
  user_id
  workspace_root = DATA_DIR/users/{user_id}/workspace
  tmp_root       = DATA_DIR/users/{user_id}/tmp
  exports_root   = DATA_DIR/users/{user_id}/exports
  qdrant_prefix  = auto_reign_user_{user_id}
```

业务 API 不接受前端传入的 `user_id` 来切换数据域。所有 service、repository、文件访问和索引操作都从 `UserScope` 获取隔离上下文。

## 账号与密码

新增本地账号体系：

- 注册接口创建普通用户。
- 用户名全局唯一。
- 密码使用成熟哈希算法保存到 `users.password_hash`。
- 登录时校验明文密码和哈希是否匹配。
- JWT 只包含认证和失效校验所需字段，不包含密码或密码哈希。

JWT payload 包含：

```text
sub             # username
user_id
token_version
exp
```

`token_version` 用于让旧 token 失效。用户修改密码或本机管理员重置密码时，后端递增 `users.token_version`。请求鉴权时，如果 JWT 中的版本和数据库不一致，返回 401，要求用户重新登录。

一期提供这些认证能力：

```text
POST /api/auth/register
POST /api/auth/login
GET  /api/auth/me
POST /api/auth/change-password
```

忘记密码不提供网页恢复。管理员恢复能力通过本机命令行脚本完成，例如：

```sh
cd backend
uv run python -m app.scripts.reset_user_password --username alice
```

脚本根据用户名查找用户，输入新密码后复用同一套密码哈希函数更新 `password_hash`，并递增 `token_version`。

## 数据模型

目标模型优先简洁，采用四张核心表：

```text
users
  id
  username
  password_hash
  display_name
  is_active
  token_version
  settings_json
  created_at
  updated_at

artifacts
  id
  user_id
  kind
  relative_path
  content_hash
  revision
  status_json
  metadata_json
  created_at
  updated_at

conversations
  id
  user_id
  kind
  title
  status
  config_json
  summary_json
  created_at
  updated_at
  deleted_at

messages
  id
  user_id
  conversation_id
  role
  message_type
  content
  metadata_json
  created_at
```

表职责：

- `users` 保存认证身份和用户级设置。`settings_json` 保存语言、schema version、active Qdrant collection 等用户级运行设置。
- `artifacts` 保存文件工作区投影。低频字段放在 `metadata_json`，例如 source refs、evidence refs、原始文件名、媒体类型、provenance、上传时间。处理状态、索引状态和恢复状态放在 `status_json`。
- `conversations` 保存学习和面试的会话入口、列表展示字段、配置快照和软删除状态。
- `messages` 保存学习和面试的聊天时间线。面试题目、回答反馈、追问、检索引用等结构化细节放在 `metadata_json`。

约束：

- `users.username` 唯一。
- `artifacts` 使用 `UNIQUE(user_id, relative_path)`。
- `conversations.id` 和 `messages.id` 可用 UUID。
- `messages.conversation_id` 指向 `conversations.id`。
- repository 查询必须以 `user_id + id` 为边界。只有文件路径型资源允许 `user_id + relative_path` 查询。
- 用户访问不存在或不属于自己的资源时统一返回 404。

该模型刻意不保留独立 `workspace_settings`、`interview_configs`、`interview_sessions`、`learning_sessions`、`learning_messages`、`reports` 或 `processing_jobs` 表。报告作为 `artifacts.kind = "report"` 的文件投影存在，会话列表需要的报告摘要保存在 `conversations.summary_json`。

## 文件目录

所有 Auto Reign 管理的本地文件都位于用户目录内：

```text
DATA_DIR/
  users/
    {user_id}/
      workspace/
        workspace.md
        sources/
          documents/
          extracted/
          notes/
          interviews/
        profile/
        knowledge/
        questions/
        projects/
        practice/
        review/
        state/
        reports/
        archive/
        .revisions/
      tmp/
      exports/
```

文件访问规则：

- 上传资料、学习笔记、真实面试记录、报告、revision、临时文件和导出文件都必须通过 `UserScope` 解析路径。
- 后端不能继续使用全局 `settings.workspace_dir` 作为业务工作区。
- 应用不会自动删除旧 `DATA_DIR/workspace`。需要切换到多账号版本前，由用户显式运行重置命令或手动清理旧数据。

## Qdrant 隔离

Qdrant 按用户使用独立 active collection。collection 名称格式：

```text
auto_reign_user_{user_id}__{time_ns}
```

索引规则：

- 索引重建只扫描当前用户的 `workspace_root`。
- 索引完成后只更新当前用户 `users.settings_json.active_collection`。
- 检索只读取当前用户 active collection。
- 删除 artifact 时只删除当前用户 collection 中该 artifact 的 chunks。
- orphan collection 清理只清理当前用户前缀下的 collection。

该设计避免不同用户共享同一个向量 collection，也避免同名 artifact id 或同名路径产生跨用户影响。

## API 与前端

后端默认保护工作台 API。公开端点限定为：

```text
POST /api/auth/register
POST /api/auth/login
GET  /api/health
GET  /api/models
```

`GET /api/models` 可保持公开，便于登录页展示可用模型状态；如果后续不需要登录页展示模型状态，可以改为登录后可见。

前端新增：

- `/login`
- `/register`
- 轻量 `AuthProvider` 或 `AuthGuard`
- `lib/auth.ts` 管理 token、token 过期时间和当前用户
- `lib/api.ts` 在 `apiJson`、`apiStream` 和 `uploadMaterials` 中统一加入 Bearer token

登录和注册成功后，前端保存 token 并进入工作台。401 响应会清除 token 并跳转到 `/login`。未登录时不渲染工作台 shell，只显示登录或注册页面。`AppShell` 当前用户区域显示登录用户和登出入口。

## 错误处理

建议错误码：

- 未带 token：401，`auth_required`
- token 过期、签名无效或 payload 缺失：401，`token_invalid`
- `token_version` 不匹配：401，`token_revoked`
- 用户不存在或被禁用：401，`user_inactive`
- 用户访问不存在或不属于自己的 artifact/conversation：404，`not_found`
- 用户目录初始化或文件写入失败：保留现有文件错误语义，但响应不能泄露其他用户路径。

## 测试策略

后端认证测试：

- 注册成功。
- 重复用户名失败。
- 登录成功。
- 登录密码错误失败。
- 已登录改密成功。
- 改密后旧 token 失效。
- 本机 CLI 重置密码后旧 token 失效，新密码可登录。

后端隔离测试：

- 两个用户分别创建学习记录、上传资料和面试会话。
- 用户 A 用用户 B 的 artifact id 或 conversation id 读取时返回 404。
- 两个用户可拥有相同 `relative_path`，互不覆盖。
- 未登录访问工作台 API 返回 401。

文件隔离测试：

- 用户 A 和用户 B 的文件分别落在 `DATA_DIR/users/{id}/workspace`。
- 同名上传文件、同名 knowledge 文件和 `.revisions` 目录互不影响。

Qdrant 隔离测试：

- 每个用户索引重建只写自己的 active collection。
- 用户检索只返回自己的资料片段。
- 当前用户 orphan collection sweep 不清理其他用户 collection。

前端测试：

- 未登录访问工作台跳转 `/login`。
- 登录后请求携带 `Authorization`。
- 401 清除 token 并跳转登录页。
- 注册成功进入工作台。
- `AppShell` 显示当前用户和登出入口。

文档更新：

- `README.md` 描述本地账号、启动后注册登录、显式数据重置和多账号文件目录。
- `docs/workbench-architecture.md` 改为本地多账号工作台架构。
- `docs/knowledge-data-flow.md` 改为用户级工作区、用户级 MySQL 投影和用户级 Qdrant collection。

## 落地边界

实施前需要显式重置旧本地数据。应用和迁移不会自动删除旧数据。

建议实施顺序：

1. 建立认证、用户模型、JWT、前端登录注册和保护路由。
2. 引入 `UserScope`，把 workspace 初始化和文件服务改为用户级目录。
3. 用四表目标模型重建 repository 和会话/资料 API。
4. 改造索引和检索为用户级 Qdrant collection。
5. 补齐后端隔离测试、前端登录态测试和文档。

每个阶段结束时应用应可启动，且相关测试通过。
