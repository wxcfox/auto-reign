from pathlib import Path

import pytest
from langchain_core.documents import Document

from app.repositories.artifact_repository import ArtifactRepository
from app.repositories.vector_store import VectorStoreUnavailable
from app.repositories.workspace_settings_repository import WorkspaceSettingsRepository
from app.services.artifact_service import ArtifactService
from app.services.index_service import IndexService
from app.services.workspace_service import WorkspaceService


class RecordingWorkspaceVectorStore:
    def __init__(
        self,
        *,
        fail_prepare: bool = False,
        fail_upsert: bool = False,
        fail_delete_collection: bool = False,
    ) -> None:
        self.fail_prepare = fail_prepare
        self.fail_upsert = fail_upsert
        self.fail_delete_collection = fail_delete_collection
        self.prepared: list[list[Document]] = []
        self.upserts: list[tuple[str, list[Document]]] = []
        self.deleted_artifacts: list[tuple[str, str]] = []
        self.deleted_collections: list[str] = []
        self.collections: set[str] = set()

    def prepare_documents(self, documents: list[Document]) -> None:
        if self.fail_prepare:
            raise VectorStoreUnavailable("prepare failed")
        self.prepared.append(documents)

    def upsert_documents(self, collection_name: str, documents: list[Document]) -> None:
        if self.fail_upsert:
            raise VectorStoreUnavailable("upsert failed")
        self.collections.add(collection_name)
        self.upserts.append((collection_name, documents))

    def delete_artifact_chunks(self, collection_name: str, artifact_id: str) -> None:
        self.deleted_artifacts.append((collection_name, artifact_id))

    def has_searchable_content(self, collection_name: str) -> bool:
        return collection_name in self.collections

    def search(self, collection_name: str, query: str, *, limit: int, metadata_filter=None):
        del collection_name, query, limit, metadata_filter
        return []

    def delete_collection(self, collection_name: str) -> None:
        if self.fail_delete_collection:
            raise VectorStoreUnavailable("delete failed")
        self.deleted_collections.append(collection_name)
        self.collections.discard(collection_name)

    def list_collections(self) -> list[str]:
        return sorted(self.collections)


class FailingTextSplitter:
    def split(self, documents: list[Document]) -> list[Document]:
        del documents
        raise RuntimeError("split failed")


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
    extracted = artifacts.create_markdown(
        "sources/extracted/resume.md",
        kind="extracted",
        body="# Extracted resume\n\npdf text",
    )
    knowledge = artifacts.create_markdown(
        "knowledge/python.md",
        kind="knowledge",
        body="# Python\n\nGIL",
        source_refs=[f"source:{text_source.artifact_id}"],
    )
    question_card = artifacts.create_markdown(
        "questions/cache-stampede.md",
        kind="question_bank",
        body="# Cache\n\ncache stampede standard answer",
    )
    project = artifacts.create_markdown(
        "projects/order-cache.md",
        kind="project",
        body="# Order cache\n\nhot key mitigation project",
    )
    interview_record = artifacts.create_markdown(
        "raw/20260625.md",
        kind="interview_record",
        body="# Interview\n\nRedis cache breakdown question",
    )
    high_frequency = artifacts.create_markdown(
        "review/high-frequency.md",
        kind="high_frequency",
        body="# High frequency\n\nTwo phase commit",
    )
    practice = artifacts.create_markdown(
        "practice/session-1.md",
        kind="practice",
        body="# Practice\n\nAnswer evidence",
    )
    report = artifacts.create_markdown(
        "reports/session.md",
        kind="report",
        body="# Report\n\nDo not index",
    )
    recovery = artifacts.create_markdown(
        "knowledge/recovery.md",
        kind="knowledge",
        body="# Recovery\n",
        recovery_required=True,
        recovery_reason="manual repair required",
    )
    store = RecordingWorkspaceVectorStore()
    service = IndexService(vector_store=store)

    with _session(client) as session:
        workspace.rebuild_projection(session, repository, artifacts)
        rows = repository.list(session)
        for row in rows:
            service.index_artifact(session, row, workspace)
        session.commit()

    indexed_ids = {
        document.metadata["artifact_id"]
        for _, documents in store.upserts
        for document in documents
    }
    assert {
        text_source.artifact_id,
        extracted.front_matter.id,
        knowledge.front_matter.id,
        question_card.front_matter.id,
        project.front_matter.id,
        interview_record.front_matter.id,
        high_frequency.front_matter.id,
        practice.front_matter.id,
    }.issubset(indexed_ids)
    assert binary_source.artifact_id not in indexed_ids
    assert report.front_matter.id not in indexed_ids
    assert recovery.front_matter.id not in indexed_ids

    documents = [document for _, upserted in store.upserts for document in upserted]
    assert all(isinstance(document, Document) for document in documents)
    assert "Do not index" not in {
        document.page_content for document in documents
    }
    knowledge_document = next(
        document
        for document in documents
        if document.metadata["artifact_id"] == knowledge.front_matter.id
    )
    assert knowledge_document.metadata["source_type"] == "artifact"
    assert knowledge_document.metadata["document_id"] == knowledge.front_matter.id
    assert knowledge_document.metadata["source_id"] == knowledge.front_matter.id
    assert knowledge_document.metadata["artifact_kind"] == "knowledge"
    assert knowledge_document.metadata["relative_path"] == "knowledge/python.md"
    assert knowledge_document.metadata["source_refs"] == [f"source:{text_source.artifact_id}"]
    assert knowledge_document.metadata["chunk_index"] == 0

    statuses = {row.id: row.index_status for row in rows}
    assert statuses[report.front_matter.id] == "completed"
    assert statuses[binary_source.artifact_id] == "completed"
    assert statuses[recovery.front_matter.id] == "stale"
    assert ("auto_reign_test", report.front_matter.id) in store.deleted_artifacts
    assert ("auto_reign_test", binary_source.artifact_id) in store.deleted_artifacts
    assert ("auto_reign_test", recovery.front_matter.id) not in store.deleted_artifacts


def test_index_artifact_marks_stale_when_build_fails_without_deleting_live_vectors(
    client, workspace_stack
) -> None:
    workspace, artifacts, repository = workspace_stack
    created = artifacts.create_markdown("knowledge/fail.md", kind="knowledge", body="# Fail\n")
    store = RecordingWorkspaceVectorStore()
    service = IndexService(vector_store=store, text_splitter=FailingTextSplitter())

    with _session(client) as session:
        workspace.rebuild_projection(session, repository, artifacts)
        row = repository.get(session, created.front_matter.id)
        service.index_artifact(session, row, workspace)
        session.commit()

    assert store.deleted_artifacts == []
    assert store.upserts == []
    with _session(client) as session:
        assert repository.get(session, created.front_matter.id).index_status == "stale"


def test_index_artifact_marks_stale_when_embedding_prepare_fails_without_deleting_vectors(
    client, workspace_stack
) -> None:
    workspace, artifacts, repository = workspace_stack
    created = artifacts.create_markdown("knowledge/prepare-fail.md", kind="knowledge", body="# Fail\n")
    store = RecordingWorkspaceVectorStore(fail_prepare=True)
    service = IndexService(vector_store=store)

    with _session(client) as session:
        workspace.rebuild_projection(session, repository, artifacts)
        row = repository.get(session, created.front_matter.id)
        service.index_artifact(session, row, workspace)
        session.commit()

    assert store.deleted_artifacts == []
    assert store.upserts == []
    with _session(client) as session:
        assert repository.get(session, created.front_matter.id).index_status == "stale"


def test_rebuild_index_builds_new_collection_and_swaps_pointer(client, workspace_stack) -> None:
    workspace, artifacts, repository = workspace_stack
    created = artifacts.create_markdown("knowledge/swap.md", kind="knowledge", body="# Swap\n")
    store = RecordingWorkspaceVectorStore()
    store.collections.update({"auto_reign_test__old", "auto_reign_test__orphan"})
    service = IndexService(vector_store=store)
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
    assert store.upserts
    assert all(isinstance(document, Document) for _, documents in store.upserts for document in documents)
    assert "auto_reign_test__old" in store.deleted_collections
    assert "auto_reign_test__orphan" in store.deleted_collections


def test_rebuild_index_failure_keeps_old_pointer_and_deletes_partial_collection(
    client, workspace_stack
) -> None:
    workspace, artifacts, repository = workspace_stack
    artifacts.create_markdown("knowledge/fail-rebuild.md", kind="knowledge", body="# Fail\n")
    store = RecordingWorkspaceVectorStore(fail_upsert=True)
    service = IndexService(vector_store=store)
    settings_repository = WorkspaceSettingsRepository()

    with _session(client) as session:
        workspace.rebuild_projection(session, repository, artifacts)
        settings = settings_repository.get_or_create(session)
        settings.active_collection = "auto_reign_test__old"
        session.commit()

    with pytest.raises(VectorStoreUnavailable):
        service.rebuild_index(
            client.app.state.session_factory,
            workspace,
            repository,
            settings_repository=settings_repository,
        )

    with _session(client) as session:
        assert settings_repository.get_or_create(session).active_collection == "auto_reign_test__old"
    assert any(name.startswith("auto_reign_test__") for name in store.deleted_collections)
    assert "auto_reign_test__old" not in store.deleted_collections
