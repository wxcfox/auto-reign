from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Integer, String, create_engine, event, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import (
    Base,
    KnowledgeDocument,
    Resource,
    Subtask,
    SubtaskContext,
    Task,
    User,
)

ALEMBIC_INI = Path(__file__).parents[1] / "alembic.ini"
TARGET_TABLES = {
    "knowledge_documents",
    "resources",
    "subtask_contexts",
    "subtasks",
    "tasks",
    "users",
}


def create_schema_engine():
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine


def test_base_metadata_contains_only_task_subtask_tables() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    assert set(inspect(engine).get_table_names()) == TARGET_TABLES


def test_global_resource_owner_is_not_a_user_foreign_key() -> None:
    resource_foreign_keys = {
        tuple(item["constrained_columns"])
        for item in inspect(create_schema_engine()).get_foreign_keys("resources")
    }
    assert ("user_id",) not in resource_foreign_keys


def test_knowledge_document_owner_must_match_collection_owner() -> None:
    engine = create_schema_engine()
    with Session(engine) as session:
        collection = Resource(
            user_id=1,
            resource_type="collection",
            name="alice-collection",
            config_json={},
        )
        session.add(collection)
        session.flush()
        session.add(
            KnowledgeDocument(
                user_id=2,
                collection_id=collection.id,
                name="cross-owner.txt",
                source_object_key="users/2/source/cross-owner.txt",
                mime_type="text/plain",
                size_bytes=11,
                content_hash="sha256:cross-owner",
                status="uploaded",
                index_generation=1,
                is_active=True,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_subtasks_allow_duplicate_message_ids_within_a_task() -> None:
    engine = create_schema_engine()
    with Session(engine) as session:
        user = User(username="alice", password_hash="a")
        session.add(user)
        session.flush()
        task = Task(user_id=user.id, name="duplicate message IDs")
        session.add(task)
        session.flush()
        session.add_all(
            [
                Subtask(
                    user_id=user.id,
                    task_id=task.id,
                    role="user",
                    message_id=42,
                ),
                Subtask(
                    user_id=user.id,
                    task_id=task.id,
                    role="assistant",
                    message_id=42,
                ),
            ]
        )
        session.commit()


def test_subtask_context_allows_zero_subtask_id_without_foreign_key() -> None:
    engine = create_schema_engine()
    with Session(engine) as session:
        user = User(username="alice", password_hash="a")
        session.add(user)
        session.flush()
        context = SubtaskContext(
            user_id=user.id,
            subtask_id=0,
            context_type="text",
            name="standalone context",
        )
        session.add(context)
        session.commit()
        assert context.id is not None


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

        resource_unique_constraints = {
            constraint["name"]
            for constraint in inspector.get_unique_constraints("resources")
        }
        assert resource_unique_constraints == {
            "uq_resources_id_owner",
            "uq_resources_owner_type_name",
        }
        knowledge_document_foreign_keys = inspector.get_foreign_keys(
            "knowledge_documents"
        )
        assert {
            (
                foreign_key["referred_table"],
                tuple(foreign_key["constrained_columns"]),
                tuple(foreign_key["referred_columns"]),
            )
            for foreign_key in knowledge_document_foreign_keys
        } == {
            (
                "resources",
                ("collection_id", "user_id"),
                ("id", "user_id"),
            )
        }
        knowledge_document_columns = {
            column["name"]: column
            for column in inspector.get_columns("knowledge_documents")
        }
        for attempt_column_name in (
            "processing_attempt_id",
            "cleanup_attempt_id",
        ):
            attempt_column = knowledge_document_columns[attempt_column_name]
            assert isinstance(attempt_column["type"], String)
            assert attempt_column["type"].length == 36
            assert attempt_column["nullable"] is True

        task_foreign_keys = inspector.get_foreign_keys("tasks")
        assert {
            (foreign_key["referred_table"], tuple(foreign_key["constrained_columns"]))
            for foreign_key in task_foreign_keys
        } == {("resources", ("agent_id",)), ("users", ("user_id",))}
        assert {
            (foreign_key["referred_table"], tuple(foreign_key["constrained_columns"]))
            for foreign_key in inspector.get_foreign_keys("subtasks")
        } == {("tasks", ("task_id",)), ("users", ("user_id",))}
        assert {
            (foreign_key["referred_table"], tuple(foreign_key["constrained_columns"]))
            for foreign_key in inspector.get_foreign_keys("subtask_contexts")
        } == {("users", ("user_id",))}
        assert not inspector.get_unique_constraints("subtasks")
        assert all(
            not (
                index["unique"] and index["column_names"] == ["task_id", "message_id"]
            )
            for index in inspector.get_indexes("subtasks")
        )

        context_columns = {
            column["name"]: column
            for column in inspector.get_columns("subtask_contexts")
        }
        assert context_columns["binary_data"]["nullable"] is True
        assert context_columns["image_base64"]["nullable"] is True
        assert context_columns["extracted_text"]["nullable"] is True
        assert isinstance(context_columns["file_size"]["type"], Integer)

        command.downgrade(config, "base")
        assert set(inspect(engine).get_table_names()) == {"alembic_version"}
        command.upgrade(config, "head")
        assert set(inspect(engine).get_table_names()) == TARGET_TABLES | {"alembic_version"}
        assert not data_dir.exists()
    finally:
        engine.dispose()
        get_settings.cache_clear()
