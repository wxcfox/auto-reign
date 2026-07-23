import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import String, create_engine, inspect, text
from sqlalchemy.dialects import mysql
from sqlalchemy.engine import Engine, URL, make_url
from sqlalchemy.exc import ArgumentError

from app.core.config import Settings
from app.db.session import create_engine_for_settings


ALEMBIC_INI = Path(__file__).parents[2] / "alembic.ini"
BASELINE_REVISION = "20260722_0001"
MIGRATION_DATABASE_SUFFIX = "_migration_test"
EXPECTED_TABLES = {
    "knowledge_documents",
    "resources",
    "subtask_contexts",
    "subtasks",
    "tasks",
    "users",
}
EXPECTED_COLUMNS = {
    "users": {
        "id",
        "username",
        "password_hash",
        "display_name",
        "role",
        "is_active",
        "token_version",
        "settings_json",
        "seed_initialized_at",
        "credential_bootstrap_status",
        "created_at",
        "updated_at",
    },
    "resources": {
        "id",
        "user_id",
        "resource_type",
        "name",
        "config_json",
        "is_active",
        "deleted_at",
        "created_at",
        "updated_at",
    },
    "knowledge_documents": {
        "id",
        "user_id",
        "collection_id",
        "name",
        "source_object_key",
        "parsed_object_key",
        "mime_type",
        "size_bytes",
        "content_hash",
        "status",
        "index_generation",
        "retriever_type",
        "processing_attempt_id",
        "cleanup_attempt_id",
        "error_code",
        "error_message",
        "is_active",
        "created_at",
        "updated_at",
        "indexed_at",
    },
    "tasks": {
        "id",
        "user_id",
        "agent_id",
        "name",
        "status",
        "model_override_json",
        "is_active",
        "created_at",
        "updated_at",
    },
    "subtasks": {
        "id",
        "user_id",
        "task_id",
        "role",
        "message_id",
        "parent_id",
        "title",
        "prompt",
        "status",
        "progress",
        "result",
        "error_message",
        "created_at",
        "updated_at",
        "completed_at",
    },
    "subtask_contexts": {
        "id",
        "user_id",
        "subtask_id",
        "context_type",
        "name",
        "status",
        "error_message",
        "binary_data",
        "image_base64",
        "extracted_text",
        "text_length",
        "mime_type",
        "file_extension",
        "file_size",
        "type_data",
        "created_at",
        "updated_at",
    },
}
EXPECTED_NEW_TABLE_COLUMN_SIGNATURES = {
    "tasks": {
        "id": ("BIGINT", False),
        "user_id": ("INTEGER", False),
        "agent_id": ("VARCHAR(36)", True),
        "name": ("VARCHAR(255)", False),
        "status": ("VARCHAR(24)", False),
        "model_override_json": ("JSON", True),
        "is_active": ("TINYINT", False),
        "created_at": ("DATETIME", False),
        "updated_at": ("DATETIME", False),
    },
    "subtasks": {
        "id": ("BIGINT", False),
        "user_id": ("INTEGER", False),
        "task_id": ("BIGINT", False),
        "role": ("VARCHAR(16)", False),
        "message_id": ("BIGINT", False),
        "parent_id": ("BIGINT", True),
        "title": ("VARCHAR(255)", False),
        "prompt": ("LONGTEXT", False),
        "status": ("VARCHAR(24)", False),
        "progress": ("INTEGER", False),
        "result": ("JSON", True),
        "error_message": ("TEXT", True),
        "created_at": ("DATETIME", False),
        "updated_at": ("DATETIME", False),
        "completed_at": ("DATETIME", True),
    },
    "subtask_contexts": {
        "id": ("BIGINT", False),
        "user_id": ("INTEGER", False),
        "subtask_id": ("BIGINT", False),
        "context_type": ("VARCHAR(32)", False),
        "name": ("VARCHAR(255)", False),
        "status": ("VARCHAR(24)", False),
        "error_message": ("TEXT", True),
        "binary_data": ("LONGBLOB", True),
        "image_base64": ("LONGTEXT", True),
        "extracted_text": ("LONGTEXT", True),
        "text_length": ("INTEGER", False),
        "mime_type": ("VARCHAR(160)", True),
        "file_extension": ("VARCHAR(32)", True),
        "file_size": ("BIGINT", True),
        "type_data": ("JSON", False),
        "created_at": ("DATETIME", False),
        "updated_at": ("DATETIME", False),
    },
}
EXPECTED_NEW_TABLE_INDEX_SIGNATURES = {
    "tasks": {
        ("ix_tasks_agent_id", ("agent_id",), False),
        ("ix_tasks_status", ("status",), False),
        ("ix_tasks_user_id", ("user_id",), False),
    },
    "subtasks": {
        ("ix_subtasks_message_id", ("message_id",), False),
        ("ix_subtasks_status", ("status",), False),
        ("ix_subtasks_task_id", ("task_id",), False),
        ("ix_subtasks_user_id", ("user_id",), False),
    },
    "subtask_contexts": {
        ("ix_subtask_contexts_context_type", ("context_type",), False),
        ("ix_subtask_contexts_subtask_id", ("subtask_id",), False),
        ("ix_subtask_contexts_user_id", ("user_id",), False),
    },
}


def _validate_migration_mysql_url(explicit_url: str) -> URL:
    try:
        parsed_url = make_url(explicit_url)
    except ArgumentError as error:
        raise ValueError("MYSQL_MIGRATION_DATABASE_URL is not a valid database URL") from error
    if not parsed_url.drivername.startswith("mysql"):
        raise ValueError("MYSQL_MIGRATION_DATABASE_URL must use a MySQL driver")
    database_name = parsed_url.database
    if not database_name or not database_name.casefold().endswith(MIGRATION_DATABASE_SUFFIX):
        raise ValueError(
            "MYSQL_MIGRATION_DATABASE_URL must name a dedicated database ending "
            f"with {MIGRATION_DATABASE_SUFFIX}"
        )
    return parsed_url


def _migration_mysql_url() -> URL:
    if os.environ.get("RUN_MYSQL_INTEGRATION") != "1":
        pytest.skip("requires RUN_MYSQL_INTEGRATION=1")
    explicit_url = os.environ.get("MYSQL_MIGRATION_DATABASE_URL")
    if not explicit_url:
        pytest.fail(
            "RUN_MYSQL_INTEGRATION=1 requires an explicit "
            "MYSQL_MIGRATION_DATABASE_URL; "
            "DATABASE_URL is never used as a fallback"
        )
    try:
        return _validate_migration_mysql_url(explicit_url)
    except ValueError as error:
        pytest.fail(str(error))


def _current_revision(engine: Engine) -> str:
    with engine.connect() as connection:
        return connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()


def _foreign_key_signatures(inspector, table_name: str):
    return {
        (
            tuple(foreign_key["constrained_columns"]),
            foreign_key["referred_table"],
            tuple(foreign_key["referred_columns"]),
            foreign_key.get("options", {}).get("ondelete"),
        )
        for foreign_key in inspector.get_foreign_keys(table_name)
    }


def _unique_constraint_names(inspector, table_name: str) -> set[str | None]:
    return {constraint["name"] for constraint in inspector.get_unique_constraints(table_name)}


def _index_names(inspector, table_name: str) -> set[str | None]:
    return {
        index["name"]
        for index in inspector.get_indexes(table_name)
        if isinstance(index["name"], str) and index["name"].startswith("ix_")
    }


def _index_signatures(inspector, table_name: str):
    return {
        (
            index["name"],
            tuple(index["column_names"]),
            bool(index.get("unique", False)),
        )
        for index in inspector.get_indexes(table_name)
    }


def _mysql_type_signature(column_type) -> str:
    if isinstance(column_type, mysql.BIGINT):
        return "BIGINT"
    if isinstance(column_type, mysql.INTEGER):
        return "INTEGER"
    if isinstance(column_type, mysql.TINYINT):
        return "TINYINT"
    if isinstance(column_type, mysql.VARCHAR):
        return f"VARCHAR({column_type.length})"
    if isinstance(column_type, mysql.LONGBLOB):
        return "LONGBLOB"
    if isinstance(column_type, mysql.LONGTEXT):
        return "LONGTEXT"
    if isinstance(column_type, mysql.TEXT):
        return "TEXT"
    if isinstance(column_type, mysql.JSON):
        return "JSON"
    if isinstance(column_type, mysql.DATETIME):
        return "DATETIME"
    raise AssertionError(f"Unexpected reflected MySQL type: {column_type!r}")


def _column_signatures(inspector, table_name: str):
    return {
        column["name"]: (_mysql_type_signature(column["type"]), column["nullable"])
        for column in inspector.get_columns(table_name)
    }


@pytest.mark.parametrize(
    "database_name",
    ["auto_reign", "mysql", "auto_reign_migration_test_backup"],
)
def test_migration_mysql_url_rejects_non_dedicated_database(
    database_name: str,
) -> None:
    with pytest.raises(ValueError, match=MIGRATION_DATABASE_SUFFIX):
        _validate_migration_mysql_url(f"mysql+pymysql://user:password@127.0.0.1/{database_name}")


def test_migration_mysql_url_never_falls_back_to_database_url(monkeypatch) -> None:
    monkeypatch.setenv("RUN_MYSQL_INTEGRATION", "1")
    monkeypatch.delenv("MYSQL_MIGRATION_DATABASE_URL", raising=False)
    monkeypatch.setenv(
        "DATABASE_URL",
        "mysql+pymysql://user:password@127.0.0.1/auto_reign_migration_test",
    )

    with pytest.raises(pytest.fail.Exception, match="explicit"):
        _migration_mysql_url()


@pytest.mark.skipif(
    os.environ.get("RUN_MYSQL_INTEGRATION") != "1",
    reason="requires RUN_MYSQL_INTEGRATION=1 and a live MySQL service",
)
def test_mysql_task_subtask_baseline_lifecycle(monkeypatch) -> None:
    migration_url = _migration_mysql_url()
    monkeypatch.setenv(
        "DATABASE_URL",
        migration_url.render_as_string(hide_password=False),
    )
    config = Config(str(ALEMBIC_INI))
    engine = create_engine(migration_url, pool_pre_ping=True)
    try:
        assert ScriptDirectory.from_config(config).get_heads() == [BASELINE_REVISION]

        command.downgrade(config, "base")
        command.upgrade(config, "head")
        assert _current_revision(engine) == BASELINE_REVISION
        _assert_task_subtask_schema(engine)

        command.downgrade(config, "base")
        assert set(inspect(engine).get_table_names()) == {"alembic_version"}
        command.upgrade(config, "head")
        assert _current_revision(engine) == BASELINE_REVISION
        _assert_task_subtask_schema(engine)
    finally:
        engine.dispose()


def _assert_task_subtask_schema(engine: Engine) -> None:
    inspector = inspect(engine)
    assert set(inspector.get_table_names()) == EXPECTED_TABLES | {"alembic_version"}
    for table_name, column_names in EXPECTED_COLUMNS.items():
        assert {column["name"] for column in inspector.get_columns(table_name)} == column_names
    for table_name, expected_signatures in EXPECTED_NEW_TABLE_COLUMN_SIGNATURES.items():
        assert _column_signatures(inspector, table_name) == expected_signatures

    assert _foreign_key_signatures(inspector, "knowledge_documents") == {
        (("collection_id", "user_id"), "resources", ("id", "user_id"), None)
    }
    assert _foreign_key_signatures(inspector, "tasks") == {
        (("agent_id",), "resources", ("id",), None),
        (("user_id",), "users", ("id",), "CASCADE"),
    }
    assert _foreign_key_signatures(inspector, "subtasks") == {
        (("task_id",), "tasks", ("id",), "CASCADE"),
        (("user_id",), "users", ("id",), "CASCADE"),
    }
    assert _foreign_key_signatures(inspector, "subtask_contexts") == {
        (("user_id",), "users", ("id",), "CASCADE")
    }

    assert _unique_constraint_names(inspector, "resources") == {
        "uq_resources_id_owner",
        "uq_resources_owner_type_name",
    }
    assert _unique_constraint_names(inspector, "subtasks") == set()
    assert not any(
        index["unique"] and index["column_names"] == ["task_id", "message_id"]
        for index in inspector.get_indexes("subtasks")
    )

    expected_existing_index_names = {
        "users": {"ix_users_username"},
        "resources": {"ix_resources_resource_type", "ix_resources_user_id"},
        "knowledge_documents": {
            "ix_knowledge_documents_collection_id",
            "ix_knowledge_documents_user_id",
        },
    }
    for table_name, index_names in expected_existing_index_names.items():
        assert _index_names(inspector, table_name) == index_names
    for table_name, expected_signatures in EXPECTED_NEW_TABLE_INDEX_SIGNATURES.items():
        assert _index_signatures(inspector, table_name) == expected_signatures
    for table_name in EXPECTED_TABLES:
        assert inspector.get_check_constraints(table_name) == []

    knowledge_document_columns = {
        column["name"]: column
        for column in inspector.get_columns("knowledge_documents")
    }
    for attempt_column_name in ("processing_attempt_id", "cleanup_attempt_id"):
        attempt_column = knowledge_document_columns[attempt_column_name]
        assert isinstance(attempt_column["type"], String)
        assert attempt_column["type"].length == 36
        assert attempt_column["nullable"] is True
    retriever_type = knowledge_document_columns["retriever_type"]
    assert isinstance(retriever_type["type"], String)
    assert retriever_type["type"].length == 32
    assert retriever_type["nullable"] is False

@pytest.mark.skipif(
    os.environ.get("RUN_MYSQL_INTEGRATION") != "1",
    reason="requires RUN_MYSQL_INTEGRATION=1 and a live MySQL service",
)
def test_application_mysql_engine_uses_read_committed_transaction_isolation() -> None:
    database_url = _migration_mysql_url().render_as_string(hide_password=False)
    engine = create_engine_for_settings(Settings(_env_file=None, database_url=database_url))
    try:
        with engine.connect() as connection:
            isolation = connection.execute(text("SELECT @@transaction_isolation")).scalar_one()
        assert isolation == "READ-COMMITTED"
    finally:
        engine.dispose()
