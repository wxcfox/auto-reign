import pytest
from sqlalchemy import text

from app.db.migration_guards import assert_tables_empty


def test_assert_tables_empty_allows_absent_and_empty_tables(client) -> None:
    with client.app.state.session_factory() as session:
        connection = session.connection()
        connection.execute(text("CREATE TABLE empty_table (id INTEGER PRIMARY KEY)"))
        assert_tables_empty(connection, ["missing_table", "empty_table"])


def test_assert_tables_empty_blocks_non_empty_tables(client) -> None:
    with client.app.state.session_factory() as session:
        connection = session.connection()
        connection.execute(text("CREATE TABLE legacy_table (id INTEGER PRIMARY KEY)"))
        connection.execute(text("INSERT INTO legacy_table (id) VALUES (1)"))
        with pytest.raises(RuntimeError, match="scripts/reset_data.py"):
            assert_tables_empty(connection, ["legacy_table"])
