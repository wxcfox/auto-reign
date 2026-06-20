from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.core.config import get_settings


def test_migration_creates_required_tables(tmp_path, monkeypatch) -> None:
    database_url = f"sqlite:///{tmp_path / 'migration.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()

    config_path = Path(__file__).parents[1] / "alembic.ini"
    config = Config(config_path)
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    try:
        tables = set(inspect(engine).get_table_names())
        assert {
            "alembic_version",
            "documents",
            "document_chunks",
            "interview_configs",
            "interview_sessions",
            "interview_turns",
            "reports",
            "memory_files",
        }.issubset(tables)
    finally:
        engine.dispose()
        get_settings.cache_clear()
