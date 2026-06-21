from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.core.config import get_settings

ALEMBIC_INI = Path(__file__).parents[1] / "alembic.ini"
APPLICATION_TABLES = {
    "documents",
    "document_chunks",
    "interview_configs",
    "interview_sessions",
    "interview_turns",
    "reports",
    "memory_files",
}


def test_migration_creates_and_drops_required_schema(tmp_path, monkeypatch) -> None:
    database_url = f"sqlite:///{tmp_path / 'migration.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()

    engine = create_engine(database_url)
    try:
        config = Config(ALEMBIC_INI)
        command.upgrade(config, "head")
        command.check(config)

        inspector = inspect(engine)
        assert set(inspector.get_table_names()) == APPLICATION_TABLES | {"alembic_version"}

        chunk_columns = {column["name"] for column in inspector.get_columns("document_chunks")}
        assert {"vector_collection", "vector_id"}.issubset(chunk_columns)
        assert {"chroma_collection", "chroma_id"}.isdisjoint(chunk_columns)

        turn_columns = {column["name"] for column in inspector.get_columns("interview_turns")}
        assert {
            "follow_up_feedback",
            "follow_up_missing_points",
            "follow_up_weaknesses",
            "follow_up_review_suggestions",
        }.issubset(turn_columns)

        chunk_uniques = {
            tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints("document_chunks")
        }
        assert ("vector_id",) in chunk_uniques

        chunk_foreign_key = next(
            foreign_key
            for foreign_key in inspector.get_foreign_keys("document_chunks")
            if foreign_key["constrained_columns"] == ["document_id"]
        )
        assert chunk_foreign_key["referred_table"] == "documents"
        assert chunk_foreign_key["referred_columns"] == ["id"]
        assert chunk_foreign_key["options"]["ondelete"] == "CASCADE"

        turn_foreign_key = next(
            foreign_key
            for foreign_key in inspector.get_foreign_keys("interview_turns")
            if foreign_key["constrained_columns"] == ["session_id"]
        )
        assert turn_foreign_key["referred_table"] == "interview_sessions"
        assert turn_foreign_key["referred_columns"] == ["id"]
        assert turn_foreign_key["options"]["ondelete"] == "CASCADE"

        command.downgrade(config, "base")
        assert set(inspect(engine).get_table_names()) == {"alembic_version"}
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_alembic_does_not_create_data_directories(tmp_path, monkeypatch) -> None:
    data_dir = tmp_path / "read-only-data"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'side-effect.db'}")
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    get_settings.cache_clear()

    try:
        assert not data_dir.exists()
        command.upgrade(Config(ALEMBIC_INI), "head")
        assert not data_dir.exists()
    finally:
        get_settings.cache_clear()


def test_mysql_offline_migration_does_not_set_json_defaults(monkeypatch, capsys) -> None:
    monkeypatch.setenv("DATABASE_URL", "mysql+pymysql://auto_reign:auto_reign@127.0.0.1:13306/auto_reign")
    get_settings.cache_clear()

    try:
        command.upgrade(Config(ALEMBIC_INI), "head", sql=True)
        stdout = capsys.readouterr().out
    finally:
        get_settings.cache_clear()

    assert "follow_up_missing_points JSON NOT NULL" in stdout
    assert "follow_up_weaknesses JSON NOT NULL" in stdout
    assert "follow_up_review_suggestions JSON NOT NULL" in stdout
    assert "JSON NOT NULL DEFAULT '[]'" not in stdout
