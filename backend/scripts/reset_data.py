from __future__ import annotations

import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, text
from sqlalchemy.engine import Engine

from app.core.config import get_settings
from app.db.session import create_engine_for_settings
from app.services.workspace_vector_store import get_workspace_vector_store


def reset_data(
    *,
    data_dir: Path,
    engine: Engine,
    vector_store,
    qdrant_collection: str,
    workspace_collection: str,
    run_migrations: Callable[[], None],
) -> Path | None:
    backup = _backup_data_dir(data_dir)
    _delete_qdrant_collections(vector_store, qdrant_collection, workspace_collection)
    _drop_all_tables(engine)
    run_migrations()
    return backup


def _backup_data_dir(data_dir: Path) -> Path | None:
    if not data_dir.exists():
        data_dir.mkdir(parents=True, exist_ok=True)
        return None
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup = data_dir.with_name(f"{data_dir.name}.backup-{timestamp}")
    counter = 1
    while backup.exists():
        backup = data_dir.with_name(f"{data_dir.name}.backup-{timestamp}-{counter}")
        counter += 1
    shutil.move(str(data_dir), str(backup))
    data_dir.mkdir(parents=True, exist_ok=True)
    return backup


def _drop_all_tables(engine: Engine) -> None:
    metadata = MetaData()
    with engine.begin() as connection:
        metadata.reflect(bind=connection)
        metadata.drop_all(bind=connection)
        connection.execute(text("DROP TABLE IF EXISTS alembic_version"))


def _delete_qdrant_collections(
    vector_store, qdrant_collection: str, workspace_collection: str
) -> None:
    vector_store.delete_collection(qdrant_collection)
    prefixes = (f"{workspace_collection}__", "auto_reign_user_")
    for collection_name in vector_store.list_collections():
        if collection_name.startswith(prefixes):
            vector_store.delete_collection(collection_name)


def main() -> None:
    settings = get_settings()
    engine = create_engine_for_settings(settings)

    def run_migrations() -> None:
        command.upgrade(Config("alembic.ini"), "head")

    backup = reset_data(
        data_dir=settings.data_dir,
        engine=engine,
        vector_store=get_workspace_vector_store(),
        qdrant_collection=settings.qdrant_collection,
        workspace_collection=settings.qdrant_collection,
        run_migrations=run_migrations,
    )
    print(
        "Deleted local user data under "
        f"{settings.data_dir / 'users'} and legacy {settings.data_dir / 'workspace'} when present."
    )
    if backup is not None:
        print(f"Backed up old data to {backup}")
    print("Reset complete.")


if __name__ == "__main__":
    main()
