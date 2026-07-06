from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db import models
from app.db.models import Base

ALEMBIC_INI = Path(__file__).parents[1] / "alembic.ini"
TARGET_TABLES = {
    "artifacts",
    "conversations",
    "messages",
    "users",
}
OLD_TABLES = {
    "interview_configs",
    "interview_sessions",
    "interview_turns",
    "learning_messages",
    "learning_sessions",
    "processing_jobs",
    "reports",
    "workspace_settings",
}


def test_base_metadata_contains_only_target_core_tables() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    inspector = inspect(engine)

    table_names = set(inspector.get_table_names())
    assert TARGET_TABLES.issubset(table_names)
    assert OLD_TABLES.isdisjoint(table_names)
    assert table_names == TARGET_TABLES


def test_artifact_paths_are_unique_per_user() -> None:
    assert hasattr(models, "User")
    assert hasattr(models, "Artifact")
    User = models.User
    Artifact = models.Artifact

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        alice = User(username="alice", password_hash="hash-a")
        bob = User(username="bob", password_hash="hash-b")
        session.add_all([alice, bob])
        session.flush()

        session.add_all(
            [
                Artifact(user_id=alice.id, kind="note", relative_path="notes/cache.md"),
                Artifact(user_id=bob.id, kind="note", relative_path="notes/cache.md"),
            ]
        )
        session.commit()

    with Session(engine) as session:
        alice_id = session.scalar(text("SELECT id FROM users WHERE username = 'alice'"))
        session.add(
            Artifact(user_id=alice_id, kind="note", relative_path="notes/cache.md")
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_messages_must_belong_to_conversation_owner() -> None:
    User = models.User
    Conversation = models.Conversation
    Message = models.Message

    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)

    with Session(engine) as session:
        alice = User(username="alice", password_hash="hash-a")
        bob = User(username="bob", password_hash="hash-b")
        session.add_all([alice, bob])
        session.flush()

        conversation = Conversation(user_id=alice.id, kind="interview")
        session.add(conversation)
        session.flush()

        session.add(
            Message(
                user_id=alice.id,
                conversation_id=conversation.id,
                role="user",
                message_type="answer",
            )
        )
        session.commit()

        conversation_id = conversation.id
        bob_id = bob.id

    with Session(engine) as session:
        session.add(
            Message(
                user_id=bob_id,
                conversation_id=conversation_id,
                role="user",
                message_type="answer",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_migration_on_empty_database_creates_target_tables_without_data_dir(
    tmp_path, monkeypatch
) -> None:
    database_url = f"sqlite:///{tmp_path / 'migration.db'}"
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    get_settings.cache_clear()

    engine = create_engine(database_url)
    try:
        assert not data_dir.exists()
        config = Config(ALEMBIC_INI)
        command.upgrade(config, "head")
        command.check(config)

        inspector = inspect(engine)
        assert set(inspector.get_table_names()) == TARGET_TABLES | {"alembic_version"}
        assert not data_dir.exists()

        artifact_unique_constraints = {
            constraint["name"]
            for constraint in inspector.get_unique_constraints("artifacts")
        }
        assert "uq_artifacts_user_path" in artifact_unique_constraints
        conversation_unique_constraints = {
            constraint["name"]
            for constraint in inspector.get_unique_constraints("conversations")
        }
        assert "uq_conversations_id_user" in conversation_unique_constraints

        message_foreign_keys = inspector.get_foreign_keys("messages")
        assert {
            (foreign_key["referred_table"], tuple(foreign_key["constrained_columns"]))
            for foreign_key in message_foreign_keys
        } == {
            ("users", ("user_id",)),
            ("conversations", ("conversation_id", "user_id")),
        }
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_migration_refuses_non_empty_legacy_tables(tmp_path, monkeypatch) -> None:
    database_url = f"sqlite:///{tmp_path / 'migration.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()

    engine = create_engine(database_url)
    try:
        config = Config(ALEMBIC_INI)
        command.upgrade(config, "20260701_0010")
        timestamp = "2026-07-06 00:00:00"
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO interview_configs "
                    "(id, target_company, target_role, job_description, extra_prompt, language, "
                    "mode, chat_model_provider, chat_model, target_rounds, is_last_used, updated_at) "
                    "VALUES "
                    "('config-1', '', '', '', '', 'zh-CN', 'comprehensive', "
                    "'openai', 'gpt-4.1-mini', 1, 0, :timestamp)"
                ),
                {"timestamp": timestamp},
            )

        with pytest.raises(RuntimeError) as exc_info:
            command.upgrade(config, "head")

        message = str(exc_info.value)
        assert "Run ./reset-data.sh explicitly before upgrading" in message
        assert "interview_configs" in message
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_mysql_offline_migration_creates_target_schema_without_json_defaults(
    monkeypatch, capsys
) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "mysql+pymysql://auto_reign:auto_reign@127.0.0.1:13306/auto_reign",
    )
    get_settings.cache_clear()

    try:
        command.upgrade(Config(ALEMBIC_INI), "head", sql=True)
        stdout = capsys.readouterr().out
    finally:
        get_settings.cache_clear()

    assert "CREATE TABLE users" in stdout
    assert "CREATE TABLE artifacts" in stdout
    assert "CREATE TABLE conversations" in stdout
    assert "CREATE TABLE messages" in stdout
    assert "CONSTRAINT uq_artifacts_user_path UNIQUE (user_id, relative_path)" in stdout
    assert "CONSTRAINT uq_conversations_id_user UNIQUE (id, user_id)" in stdout
    assert "FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE" in stdout
    assert (
        "FOREIGN KEY(conversation_id, user_id) REFERENCES conversations (id, user_id) "
        "ON DELETE CASCADE"
    ) in stdout
    assert "settings_json JSON NOT NULL" in stdout
    assert "status_json JSON NOT NULL" in stdout
    assert "metadata_json JSON NOT NULL" in stdout
    assert "config_json JSON NOT NULL" in stdout
    assert "summary_json JSON NOT NULL" in stdout
    assert "JSON NOT NULL DEFAULT" not in stdout
