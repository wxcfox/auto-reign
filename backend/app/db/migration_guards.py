from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection


def assert_tables_empty(connection: Connection, tables: list[str]) -> None:
    existing = set(inspect(connection).get_table_names())
    non_empty: list[str] = []
    for table in tables:
        if table not in existing:
            continue
        count = connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
        if int(count) > 0:
            non_empty.append(table)
    if non_empty:
        joined = ", ".join(non_empty)
        raise RuntimeError(
            f"Refusing to drop non-empty legacy tables: {joined}. "
            "Run `uv run python scripts/reset_data.py` for an explicit full reset."
        )
