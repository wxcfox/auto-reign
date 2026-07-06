# 本地多账号 JWT 隔离 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Auto Reign 从单用户本地工作台改成默认必须登录的本地多账号工作台，并以 `user_id` 隔离文件、MySQL 记录和 Qdrant collection。

**Architecture:** 使用本地用户名密码注册登录，后端签发 HS256 JWT，所有业务路由通过 `get_current_user` 和 `get_user_scope` 获取当前用户边界。数据模型收敛为 `users`、`artifacts`、`conversations`、`messages` 四张核心表，workspace 文件和 Qdrant active collection 都从 `UserScope` 派生。

**Tech Stack:** FastAPI, SQLAlchemy 2, Alembic, Pydantic, pytest, Next.js, React, TypeScript, Vitest, Qdrant, LangChain

---

## 文件结构地图

后端认证与用户上下文：

- Create: `backend/app/core/passwords.py`，负责密码哈希和校验。
- Create: `backend/app/core/auth.py`，负责 JWT 创建、解析和认证错误。
- Create: `backend/app/core/user_scope.py`，负责根据当前用户派生用户目录和 Qdrant 前缀。
- Create: `backend/app/api/dependencies.py`，提供 `get_session`、`get_current_user`、`get_user_scope`。
- Create: `backend/app/api/auth.py`，提供注册、登录、当前用户、修改密码 API。
- Create: `backend/app/schemas/auth.py`，定义认证 API 请求和响应。
- Create: `backend/app/scripts/reset_user_password.py`，提供本机命令行密码重置。

后端数据模型和迁移：

- Rewrite: `backend/app/db/models.py`，保留 `UTCDateTime`，改为四张目标表。
- Add: `backend/alembic/versions/20260706_0011_rebuild_local_multi_user_schema.py`，带数据保护 guard 的目标 schema 迁移。
- Modify: `backend/app/db/session.py`，保持 session 工厂接口不变。

后端业务隔离：

- Rewrite: `backend/app/repositories/artifact_repository.py`，所有查询接收 `user_id`。
- Create: `backend/app/repositories/conversation_repository.py`，统一 conversations/messages 读写。
- Modify: `backend/app/repositories/workspace_settings_repository.py`，删除或改为读写 `users.settings_json` 的兼容适配器。
- Modify: `backend/app/services/workspace_service.py`，不再使用全局 workspace root。
- Modify: `backend/app/services/artifact_service.py`，继续通过注入的 `WorkspaceService` 访问用户 workspace。
- Modify: `backend/app/services/ingestion_service.py`，写入当前用户 artifact。
- Modify: `backend/app/services/workspace_content_service.py`，保存学习笔记和真实面试记录时使用 `UserScope`。
- Modify: `backend/app/services/conversation_service.py`，使用 `conversations` 和 `messages`。
- Modify: `backend/app/services/learning_conversation_service.py`，合并或删除其职责，把学习消息写入 `ConversationRepository`。
- Modify: `backend/app/services/interview_service.py`，以 conversation/message metadata 保存面试状态。
- Modify: `backend/app/services/interview_completion_service.py`，报告作为 artifact，摘要写入 conversation。
- Modify: `backend/app/services/interview_artifact_service.py`，写入当前用户 workspace。
- Modify: `backend/app/services/index_service.py`，只处理当前用户 workspace 和 collection。
- Modify: `backend/app/services/workspace_retrieval_service.py`，只读取当前用户 active collection。
- Modify: `backend/app/services/context_assembler.py`，从当前用户 workspace 读取上下文。

后端 API：

- Modify: `backend/app/main.py`，注册 auth router，移除全局 workspace/artifact service state。
- Modify: `backend/app/api/workspace.py`，所有端点要求 `UserScope`。
- Modify: `backend/app/api/interviews.py`，所有端点要求 `UserScope`。
- Modify: `backend/app/api/conversations.py`，所有端点要求 `UserScope`。
- Modify: `backend/app/api/reports.py`，报告列表来自用户 artifact/conversation。
- Leave public: `backend/app/api/health.py`、`backend/app/api/models.py`。

前端：

- Create: `frontend/src/lib/auth.ts`，管理 token、当前用户和登录态。
- Modify: `frontend/src/lib/api.ts`，统一加 Bearer token，并处理 401。
- Create: `frontend/src/components/AuthGuard.tsx`，保护工作台页面。
- Modify: `frontend/src/app/layout.tsx`，登录/注册页不渲染工作台 shell。
- Create: `frontend/src/app/login/page.tsx`。
- Create: `frontend/src/app/register/page.tsx`。
- Modify: `frontend/src/components/AppShell.tsx`，显示当前用户和登出。
- Modify: `frontend/src/i18n/locales/zh-CN/common.json`、`frontend/src/i18n/locales/en/common.json`，加入登录、注册、登出文案。

测试和文档：

- Create: `backend/tests/test_auth_api.py`
- Create: `backend/tests/test_passwords.py`
- Create: `backend/tests/test_reset_user_password.py`
- Create: `backend/tests/test_user_scope.py`
- Create: `backend/tests/test_user_artifact_isolation.py`
- Create: `backend/tests/test_user_conversation_isolation.py`
- Create: `backend/tests/test_user_index_isolation.py`
- Modify: `backend/tests/conftest.py`
- Create: `frontend/src/lib/auth.test.ts`
- Modify: `frontend/src/lib/api.test.ts`
- Create: `frontend/src/components/__tests__/AuthGuard.test.tsx`
- Create: `frontend/src/app/login/page.test.tsx`
- Create: `frontend/src/app/register/page.test.tsx`
- Modify: `README.md`
- Modify: `docs/workbench-architecture.md`
- Modify: `docs/knowledge-data-flow.md`

---

### Task 1: 密码哈希和 JWT 原语

**Files:**
- Create: `backend/app/core/passwords.py`
- Create: `backend/app/core/auth.py`
- Modify: `backend/app/core/config.py`
- Test: `backend/tests/test_passwords.py`

- [ ] **Step 1: Write the failing password tests**

Create `backend/tests/test_passwords.py`:

```python
from app.core.passwords import hash_password, verify_password


def test_hash_password_does_not_store_plaintext() -> None:
    hashed = hash_password("correct horse battery staple")

    assert hashed != "correct horse battery staple"
    assert hashed.startswith("pbkdf2_sha256$")


def test_verify_password_accepts_correct_password() -> None:
    hashed = hash_password("correct horse battery staple")

    assert verify_password("correct horse battery staple", hashed) is True


def test_verify_password_rejects_wrong_password() -> None:
    hashed = hash_password("correct horse battery staple")

    assert verify_password("wrong password", hashed) is False


def test_hash_password_uses_unique_salt() -> None:
    first = hash_password("same password")
    second = hash_password("same password")

    assert first != second
    assert verify_password("same password", first) is True
    assert verify_password("same password", second) is True
```

- [ ] **Step 2: Run password tests to verify they fail**

Run:

```sh
cd backend
uv run pytest tests/test_passwords.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.core.passwords'`.

- [ ] **Step 3: Implement password hashing**

Create `backend/app/core/passwords.py`:

```python
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets


_ALGORITHM = "pbkdf2_sha256"
_ITERATIONS = 600_000
_SALT_BYTES = 16


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}".encode("ascii"))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        _ITERATIONS,
    )
    return f"{_ALGORITHM}${_ITERATIONS}${_b64encode(salt)}${_b64encode(digest)}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, iterations_text, salt_text, digest_text = password_hash.split("$", 3)
        if algorithm != _ALGORITHM:
            return False
        iterations = int(iterations_text)
        salt = _b64decode(salt_text)
        expected_digest = _b64decode(digest_text)
    except (ValueError, TypeError):
        return False

    actual_digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
    )
    return hmac.compare_digest(actual_digest, expected_digest)
```

- [ ] **Step 4: Run password tests to verify they pass**

Run:

```sh
cd backend
uv run pytest tests/test_passwords.py -v
```

Expected: PASS.

- [ ] **Step 5: Write failing JWT tests**

Append to `backend/tests/test_passwords.py`:

```python
from datetime import UTC, datetime, timedelta

import pytest

from app.core.auth import (
    TokenInvalidError,
    create_access_token,
    decode_access_token,
)


def test_access_token_round_trip(monkeypatch) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    from app.core.config import get_settings

    get_settings.cache_clear()
    token = create_access_token(username="alice", user_id=7, token_version=2)

    payload = decode_access_token(token)

    assert payload.username == "alice"
    assert payload.user_id == 7
    assert payload.token_version == 2
    get_settings.cache_clear()


def test_access_token_rejects_expired_token(monkeypatch) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    from app.core.config import get_settings

    get_settings.cache_clear()
    token = create_access_token(
        username="alice",
        user_id=7,
        token_version=2,
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )

    with pytest.raises(TokenInvalidError):
        decode_access_token(token)

    get_settings.cache_clear()
```

- [ ] **Step 6: Run JWT tests to verify they fail**

Run:

```sh
cd backend
uv run pytest tests/test_passwords.py::test_access_token_round_trip tests/test_passwords.py::test_access_token_rejects_expired_token -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.core.auth'`.

- [ ] **Step 7: Add JWT settings**

Modify `backend/app/core/config.py` inside `Settings`:

```python
    jwt_secret_key: str = "auto-reign-local-dev-secret-change-me"
    access_token_expire_minutes: int = 60 * 24 * 7
```

- [ ] **Step 8: Implement JWT helpers**

Create `backend/app/core/auth.py`:

```python
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.config import get_settings


class TokenInvalidError(ValueError):
    pass


@dataclass(frozen=True)
class AccessTokenPayload:
    username: str
    user_id: int
    token_version: int
    expires_at: datetime


def _b64encode_json(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode_json(value: str) -> dict[str, Any]:
    padding = "=" * (-len(value) % 4)
    raw = base64.urlsafe_b64decode(f"{value}{padding}".encode("ascii"))
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise TokenInvalidError("JWT payload must be an object.")
    return data


def _sign(message: str, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), message.encode("ascii"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def create_access_token(
    *,
    username: str,
    user_id: int,
    token_version: int,
    expires_at: datetime | None = None,
) -> str:
    settings = get_settings()
    expire = expires_at or (
        datetime.now(UTC) + timedelta(minutes=settings.access_token_expire_minutes)
    )
    header = _b64encode_json({"alg": "HS256", "typ": "JWT"})
    payload = _b64encode_json(
        {
            "sub": username,
            "user_id": user_id,
            "token_version": token_version,
            "exp": int(expire.timestamp()),
        }
    )
    signing_input = f"{header}.{payload}"
    signature = _sign(signing_input, settings.jwt_secret_key)
    return f"{signing_input}.{signature}"


def decode_access_token(token: str) -> AccessTokenPayload:
    settings = get_settings()
    try:
        header, payload, signature = token.split(".", 2)
    except ValueError as exc:
        raise TokenInvalidError("Malformed JWT.") from exc

    expected_signature = _sign(f"{header}.{payload}", settings.jwt_secret_key)
    if not hmac.compare_digest(signature, expected_signature):
        raise TokenInvalidError("JWT signature is invalid.")

    header_data = _b64decode_json(header)
    if header_data.get("alg") != "HS256" or header_data.get("typ") != "JWT":
        raise TokenInvalidError("JWT header is invalid.")

    payload_data = _b64decode_json(payload)
    username = payload_data.get("sub")
    user_id = payload_data.get("user_id")
    token_version = payload_data.get("token_version")
    exp = payload_data.get("exp")
    if not isinstance(username, str) or not username:
        raise TokenInvalidError("JWT subject is missing.")
    if not isinstance(user_id, int) or user_id <= 0:
        raise TokenInvalidError("JWT user_id is missing.")
    if not isinstance(token_version, int) or token_version < 0:
        raise TokenInvalidError("JWT token_version is missing.")
    if not isinstance(exp, int):
        raise TokenInvalidError("JWT exp is missing.")

    expires_at = datetime.fromtimestamp(exp, UTC)
    if expires_at <= datetime.now(UTC):
        raise TokenInvalidError("JWT is expired.")
    return AccessTokenPayload(
        username=username,
        user_id=user_id,
        token_version=token_version,
        expires_at=expires_at,
    )
```

- [ ] **Step 9: Run backend primitive tests**

Run:

```sh
cd backend
uv run pytest tests/test_passwords.py -v
```

Expected: PASS.

- [ ] **Step 10: Commit**

```sh
git add backend/app/core/passwords.py backend/app/core/auth.py backend/app/core/config.py backend/tests/test_passwords.py
git commit -m "Add local password and JWT primitives"
```

---

### Task 2: 四表目标模型和数据保护迁移

**Files:**
- Modify: `backend/app/db/models.py`
- Create: `backend/alembic/versions/20260706_0011_rebuild_local_multi_user_schema.py`
- Modify: `backend/tests/test_schema.py`
- Test: `backend/tests/test_schema.py`

- [ ] **Step 1: Write failing schema tests**

Append to `backend/tests/test_schema.py`:

```python
from sqlalchemy import inspect


def test_local_multi_user_schema_contains_target_tables(client) -> None:
    from app.db.session import session_scope

    with session_scope(client.app.state.session_factory) as session:
        tables = set(inspect(session.get_bind()).get_table_names())

    assert {"users", "artifacts", "conversations", "messages"}.issubset(tables)
    assert "interview_sessions" not in tables
    assert "learning_sessions" not in tables
    assert "workspace_settings" not in tables


def test_artifact_paths_are_unique_per_user(client) -> None:
    from app.db.models import Artifact, User
    from app.db.session import session_scope

    with session_scope(client.app.state.session_factory) as session:
        first = User(username="alice", password_hash="hash", display_name="Alice")
        second = User(username="bob", password_hash="hash", display_name="Bob")
        session.add_all([first, second])
        session.flush()
        session.add(
            Artifact(
                id="artifact-a",
                user_id=first.id,
                kind="knowledge",
                relative_path="knowledge/mysql.md",
                content_hash="a",
                revision=1,
                status_json={},
                metadata_json={},
            )
        )
        session.add(
            Artifact(
                id="artifact-b",
                user_id=second.id,
                kind="knowledge",
                relative_path="knowledge/mysql.md",
                content_hash="b",
                revision=1,
                status_json={},
                metadata_json={},
            )
        )
```

- [ ] **Step 2: Run schema tests to verify they fail**

Run:

```sh
cd backend
uv run pytest tests/test_schema.py::test_local_multi_user_schema_contains_target_tables tests/test_schema.py::test_artifact_paths_are_unique_per_user -v
```

Expected: FAIL because old tables still exist and `User` is not defined.

- [ ] **Step 3: Replace ORM models with target schema**

Modify `backend/app/db/models.py` to keep `_uuid`, `_now`, `UTCDateTime`, `Base`, then define these models:

```python
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    token_version: Mapped[int] = mapped_column(Integer, default=1)
    settings_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, onupdate=_now)

    artifacts: Mapped[list["Artifact"]] = relationship(back_populates="user")
    conversations: Mapped[list["Conversation"]] = relationship(back_populates="user")
    messages: Mapped[list["Message"]] = relationship(back_populates="user")


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (UniqueConstraint("user_id", "relative_path", name="uq_artifacts_user_path"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    relative_path: Mapped[str] = mapped_column(String(512), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), default="")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    status_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, onupdate=_now)

    user: Mapped[User] = relationship(back_populates="artifacts")


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(32), default="active")
    config_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    summary_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, onupdate=_now)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    user: Mapped[User] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        index=True,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    message_type: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="")
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now)

    user: Mapped[User] = relationship(back_populates="messages")
    conversation: Mapped[Conversation] = relationship(back_populates="messages")
```

Also add imports:

```python
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
```

- [ ] **Step 4: Add guarded Alembic migration**

Create `backend/alembic/versions/20260706_0011_rebuild_local_multi_user_schema.py`:

```python
"""Rebuild schema for local multi-user isolation.

Revision ID: 20260706_0011
Revises: 20260701_0010
Create Date: 2026-07-06
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260706_0011"
down_revision: str | None = "20260701_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


LEGACY_TABLES = (
    "processing_jobs",
    "reports",
    "learning_messages",
    "learning_sessions",
    "interview_turns",
    "interview_sessions",
    "interview_configs",
    "artifacts",
    "workspace_settings",
)


def _legacy_table_has_rows(table_name: str) -> bool:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if table_name not in inspector.get_table_names():
        return False
    result = connection.execute(sa.text(f"SELECT 1 FROM {table_name} LIMIT 1"))
    return result.first() is not None


def _drop_table_if_exists(table_name: str) -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if table_name in inspector.get_table_names():
        op.drop_table(table_name)


def upgrade() -> None:
    non_empty = [table for table in LEGACY_TABLES if _legacy_table_has_rows(table)]
    if non_empty:
        joined = ", ".join(non_empty)
        raise RuntimeError(
            "Local multi-user schema does not migrate legacy local data automatically. "
            f"Run ./reset-data.sh explicitly before upgrading. Non-empty tables: {joined}"
        )

    for table_name in LEGACY_TABLES:
        _drop_table_if_exists(table_name)

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("username", sa.String(length=80), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("settings_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username", name="uq_users_username"),
    )
    op.create_index("ix_users_username", "users", ["username"])

    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("relative_path", sa.String(length=512), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status_json", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "relative_path", name="uq_artifacts_user_path"),
    )
    op.create_index("ix_artifacts_user_id", "artifacts", ["user_id"])
    op.create_index("ix_artifacts_kind", "artifacts", ["kind"])

    op.create_table(
        "conversations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("summary_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_conversations_user_id", "conversations", ["user_id"])
    op.create_index("ix_conversations_kind", "conversations", ["kind"])

    op.create_table(
        "messages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("message_type", sa.String(length=64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_messages_user_id", "messages", ["user_id"])
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])


def downgrade() -> None:
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("artifacts")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_table("users")
```

- [ ] **Step 5: Run schema tests**

Run:

```sh
cd backend
uv run pytest tests/test_schema.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```sh
git add backend/app/db/models.py backend/alembic/versions/20260706_0011_rebuild_local_multi_user_schema.py backend/tests/test_schema.py
git commit -m "Rebuild schema for local user isolation"
```

---

### Task 3: 认证 API 和命令行密码重置

**Files:**
- Create: `backend/app/schemas/auth.py`
- Create: `backend/app/core/user_scope.py`
- Create: `backend/app/api/auth.py`
- Create: `backend/app/scripts/reset_user_password.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_auth_api.py`
- Test: `backend/tests/test_reset_user_password.py`

- [ ] **Step 1: Write failing auth API tests**

Create `backend/tests/test_auth_api.py`:

```python
def test_register_returns_token_and_user(client) -> None:
    response = client.post(
        "/api/auth/register",
        json={"username": "alice", "password": "correct horse battery staple"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["token_type"] == "bearer"
    assert body["user"]["username"] == "alice"
    assert "password" not in body["user"]
    assert "password_hash" not in body["user"]


def test_register_rejects_duplicate_username(client) -> None:
    client.post(
        "/api/auth/register",
        json={"username": "alice", "password": "correct horse battery staple"},
    )

    response = client.post(
        "/api/auth/register",
        json={"username": "alice", "password": "another good password"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "username_taken"


def test_login_and_me(client) -> None:
    client.post(
        "/api/auth/register",
        json={"username": "alice", "password": "correct horse battery staple"},
    )

    login = client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "correct horse battery staple"},
    )
    token = login.json()["access_token"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert login.status_code == 200
    assert me.status_code == 200
    assert me.json()["username"] == "alice"


def test_change_password_revokes_old_token(client) -> None:
    registered = client.post(
        "/api/auth/register",
        json={"username": "alice", "password": "correct horse battery staple"},
    ).json()
    old_token = registered["access_token"]

    changed = client.post(
        "/api/auth/change-password",
        json={
            "old_password": "correct horse battery staple",
            "new_password": "new correct horse battery staple",
        },
        headers={"Authorization": f"Bearer {old_token}"},
    )

    assert changed.status_code == 200
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {old_token}"}).status_code == 401
    new_login = client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "new correct horse battery staple"},
    )
    assert new_login.status_code == 200
```

- [ ] **Step 2: Run auth API tests to verify they fail**

Run:

```sh
cd backend
uv run pytest tests/test_auth_api.py -v
```

Expected: FAIL with 404 for `/api/auth/register`.

- [ ] **Step 3: Implement auth schemas**

Create `backend/app/schemas/auth.py`:

```python
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str = Field(min_length=12, max_length=256)
    display_name: str = Field(default="", max_length=120)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


class UserResponse(BaseModel):
    id: int
    username: str
    display_name: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse
```

- [ ] **Step 4: Implement UserScope**

Create `backend/app/core/user_scope.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings
from app.db.models import User


@dataclass(frozen=True)
class UserScope:
    user_id: int
    workspace_root: Path
    tmp_root: Path
    exports_root: Path
    qdrant_prefix: str


def build_user_scope(settings: Settings, user: User) -> UserScope:
    user_root = settings.data_dir / "users" / str(user.id)
    scope = UserScope(
        user_id=user.id,
        workspace_root=user_root / "workspace",
        tmp_root=user_root / "tmp",
        exports_root=user_root / "exports",
        qdrant_prefix=f"auto_reign_user_{user.id}",
    )
    scope.tmp_root.mkdir(parents=True, exist_ok=True)
    scope.exports_root.mkdir(parents=True, exist_ok=True)
    return scope
```

- [ ] **Step 5: Implement auth dependencies**

Create `backend/app/api/dependencies.py`:

```python
from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.auth import TokenInvalidError, decode_access_token
from app.core.user_scope import UserScope, build_user_scope
from app.db import models
from app.db.session import session_scope


def get_session(request: Request) -> Iterator[Session]:
    with session_scope(request.app.state.session_factory) as session:
        yield session


def _auth_error(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=401, detail={"code": code, "message": message})


def get_current_user(
    authorization: str = Header(default=""),
    session: Session = Depends(get_session),
) -> models.User:
    if not authorization:
        raise _auth_error("auth_required", "Authentication is required.")
    parts = authorization.split(maxsplit=1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise _auth_error("token_invalid", "Bearer token is required.")
    try:
        payload = decode_access_token(parts[1])
    except TokenInvalidError as exc:
        raise _auth_error("token_invalid", "Token is invalid.") from exc

    user = session.get(models.User, payload.user_id)
    if user is None or not user.is_active:
        raise _auth_error("user_inactive", "User is inactive.")
    if user.username != payload.username or user.token_version != payload.token_version:
        raise _auth_error("token_revoked", "Token has been revoked.")
    return user


def get_user_scope(
    request: Request,
    current_user: models.User = Depends(get_current_user),
) -> UserScope:
    return build_user_scope(request.app.state.settings, current_user)
```

- [ ] **Step 6: Implement auth API**

Create `backend/app/api/auth.py`:

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, get_session
from app.core.auth import create_access_token
from app.core.passwords import hash_password, verify_password
from app.db import models
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)


router = APIRouter(prefix="/api/auth")


def _token_response(user: models.User) -> TokenResponse:
    token = create_access_token(
        username=user.username,
        user_id=user.id,
        token_version=user.token_version,
    )
    return TokenResponse(access_token=token, user=UserResponse.model_validate(user))


@router.post("/register", response_model=TokenResponse)
def register(payload: RegisterRequest, session: Session = Depends(get_session)) -> TokenResponse:
    username = payload.username.strip()
    existing = session.scalar(select(models.User).where(models.User.username == username))
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail={"code": "username_taken", "message": "Username is already taken."},
        )
    user = models.User(
        username=username,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name.strip() or username,
        settings_json={"schema_version": 1, "language": "zh-CN", "active_collection": ""},
    )
    session.add(user)
    session.flush()
    return _token_response(user)


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, session: Session = Depends(get_session)) -> TokenResponse:
    user = session.scalar(select(models.User).where(models.User.username == payload.username.strip()))
    if user is None or not user.is_active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=401,
            detail={"code": "invalid_credentials", "message": "Username or password is incorrect."},
        )
    return _token_response(user)


@router.get("/me", response_model=UserResponse)
def me(current_user: models.User = Depends(get_current_user)) -> UserResponse:
    return UserResponse.model_validate(current_user)


@router.post("/change-password", response_model=UserResponse)
def change_password(
    payload: ChangePasswordRequest,
    current_user: models.User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> UserResponse:
    user = session.get(models.User, current_user.id)
    if user is None or not verify_password(payload.old_password, user.password_hash):
        raise HTTPException(
            status_code=401,
            detail={"code": "invalid_credentials", "message": "Old password is incorrect."},
        )
    user.password_hash = hash_password(payload.new_password)
    user.token_version += 1
    session.flush()
    return UserResponse.model_validate(user)
```

- [ ] **Step 7: Register auth router and settings in app state**

Modify `backend/app/main.py`:

```python
from app.api.auth import router as auth_router
```

Inside `create_app()`, after `app = FastAPI(...)` and before other routers:

```python
    app.state.settings = settings
    app.include_router(auth_router)
```

Keep `health_router` and `models_router` public.

- [ ] **Step 8: Run auth API tests**

Run:

```sh
cd backend
uv run pytest tests/test_auth_api.py -v
```

Expected: PASS.

- [ ] **Step 9: Write failing password reset CLI test**

Create `backend/tests/test_reset_user_password.py`:

```python
from app.core.passwords import verify_password


def test_reset_user_password_updates_hash_and_token_version(client, monkeypatch) -> None:
    from app.db.models import User
    from app.db.session import session_scope
    from app.scripts.reset_user_password import reset_user_password

    with session_scope(client.app.state.session_factory) as session:
        user = User(username="alice", password_hash="old", display_name="Alice")
        session.add(user)
        session.flush()
        user_id = user.id

    monkeypatch.setattr("getpass.getpass", lambda prompt: "new correct horse battery staple")
    reset_user_password(
        session_factory=client.app.state.session_factory,
        username="alice",
    )

    with session_scope(client.app.state.session_factory) as session:
        user = session.get(User, user_id)
        assert user is not None
        assert user.token_version == 2
        assert verify_password("new correct horse battery staple", user.password_hash)
```

- [ ] **Step 10: Implement password reset CLI**

Create `backend/app/scripts/reset_user_password.py`:

```python
from __future__ import annotations

import argparse
import getpass

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.passwords import hash_password
from app.db.models import User
from app.db.session import create_engine_for_settings, make_session_factory, session_scope


def reset_user_password(*, session_factory: sessionmaker[Session], username: str) -> None:
    new_password = getpass.getpass("New password: ")
    confirm_password = getpass.getpass("Confirm password: ")
    if new_password != confirm_password:
        raise SystemExit("Passwords do not match.")
    if len(new_password) < 12:
        raise SystemExit("Password must contain at least 12 characters.")

    with session_scope(session_factory) as session:
        user = session.scalar(select(User).where(User.username == username))
        if user is None:
            raise SystemExit(f"User not found: {username}")
        user.password_hash = hash_password(new_password)
        user.token_version += 1
        session.flush()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", required=True)
    args = parser.parse_args()
    engine = create_engine_for_settings(get_settings())
    try:
        reset_user_password(
            session_factory=make_session_factory(engine),
            username=args.username,
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
```

- [ ] **Step 11: Run reset CLI test**

Run:

```sh
cd backend
uv run pytest tests/test_reset_user_password.py -v
```

Expected: PASS.

- [ ] **Step 12: Commit**

```sh
git add backend/app/schemas/auth.py backend/app/core/user_scope.py backend/app/api/dependencies.py backend/app/api/auth.py backend/app/main.py backend/app/scripts/reset_user_password.py backend/tests/test_auth_api.py backend/tests/test_reset_user_password.py
git commit -m "Add local auth API and password reset command"
```

---

### Task 4: UserScope 和默认保护业务 API

**Files:**
- Modify: `backend/app/core/user_scope.py`
- Modify: `backend/app/api/workspace.py`
- Modify: `backend/app/api/interviews.py`
- Modify: `backend/app/api/conversations.py`
- Modify: `backend/app/api/reports.py`
- Test: `backend/tests/test_user_scope.py`

- [ ] **Step 1: Write failing UserScope tests**

Create `backend/tests/test_user_scope.py`:

```python
def _register(client, username: str) -> str:
    response = client.post(
        "/api/auth/register",
        json={"username": username, "password": "correct horse battery staple"},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def test_workspace_api_requires_auth(client) -> None:
    response = client.get("/api/workspace")

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "auth_required"


def test_user_scope_creates_user_directories(client) -> None:
    token = _register(client, "alice")

    response = client.get("/api/workspace", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    data_dir = client.app.state.settings.data_dir
    assert (data_dir / "users" / "1" / "workspace").exists()
    assert (data_dir / "users" / "1" / "tmp").exists()
    assert (data_dir / "users" / "1" / "exports").exists()
```

- [ ] **Step 2: Run UserScope tests to verify they fail**

Run:

```sh
cd backend
uv run pytest tests/test_user_scope.py -v
```

Expected: FAIL because business endpoints are still public and `UserScope` is missing.

- [ ] **Step 3: Protect workspace API with auth dependency**

Modify each endpoint in `backend/app/api/workspace.py` to include:

```python
from app.api.dependencies import get_session, get_user_scope
from app.core.user_scope import UserScope
```

Then add `scope: UserScope = Depends(get_user_scope)` to route functions and construct per-user services:

```python
workspace_service = WorkspaceService(scope.workspace_root)
workspace_service.initialize()
artifact_service = ArtifactService(workspace_service)
```

For `workspace_status`, use:

```python
@router.get("", response_model=WorkspaceStatusResponse)
def workspace_status(
    scope: UserScope = Depends(get_user_scope),
    session: Session = Depends(get_session),
) -> WorkspaceStatusResponse:
    workspace_service = WorkspaceService(scope.workspace_root)
    workspace_service.initialize()
    artifacts = ArtifactRepository().list(session, user_id=scope.user_id)
    return WorkspaceStatusResponse(
        schema_version=1,
        language="zh-CN",
        artifact_count=len(artifacts),
        initialized=True,
    )
```

- [ ] **Step 4: Protect interview, conversation and report APIs**

In `backend/app/api/interviews.py`, `backend/app/api/conversations.py`, and `backend/app/api/reports.py`, replace local `get_session` definitions with imports from `app.api.dependencies` and add:

```python
scope: UserScope = Depends(get_user_scope)
```

Pass `scope` into services in later tasks. In this task, the minimal behavior is that unauthenticated requests fail before reaching service logic.

- [ ] **Step 5: Run UserScope tests**

Run:

```sh
cd backend
uv run pytest tests/test_user_scope.py -v
```

Expected: PASS.

- [ ] **Step 6: Run auth tests again**

Run:

```sh
cd backend
uv run pytest tests/test_auth_api.py tests/test_user_scope.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```sh
git add backend/app/api/workspace.py backend/app/api/interviews.py backend/app/api/conversations.py backend/app/api/reports.py backend/tests/test_user_scope.py
git commit -m "Require auth for workspace APIs"
```

---

### Task 5: 用户级 artifacts 和文件工作区

**Files:**
- Modify: `backend/app/repositories/artifact_repository.py`
- Modify: `backend/app/services/workspace_service.py`
- Modify: `backend/app/services/ingestion_service.py`
- Modify: `backend/app/services/workspace_content_service.py`
- Modify: `backend/app/api/workspace.py`
- Test: `backend/tests/test_user_artifact_isolation.py`

- [ ] **Step 1: Write failing artifact isolation tests**

Create `backend/tests/test_user_artifact_isolation.py`:

```python
def _register(client, username: str) -> str:
    return client.post(
        "/api/auth/register",
        json={"username": username, "password": "correct horse battery staple"},
    ).json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _sse_result(body: str) -> dict[str, object]:
    import json

    for frame in body.strip().split("\n\n"):
        lines = frame.splitlines()
        if "event: result" not in lines:
            continue
        data = "\n".join(
            line.removeprefix("data:").strip()
            for line in lines
            if line.startswith("data:")
        )
        return json.loads(data)
    raise AssertionError("SSE response did not include a result event.")


def test_users_can_create_same_learning_artifact_path_without_collision(client, monkeypatch) -> None:
    class NoopIndexService:
        def rebuild_index(self, *args, **kwargs) -> str:
            return "noop"

    monkeypatch.setattr("app.api.workspace.IndexService", NoopIndexService)
    alice = _register(client, "alice")
    bob = _register(client, "bob")

    first = client.post(
        "/api/workspace/learning-notes/stream",
        json={"text": "今天学习了 MySQL 覆盖索引。", "language": "zh-CN"},
        headers=_auth(alice),
    )
    second = client.post(
        "/api/workspace/learning-notes/stream",
        json={"text": "今天学习了 MySQL 覆盖索引。", "language": "zh-CN"},
        headers=_auth(bob),
    )

    assert first.status_code == 200
    assert second.status_code == 200
    data_dir = client.app.state.settings.data_dir
    assert (data_dir / "users" / "1" / "workspace" / "knowledge").exists()
    assert (data_dir / "users" / "2" / "workspace" / "knowledge").exists()


def test_user_cannot_read_other_users_artifact(client, monkeypatch) -> None:
    class NoopIndexService:
        def rebuild_index(self, *args, **kwargs) -> str:
            return "noop"

    monkeypatch.setattr("app.api.workspace.IndexService", NoopIndexService)
    alice = _register(client, "alice")
    bob = _register(client, "bob")
    response = client.post(
        "/api/workspace/learning-notes/stream",
        json={"text": "今天学习了 Redis 缓存穿透。", "language": "zh-CN"},
        headers=_auth(alice),
    )
    artifact_id = _sse_result(response.text)["artifact"]["id"]

    forbidden = client.get(f"/api/workspace/artifacts/{artifact_id}", headers=_auth(bob))

    assert forbidden.status_code == 404
```

- [ ] **Step 2: Run artifact isolation tests to verify they fail**

Run:

```sh
cd backend
uv run pytest tests/test_user_artifact_isolation.py -v
```

Expected: FAIL because repository and workspace services are not fully user-scoped.

- [ ] **Step 3: Refactor ArtifactRepository to require user_id**

Modify `backend/app/repositories/artifact_repository.py`:

```python
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models


class ArtifactRepository:
    def get(self, session: Session, *, user_id: int, artifact_id: str) -> models.Artifact | None:
        return session.scalar(
            select(models.Artifact).where(
                models.Artifact.user_id == user_id,
                models.Artifact.id == artifact_id,
            )
        )

    def get_by_relative_path(
        self, session: Session, *, user_id: int, relative_path: str
    ) -> models.Artifact | None:
        return session.scalar(
            select(models.Artifact).where(
                models.Artifact.user_id == user_id,
                models.Artifact.relative_path == relative_path,
            )
        )

    def get_source_by_content_hash(
        self, session: Session, *, user_id: int, content_hash: str
    ) -> models.Artifact | None:
        return session.scalar(
            select(models.Artifact).where(
                models.Artifact.user_id == user_id,
                models.Artifact.kind == "source",
                models.Artifact.content_hash == content_hash,
            )
        )

    def list(self, session: Session, *, user_id: int) -> list[models.Artifact]:
        return list(
            session.scalars(
                select(models.Artifact)
                .where(models.Artifact.user_id == user_id)
                .order_by(models.Artifact.relative_path)
            )
        )

    def upsert(
        self,
        session: Session,
        *,
        user_id: int,
        artifact_id: str,
        kind: str,
        relative_path: str,
        content_hash: str,
        revision: int,
        status_json: dict[str, object] | None = None,
        metadata_json: dict[str, object] | None = None,
    ) -> models.Artifact:
        artifact = self.get(session, user_id=user_id, artifact_id=artifact_id)
        if artifact is None:
            artifact = self.get_by_relative_path(
                session,
                user_id=user_id,
                relative_path=relative_path,
            )
        if artifact is None:
            artifact = models.Artifact(
                id=artifact_id,
                user_id=user_id,
                kind=kind,
                relative_path=relative_path,
            )
            session.add(artifact)
        artifact.kind = kind
        artifact.relative_path = relative_path
        artifact.content_hash = content_hash
        artifact.revision = revision
        artifact.status_json = status_json or {}
        artifact.metadata_json = metadata_json or {}
        session.flush()
        return artifact

    def delete(self, session: Session, artifact: models.Artifact) -> None:
        session.delete(artifact)
        session.flush()
```

- [ ] **Step 4: Update WorkspaceService projection to accept user_id**

Modify `WorkspaceService.rebuild_projection` signature:

```python
def rebuild_projection(self, session: Session, repository, artifact_service, *, user_id: int) -> None:
```

Inside it, replace:

```python
existing_by_path = {artifact.relative_path: artifact for artifact in repository.list(session)}
```

with:

```python
existing_by_path = {
    artifact.relative_path: artifact for artifact in repository.list(session, user_id=user_id)
}
```

Replace every `repository.upsert(...)` call with `repository.upsert(user_id=user_id, ...)`, and map old columns into JSON:

```python
status_json={
    "processing_status": processing_status,
    "index_status": index_status,
    "recovery_required": recovery_required,
    "recovery_reason": recovery_reason,
},
metadata_json={
    "source_refs": source_refs,
    "evidence_refs": evidence_refs,
    "language": language,
    "source_filename": source_filename,
    "media_type": media_type,
    "size_bytes": size_bytes,
    "origin": origin,
    "edited_by": edited_by,
    "uploaded_at": uploaded_at.isoformat() if uploaded_at else None,
},
```

- [ ] **Step 5: Add artifact compatibility helpers**

Create helper functions in `backend/app/services/artifact_document_service.py` or a new `backend/app/services/artifact_metadata.py`:

```python
def artifact_index_status(artifact) -> str:
    return str((artifact.status_json or {}).get("index_status") or "pending")


def artifact_processing_status(artifact) -> str:
    return str((artifact.status_json or {}).get("processing_status") or "completed")


def artifact_language(artifact) -> str:
    return str((artifact.metadata_json or {}).get("language") or "zh-CN")


def artifact_source_refs(artifact) -> list[str]:
    value = (artifact.metadata_json or {}).get("source_refs") or []
    return [str(item) for item in value if isinstance(item, str)]
```

Use these helpers wherever old `artifact.index_status`, `artifact.processing_status`, `artifact.language`, `artifact.source_refs`, or `artifact.evidence_refs` properties were read.

- [ ] **Step 6: Update workspace API artifact reads**

In `backend/app/api/workspace.py`, every artifact lookup must include current user:

```python
artifact = ArtifactRepository().get(session, user_id=scope.user_id, artifact_id=artifact_id)
```

Every list call:

```python
artifacts = ArtifactRepository().list(session, user_id=scope.user_id)
```

Every projection rebuild:

```python
workspace_service.rebuild_projection(
    session,
    repository,
    artifact_service,
    user_id=scope.user_id,
)
```

- [ ] **Step 7: Run artifact isolation tests**

Run:

```sh
cd backend
uv run pytest tests/test_user_artifact_isolation.py tests/test_workspace_api.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```sh
git add backend/app/repositories/artifact_repository.py backend/app/services/workspace_service.py backend/app/services/artifact_document_service.py backend/app/api/workspace.py backend/tests/test_user_artifact_isolation.py
git commit -m "Scope workspace artifacts by user"
```

---

### Task 6: 统一 conversations/messages 会话模型

**Files:**
- Create: `backend/app/repositories/conversation_repository.py`
- Modify: `backend/app/services/conversation_service.py`
- Modify: `backend/app/services/workspace_content_service.py`
- Modify: `backend/app/api/conversations.py`
- Modify: `backend/app/api/workspace.py`
- Test: `backend/tests/test_user_conversation_isolation.py`
- Modify: `backend/tests/test_conversations.py`

- [ ] **Step 1: Write failing conversation isolation tests**

Create `backend/tests/test_user_conversation_isolation.py`:

```python
def _register(client, username: str) -> str:
    return client.post(
        "/api/auth/register",
        json={"username": username, "password": "correct horse battery staple"},
    ).json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_conversation_list_is_user_scoped(client, monkeypatch) -> None:
    class NoopIndexService:
        def rebuild_index(self, *args, **kwargs) -> str:
            return "noop"

    monkeypatch.setattr("app.api.workspace.IndexService", NoopIndexService)
    alice = _register(client, "alice")
    bob = _register(client, "bob")

    client.post(
        "/api/workspace/learning-notes/stream",
        json={"text": "Alice 学习 Redis。", "language": "zh-CN"},
        headers=_auth(alice),
    )
    client.post(
        "/api/workspace/learning-notes/stream",
        json={"text": "Bob 学习 MySQL。", "language": "zh-CN"},
        headers=_auth(bob),
    )

    alice_list = client.get("/api/conversations", headers=_auth(alice)).json()["conversations"]
    bob_list = client.get("/api/conversations", headers=_auth(bob)).json()["conversations"]

    assert len(alice_list) == 1
    assert len(bob_list) == 1
    assert alice_list[0]["id"] != bob_list[0]["id"]


def test_user_cannot_read_other_users_conversation(client, monkeypatch) -> None:
    class NoopIndexService:
        def rebuild_index(self, *args, **kwargs) -> str:
            return "noop"

    monkeypatch.setattr("app.api.workspace.IndexService", NoopIndexService)
    alice = _register(client, "alice")
    bob = _register(client, "bob")
    result = client.post(
        "/api/workspace/learning-notes/stream",
        json={"text": "Alice 学习 Redis。", "language": "zh-CN"},
        headers=_auth(alice),
    ).text
    conversation_id = result.split('"conversation_id":"')[1].split('"')[0]

    response = client.get(f"/api/conversations/{conversation_id}", headers=_auth(bob))

    assert response.status_code == 404
```

- [ ] **Step 2: Run conversation tests to verify they fail**

Run:

```sh
cd backend
uv run pytest tests/test_user_conversation_isolation.py -v
```

Expected: FAIL because old learning/interview repositories are still used.

- [ ] **Step 3: Implement ConversationRepository**

Create `backend/app/repositories/conversation_repository.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models


class ConversationRepository:
    def create(
        self,
        session: Session,
        *,
        user_id: int,
        kind: str,
        title: str,
        config_json: dict[str, object] | None = None,
        summary_json: dict[str, object] | None = None,
    ) -> models.Conversation:
        conversation = models.Conversation(
            user_id=user_id,
            kind=kind,
            title=title,
            config_json=config_json or {},
            summary_json=summary_json or {},
        )
        session.add(conversation)
        session.flush()
        return conversation

    def get(
        self, session: Session, *, user_id: int, conversation_id: str
    ) -> models.Conversation | None:
        return session.scalar(
            select(models.Conversation).where(
                models.Conversation.user_id == user_id,
                models.Conversation.id == conversation_id,
                models.Conversation.deleted_at.is_(None),
            )
        )

    def list_recent(self, session: Session, *, user_id: int, limit: int = 50) -> list[models.Conversation]:
        return list(
            session.scalars(
                select(models.Conversation)
                .where(
                    models.Conversation.user_id == user_id,
                    models.Conversation.deleted_at.is_(None),
                )
                .order_by(models.Conversation.updated_at.desc())
                .limit(limit)
            )
        )

    def add_message(
        self,
        session: Session,
        *,
        user_id: int,
        conversation_id: str,
        role: str,
        message_type: str,
        content: str,
        metadata_json: dict[str, object] | None = None,
    ) -> models.Message:
        message = models.Message(
            user_id=user_id,
            conversation_id=conversation_id,
            role=role,
            message_type=message_type,
            content=content,
            metadata_json=metadata_json or {},
        )
        session.add(message)
        conversation = self.get(session, user_id=user_id, conversation_id=conversation_id)
        if conversation is not None:
            conversation.updated_at = datetime.now(UTC)
        session.flush()
        return message

    def list_messages(
        self, session: Session, *, user_id: int, conversation_id: str
    ) -> list[models.Message]:
        return list(
            session.scalars(
                select(models.Message)
                .where(
                    models.Message.user_id == user_id,
                    models.Message.conversation_id == conversation_id,
                )
                .order_by(models.Message.created_at)
            )
        )

    def rename(
        self, session: Session, *, user_id: int, conversation_id: str, title: str
    ) -> models.Conversation | None:
        conversation = self.get(session, user_id=user_id, conversation_id=conversation_id)
        if conversation is None:
            return None
        conversation.title = title
        session.flush()
        return conversation

    def soft_delete(self, session: Session, *, user_id: int, conversation_id: str) -> bool:
        conversation = self.get(session, user_id=user_id, conversation_id=conversation_id)
        if conversation is None:
            return False
        conversation.deleted_at = datetime.now(UTC)
        session.flush()
        return True
```

- [ ] **Step 4: Refactor ConversationService to use repository**

Modify `backend/app/services/conversation_service.py` so public methods accept `user_id`:

```python
class ConversationService:
    def __init__(self, repository: ConversationRepository | None = None) -> None:
        self.repository = repository or ConversationRepository()

    def list_conversations(self, session: Session, *, user_id: int) -> list[ConversationHistoryItemResponse]:
        conversations = self.repository.list_recent(session, user_id=user_id)
        return [self._history_item(conversation) for conversation in conversations]
```

The `_history_item` method must build:

```python
href = f"/learn?session={conversation.id}" if conversation.kind == "learning" else f"/interview?session={conversation.id}"
last_message = str((conversation.summary_json or {}).get("last_message") or "")
```

For details, use `self.repository.list_messages(session, user_id=user_id, conversation_id=conversation.id)`.

- [ ] **Step 5: Update learning note persistence to write conversation/messages**

In `backend/app/api/workspace.py`, replace `_persist_learning_conversation` with:

```python
def _persist_learning_conversation(
    request: Request,
    payload: LearningNoteRequest,
    note: str,
    response: LearningNoteResponse,
    *,
    user_id: int,
) -> str:
    repository = ConversationRepository()
    with session_scope(request.app.state.session_factory) as session:
        conversation = None
        if payload.conversation_id:
            conversation = repository.get(
                session,
                user_id=user_id,
                conversation_id=payload.conversation_id,
            )
            if conversation is None:
                raise not_found("conversation_not_found", "Conversation not found.")
        if conversation is None:
            conversation = repository.create(
                session,
                user_id=user_id,
                kind="learning",
                title=response.summary.title or "学习记录",
                config_json={
                    "language": payload.language,
                    "provider": payload.provider or "",
                    "model": payload.model or "",
                },
                summary_json={"last_message": note[:160]},
            )
        repository.add_message(
            session,
            user_id=user_id,
            conversation_id=conversation.id,
            role="user",
            message_type="learning_note",
            content=note,
        )
        repository.add_message(
            session,
            user_id=user_id,
            conversation_id=conversation.id,
            role="assistant",
            message_type="learning_summary",
            content=_learning_assistant_message(response),
            metadata_json={
                "source_artifact_id": response.source.artifact_id,
                "source_relative_path": response.source.relative_path,
                "artifact_id": response.artifact.id,
                "artifact_path": response.artifact.relative_path,
            },
        )
        conversation.summary_json = {"last_message": note[:160]}
        return conversation.id
```

- [ ] **Step 6: Update conversation API to pass user_id**

In `backend/app/api/conversations.py`, all calls become:

```python
ConversationService().list_conversations(session, user_id=scope.user_id)
ConversationService().get_conversation(session, user_id=scope.user_id, conversation_id=conversation_id)
ConversationService().rename_conversation(session, user_id=scope.user_id, conversation_id=conversation_id, title=payload.title)
ConversationService().delete_conversation(session, user_id=scope.user_id, conversation_id=conversation_id)
```

- [ ] **Step 7: Run conversation tests**

Run:

```sh
cd backend
uv run pytest tests/test_user_conversation_isolation.py tests/test_conversations.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```sh
git add backend/app/repositories/conversation_repository.py backend/app/services/conversation_service.py backend/app/api/workspace.py backend/app/api/conversations.py backend/tests/test_user_conversation_isolation.py backend/tests/test_conversations.py
git commit -m "Unify conversation storage with user scoping"
```

---

### Task 7: 用户级面试流程和报告 artifact

**Files:**
- Modify: `backend/app/services/interview_service.py`
- Modify: `backend/app/services/interview_completion_service.py`
- Modify: `backend/app/services/interview_artifact_service.py`
- Modify: `backend/app/api/interviews.py`
- Modify: `backend/app/api/reports.py`
- Test: `backend/tests/test_interviews.py`
- Test: `backend/tests/test_reports.py`

- [ ] **Step 1: Add failing interview isolation test**

Append to `backend/tests/test_interviews.py`:

```python
def _register(client, username: str) -> str:
    return client.post(
        "/api/auth/register",
        json={"username": username, "password": "correct horse battery staple"},
    ).json()["access_token"]


def test_user_cannot_read_other_users_interview(client) -> None:
    alice = _register(client, "alice")
    bob = _register(client, "bob")
    created = client.post(
        "/api/interview-sessions",
        json={
            "target_company": "",
            "target_role": "",
            "job_description": "",
            "extra_prompt": "Redis",
            "language": "zh-CN",
            "mode": "comprehensive",
            "chat_model_provider": "qwen",
            "chat_model": "qwen3.7-plus",
            "target_rounds": 1,
        },
        headers={"Authorization": f"Bearer {alice}"},
    )
    session_id = created.json()["session"]["id"]

    response = client.get(
        f"/api/interview-sessions/{session_id}",
        headers={"Authorization": f"Bearer {bob}"},
    )

    assert response.status_code == 404
```

- [ ] **Step 2: Run interview isolation test to verify it fails**

Run:

```sh
cd backend
uv run pytest tests/test_interviews.py::test_user_cannot_read_other_users_interview -v
```

Expected: FAIL because interview service still uses old tables or global lookup.

- [ ] **Step 3: Store interview sessions in conversations**

Refactor `InterviewService.create_session` to create a conversation:

```python
conversation = ConversationRepository().create(
    session,
    user_id=scope.user_id,
    kind="interview",
    title=config_in.extra_prompt[:80] or "模拟面试",
    config_json=config_in.model_dump(mode="json"),
    summary_json={"current_round": 1, "last_message": ""},
)
```

Store the first question as a message:

```python
ConversationRepository().add_message(
    session,
    user_id=scope.user_id,
    conversation_id=conversation.id,
    role="assistant",
    message_type="interview_question",
    content=question.question,
    metadata_json={
        "round_index": 1,
        "retrieved_context_refs": question.retrieved_context_refs,
    },
)
```

Build `InterviewSessionCreatedResponse` from conversation/message data. Keep response shape stable for the frontend:

```python
session_response = {
    "id": conversation.id,
    "config_id": conversation.id,
    "status": conversation.status,
    "current_round": int(conversation.summary_json.get("current_round") or 1),
    "started_at": conversation.created_at,
    "ended_at": None,
    "report_path": conversation.summary_json.get("report_path"),
}
```

- [ ] **Step 4: Store answers and feedback in messages**

In answer submission, append:

```python
repository.add_message(
    session,
    user_id=scope.user_id,
    conversation_id=conversation.id,
    role="user",
    message_type="interview_answer",
    content=answer,
    metadata_json={"round_index": current_round},
)
repository.add_message(
    session,
    user_id=scope.user_id,
    conversation_id=conversation.id,
    role="assistant",
    message_type="interview_feedback",
    content=feedback.feedback,
    metadata_json=feedback.model_dump(mode="json"),
)
```

Follow-up question, follow-up answer and follow-up feedback use message types:

```text
interview_follow_up_question
interview_follow_up_answer
interview_follow_up_feedback
```

- [ ] **Step 5: Build interview detail from messages**

Refactor `InterviewService.get_session_detail` to query:

```python
conversation = ConversationRepository().get(session, user_id=scope.user_id, conversation_id=session_id)
messages = ConversationRepository().list_messages(session, user_id=scope.user_id, conversation_id=session_id)
```

Group messages by `metadata_json.round_index` to produce the existing `turns` response.

- [ ] **Step 6: Store reports as artifacts**

In `InterviewCompletionService.finish_session`, write report markdown through `InterviewArtifactService` using the scoped workspace. Upsert artifact with:

```python
kind="report"
relative_path=f"reports/{conversation.id}.md"
metadata_json={
    "conversation_id": conversation.id,
    "summary": report.summary,
    "weaknesses": report.weaknesses,
}
status_json={"processing_status": "completed", "index_status": "skipped"}
```

Update `conversation.summary_json`:

```python
conversation.summary_json = {
    **(conversation.summary_json or {}),
    "report_path": report_path,
    "summary": report.summary,
    "weaknesses": report.weaknesses,
}
conversation.status = "completed"
```

- [ ] **Step 7: Update interview API signatures**

Every interview endpoint must pass `scope`:

```python
_interview_service(request, scope).create_session(session, config_in)
_interview_service(request, scope).get_session_detail(session, session_id)
```

Construct `_interview_service` with scoped services:

```python
def _interview_service(request: Request, scope: UserScope) -> InterviewService:
    workspace_service = WorkspaceService(scope.workspace_root)
    workspace_service.initialize()
    return InterviewService(
        artifact_service=ArtifactService(workspace_service),
        workspace_service=workspace_service,
        user_id=scope.user_id,
    )
```

- [ ] **Step 8: Run interview and report tests**

Run:

```sh
cd backend
uv run pytest tests/test_interviews.py tests/test_reports.py tests/test_conversations.py -v
```

Expected: PASS.

- [ ] **Step 9: Commit**

```sh
git add backend/app/services/interview_service.py backend/app/services/interview_completion_service.py backend/app/services/interview_artifact_service.py backend/app/api/interviews.py backend/app/api/reports.py backend/tests/test_interviews.py backend/tests/test_reports.py
git commit -m "Store interviews in scoped conversations"
```

---

### Task 8: 用户级 Qdrant 索引和检索

**Files:**
- Modify: `backend/app/services/index_service.py`
- Modify: `backend/app/services/workspace_vector_store.py`
- Modify: `backend/app/services/workspace_retrieval_service.py`
- Modify: `backend/app/services/context_assembler.py`
- Modify: `backend/app/api/workspace.py`
- Modify: `backend/app/api/interviews.py`
- Test: `backend/tests/test_user_index_isolation.py`

- [ ] **Step 1: Write failing Qdrant isolation tests**

Create `backend/tests/test_user_index_isolation.py`:

```python
def _register(client, username: str) -> str:
    return client.post(
        "/api/auth/register",
        json={"username": username, "password": "correct horse battery staple"},
    ).json()["access_token"]


def test_index_rebuild_uses_user_collection_prefix(client) -> None:
    token = _register(client, "alice")

    response = client.post(
        "/api/workspace/rebuild-index",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["collection"].startswith("auto_reign_user_1__")


def test_two_users_get_different_active_collections(client) -> None:
    alice = _register(client, "alice")
    bob = _register(client, "bob")

    first = client.post("/api/workspace/rebuild-index", headers={"Authorization": f"Bearer {alice}"})
    second = client.post("/api/workspace/rebuild-index", headers={"Authorization": f"Bearer {bob}"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["collection"].startswith("auto_reign_user_1__")
    assert second.json()["collection"].startswith("auto_reign_user_2__")
```

- [ ] **Step 2: Run Qdrant isolation tests to verify they fail**

Run:

```sh
cd backend
uv run pytest tests/test_user_index_isolation.py -v
```

Expected: FAIL because collection names still use global `settings.qdrant_collection`.

- [ ] **Step 3: Refactor IndexService signatures**

Modify `IndexService.rebuild_index`:

```python
def rebuild_index(
    self,
    session_factory: Callable[[], Session],
    workspace: WorkspaceService,
    artifact_repository: ArtifactRepository,
    *,
    user_id: int,
    collection_prefix: str,
) -> str:
```

Inside `_rebuild_index_unlocked`, create:

```python
new_collection = f"{collection_prefix}__{time.time_ns()}"
```

List only user artifacts:

```python
artifacts = artifact_repository.list(session, user_id=user_id)
```

Read and update active collection through user settings:

```python
user = session.get(models.User, user_id)
settings_json = dict(user.settings_json or {})
old_collection = str(settings_json.get("active_collection") or "")
settings_json["active_collection"] = new_collection
user.settings_json = settings_json
```

- [ ] **Step 4: Refactor ensure_current and sweep**

`ensure_current` accepts `user_id` and `collection_prefix`. It checks only current user artifacts and current user active collection.

`sweep_orphan_collections` accepts:

```python
def sweep_orphan_collections(self, session_factory, *, user_id: int, collection_prefix: str) -> None:
```

It must only delete collections where:

```python
collection_name.startswith(f"{collection_prefix}__")
```

and `collection_name != active_collection`.

- [ ] **Step 5: Update delete_artifact_chunks calls**

In workspace delete, use current user active collection:

```python
active_collection = str((current_user.settings_json or {}).get("active_collection") or "")
```

Call:

```python
index_service.vector_store.delete_artifact_chunks(active_collection, artifact_id)
```

The artifact lookup already includes `user_id`, so cross-user artifact delete is impossible.

- [ ] **Step 6: Update retrieval services**

`WorkspaceRetrievalService` and `ContextAssembler` must receive `user_id` or `UserScope`, and read active collection from current user's `settings_json`.

Use:

```python
active_collection = str((user.settings_json or {}).get("active_collection") or "")
```

Return empty retrieval results if active collection is empty.

- [ ] **Step 7: Run Qdrant and retrieval tests**

Run:

```sh
cd backend
uv run pytest tests/test_user_index_isolation.py tests/test_workspace_vector_store.py tests/test_context_assembler.py tests/test_retrieval_postprocessor.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```sh
git add backend/app/services/index_service.py backend/app/services/workspace_vector_store.py backend/app/services/workspace_retrieval_service.py backend/app/services/context_assembler.py backend/app/api/workspace.py backend/app/api/interviews.py backend/tests/test_user_index_isolation.py
git commit -m "Scope vector indexing by user"
```

---

### Task 9: 前端登录、注册、鉴权请求和登出

**Files:**
- Create: `frontend/src/lib/auth.ts`
- Modify: `frontend/src/lib/api.ts`
- Create: `frontend/src/components/AuthGuard.tsx`
- Modify: `frontend/src/app/layout.tsx`
- Create: `frontend/src/app/login/page.tsx`
- Create: `frontend/src/app/register/page.tsx`
- Modify: `frontend/src/components/AppShell.tsx`
- Modify: `frontend/src/i18n/locales/zh-CN/common.json`
- Modify: `frontend/src/i18n/locales/en/common.json`
- Test: `frontend/src/lib/auth.test.ts`
- Modify: `frontend/src/lib/api.test.ts`
- Test: `frontend/src/components/__tests__/AuthGuard.test.tsx`
- Test: `frontend/src/app/login/page.test.tsx`
- Test: `frontend/src/app/register/page.test.tsx`

- [ ] **Step 1: Write failing auth storage tests**

Create `frontend/src/lib/auth.test.ts`:

```typescript
import { clearAuthToken, getAuthToken, setAuthToken } from "./auth";

describe("auth token storage", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("stores and reads token", () => {
    setAuthToken("token-1");

    expect(getAuthToken()).toBe("token-1");
  });

  it("clears token", () => {
    setAuthToken("token-1");
    clearAuthToken();

    expect(getAuthToken()).toBeNull();
  });
});
```

- [ ] **Step 2: Run auth storage test to verify it fails**

Run:

```sh
cd frontend
npm test -- src/lib/auth.test.ts
```

Expected: FAIL because `src/lib/auth.ts` does not exist.

- [ ] **Step 3: Implement auth storage**

Create `frontend/src/lib/auth.ts`:

```typescript
import type { User } from "./types";

const TOKEN_KEY = "auto-reign-auth-token";

export type AuthUser = User;

export function setAuthToken(token: string) {
  window.localStorage.setItem(TOKEN_KEY, token);
}

export function getAuthToken(): string | null {
  if (typeof window === "undefined") {
    return null;
  }
  const token = window.localStorage.getItem(TOKEN_KEY);
  if (!token || isTokenExpired(token)) {
    clearAuthToken();
    return null;
  }
  return token;
}

export function clearAuthToken() {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.removeItem(TOKEN_KEY);
}

export function isAuthenticated() {
  return Boolean(getAuthToken());
}

function isTokenExpired(token: string) {
  try {
    const payload = JSON.parse(window.atob(token.split(".")[1]));
    if (typeof payload.exp !== "number") {
      return false;
    }
    return Date.now() >= payload.exp * 1000;
  } catch {
    return false;
  }
}
```

Add to `frontend/src/lib/types.ts`:

```typescript
export interface User {
  id: number;
  username: string;
  display_name: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface AuthTokenResponse {
  access_token: string;
  token_type: "bearer";
  user: User;
}
```

- [ ] **Step 4: Update API client to add Authorization**

Modify `frontend/src/lib/api.ts`:

```typescript
import { clearAuthToken, getAuthToken } from "./auth";
```

Inside `apiJson`, before fetch:

```typescript
const token = getAuthToken();
if (token) {
  headers.set("Authorization", `Bearer ${token}`);
}
```

For 401 handling:

```typescript
if (response.status === 401) {
  clearAuthToken();
  if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
    window.location.href = "/login";
  }
}
```

Apply the same `Authorization` header logic to `uploadMaterials` and `apiStream`.

- [ ] **Step 5: Add auth API functions**

Append to `frontend/src/lib/api.ts`:

```typescript
export function registerUser(username: string, password: string): Promise<AuthTokenResponse> {
  return apiJson<AuthTokenResponse>("/api/auth/register", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
}

export function loginUser(username: string, password: string): Promise<AuthTokenResponse> {
  return apiJson<AuthTokenResponse>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
}

export function getCurrentUser(): Promise<User> {
  return apiJson<User>("/api/auth/me");
}
```

- [ ] **Step 6: Implement AuthGuard**

Create `frontend/src/components/AuthGuard.tsx`:

```tsx
"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { isAuthenticated } from "@/lib/auth";

const PUBLIC_PATHS = new Set(["/login", "/register"]);

export function AuthGuard({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (PUBLIC_PATHS.has(pathname)) {
      setReady(true);
      return;
    }
    if (!isAuthenticated()) {
      router.replace(`/login?redirect=${encodeURIComponent(pathname)}`);
      return;
    }
    setReady(true);
  }, [pathname, router]);

  if (!ready) {
    return null;
  }
  return <>{children}</>;
}
```

- [ ] **Step 7: Use AuthGuard in layout and skip shell on auth pages**

Modify `frontend/src/app/layout.tsx`:

```tsx
import { AuthGuard } from "@/components/AuthGuard";
```

Wrap:

```tsx
<I18nProvider>
  <AuthGuard>
    <AppShell>{children}</AppShell>
  </AuthGuard>
</I18nProvider>
```

Modify `AppShell` so if `currentPath` is `/login` or `/register`, it returns:

```tsx
return <>{children}</>;
```

- [ ] **Step 8: Implement login and register pages**

Create `frontend/src/app/login/page.tsx`:

```tsx
"use client";

import { FormEvent, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";

import { loginUser } from "@/lib/api";
import { setAuthToken } from "@/lib/auth";

export default function LoginPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    try {
      const response = await loginUser(username, password);
      setAuthToken(response.access_token);
      router.replace(searchParams.get("redirect") || "/");
    } catch {
      setError("用户名或密码不正确。");
    }
  }

  return (
    <main className="auth-page">
      <form className="auth-panel" onSubmit={submit}>
        <h1>登录 Auto Reign</h1>
        <label>
          用户名
          <input value={username} onChange={(event) => setUsername(event.target.value)} />
        </label>
        <label>
          密码
          <input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>
        {error ? <p role="alert" className="form-error">{error}</p> : null}
        <button className="button button-primary" type="submit">登录</button>
        <Link href="/register">创建本地账号</Link>
      </form>
    </main>
  );
}
```

Create `frontend/src/app/register/page.tsx` with the same structure, calling `registerUser(username, password)` and requiring password length at least 12 before submit.

- [ ] **Step 9: Add AppShell user menu logout**

In `frontend/src/components/AppShell.tsx`, import:

```tsx
import { clearAuthToken } from "@/lib/auth";
```

Add logout handler:

```tsx
function handleLogout() {
  clearAuthToken();
  router.replace("/login");
}
```

Replace the static local user action with a button:

```tsx
<button className="sidebar-user" type="button" onClick={handleLogout}>
  <UserCircle aria-hidden="true" size={18} />
  <span>{t("user.logout")}</span>
</button>
```

- [ ] **Step 10: Run frontend tests**

Run:

```sh
cd frontend
npm test
```

Expected: PASS.

- [ ] **Step 11: Run frontend build**

Run:

```sh
cd frontend
npm run build
```

Expected: PASS.

- [ ] **Step 12: Commit**

```sh
git add frontend/src/lib/auth.ts frontend/src/lib/types.ts frontend/src/lib/api.ts frontend/src/components/AuthGuard.tsx frontend/src/app/layout.tsx frontend/src/app/login/page.tsx frontend/src/app/register/page.tsx frontend/src/components/AppShell.tsx frontend/src/i18n/locales/zh-CN/common.json frontend/src/i18n/locales/en/common.json frontend/src/lib/auth.test.ts frontend/src/lib/api.test.ts frontend/src/components/__tests__/AuthGuard.test.tsx frontend/src/app/login/page.test.tsx frontend/src/app/register/page.test.tsx
git commit -m "Add frontend local authentication flow"
```

---

### Task 10: 文档、重置提示和全量验证

**Files:**
- Modify: `README.md`
- Modify: `docs/workbench-architecture.md`
- Modify: `docs/knowledge-data-flow.md`
- Modify: `scripts/reset_all_data.py`
- Modify: `backend/scripts/reset_data.py`
- Test: existing backend and frontend suites

- [ ] **Step 1: Update README behavior**

In `README.md`, replace the single-user limitation section with:

```markdown
当前版本默认必须登录。用户通过本地用户名和密码注册账号；密码只保存哈希，不保存明文。每个账号拥有独立的本地工作区、MySQL 投影和 Qdrant collection。用户文件位于 `DATA_DIR/users/{user_id}/workspace`。

旧版单用户 `DATA_DIR/workspace` 数据不会自动迁移或自动删除。切换到多账号版本前，如需清理本地数据，请显式运行：

```sh
./reset-data.sh
```
```

- [ ] **Step 2: Update architecture doc**

In `docs/workbench-architecture.md`, change product positioning from single-user to local multi-account and add:

```markdown
多账号隔离边界是 `user_id`。后端只从 JWT 当前用户派生工作区路径、数据库查询条件和 Qdrant collection 前缀；前端不能通过传入 `user_id` 切换数据域。
```

Update workspace path section to:

```text
DATA_DIR/users/{user_id}/workspace/
```

- [ ] **Step 3: Update knowledge data flow**

In `docs/knowledge-data-flow.md`, change every `DATA_DIR/workspace/...` path to:

```text
DATA_DIR/users/{user_id}/workspace/...
```

Add:

```markdown
资料入库、投影重建、索引重建和检索都只处理当前 JWT 用户对应的工作区。Qdrant active collection 保存在当前用户的 `settings_json.active_collection`。
```

- [ ] **Step 4: Update reset scripts to remove user directories explicitly**

In `scripts/reset_all_data.py` and `backend/scripts/reset_data.py`, ensure explicit reset removes:

```text
DATA_DIR/users
DATA_DIR/workspace
```

The scripts must print that they are deleting local user data. Do not add any startup-time automatic deletion.

- [ ] **Step 5: Run backend tests**

Run:

```sh
cd backend
uv run pytest -v
uv run ruff check .
```

Expected: PASS.

- [ ] **Step 6: Run frontend tests and build**

Run:

```sh
cd frontend
npm test
npm run build
```

Expected: PASS.

- [ ] **Step 7: Validate compose config**

Run:

```sh
cd ..
docker compose config
```

Expected: PASS and prints the normalized compose configuration.

- [ ] **Step 8: Manual smoke with local stack**

Run:

```sh
./start.sh --restart
```

Open the frontend URL printed by `start.sh`. Verify:

```text
1. 未登录访问 / 会跳转到 /login。
2. 注册 alice 后进入工作台。
3. 新建一条学习记录。
4. 登出。
5. 注册 bob 后资料库为空。
6. bob 无法通过 alice 的 artifact URL 查看 alice 资料。
7. ./start.sh --stop 能正常停止服务。
```

- [ ] **Step 9: Commit**

```sh
git add README.md docs/workbench-architecture.md docs/knowledge-data-flow.md scripts/reset_all_data.py backend/scripts/reset_data.py
git commit -m "Document local multi-user isolation"
```

---

## Self-Review Checklist

- Spec coverage:
  - JWT、本地注册、密码哈希、改密和 CLI 重置由 Tasks 1 和 3 覆盖。
  - 四表模型由 Task 2 覆盖。
  - `UserScope`、默认登录保护和用户目录由 Task 4 覆盖。
  - 文件和 artifact 隔离由 Task 5 覆盖。
  - conversations/messages 统一模型由 Task 6 覆盖。
  - 面试和报告统一到 conversation/artifact 由 Task 7 覆盖。
  - Qdrant 用户级 collection 由 Task 8 覆盖。
  - 前端登录态和 Bearer token 由 Task 9 覆盖。
  - README 和长期中文文档由 Task 10 覆盖。
- Placeholder scan:
  - 已搜索常见未完成标记；计划正文没有待补步骤。
- Type consistency:
  - 后端用户字段统一为 `username`、`display_name`、`token_version`、`settings_json`。
  - 隔离上下文统一为 `UserScope.user_id`、`workspace_root`、`qdrant_prefix`。
  - 会话表统一为 `conversations` 和 `messages`，API 对外继续使用 conversation/session id。
