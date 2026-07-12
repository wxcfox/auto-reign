from collections.abc import Collection

from sqlalchemy import Connection, inspect, text

LEGACY_TABLES = frozenset(
    {
        "artifacts",
        "document_chunks",
        "documents",
        "interview_configs",
        "interview_sessions",
        "interview_turns",
        "learning_messages",
        "learning_sessions",
        "memory_files",
        "processing_jobs",
        "reports",
        "workspace_settings",
    }
)


def assert_schema_compatible(
    connection: Connection, known_revisions: Collection[str]
) -> None:
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    legacy = sorted(tables & LEGACY_TABLES)
    if legacy:
        names = ", ".join(legacy)
        raise RuntimeError(
            "Refusing to migrate legacy schema; run ./reset-data.sh --yes explicitly. "
            f"Legacy tables: {names}"
        )
    if "alembic_version" not in tables:
        if tables:
            raise RuntimeError(
                "Refusing to apply the Agent baseline to a non-empty unversioned schema; "
                "run ./reset-data.sh --yes explicitly."
            )
        return
    revision_values = [
        row[0]
        for row in connection.execute(text("SELECT version_num FROM alembic_version"))
    ]
    if not revision_values:
        if tables != {"alembic_version"}:
            raise RuntimeError(
                "Refusing to apply the Agent baseline to a non-empty unversioned schema; "
                "run ./reset-data.sh --yes explicitly."
            )
        return
    if any(
        not isinstance(value, str) or not value.strip()
        for value in revision_values
    ):
        raise RuntimeError(
            "Refusing to migrate an invalid Alembic revision; "
            "run ./reset-data.sh --yes explicitly."
        )
    revisions = set(revision_values)
    unknown = sorted(revisions - set(known_revisions))
    if unknown:
        values = ", ".join(unknown)
        raise RuntimeError(
            "Refusing to migrate an old Alembic revision; run ./reset-data.sh --yes "
            f"explicitly. Revisions: {values}"
        )
