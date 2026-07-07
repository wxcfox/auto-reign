import os

import pytest
from sqlalchemy import create_engine, inspect


EXPECTED_TABLES = {
    "users",
    "artifacts",
    "conversations",
    "messages",
}

REMOVED_TABLES = {
    "interview_configs",
    "interview_sessions",
    "interview_turns",
    "learning_sessions",
    "learning_messages",
    "processing_jobs",
    "reports",
    "workspace_settings",
}


@pytest.mark.skipif(
    os.environ.get("RUN_MYSQL_INTEGRATION") != "1",
    reason="requires RUN_MYSQL_INTEGRATION=1 and a live MySQL service",
)
def test_mysql_schema_matches_expected_tables() -> None:
    database_url = os.environ["DATABASE_URL"]
    engine = create_engine(database_url)
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    assert EXPECTED_TABLES.issubset(tables)
    assert tables.isdisjoint(REMOVED_TABLES)
