from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier

from fastapi import HTTPException
import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

from app.core.config import Settings
from app.db import models
from app.db.session import create_engine_for_settings, make_session_factory, session_scope
from app.schemas.agents import AgentConfig
from app.schemas.conversations import ConversationSendRequest
from app.schemas.modeling import ModelRef
from app.services.generation_service import GenerationService


class _Runtime:
    def prepare_turn(self, turn):
        return turn

    def stream_turn(self, _turn, *, observer):
        del observer
        return iter(("answer",))


def _database_identity(url: URL) -> tuple[str | None, int, str | None]:
    host = (url.host or "").casefold().rstrip(".")
    if host in {"localhost", "127.0.0.1", "::1"}:
        host = "loopback"
    return host, url.port or 3306, url.database.casefold() if url.database else None


def _validate_disposable_mysql_url(explicit: str) -> URL:
    try:
        url = make_url(explicit)
    except ArgumentError as error:
        raise ValueError(
            "MYSQL_ATTACHMENT_BINDING_DATABASE_URL is invalid"
        ) from error
    if not url.drivername.startswith("mysql") or not url.database:
        raise ValueError(
            "MYSQL_ATTACHMENT_BINDING_DATABASE_URL must name a MySQL database"
        )
    if url.database.casefold() in {
        "information_schema",
        "mysql",
        "performance_schema",
        "sys",
    }:
        raise ValueError(
            "MYSQL_ATTACHMENT_BINDING_DATABASE_URL must not name a system database"
        )
    if not url.database.casefold().endswith("_test"):
        raise ValueError(
            "MYSQL_ATTACHMENT_BINDING_DATABASE_URL database name must end with _test"
        )
    return url


def _disposable_mysql_url() -> URL:
    if os.environ.get("RUN_MYSQL_INTEGRATION") != "1":
        pytest.skip("requires RUN_MYSQL_INTEGRATION=1")
    explicit = os.environ.get("MYSQL_ATTACHMENT_BINDING_DATABASE_URL")
    if not explicit:
        pytest.skip("requires MYSQL_ATTACHMENT_BINDING_DATABASE_URL")
    try:
        url = _validate_disposable_mysql_url(explicit)
    except ValueError as error:
        pytest.fail(str(error))
    configured = os.environ.get("DATABASE_URL")
    if configured:
        try:
            if _database_identity(url) == _database_identity(make_url(configured)):
                pytest.skip("attachment binding database must differ from DATABASE_URL")
        except ArgumentError:
            pytest.skip("cannot prove attachment binding database is disposable")
    return url


def test_attachment_binding_database_requires_explicit_test_suffix() -> None:
    with pytest.raises(ValueError, match="must end with _test"):
        _validate_disposable_mysql_url(
            "mysql+pymysql://user:password@localhost/auto_reign"
        )

    allowed = _validate_disposable_mysql_url(
        "mysql+pymysql://user:password@localhost/auto_reign_migration_test"
    )
    assert allowed.database == "auto_reign_migration_test"


def test_two_senders_can_bind_the_same_draft_only_once() -> None:
    database_url = _disposable_mysql_url()
    engine = create_engine_for_settings(
        Settings(
            _env_file=None,
            database_url=database_url.render_as_string(hide_password=False),
        )
    )
    try:
        models.Base.metadata.drop_all(engine)
        models.Base.metadata.create_all(engine)
        session_factory = make_session_factory(engine)
        with session_scope(session_factory) as session:
            user = models.User(
                username="attachment-race",
                password_hash="not-used",
                display_name="Attachment Race",
                role="user",
                is_active=True,
                token_version=1,
                settings_json={},
            )
            session.add(user)
            session.flush()
            agent = models.Resource(
                user_id=user.id,
                resource_type="agent",
                name="Attachment Race Agent",
                config_json=AgentConfig(
                    system_prompt="Answer.",
                    default_model=ModelRef(
                        provider="qwen",
                        model="qwen3.7-plus",
                    ),
                ).model_dump(mode="json"),
            )
            session.add(agent)
            session.flush()
            attachment = models.Attachment(
                id="shared-draft",
                user_id=user.id,
                original_filename="source.txt",
                object_key=f"users/{user.id}/attachments/shared-draft/original",
                parsed_object_key=f"users/{user.id}/attachments/shared-draft/parsed",
                mime_type="text/plain",
                size_bytes=4,
                content_hash="sha256:source",
                parsed_size_bytes=4,
                parsed_content_hash="sha256:parsed",
            )
            session.add(attachment)
            user_id = user.id
            agent_id = agent.id

        start = Barrier(2)

        def send(index: int) -> tuple[str, object]:
            service = GenerationService(
                session_factory=session_factory,
                runtime=_Runtime(),  # type: ignore[arg-type]
                settings=Settings(
                    _env_file=None,
                    database_url=engine.url.render_as_string(hide_password=False),
                    qwen_api_key="test-key",
                ),
            )
            start.wait(timeout=30)
            try:
                events = list(
                    service.stream_turn(
                        user_id=user_id,
                        request=ConversationSendRequest(
                            text=f"sender {index}",
                            agent_id=agent_id,
                            attachment_ids=["shared-draft"],
                        ),
                    )
                )
                return events[0].event, events[0].data
            except HTTPException as error:
                assert isinstance(error.detail, dict)
                return "error", error.detail["code"]

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(send, range(2)))

        assert sorted(item[0] for item in outcomes) == ["accepted", "error"]
        assert next(item[1] for item in outcomes if item[0] == "error") == (
            "attachment_not_ready"
        )
        accepted = next(item[1] for item in outcomes if item[0] == "accepted")
        assert isinstance(accepted, dict)
        with session_scope(session_factory) as session:
            persisted = session.get(models.Attachment, "shared-draft")
            assert persisted is not None
            assert persisted.message_id == accepted["user_message_id"]
            assert session.scalar(select(func.count(models.Conversation.id))) == 1
            assert session.scalar(select(func.count(models.Message.id))) == 2
    finally:
        try:
            models.Base.metadata.drop_all(engine)
        finally:
            engine.dispose()
