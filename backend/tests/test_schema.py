from sqlalchemy import inspect

from app.core.config import get_settings
from app.db.session import create_engine_for_settings, init_db


def test_init_db_creates_required_tables(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CHROMA_DIR", str(tmp_path / "chroma"))
    get_settings.cache_clear()
    settings = get_settings()
    engine = create_engine_for_settings(settings)
    init_db(engine)
    tables = set(inspect(engine).get_table_names())
    assert {
        "documents",
        "document_chunks",
        "interview_configs",
        "interview_sessions",
        "interview_turns",
        "reports",
        "memory_files",
    }.issubset(tables)
