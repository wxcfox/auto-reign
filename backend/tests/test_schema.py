from sqlalchemy import inspect

from app.core.config import get_settings
from app.db.models import Base
from app.db.session import create_engine_for_settings


def test_metadata_creates_required_tables(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("QDRANT_URL", ":memory:")
    monkeypatch.setenv("QDRANT_COLLECTION", "auto_reign_test")
    get_settings.cache_clear()
    settings = get_settings()
    engine = create_engine_for_settings(settings)
    Base.metadata.create_all(bind=engine)
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

    columns = {
        column["name"]: column for column in inspect(engine).get_columns("document_chunks")
    }
    assert "chroma_collection" not in columns
    assert "chroma_id" not in columns
    assert columns["vector_collection"]["type"].length == 120
    assert columns["vector_id"]["type"].length == 255
    unique_columns = {
        tuple(constraint["column_names"])
        for constraint in inspect(engine).get_unique_constraints("document_chunks")
    }
    assert ("vector_id",) in unique_columns
    engine.dispose()
