from pathlib import Path

import pytest

from app.repositories.artifact_repository import ArtifactRepository
from app.repositories.vector_store import VectorChunk, VectorStoreError, stable_vector_id
from app.repositories.workspace_settings_repository import WorkspaceSettingsRepository
from app.services.artifact_service import ArtifactService
from app.services.index_service import IndexService
from app.services.workspace_service import WorkspaceService


class RecordingVectorStore:
    def __init__(self, *, fail_upsert: bool = False, fail_delete_collection: bool = False) -> None:
        self.fail_upsert = fail_upsert
        self.fail_delete_collection = fail_delete_collection
        self.upserts: list[tuple[str, list[VectorChunk]]] = []
        self.deleted_documents: list[tuple[str, str]] = []
        self.deleted_collections: list[str] = []
        self.collections: set[str] = set()

    def upsert_chunks(self, collection_name: str, chunks: list[VectorChunk]) -> None:
        if self.fail_upsert:
            raise VectorStoreError("upsert failed")
        self.collections.add(collection_name)
        self.upserts.append((collection_name, chunks))

    def delete_document_chunks(self, collection_name: str, document_id: str) -> None:
        self.deleted_documents.append((collection_name, document_id))

    def has_searchable_content(self, collection_name: str) -> bool:
        return collection_name in self.collections

    def search(self, collection_name: str, query_embedding: list[float], limit: int):
        return []

    def delete_collection(self, collection_name: str) -> None:
        if self.fail_delete_collection:
            raise VectorStoreError("delete failed")
        self.deleted_collections.append(collection_name)
        self.collections.discard(collection_name)

    def list_collections(self) -> list[str]:
        return sorted(self.collections)


class FakeEmbedder:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def split_text(self, text: str, chunk_size: int = 900, overlap: int = 120) -> list[str]:
        del chunk_size, overlap
        return [chunk for chunk in text.split("\n\n") if chunk.strip()]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if self.fail:
            raise RuntimeError("embedding failed")
        return [[float(index + 1), 0.0] for index, _ in enumerate(texts)]


@pytest.fixture
def workspace_stack(tmp_path: Path):
    workspace = WorkspaceService(tmp_path / "workspace")
    workspace.initialize()
    artifacts = ArtifactService(workspace)
    repository = ArtifactRepository()
    return workspace, artifacts, repository


def _session(client):
    return client.app.state.session_factory()


def test_index_artifact_indexes_only_allowed_content(client, workspace_stack) -> None:
    workspace, artifacts, repository = workspace_stack
    text_source = artifacts.store_source(
        source_filename="notes.md",
        media_type="text/markdown",
        content=b"# Source note\n\nsource body",
    )
    binary_source = artifacts.store_source(
        source_filename="resume.pdf",
        media_type="application/pdf",
        content=b"%PDF",
    )
    artifacts.create_markdown("knowledge/python.md", kind="knowledge", body="# Python\n\nGIL")
    artifacts.create_markdown("reports/session.md", kind="report", body="# Report\n\nDo not index")
    artifacts.create_markdown(
        "knowledge/recovery.md",
        kind="knowledge",
        body="# Recovery\n",
        recovery_required=True,
        recovery_reason="manual repair required",
    )
    store = RecordingVectorStore()
    service = IndexService(vector_store=store, embedder=FakeEmbedder())

    with _session(client) as session:
        workspace.rebuild_projection(session, repository, artifacts)
        rows = repository.list(session)
        for row in rows:
            service.index_artifact(session, row, workspace)
        session.commit()

    indexed_ids = {
        chunk.metadata["artifact_id"] for _, chunks in store.upserts for chunk in chunks
    }
    assert text_source.artifact_id in indexed_ids
    assert binary_source.artifact_id not in indexed_ids
    assert {row.kind: row.index_status for row in rows}["report"] == "completed"
    assert all(chunk.metadata["source_refs"] == [] for _, chunks in store.upserts for chunk in chunks)


def test_index_artifact_marks_stale_when_build_fails_without_deleting_live_vectors(
    client, workspace_stack
) -> None:
    workspace, artifacts, repository = workspace_stack
    created = artifacts.create_markdown("knowledge/fail.md", kind="knowledge", body="# Fail\n")
    store = RecordingVectorStore()
    service = IndexService(vector_store=store, embedder=FakeEmbedder(fail=True))

    with _session(client) as session:
        workspace.rebuild_projection(session, repository, artifacts)
        row = repository.get(session, created.front_matter.id)
        service.index_artifact(session, row, workspace)
        session.commit()

    assert store.deleted_documents == []
    with _session(client) as session:
        assert repository.get(session, created.front_matter.id).index_status == "stale"


def test_rebuild_index_builds_new_collection_and_swaps_pointer(client, workspace_stack) -> None:
    workspace, artifacts, repository = workspace_stack
    created = artifacts.create_markdown("knowledge/swap.md", kind="knowledge", body="# Swap\n")
    store = RecordingVectorStore()
    store.collections.update({"auto_reign_test__old", "auto_reign_test__orphan"})
    service = IndexService(vector_store=store, embedder=FakeEmbedder())
    settings_repository = WorkspaceSettingsRepository()

    with _session(client) as session:
        workspace.rebuild_projection(session, repository, artifacts)
        settings = settings_repository.get_or_create(session)
        settings.active_collection = "auto_reign_test__old"
        session.commit()

    service.rebuild_index(
        client.app.state.session_factory,
        workspace,
        repository,
        settings_repository=settings_repository,
    )

    with _session(client) as session:
        settings = settings_repository.get_or_create(session)
        row = repository.get(session, created.front_matter.id)
        assert settings.active_collection.startswith("auto_reign_test__")
        assert settings.active_collection != "auto_reign_test__old"
        assert row.index_status == "completed"
    assert "auto_reign_test__old" in store.deleted_collections
    assert "auto_reign_test__orphan" in store.deleted_collections


def test_rebuild_index_failure_keeps_old_pointer_and_deletes_partial_collection(
    client, workspace_stack
) -> None:
    workspace, artifacts, repository = workspace_stack
    artifacts.create_markdown("knowledge/fail-rebuild.md", kind="knowledge", body="# Fail\n")
    store = RecordingVectorStore(fail_upsert=True)
    service = IndexService(vector_store=store, embedder=FakeEmbedder())
    settings_repository = WorkspaceSettingsRepository()

    with _session(client) as session:
        workspace.rebuild_projection(session, repository, artifacts)
        settings = settings_repository.get_or_create(session)
        settings.active_collection = "auto_reign_test__old"
        session.commit()

    with pytest.raises(VectorStoreError):
        service.rebuild_index(
            client.app.state.session_factory,
            workspace,
            repository,
            settings_repository=settings_repository,
        )

    with _session(client) as session:
        assert settings_repository.get_or_create(session).active_collection == "auto_reign_test__old"
    assert any(name.startswith("auto_reign_test__") for name in store.deleted_collections)


def test_stable_vector_id_is_used_for_workspace_artifacts(client, workspace_stack) -> None:
    workspace, artifacts, repository = workspace_stack
    created = artifacts.create_markdown("knowledge/id.md", kind="knowledge", body="# ID\n")
    store = RecordingVectorStore()
    service = IndexService(vector_store=store, embedder=FakeEmbedder())

    with _session(client) as session:
        workspace.rebuild_projection(session, repository, artifacts)
        row = repository.get(session, created.front_matter.id)
        service.index_artifact(session, row, workspace, collection_name="workspace")

    assert store.upserts[0][1][0].id == stable_vector_id("artifact", created.front_matter.id, 0)
