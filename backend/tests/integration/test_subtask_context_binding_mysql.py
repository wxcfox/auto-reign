from __future__ import annotations

import base64
import os

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import Session, sessionmaker

from app.db import models
from app.repositories.subtask_context_repository import (
    SubtaskContextRepository,
    SubtaskContextRepositoryError,
)


def _database_identity(url: URL) -> tuple[str | None, int, str | None]:
    host = (url.host or "").casefold().rstrip(".")
    if host in {"localhost", "127.0.0.1", "::1"}:
        host = "loopback"
    return host, url.port or 3306, url.database.casefold() if url.database else None


def _mysql_url() -> URL:
    if os.environ.get("RUN_MYSQL_INTEGRATION") != "1":
        pytest.skip("requires RUN_MYSQL_INTEGRATION=1")
    explicit = os.environ.get("MYSQL_SUBTASK_CONTEXT_DATABASE_URL")
    if not explicit:
        pytest.fail(
            "RUN_MYSQL_INTEGRATION=1 requires MYSQL_SUBTASK_CONTEXT_DATABASE_URL"
        )
    try:
        url = make_url(explicit)
    except ArgumentError as error:
        pytest.fail(f"MYSQL_SUBTASK_CONTEXT_DATABASE_URL is invalid: {error}")
    if not url.drivername.startswith("mysql") or not url.database:
        pytest.fail("MYSQL_SUBTASK_CONTEXT_DATABASE_URL must name a MySQL database")
    if not url.database.casefold().endswith("_test"):
        pytest.fail("MYSQL_SUBTASK_CONTEXT_DATABASE_URL database name must end with _test")
    if url.database.casefold() in {"mysql", "sys", "information_schema", "performance_schema"}:
        pytest.fail("MYSQL_SUBTASK_CONTEXT_DATABASE_URL must not name a system database")
    configured = os.environ.get("DATABASE_URL")
    if configured:
        try:
            if _database_identity(url) == _database_identity(make_url(configured)):
                pytest.fail("subtask context database must differ from DATABASE_URL")
        except ArgumentError as error:
            pytest.fail(
                "cannot prove subtask context database is disposable because "
                "DATABASE_URL is invalid"
            )
            raise AssertionError from error
    return url


def test_integration_flag_requires_explicit_subtask_context_url(monkeypatch) -> None:
    monkeypatch.setenv("RUN_MYSQL_INTEGRATION", "1")
    monkeypatch.delenv("MYSQL_SUBTASK_CONTEXT_DATABASE_URL", raising=False)

    with pytest.raises(pytest.fail.Exception, match="requires"):
        _mysql_url()


def test_mysql_persists_and_atomically_binds_subtask_contexts() -> None:
    engine = create_engine(_mysql_url())
    try:
        models.Base.metadata.drop_all(engine)
        models.Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        _exercise_mysql_binding(factory)
    finally:
        try:
            models.Base.metadata.drop_all(engine)
        finally:
            engine.dispose()


def _exercise_mysql_binding(factory: sessionmaker[Session]) -> None:
    repository = SubtaskContextRepository()
    source = b"\x00binary\xff"
    large_text = "上下文" * 50_000
    with factory.begin() as session:
        user = models.User(
            username="context-integration",
            password_hash="unused",
            display_name="Context Integration",
            role="user",
            is_active=True,
            token_version=1,
            settings_json={},
        )
        session.add(user)
        session.flush()
        task = models.Task(user_id=user.id, name="Task", status="PENDING")
        session.add(task)
        session.flush()
        subtask = models.Subtask(
            user_id=user.id,
            task_id=task.id,
            role="USER",
            message_id=1,
            prompt="prompt",
            status="COMPLETED",
            progress=100,
        )
        session.add(subtask)
        session.flush()
        first = repository.create_draft(
            session,
            user_id=user.id,
            context_type="attachment",
            name="binary.bin",
            status="ready",
            binary_data=source,
            image_base64=base64.b64encode(source).decode("ascii"),
            extracted_text=large_text,
            mime_type="image/png",
            file_extension=".png",
            file_size=len(source),
        )
        second = repository.create_draft(
            session,
            user_id=user.id,
            context_type="selected_documents",
            name="Selected documents",
            status="ready",
            type_data={"knowledge_id": "k", "document_ids": ["d2", "d1"]},
        )
        user_id, subtask_id = user.id, subtask.id

    with factory.begin() as session:
        bound = repository.bind_drafts(
            session,
            user_id=user_id,
            context_ids=[second.id, first.id],
            subtask_id=subtask_id,
        )
        assert [row.id for row in bound] == [second.id, first.id]

    with factory() as session:
        binary = session.get(models.SubtaskContext, first.id)
        assert binary is not None
        assert binary.binary_data == source
        assert binary.image_base64 == base64.b64encode(source).decode("ascii")
        assert binary.extracted_text == large_text
        assert binary.subtask_id == subtask_id
        rows = repository.list_for_subtasks(
            session,
            user_id=user_id,
            subtask_ids=[subtask_id],
        )
        assert [row.id for row in rows] == [first.id, second.id]

    with factory() as session:
        draft = repository.create_draft(
            session,
            user_id=user_id,
            context_type="attachment",
            name="still-draft.txt",
            status="ready",
        )
        session.commit()
        draft_id = draft.id
        with pytest.raises(SubtaskContextRepositoryError, match="context_not_ready"):
            repository.bind_drafts(
                session,
                user_id=user_id,
                context_ids=[draft.id, 999_999],
                subtask_id=subtask_id,
            )
        session.rollback()
    with factory() as session:
        assert session.scalar(
            select(models.SubtaskContext.subtask_id).where(
                models.SubtaskContext.id == draft_id
            )
        ) == 0

    with factory.begin() as session:
        rollback_first = repository.create_draft(
            session,
            user_id=user_id,
            context_type="attachment",
            name="rollback-first.txt",
            status="ready",
        )
        rollback_second = repository.create_draft(
            session,
            user_id=user_id,
            context_type="attachment",
            name="rollback-second.txt",
            status="ready",
        )
    with pytest.raises(RuntimeError, match="force transaction rollback"):
        with factory.begin() as session:
            bound = repository.bind_drafts(
                session,
                user_id=user_id,
                context_ids=[rollback_first.id, rollback_second.id],
                subtask_id=subtask_id,
            )
            assert [row.subtask_id for row in bound] == [subtask_id, subtask_id]
            raise RuntimeError("force transaction rollback")
    with factory() as session:
        rolled_back = list(
            session.scalars(
                select(models.SubtaskContext)
                .where(
                    models.SubtaskContext.id.in_([rollback_first.id, rollback_second.id])
                )
                .order_by(models.SubtaskContext.id)
            )
        )
        assert [row.subtask_id for row in rolled_back] == [0, 0]
