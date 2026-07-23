from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    JSON,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.mysql import LONGBLOB, LONGTEXT
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from app.core.limits import (
    MAX_DISPLAY_NAME_LENGTH,
    MAX_RESOURCE_NAME_LENGTH,
    MAX_USERNAME_LENGTH,
)


BIGINT_TYPE = BigInteger().with_variant(Integer(), "sqlite")
LONGBLOB_TYPE = LONGBLOB().with_variant(LargeBinary(), "sqlite")
LONGTEXT_TYPE = LONGTEXT().with_variant(Text(), "sqlite")


def _uuid() -> str:
    return str(uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    impl = DateTime
    cache_ok = True

    def __init__(self) -> None:
        super().__init__(timezone=True)

    def process_bind_param(self, value: datetime | None, _dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, _dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(
        String(MAX_USERNAME_LENGTH),
        unique=True,
        index=True,
    )
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(
        String(MAX_DISPLAY_NAME_LENGTH),
        default="",
    )
    role: Mapped[str] = mapped_column(String(16), default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    token_version: Mapped[int] = mapped_column(Integer, default=1)
    settings_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    seed_initialized_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    credential_bootstrap_status: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, onupdate=_now)

class Resource(Base):
    __tablename__ = "resources"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "resource_type", "name", name="uq_resources_owner_type_name"
        ),
        UniqueConstraint("id", "user_id", name="uq_resources_id_owner"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    resource_type: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(MAX_RESOURCE_NAME_LENGTH))
    config_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, onupdate=_now)


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (
        ForeignKeyConstraint(
            ["collection_id", "user_id"],
            ["resources.id", "resources.user_id"],
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    collection_id: Mapped[str] = mapped_column(String(36), index=True)
    name: Mapped[str] = mapped_column(String(255))
    source_object_key: Mapped[str] = mapped_column(String(1024))
    parsed_object_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    mime_type: Mapped[str] = mapped_column(String(160))
    size_bytes: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="uploaded")
    index_generation: Mapped[int] = mapped_column(Integer, default=1)
    retriever_type: Mapped[str] = mapped_column(
        String(32), default="elasticsearch"
    )
    processing_attempt_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True
    )
    cleanup_attempt_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, onupdate=_now)
    indexed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(BIGINT_TYPE, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    agent_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("resources.id"), index=True, nullable=True
    )
    name: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(24), default="PENDING", index=True)
    model_override_json: Mapped[dict[str, object] | None] = mapped_column(
        JSON, nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, onupdate=_now)


class Subtask(Base):
    __tablename__ = "subtasks"

    id: Mapped[int] = mapped_column(BIGINT_TYPE, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    task_id: Mapped[int] = mapped_column(
        BIGINT_TYPE, ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))
    message_id: Mapped[int] = mapped_column(BIGINT_TYPE, index=True)
    parent_id: Mapped[int | None] = mapped_column(BIGINT_TYPE, nullable=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    prompt: Mapped[str] = mapped_column(LONGTEXT_TYPE, default="")
    status: Mapped[str] = mapped_column(String(24), default="PENDING", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, onupdate=_now)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class SubtaskContext(Base):
    __tablename__ = "subtask_contexts"

    id: Mapped[int] = mapped_column(BIGINT_TYPE, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    subtask_id: Mapped[int] = mapped_column(BIGINT_TYPE, default=0, index=True)
    context_type: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(24), default="pending")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    binary_data: Mapped[bytes | None] = mapped_column(LONGBLOB_TYPE, nullable=True)
    image_base64: Mapped[str | None] = mapped_column(LONGTEXT_TYPE, nullable=True)
    extracted_text: Mapped[str | None] = mapped_column(LONGTEXT_TYPE, nullable=True)
    text_length: Mapped[int] = mapped_column(Integer, default=0)
    mime_type: Mapped[str | None] = mapped_column(String(160), nullable=True)
    file_extension: Mapped[str | None] = mapped_column(String(32), nullable=True)
    file_size: Mapped[int | None] = mapped_column(BIGINT_TYPE, nullable=True)
    type_data: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, onupdate=_now)
