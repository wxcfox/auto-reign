from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.types import TypeDecorator
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


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


class InterviewConfig(Base):
    __tablename__ = "interview_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    target_company: Mapped[str] = mapped_column(String(255))
    target_role: Mapped[str] = mapped_column(String(255))
    job_description: Mapped[str] = mapped_column(Text, default="")
    extra_prompt: Mapped[str] = mapped_column(Text, default="")
    language: Mapped[str] = mapped_column(String(16), default="en")
    mode: Mapped[str] = mapped_column(String(64))
    chat_model_provider: Mapped[str] = mapped_column(String(64))
    chat_model: Mapped[str] = mapped_column(String(120))
    target_rounds: Mapped[int] = mapped_column(Integer, default=3)
    is_last_used: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, onupdate=_now)

    sessions: Mapped[list["InterviewSession"]] = relationship(back_populates="config")


class InterviewSession(Base):
    __tablename__ = "interview_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    config_id: Mapped[str] = mapped_column(ForeignKey("interview_configs.id"))
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active")
    current_round: Mapped[int] = mapped_column(Integer, default=1)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    report_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    config: Mapped[InterviewConfig] = relationship(back_populates="sessions")
    turns: Mapped[list["InterviewTurn"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    reports: Mapped[list["Report"]] = relationship(back_populates="session")


class InterviewTurn(Base):
    __tablename__ = "interview_turns"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(ForeignKey("interview_sessions.id", ondelete="CASCADE"))
    round_index: Mapped[int] = mapped_column(Integer)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    missing_points: Mapped[list[str]] = mapped_column(JSON, default=list)
    follow_up_question: Mapped[str | None] = mapped_column(Text, nullable=True)
    follow_up_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    follow_up_feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    follow_up_missing_points: Mapped[list[str]] = mapped_column(JSON, default=list)
    follow_up_weaknesses: Mapped[list[str]] = mapped_column(JSON, default=list)
    follow_up_review_suggestions: Mapped[list[str]] = mapped_column(JSON, default=list)
    follow_up_better_answer: Mapped[str] = mapped_column(Text, default="")
    follow_up_mastery_change: Mapped[str] = mapped_column(String(64), default="unchanged")
    follow_up_should_write_weakness: Mapped[bool] = mapped_column(Boolean, default=False)
    follow_up_should_write_high_frequency: Mapped[bool] = mapped_column(Boolean, default=False)
    follow_up_tested_points: Mapped[list[str]] = mapped_column(JSON, default=list)
    weaknesses: Mapped[list[str]] = mapped_column(JSON, default=list)
    review_suggestions: Mapped[list[str]] = mapped_column(JSON, default=list)
    better_answer: Mapped[str] = mapped_column(Text, default="")
    mastery_change: Mapped[str] = mapped_column(String(64), default="unchanged")
    should_write_weakness: Mapped[bool] = mapped_column(Boolean, default=False)
    should_write_high_frequency: Mapped[bool] = mapped_column(Boolean, default=False)
    tested_points: Mapped[list[str]] = mapped_column(JSON, default=list)
    retrieved_context_refs: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now)

    session: Mapped[InterviewSession] = relationship(back_populates="turns")


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(ForeignKey("interview_sessions.id"))
    report_path: Mapped[str] = mapped_column(String(1024))
    summary: Mapped[str] = mapped_column(Text)
    weaknesses: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now)

    session: Mapped[InterviewSession] = relationship(back_populates="reports")


class LearningSession(Base):
    __tablename__ = "learning_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(255), default="学习记录")
    language: Mapped[str] = mapped_column(String(16), default="zh-CN")
    chat_model_provider: Mapped[str] = mapped_column(String(64), default="")
    chat_model: Mapped[str] = mapped_column(String(120), default="")
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, onupdate=_now)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    messages: Mapped[list["LearningMessage"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
    )


class LearningMessage(Base):
    __tablename__ = "learning_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("learning_sessions.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(String(16))
    message_type: Mapped[str] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text)
    artifact_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    artifact_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    message_metadata: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now)

    session: Mapped[LearningSession] = relationship(back_populates="messages")


class WorkspaceSettings(Base):
    __tablename__ = "workspace_settings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default="default")
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    language: Mapped[str] = mapped_column(String(16), default="zh-CN")
    embedding_config: Mapped[str] = mapped_column(String(255), default="")
    active_collection: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, onupdate=_now)


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    relative_path: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), default="")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    source_refs: Mapped[list[str]] = mapped_column(JSON, default=list)
    evidence_refs: Mapped[list[str]] = mapped_column(JSON, default=list)
    processing_status: Mapped[str] = mapped_column(String(32), default="completed")
    index_status: Mapped[str] = mapped_column(String(32), default="pending")
    language: Mapped[str] = mapped_column(String(16), default="zh-CN")
    source_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    media_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    origin: Mapped[str] = mapped_column(String(16), default="llm")
    edited_by: Mapped[str] = mapped_column(String(16), default="system")
    recovery_required: Mapped[bool] = mapped_column(Boolean, default=False)
    recovery_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    uploaded_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, onupdate=_now)


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
