import os
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import Integer, String, create_engine, inspect, text
from sqlalchemy.engine import Engine, URL, make_url
from sqlalchemy.exc import ArgumentError

from app.core.config import Settings
from app.db.session import create_engine_for_settings


ALEMBIC_INI = Path(__file__).parents[2] / "alembic.ini"
BASELINE_REVISION = "20260713_0001"
ATTACHMENT_INTEGRITY_REVISION = "20260713_0002"
KNOWLEDGE_ATTEMPT_REVISION = "20260714_0003"
MIGRATION_DATABASE_SUFFIX = "_migration_test"
EXPECTED_TABLES = {
    "attachments",
    "conversations",
    "knowledge_documents",
    "messages",
    "resources",
    "users",
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
        pytest.skip(
            "requires an explicit MYSQL_MIGRATION_DATABASE_URL; "
            "DATABASE_URL is never used as a fallback"
        )
    try:
        return _validate_migration_mysql_url(explicit_url)
    except ValueError as error:
        pytest.fail(str(error))


def _current_revision(engine: Engine) -> str:
    with engine.connect() as connection:
        return connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()


def _attachment_columns(engine: Engine) -> dict[str, dict[str, object]]:
    return {column["name"]: column for column in inspect(engine).get_columns("attachments")}


def _assert_attachment_integrity_columns(engine: Engine, *, present: bool) -> None:
    columns = _attachment_columns(engine)
    if not present:
        assert "parsed_size_bytes" not in columns
        assert "parsed_content_hash" not in columns
        return

    parsed_size = columns["parsed_size_bytes"]
    assert isinstance(parsed_size["type"], Integer)
    assert parsed_size["nullable"] is True
    parsed_hash = columns["parsed_content_hash"]
    assert isinstance(parsed_hash["type"], String)
    assert parsed_hash["type"].length == 128
    assert parsed_hash["nullable"] is True


def _assert_knowledge_attempt_columns(engine: Engine, *, present: bool) -> None:
    columns = {
        column["name"]: column
        for column in inspect(engine).get_columns("knowledge_documents")
    }
    if not present:
        assert "processing_attempt_id" not in columns
        assert "cleanup_attempt_id" not in columns
        return

    for column_name in ("processing_attempt_id", "cleanup_attempt_id"):
        column = columns[column_name]
        assert isinstance(column["type"], String)
        assert column["type"].length == 36
        assert column["nullable"] is True


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
    return {index["name"] for index in inspector.get_indexes(table_name)}


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

    with pytest.raises(pytest.skip.Exception, match="explicit"):
        _migration_mysql_url()


@pytest.mark.skipif(
    os.environ.get("RUN_MYSQL_INTEGRATION") != "1",
    reason="requires RUN_MYSQL_INTEGRATION=1 and a live MySQL service",
)
def test_mysql_knowledge_migration_lifecycle(monkeypatch) -> None:
    migration_url = _migration_mysql_url()
    monkeypatch.setenv(
        "DATABASE_URL",
        migration_url.render_as_string(hide_password=False),
    )
    config = Config(str(ALEMBIC_INI))
    engine = create_engine(migration_url, pool_pre_ping=True)
    try:
        # CI prepares the empty schema at head. Repeating upgrade keeps local runs safe
        # and establishes the known starting point before entering the tested lifecycle.
        command.upgrade(config, "head")
        assert _current_revision(engine) == KNOWLEDGE_ATTEMPT_REVISION
        _assert_knowledge_attempt_columns(engine, present=True)

        command.downgrade(config, ATTACHMENT_INTEGRITY_REVISION)
        assert _current_revision(engine) == ATTACHMENT_INTEGRITY_REVISION
        _assert_attachment_integrity_columns(engine, present=True)
        _assert_knowledge_attempt_columns(engine, present=False)

        command.downgrade(config, BASELINE_REVISION)
        assert _current_revision(engine) == BASELINE_REVISION
        _assert_attachment_integrity_columns(engine, present=False)
        baseline_indexes = _index_names(inspect(engine), "attachments")

        command.upgrade(config, ATTACHMENT_INTEGRITY_REVISION)
        assert _current_revision(engine) == ATTACHMENT_INTEGRITY_REVISION
        _assert_attachment_integrity_columns(engine, present=True)
        _assert_knowledge_attempt_columns(engine, present=False)
        assert _index_names(inspect(engine), "attachments") == baseline_indexes

        command.downgrade(config, BASELINE_REVISION)
        assert _current_revision(engine) == BASELINE_REVISION
        _assert_attachment_integrity_columns(engine, present=False)
        assert _index_names(inspect(engine), "attachments") == baseline_indexes

        command.upgrade(config, "head")
        assert _current_revision(engine) == KNOWLEDGE_ATTEMPT_REVISION
        _assert_attachment_integrity_columns(engine, present=True)
        _assert_knowledge_attempt_columns(engine, present=True)
        assert _index_names(inspect(engine), "attachments") == baseline_indexes
    finally:
        engine.dispose()


@pytest.mark.skipif(
    os.environ.get("RUN_MYSQL_INTEGRATION") != "1",
    reason="requires RUN_MYSQL_INTEGRATION=1 and a live MySQL service",
)
def test_mysql_schema_matches_expected_tables() -> None:
    database_url = os.environ["DATABASE_URL"]
    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        assert tables == EXPECTED_TABLES | {"alembic_version"}

        assert _foreign_key_signatures(inspector, "knowledge_documents") == {
            (
                ("collection_id", "user_id"),
                "resources",
                ("id", "user_id"),
                None,
            )
        }
        assert _foreign_key_signatures(inspector, "conversations") == {
            (("agent_id",), "resources", ("id",), None),
            (("user_id",), "users", ("id",), "CASCADE"),
        }
        assert _foreign_key_signatures(inspector, "messages") == {
            (
                ("conversation_id", "user_id"),
                "conversations",
                ("id", "user_id"),
                "CASCADE",
            ),
            (("user_id",), "users", ("id",), "CASCADE"),
        }
        assert _foreign_key_signatures(inspector, "attachments") == {
            (
                ("message_id", "user_id"),
                "messages",
                ("id", "user_id"),
                "CASCADE",
            ),
            (("user_id",), "users", ("id",), "CASCADE"),
        }

        assert _unique_constraint_names(inspector, "resources") == {
            "uq_resources_id_owner",
            "uq_resources_owner_type_name",
        }
        assert _unique_constraint_names(inspector, "conversations") == {"uq_conversations_id_user"}
        assert _unique_constraint_names(inspector, "messages") == {
            "uq_messages_conversation_sequence",
            "uq_messages_id_user",
        }

        expected_indexes = {
            "users": {"ix_users_username"},
            "resources": {"ix_resources_resource_type", "ix_resources_user_id"},
            "knowledge_documents": {
                "ix_knowledge_documents_collection_id",
                "ix_knowledge_documents_user_id",
            },
            "conversations": {"ix_conversations_agent_id", "ix_conversations_user_id"},
            "messages": {"ix_messages_conversation_id", "ix_messages_user_id"},
            "attachments": {"ix_attachments_message_id", "ix_attachments_user_id"},
        }
        for table_name, index_names in expected_indexes.items():
            assert index_names <= _index_names(inspector, table_name)

        attachment_columns = {
            column["name"]: column for column in inspector.get_columns("attachments")
        }
        parsed_size = attachment_columns["parsed_size_bytes"]
        assert isinstance(parsed_size["type"], Integer)
        assert parsed_size["nullable"] is True
        parsed_hash = attachment_columns["parsed_content_hash"]
        assert isinstance(parsed_hash["type"], String)
        assert parsed_hash["type"].length == 128
        assert parsed_hash["nullable"] is True
        _assert_knowledge_attempt_columns(engine, present=True)
    finally:
        engine.dispose()


@pytest.mark.skipif(
    os.environ.get("RUN_MYSQL_INTEGRATION") != "1",
    reason="requires RUN_MYSQL_INTEGRATION=1 and a live MySQL service",
)
def test_application_mysql_engine_uses_read_committed_transaction_isolation() -> None:
    database_url = os.environ["DATABASE_URL"]
    engine = create_engine_for_settings(Settings(_env_file=None, database_url=database_url))
    try:
        with engine.connect() as connection:
            isolation = connection.execute(text("SELECT @@transaction_isolation")).scalar_one()
        assert isolation == "READ-COMMITTED"
    finally:
        engine.dispose()
