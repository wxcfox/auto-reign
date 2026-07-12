from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Integer, String, create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import (
    Base,
    Conversation,
    KnowledgeDocument,
    Message,
    Resource,
    User,
)

ALEMBIC_INI = Path(__file__).parents[1] / "alembic.ini"
TARGET_TABLES = {
    "attachments",
    "conversations",
    "knowledge_documents",
    "messages",
    "resources",
    "users",
}


def create_schema_engine():
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine


def assert_rejected_schema_preserved(engine, table_name: str) -> None:
    tables = set(inspect(engine).get_table_names())
    assert table_name in tables
    assert TARGET_TABLES.isdisjoint(tables)
    with engine.connect() as connection:
        row = connection.execute(
            text(f'SELECT id, payload FROM "{table_name}"')
        ).one()
    assert tuple(row) == ("sentinel", "untouched")


def test_base_metadata_contains_only_agent_platform_tables() -> None:
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


def test_message_owner_must_match_conversation_owner() -> None:
    engine = create_schema_engine()
    with Session(engine) as session:
        alice = User(username="alice", password_hash="a")
        bob = User(username="bob", password_hash="b")
        agent = Resource(
            user_id=0, resource_type="agent", name="global", config_json={}
        )
        session.add_all([alice, bob, agent])
        session.flush()
        conversation = Conversation(user_id=alice.id, agent_id=agent.id)
        session.add(conversation)
        session.flush()
        session.add(
            Message(
                user_id=bob.id,
                conversation_id=conversation.id,
                sequence=1,
                role="user",
                status="completed",
                content="cross owner",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_message_sequence_is_unique_within_conversation() -> None:
    engine = create_schema_engine()
    with Session(engine) as session:
        user = User(username="alice", password_hash="a")
        agent = Resource(
            user_id=0, resource_type="agent", name="global", config_json={}
        )
        session.add_all([user, agent])
        session.flush()
        conversation = Conversation(user_id=user.id, agent_id=agent.id)
        session.add(conversation)
        session.flush()
        session.add_all(
            [
                Message(
                    user_id=user.id,
                    conversation_id=conversation.id,
                    sequence=1,
                    role="user",
                    status="completed",
                ),
                Message(
                    user_id=user.id,
                    conversation_id=conversation.id,
                    sequence=1,
                    role="assistant",
                    status="completed",
                ),
            ]
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_sequence_can_repeat_across_conversations() -> None:
    engine = create_schema_engine()
    with Session(engine) as session:
        user = User(username="alice", password_hash="a")
        agent = Resource(
            user_id=0, resource_type="agent", name="global", config_json={}
        )
        session.add_all([user, agent])
        session.flush()
        first_conversation = Conversation(user_id=user.id, agent_id=agent.id)
        second_conversation = Conversation(user_id=user.id, agent_id=agent.id)
        session.add_all([first_conversation, second_conversation])
        session.flush()
        session.add_all(
            [
                Message(
                    user_id=user.id,
                    conversation_id=first_conversation.id,
                    sequence=1,
                    role="user",
                    status="completed",
                ),
                Message(
                    user_id=user.id,
                    conversation_id=second_conversation.id,
                    sequence=1,
                    role="user",
                    status="completed",
                ),
            ]
        )
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
        message_columns = {column["name"] for column in inspector.get_columns("messages")}
        assert "sequence" in message_columns
        message_unique_constraints = {
            constraint["name"] for constraint in inspector.get_unique_constraints("messages")
        }
        assert message_unique_constraints == {
            "uq_messages_conversation_sequence",
            "uq_messages_id_user",
        }
        attachment_foreign_keys = inspector.get_foreign_keys("attachments")
        assert {
            (foreign_key["referred_table"], tuple(foreign_key["constrained_columns"]))
            for foreign_key in attachment_foreign_keys
        } == {
            ("users", ("user_id",)),
            ("messages", ("message_id", "user_id")),
        }

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
        attachment_indexes = {
            index["name"] for index in inspector.get_indexes("attachments")
        }
        assert attachment_indexes == {
            "ix_attachments_message_id",
            "ix_attachments_user_id",
        }

        command.downgrade(config, "20260713_0001")
        downgraded_columns = {
            column["name"] for column in inspect(engine).get_columns("attachments")
        }
        assert "parsed_size_bytes" not in downgraded_columns
        assert "parsed_content_hash" not in downgraded_columns
        downgraded_knowledge_columns = {
            column["name"]
            for column in inspect(engine).get_columns("knowledge_documents")
        }
        assert "processing_attempt_id" not in downgraded_knowledge_columns
        assert "cleanup_attempt_id" not in downgraded_knowledge_columns
        assert {
            index["name"] for index in inspect(engine).get_indexes("attachments")
        } == attachment_indexes
        command.upgrade(config, "head")

        command.downgrade(config, "base")
        assert set(inspect(engine).get_table_names()) == {"alembic_version"}
        assert not data_dir.exists()
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_new_baseline_refuses_old_revision_without_dropping_tables(
    tmp_path, monkeypatch
) -> None:
    database_url = f"sqlite:///{tmp_path / 'old.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()

    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
            )
            connection.execute(
                text("INSERT INTO alembic_version VALUES ('20260706_0011')")
            )
            connection.execute(
                text(
                    "CREATE TABLE custom_data "
                    "(id VARCHAR(36) PRIMARY KEY, payload VARCHAR(32) NOT NULL)"
                )
            )
            connection.execute(
                text("INSERT INTO custom_data VALUES ('sentinel', 'untouched')")
            )

        with pytest.raises(RuntimeError) as exc_info:
            command.upgrade(Config(ALEMBIC_INI), "head")

        message = str(exc_info.value)
        assert "old Alembic revision" in message
        assert "./reset-data.sh --yes" in message
        assert_rejected_schema_preserved(engine, "custom_data")
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_new_baseline_refuses_non_empty_unversioned_schema(
    tmp_path, monkeypatch
) -> None:
    database_url = f"sqlite:///{tmp_path / 'unversioned.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()

    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE custom_data "
                    "(id VARCHAR(36) PRIMARY KEY, payload VARCHAR(32) NOT NULL)"
                )
            )
            connection.execute(
                text("INSERT INTO custom_data VALUES ('sentinel', 'untouched')")
            )

        with pytest.raises(RuntimeError) as exc_info:
            command.upgrade(Config(ALEMBIC_INI), "head")

        message = str(exc_info.value)
        assert "non-empty unversioned schema" in message
        assert "./reset-data.sh --yes" in message
        assert_rejected_schema_preserved(engine, "custom_data")
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_new_baseline_refuses_empty_version_table_with_custom_data(
    tmp_path, monkeypatch
) -> None:
    database_url = f"sqlite:///{tmp_path / 'empty-version.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()

    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
            )
            connection.execute(
                text(
                    "CREATE TABLE custom_data "
                    "(id VARCHAR(36) PRIMARY KEY, payload VARCHAR(32) NOT NULL)"
                )
            )
            connection.execute(
                text("INSERT INTO custom_data VALUES ('sentinel', 'untouched')")
            )

        with pytest.raises(RuntimeError) as exc_info:
            command.upgrade(Config(ALEMBIC_INI), "head")

        message = str(exc_info.value)
        assert "non-empty unversioned schema" in message
        assert "./reset-data.sh --yes" in message
        assert_rejected_schema_preserved(engine, "custom_data")
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_new_baseline_refuses_empty_legacy_business_table(
    tmp_path, monkeypatch
) -> None:
    database_url = f"sqlite:///{tmp_path / 'legacy.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()

    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE interview_sessions "
                    "(id VARCHAR(36) PRIMARY KEY, payload VARCHAR(32) NOT NULL)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO interview_sessions VALUES ('sentinel', 'untouched')"
                )
            )

        with pytest.raises(RuntimeError, match="legacy schema"):
            command.upgrade(Config(ALEMBIC_INI), "head")

        assert_rejected_schema_preserved(engine, "interview_sessions")
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_new_baseline_refuses_memory_files_at_current_revision(
    tmp_path, monkeypatch
) -> None:
    database_url = f"sqlite:///{tmp_path / 'memory-files.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()

    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
            )
            connection.execute(
                text("INSERT INTO alembic_version VALUES ('20260713_0001')")
            )
            connection.execute(
                text(
                    "CREATE TABLE memory_files "
                    "(id VARCHAR(36) PRIMARY KEY, payload VARCHAR(32) NOT NULL)"
                )
            )
            connection.execute(
                text("INSERT INTO memory_files VALUES ('sentinel', 'untouched')")
            )

        with pytest.raises(RuntimeError, match="legacy schema"):
            command.upgrade(Config(ALEMBIC_INI), "head")

        assert_rejected_schema_preserved(engine, "memory_files")
    finally:
        engine.dispose()
        get_settings.cache_clear()


@pytest.mark.parametrize(
    "revision_value",
    [None, b"not-a-revision", "", "   "],
    ids=["null", "bytes", "empty", "whitespace"],
)
def test_new_baseline_refuses_invalid_revision_value(
    tmp_path, monkeypatch, revision_value
) -> None:
    database_url = f"sqlite:///{tmp_path / 'invalid-revision.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()

    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE alembic_version (version_num BLOB)"))
            connection.execute(
                text("INSERT INTO alembic_version VALUES (:revision_value)"),
                {"revision_value": revision_value},
            )

        with pytest.raises(RuntimeError) as exc_info:
            command.upgrade(Config(ALEMBIC_INI), "head")

        message = str(exc_info.value)
        assert "invalid Alembic revision" in message
        assert "./reset-data.sh --yes" in message
        assert set(inspect(engine).get_table_names()) == {"alembic_version"}
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
    assert "CREATE TABLE resources" in stdout
    assert "CREATE TABLE knowledge_documents" in stdout
    assert "CREATE TABLE conversations" in stdout
    assert "CREATE TABLE messages" in stdout
    assert "CREATE TABLE attachments" in stdout
    assert "ALTER TABLE attachments ADD COLUMN parsed_size_bytes INTEGER" in stdout
    assert (
        "ALTER TABLE attachments ADD COLUMN parsed_content_hash VARCHAR(128)"
        in stdout
    )
    assert (
        "ALTER TABLE knowledge_documents ADD COLUMN processing_attempt_id VARCHAR(36)"
        in stdout
    )
    assert (
        "ALTER TABLE knowledge_documents ADD COLUMN cleanup_attempt_id VARCHAR(36)"
        in stdout
    )
    assert "CONSTRAINT uq_resources_id_owner UNIQUE (id, user_id)" in stdout
    assert (
        "CONSTRAINT uq_resources_owner_type_name UNIQUE (user_id, resource_type, name)"
        in stdout
    )
    assert "CONSTRAINT uq_conversations_id_user UNIQUE (id, user_id)" in stdout
    assert "sequence INTEGER NOT NULL" in stdout
    assert "CONSTRAINT uq_messages_conversation_sequence UNIQUE (conversation_id, sequence)" in stdout
    assert "CONSTRAINT uq_messages_id_user UNIQUE (id, user_id)" in stdout
    assert (
        "FOREIGN KEY(collection_id, user_id) REFERENCES resources (id, user_id)"
        in stdout
    )
    assert "FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE" in stdout
    assert (
        "FOREIGN KEY(conversation_id, user_id) REFERENCES conversations (id, user_id) "
        "ON DELETE CASCADE"
    ) in stdout
    assert (
        "FOREIGN KEY(message_id, user_id) REFERENCES messages (id, user_id) "
        "ON DELETE CASCADE"
    ) in stdout
    assert "settings_json JSON NOT NULL" in stdout
    assert "metadata_json JSON NOT NULL" in stdout
    assert "config_json JSON NOT NULL" in stdout
    assert "JSON NOT NULL DEFAULT" not in stdout
    assert "DROP TABLE" not in stdout
    assert "ALTER TABLE artifacts" not in stdout
    assert "20260706_0011" not in stdout
