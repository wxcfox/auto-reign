from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

from scripts.reset_data import reset_data


class FakeStore:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.collections = {"v1", "workspace__1", "workspace__2", "other"}
        self.deleted: list[str] = []

    def delete_collection(self, collection_name: str) -> None:
        if self.fail:
            raise RuntimeError("qdrant down")
        self.deleted.append(collection_name)
        self.collections.discard(collection_name)

    def list_collections(self) -> list[str]:
        return sorted(self.collections)


def test_reset_backs_up_data_drops_reflected_tables_and_deletes_workspace_collections(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    (data_dir / "uploads").mkdir(parents=True)
    (data_dir / "workspace" / "knowledge").mkdir(parents=True)
    (data_dir / "uploads" / "old.txt").write_text("old", encoding="utf-8")
    (data_dir / "workspace" / "knowledge" / "old.md").write_text("old", encoding="utf-8")
    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE legacy_v1 (id INTEGER PRIMARY KEY)"))
        connection.execute(text("INSERT INTO legacy_v1 (id) VALUES (1)"))
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
    store = FakeStore()
    migrated = False

    def run_migrations() -> None:
        nonlocal migrated
        migrated = True

    backup = reset_data(
        data_dir=data_dir,
        engine=engine,
        qdrant_store=store,
        qdrant_collection="v1",
        workspace_collection="workspace",
        run_migrations=run_migrations,
    )

    assert backup is not None
    assert (backup / "uploads" / "old.txt").read_text(encoding="utf-8") == "old"
    assert not (data_dir / "uploads" / "old.txt").exists()
    assert (data_dir / "workspace" / "workspace.md").exists()
    assert inspect(engine).get_table_names() == []
    assert migrated is True
    assert store.deleted == ["v1", "workspace__1", "workspace__2"]


def test_reset_aborts_if_qdrant_delete_fails(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}")

    with pytest.raises(RuntimeError, match="qdrant down"):
        reset_data(
            data_dir=data_dir,
            engine=engine,
            qdrant_store=FakeStore(fail=True),
            qdrant_collection="v1",
            workspace_collection="workspace",
            run_migrations=lambda: None,
        )
