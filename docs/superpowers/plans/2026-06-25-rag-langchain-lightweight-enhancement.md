# RAG LangChain Lightweight Enhancement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace legacy document/RAG paths with workspace-only RAG, and use LangChain for splitter, embedding, Qdrant vectorstore, and retriever components while Auto Reign keeps ownership of workspace data semantics.

**Architecture:** Auto Reign continues to own artifact projection, provenance, active collection swapping, interview context priority, and prompt safety. LangChain is introduced behind small local services for `Document` building, Markdown-aware splitting, embeddings, Qdrant vectorstore access, and retriever execution. The implementation proceeds in small stages so the existing workspace interview flow keeps passing after each stage.

**Tech Stack:** FastAPI, SQLAlchemy 2, Alembic, Qdrant, LangChain, `langchain-openai`, `langchain-text-splitters`, `langchain-qdrant`, pytest, Ruff, Next.js.

---

## File Structure

Create:

- `backend/app/services/embedding_service.py`  
  LangChain `Embeddings` provider factory. Supports OpenAI, Qwen OpenAI-compatible base URL, and deterministic test embeddings.

- `backend/app/services/artifact_document_service.py`  
  Converts workspace artifact rows plus body text into LangChain `Document` objects and applies Markdown-aware splitting.

- `backend/app/services/workspace_vector_store.py`  
  Local wrapper around LangChain `QdrantVectorStore` plus direct Qdrant collection operations needed for rolling collections.

- `backend/app/services/retrieval_query_planner.py`  
  Deterministic query plan builder for question generation, answer feedback, follow-up feedback, project deep dive, and generic retrieval.

- `backend/app/services/retrieval_postprocessor.py`  
  Filters retrieved records by score, per-artifact cap, source diversity, and final prompt limit.

- `backend/app/services/context_assembler.py`  
  Applies prompt context budget and combines direct workspace context, project context, and retrieved snippets.

- `backend/alembic/versions/20260625_0005_drop_legacy_document_tables.py`  
  Drops legacy `documents` and `document_chunks` tables.

- `backend/tests/test_embedding_service.py`
- `backend/tests/test_artifact_document_service.py`
- `backend/tests/test_workspace_vector_store.py`
- `backend/tests/test_retrieval_query_planner.py`
- `backend/tests/test_retrieval_postprocessor.py`
- `backend/tests/test_context_assembler.py`
- `backend/tests/test_legacy_rag_removed.py`

Modify:

- `backend/pyproject.toml`, `backend/uv.lock`  
  Add `langchain-text-splitters` and `langchain-qdrant`.

- `backend/app/main.py`  
  Remove legacy documents and rag routers.

- `backend/app/db/models.py`  
  Remove `Document` and `DocumentChunk` models.

- `backend/app/repositories/database.py`  
  Remove `DocumentRepository` and `DocumentChunkRepository`.

- `backend/app/repositories/vector_store.py`  
  Rename document-oriented delete protocol to source-oriented semantics or keep a compatibility alias inside `workspace_vector_store.py`. Prefer moving new code off this protocol.

- `backend/app/repositories/qdrant_store.py`  
  Keep low-level Qdrant administrative operations as needed, but move retrieval/upsert usage to `WorkspaceVectorStore`.

- `backend/app/services/index_service.py`  
  Use `ArtifactDocumentService`, `EmbeddingService`, and `WorkspaceVectorStore`.

- `backend/app/services/workspace_retrieval_service.py`  
  Use `RetrievalQueryPlanner`, `WorkspaceVectorStore`, and `RetrievalPostProcessor`.

- `backend/app/services/interview_service.py`  
  Pass retrieval purpose and mode to workspace retrieval; remove unused `RagService` dependency.

- `backend/app/services/memory_service.py`  
  Remove vector indexing of reports and legacy memory files. Keep memory file generation/read endpoints.

- `backend/app/api/workspace.py`  
  Delete artifact chunks through the new vector store wrapper.

- `backend/tests/test_index_service.py`, `backend/tests/test_interviews.py`, `backend/tests/test_memory.py`, `backend/tests/test_workspace_api.py`, `backend/tests/test_schema.py`, `backend/tests/integration/test_mysql_schema.py`, `backend/tests/test_health_and_models.py`  
  Update expectations for workspace-only RAG and dropped legacy tables.

Delete:

- `backend/app/api/documents.py`
- `backend/app/api/rag.py`
- `backend/app/schemas/documents.py`
- `backend/app/services/document_service.py`
- `backend/app/services/rag_service.py`
- `backend/tests/test_documents.py`
- `backend/tests/test_rag.py`

Docs:

- `README.md`
- `docs/workbench-architecture.md`
- `docs/knowledge-data-flow.md`

---

### Task 1: Remove Legacy Document And RAG Surface

**Files:**
- Create: `backend/tests/test_legacy_rag_removed.py`
- Create: `backend/alembic/versions/20260625_0005_drop_legacy_document_tables.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/db/models.py`
- Modify: `backend/app/repositories/database.py`
- Modify: `backend/tests/test_schema.py`
- Modify: `backend/tests/integration/test_mysql_schema.py`
- Modify: `backend/tests/test_health_and_models.py`
- Delete: `backend/app/api/documents.py`
- Delete: `backend/app/api/rag.py`
- Delete: `backend/app/schemas/documents.py`
- Delete: `backend/app/services/document_service.py`
- Delete: `backend/tests/test_documents.py`
- Delete: `backend/tests/test_rag.py`

- [ ] **Step 1: Write failing tests that assert legacy APIs and tables are gone**

Create `backend/tests/test_legacy_rag_removed.py`:

```python
from fastapi.testclient import TestClient


def test_legacy_documents_api_is_not_registered(client: TestClient) -> None:
    response = client.get("/api/documents")

    assert response.status_code == 404


def test_legacy_rag_search_api_is_not_registered(client: TestClient) -> None:
    response = client.post("/api/rag/search", json={"query": "redis", "limit": 3})

    assert response.status_code == 404
```

Update `backend/tests/integration/test_mysql_schema.py` expected table set by removing `documents` and `document_chunks`:

```python
EXPECTED_TABLES = {
    "interview_configs",
    "interview_sessions",
    "interview_turns",
    "reports",
    "memory_files",
    "workspace_settings",
    "artifacts",
    "processing_jobs",
}
```

Update `backend/tests/test_schema.py` by deleting assertions that inspect `document_chunks` columns, unique constraints, and foreign keys. Keep assertions for active tables only:

```python
def test_database_schema_contains_current_tables(inspector) -> None:
    assert {
        "interview_configs",
        "interview_sessions",
        "interview_turns",
        "reports",
        "memory_files",
        "workspace_settings",
        "artifacts",
        "processing_jobs",
    }.issubset(set(inspector.get_table_names()))
```

- [ ] **Step 2: Run the targeted tests to verify failure**

Run:

```sh
cd backend
uv run pytest tests/test_legacy_rag_removed.py tests/test_schema.py tests/integration/test_mysql_schema.py -v
```

Expected: `test_legacy_documents_api_is_not_registered` and `test_legacy_rag_search_api_is_not_registered` fail because the routers are still registered.

- [ ] **Step 3: Remove legacy routers from the app**

Modify `backend/app/main.py` so the import block no longer imports `documents_router` or `rag_router`:

```python
from app.api.health import router as health_router
from app.api.interviews import router as interviews_router
from app.api.memory import router as memory_router
from app.api.models import router as models_router
from app.api.reports import router as reports_router
from app.api.workspace import router as workspace_router
```

Modify `create_app()` so these are the only included routers:

```python
    app.include_router(health_router)
    app.include_router(interviews_router)
    app.include_router(memory_router)
    app.include_router(models_router)
    app.include_router(reports_router)
    app.include_router(workspace_router)
```

- [ ] **Step 4: Delete legacy files**

Run:

```sh
git rm backend/app/api/documents.py backend/app/api/rag.py backend/app/schemas/documents.py backend/app/services/document_service.py backend/tests/test_documents.py backend/tests/test_rag.py
```

Expected: git stages the file removals.

- [ ] **Step 5: Remove legacy models**

In `backend/app/db/models.py`, remove these classes entirely:

```python
class Document(Base):
    __tablename__ = "documents"
    ...


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    ...
```

Also remove unused imports that only supported these classes:

```python
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text
```

Keep `ForeignKey` because other tables still use it.

- [ ] **Step 6: Remove legacy repositories**

In `backend/app/repositories/database.py`, remove the `Iterable` import and delete `DocumentRepository` and `DocumentChunkRepository`. The file should start with:

```python
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models
```

The first repository class should be `InterviewConfigRepository`.

- [ ] **Step 7: Add migration that drops legacy tables**

Create `backend/alembic/versions/20260625_0005_drop_legacy_document_tables.py`:

```python
"""drop legacy document tables

Revision ID: 20260625_0005
Revises: 20260622_0004
Create Date: 2026-06-25 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260625_0005"
down_revision: str | None = "20260622_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("document_chunks")
    op.drop_table("documents")


def downgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("collection", sa.String(length=120), nullable=False),
        sa.Column("source_filename", sa.String(length=255), nullable=False),
        sa.Column("file_path", sa.String(length=1024), nullable=False),
        sa.Column("file_type", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("knowledge_points", sa.JSON(), nullable=False),
        sa.Column("weakness_candidates", sa.JSON(), nullable=False),
        sa.Column("analysis_status", sa.String(length=32), nullable=False),
        sa.Column("index_status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("vector_collection", sa.String(length=120), nullable=False),
        sa.Column("vector_id", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("vector_id"),
    )
```

- [ ] **Step 8: Run targeted tests**

Run:

```sh
cd backend
uv run pytest tests/test_legacy_rag_removed.py tests/test_schema.py tests/integration/test_mysql_schema.py tests/test_health_and_models.py -v
```

Expected: all selected tests pass.

- [ ] **Step 9: Commit Task 1**

Run:

```sh
git add backend/app/main.py backend/app/db/models.py backend/app/repositories/database.py backend/alembic/versions/20260625_0005_drop_legacy_document_tables.py backend/tests/test_legacy_rag_removed.py backend/tests/test_schema.py backend/tests/integration/test_mysql_schema.py backend/tests/test_health_and_models.py
git add -u backend/app/api backend/app/schemas backend/app/services backend/tests
git commit -m "refactor: remove legacy document rag api"
```

Expected: commit succeeds.

---

### Task 2: Add LangChain Embedding Service

**Files:**
- Create: `backend/app/services/embedding_service.py`
- Create: `backend/tests/test_embedding_service.py`
- Modify: `backend/pyproject.toml`
- Modify: `backend/uv.lock`

- [ ] **Step 1: Add LangChain integration dependencies**

Run:

```sh
cd backend
uv add langchain-text-splitters langchain-qdrant
```

Expected: `backend/pyproject.toml` includes `langchain-text-splitters` and `langchain-qdrant`; `backend/uv.lock` changes.

- [ ] **Step 2: Write failing embedding tests**

Create `backend/tests/test_embedding_service.py`:

```python
from types import SimpleNamespace

from app.core.config import Settings
from app.services.embedding_service import DeterministicEmbeddings, EmbeddingService


def test_deterministic_embeddings_are_stable_and_normalized() -> None:
    embeddings = DeterministicEmbeddings(dimension=32)

    first = embeddings.embed_query("Redis cache stampede")
    second = embeddings.embed_query("Redis cache stampede")

    assert first == second
    assert len(first) == 32
    assert round(sum(value * value for value in first), 6) == 1.0


def test_embedding_service_uses_deterministic_fallback(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'app.db'}",
        qdrant_url=":memory:",
        deterministic_model_fallback=True,
    )
    service = EmbeddingService(settings=settings)

    assert service.embed_documents(["one", "two"]) == service.embed_documents(["one", "two"])
    assert service.embed_query("one") == service.embed_documents(["one"])[0]


def test_embedding_service_configures_qwen_openai_compatible_client(monkeypatch, tmp_path) -> None:
    calls: list[dict[str, object]] = []

    class FakeOpenAIEmbeddings:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def embed_documents(self, texts):
            return [[float(index), 0.0] for index, _ in enumerate(texts)]

        def embed_query(self, text):
            return [1.0, 0.0]

    monkeypatch.setattr(
        "app.services.embedding_service.OpenAIEmbeddings",
        FakeOpenAIEmbeddings,
    )
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'app.db'}",
        qdrant_url=":memory:",
        deterministic_model_fallback=False,
        embedding_provider="qwen",
        embedding_model="text-embedding-v4",
        qwen_api_key="qwen-secret",
        qwen_base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    )

    service = EmbeddingService(settings=settings)

    assert service.embed_query("redis") == [1.0, 0.0]
    assert calls == [
        {
            "model": "text-embedding-v4",
            "api_key": "qwen-secret",
            "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        }
    ]
```

- [ ] **Step 3: Run embedding tests to verify failure**

Run:

```sh
cd backend
uv run pytest tests/test_embedding_service.py -v
```

Expected: FAIL because `app.services.embedding_service` does not exist.

- [ ] **Step 4: Implement embedding service**

Create `backend/app/services/embedding_service.py`:

```python
from __future__ import annotations

import hashlib
import math
import re

from fastapi import HTTPException
from langchain_core.embeddings import Embeddings
from langchain_openai import OpenAIEmbeddings

from app.core.config import Settings, get_settings
from app.core.errors import service_unavailable


class DeterministicEmbeddings(Embeddings):
    def __init__(self, dimension: int = 32) -> None:
        self.dimension = dimension

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        words = re.findall(r"[A-Za-z][A-Za-z0-9_-]*", text.lower())
        for word in words or [text.lower()]:
            digest = hashlib.sha256(word.encode("utf-8")).digest()
            index = digest[0] % len(vector)
            sign = 1.0 if digest[1] % 2 == 0 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


class EmbeddingService:
    def __init__(
        self,
        settings: Settings | None = None,
        embeddings: Embeddings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._embeddings = embeddings

    @property
    def embeddings(self) -> Embeddings:
        if self._embeddings is None:
            self._embeddings = self._build_embeddings()
        return self._embeddings

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return self.embeddings.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        return self.embeddings.embed_query(text)

    def _build_embeddings(self) -> Embeddings:
        if self.settings.deterministic_model_fallback:
            return DeterministicEmbeddings()
        provider_config = self._resolve_provider()
        if provider_config is None:
            raise service_unavailable(
                "embedding_provider_not_configured",
                "The configured embedding provider is not available.",
            )
        try:
            api_key, base_url = provider_config
            kwargs: dict[str, str] = {
                "model": self.settings.embedding_model,
                "api_key": api_key,
            }
            if base_url:
                kwargs["base_url"] = base_url
            return OpenAIEmbeddings(**kwargs)
        except HTTPException:
            raise
        except Exception as exc:
            raise service_unavailable(
                "embedding_provider_not_configured",
                "The configured embedding provider is not available.",
            ) from exc

    def _resolve_provider(self) -> tuple[str, str | None] | None:
        if self.settings.embedding_provider == "openai":
            if not self.settings.openai_api_key:
                return None
            return self.settings.openai_api_key, None
        if self.settings.embedding_provider == "qwen":
            if not self.settings.qwen_api_key:
                return None
            return self.settings.qwen_api_key, self.settings.qwen_base_url
        return None
```

- [ ] **Step 5: Run embedding tests**

Run:

```sh
cd backend
uv run pytest tests/test_embedding_service.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit Task 2**

Run:

```sh
git add backend/pyproject.toml backend/uv.lock backend/app/services/embedding_service.py backend/tests/test_embedding_service.py
git commit -m "feat: add langchain embedding service"
```

Expected: commit succeeds.

---

### Task 3: Add Artifact Document Builder And Markdown-Aware Splitter

**Files:**
- Create: `backend/app/services/artifact_document_service.py`
- Create: `backend/tests/test_artifact_document_service.py`

- [ ] **Step 1: Write failing document builder and splitter tests**

Create `backend/tests/test_artifact_document_service.py`:

```python
from datetime import UTC, datetime
from types import SimpleNamespace

from app.services.artifact_document_service import (
    ArtifactDocumentBuilder,
    ArtifactTextSplitter,
)


def artifact(**overrides):
    values = {
        "id": "artifact-1",
        "kind": "knowledge",
        "relative_path": "knowledge/redis.md",
        "revision": 3,
        "source_refs": ["source:abc"],
        "evidence_refs": [],
        "language": "zh-CN",
        "origin": "human",
        "edited_by": "user",
        "created_at": datetime(2026, 6, 25, tzinfo=UTC),
        "updated_at": datetime(2026, 6, 25, tzinfo=UTC),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_builder_preserves_artifact_metadata() -> None:
    document = ArtifactDocumentBuilder().build(artifact(), "# Redis\n\n缓存击穿")

    assert document.page_content == "# Redis\n\n缓存击穿"
    assert document.metadata["artifact_id"] == "artifact-1"
    assert document.metadata["artifact_kind"] == "knowledge"
    assert document.metadata["relative_path"] == "knowledge/redis.md"
    assert document.metadata["source_refs"] == ["source:abc"]
    assert document.metadata["revision"] == 3


def test_markdown_splitter_preserves_header_metadata() -> None:
    document = ArtifactDocumentBuilder().build(
        artifact(),
        "# Redis\n\n## 缓存击穿\n\n互斥锁和逻辑过期。\n\n## 缓存穿透\n\n布隆过滤器。",
    )

    chunks = ArtifactTextSplitter(chunk_size=80, chunk_overlap=10).split([document])

    assert len(chunks) >= 2
    assert all(chunk.metadata["artifact_id"] == "artifact-1" for chunk in chunks)
    assert {chunk.metadata.get("h2") for chunk in chunks} >= {"缓存击穿", "缓存穿透"}
    assert all("chunk_index" in chunk.metadata for chunk in chunks)
```

- [ ] **Step 2: Run document service tests to verify failure**

Run:

```sh
cd backend
uv run pytest tests/test_artifact_document_service.py -v
```

Expected: FAIL because `artifact_document_service.py` does not exist.

- [ ] **Step 3: Implement artifact document service**

Create `backend/app/services/artifact_document_service.py`:

```python
from __future__ import annotations

from typing import Any

from langchain_core.documents import Document
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter


class ArtifactDocumentBuilder:
    def build(self, artifact: Any, body: str) -> Document:
        return Document(
            page_content=body,
            metadata={
                "artifact_id": artifact.id,
                "source_id": artifact.id,
                "document_id": artifact.id,
                "artifact_kind": artifact.kind,
                "source_type": "artifact",
                "relative_path": artifact.relative_path,
                "revision": artifact.revision,
                "source_refs": list(artifact.source_refs or []),
                "evidence_refs": list(artifact.evidence_refs or []),
                "language": artifact.language,
            },
        )


class ArtifactTextSplitter:
    def __init__(self, *, chunk_size: int = 900, chunk_overlap: int = 120) -> None:
        self.markdown_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=[
                ("#", "h1"),
                ("##", "h2"),
                ("###", "h3"),
                ("####", "h4"),
            ],
            strip_headers=False,
        )
        self.recursive_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=["\n\n", "\n", "。", ".", " ", ""],
        )

    def split(self, documents: list[Document]) -> list[Document]:
        chunks: list[Document] = []
        for document in documents:
            section_docs = self._split_markdown(document)
            for section_doc in section_docs:
                chunks.extend(self.recursive_splitter.split_documents([section_doc]))
        for index, chunk in enumerate(chunks):
            chunk.metadata = {**chunk.metadata, "chunk_index": index}
        return chunks

    def _split_markdown(self, document: Document) -> list[Document]:
        try:
            sections = self.markdown_splitter.split_text(document.page_content)
        except Exception:
            sections = []
        if not sections:
            return [document]
        return [
            Document(
                page_content=section.page_content,
                metadata={**document.metadata, **section.metadata},
            )
            for section in sections
        ]
```

- [ ] **Step 4: Run document service tests**

Run:

```sh
cd backend
uv run pytest tests/test_artifact_document_service.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit Task 3**

Run:

```sh
git add backend/app/services/artifact_document_service.py backend/tests/test_artifact_document_service.py
git commit -m "feat: add artifact document splitter"
```

Expected: commit succeeds.

---

### Task 4: Add LangChain Qdrant Workspace Vector Store

**Files:**
- Create: `backend/app/services/workspace_vector_store.py`
- Create: `backend/tests/test_workspace_vector_store.py`
- Modify: `backend/app/repositories/qdrant_store.py` only if shared Qdrant client construction should move to a helper.

- [ ] **Step 1: Write failing vector store tests**

Create `backend/tests/test_workspace_vector_store.py`:

```python
from langchain_core.documents import Document

from app.services.embedding_service import DeterministicEmbeddings
from app.services.workspace_vector_store import WorkspaceVectorStore


class FakeLangChainQdrant:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.added = []
        FakeLangChainQdrant.instances.append(self)

    def add_documents(self, documents, ids):
        self.added.append((documents, ids))

    def similarity_search_with_score(self, query, k, filter=None):
        return [
            (
                Document(
                    page_content="Redis cache stampede",
                    metadata={"artifact_id": "a1", "artifact_kind": "knowledge"},
                ),
                0.88,
            )
        ]


class FakeQdrantClient:
    def __init__(self):
        self.deleted_collections = []
        self.collection_names = ["auto_reign_test__1"]

    def delete_collection(self, collection_name):
        self.deleted_collections.append(collection_name)

    def get_collections(self):
        class Collection:
            def __init__(self, name):
                self.name = name

        class Response:
            collections = [Collection("auto_reign_test__1")]

        return Response()

    def collection_exists(self, collection_name):
        return collection_name in self.collection_names

    def count(self, collection_name, exact=False):
        class Count:
            count = 1

        return Count()


def test_upsert_documents_uses_langchain_qdrant(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.workspace_vector_store.QdrantVectorStore",
        FakeLangChainQdrant,
    )
    store = WorkspaceVectorStore(
        client=FakeQdrantClient(),
        embeddings=DeterministicEmbeddings(),
    )
    documents = [
        Document(page_content="body", metadata={"artifact_id": "a1", "chunk_index": 0})
    ]

    store.upsert_documents("auto_reign_test__1", documents)

    instance = FakeLangChainQdrant.instances[-1]
    assert instance.kwargs["collection_name"] == "auto_reign_test__1"
    assert instance.added[0][1] == ["a1:0"]


def test_search_maps_langchain_results(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.workspace_vector_store.QdrantVectorStore",
        FakeLangChainQdrant,
    )
    store = WorkspaceVectorStore(
        client=FakeQdrantClient(),
        embeddings=DeterministicEmbeddings(),
    )

    hits = store.search("auto_reign_test__1", "Redis", limit=4)

    assert hits[0].content == "Redis cache stampede"
    assert hits[0].score == 0.88
    assert hits[0].metadata["artifact_id"] == "a1"
```

- [ ] **Step 2: Run vector store tests to verify failure**

Run:

```sh
cd backend
uv run pytest tests/test_workspace_vector_store.py -v
```

Expected: FAIL because `workspace_vector_store.py` does not exist.

- [ ] **Step 3: Implement workspace vector store**

Create `backend/app/services/workspace_vector_store.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from qdrant_client.http.models import FieldCondition, Filter, FilterSelector, MatchValue

from app.core.config import Settings, get_settings
from app.repositories.vector_store import VectorStoreError, VectorStoreUnavailable
from app.services.embedding_service import EmbeddingService


@dataclass(frozen=True)
class WorkspaceVectorHit:
    content: str
    score: float
    metadata: dict[str, Any]


class WorkspaceVectorStore:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        client: QdrantClient | None = None,
        embeddings: Embeddings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.client = client or self._build_client()
        self.embeddings = embeddings or EmbeddingService(self.settings).embeddings

    def upsert_documents(self, collection_name: str, documents: list[Document]) -> None:
        if not documents:
            return
        ids = [self._document_id(document) for document in documents]
        try:
            self._vector_store(collection_name).add_documents(documents=documents, ids=ids)
        except Exception as exc:
            raise VectorStoreUnavailable("Qdrant LangChain upsert failed") from exc

    def search(
        self,
        collection_name: str,
        query: str,
        *,
        limit: int,
        metadata_filter: Filter | None = None,
    ) -> list[WorkspaceVectorHit]:
        if not self.has_searchable_content(collection_name):
            return []
        try:
            results = self._vector_store(collection_name).similarity_search_with_score(
                query,
                k=limit,
                filter=metadata_filter,
            )
        except Exception as exc:
            raise VectorStoreUnavailable("Qdrant LangChain search failed") from exc
        return [
            WorkspaceVectorHit(
                content=document.page_content,
                score=float(score),
                metadata=dict(document.metadata or {}),
            )
            for document, score in results
        ]

    def delete_artifact_chunks(self, collection_name: str, artifact_id: str) -> None:
        if not self._collection_exists(collection_name):
            return
        selector = FilterSelector(
            filter=Filter(
                must=[
                    FieldCondition(
                        key="metadata.artifact_id",
                        match=MatchValue(value=artifact_id),
                    )
                ]
            )
        )
        try:
            self.client.delete(collection_name=collection_name, points_selector=selector, wait=True)
        except Exception as exc:
            raise VectorStoreUnavailable("Qdrant delete failed") from exc

    def delete_collection(self, collection_name: str) -> None:
        if not self._collection_exists(collection_name):
            return
        try:
            self.client.delete_collection(collection_name=collection_name)
        except Exception as exc:
            raise VectorStoreUnavailable("Qdrant delete collection failed") from exc

    def list_collections(self) -> list[str]:
        try:
            response = self.client.get_collections()
            return [collection.name for collection in response.collections]
        except Exception as exc:
            raise VectorStoreUnavailable("Qdrant list collections failed") from exc

    def has_searchable_content(self, collection_name: str) -> bool:
        if not self._collection_exists(collection_name):
            return False
        try:
            response = self.client.count(collection_name=collection_name, exact=False)
            return int(response.count or 0) > 0
        except Exception as exc:
            raise VectorStoreUnavailable("Qdrant count failed") from exc

    def _vector_store(self, collection_name: str) -> QdrantVectorStore:
        return QdrantVectorStore(
            client=self.client,
            collection_name=collection_name,
            embedding=self.embeddings,
        )

    def _build_client(self) -> QdrantClient:
        if self.settings.qdrant_url == ":memory:":
            return QdrantClient(location=":memory:")
        return QdrantClient(url=self.settings.qdrant_url)

    def _collection_exists(self, collection_name: str) -> bool:
        try:
            return bool(self.client.collection_exists(collection_name=collection_name))
        except Exception as exc:
            raise VectorStoreUnavailable("Qdrant collection_exists failed") from exc

    def _document_id(self, document: Document) -> str:
        metadata = document.metadata or {}
        artifact_id = str(metadata.get("artifact_id") or metadata.get("source_id") or "artifact")
        chunk_index = str(metadata.get("chunk_index", "0"))
        return f"{artifact_id}:{chunk_index}"
```

- [ ] **Step 4: Run vector store tests**

Run:

```sh
cd backend
uv run pytest tests/test_workspace_vector_store.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit Task 4**

Run:

```sh
git add backend/app/services/workspace_vector_store.py backend/tests/test_workspace_vector_store.py
git commit -m "feat: add langchain qdrant workspace store"
```

Expected: commit succeeds.

---

### Task 5: Rewire IndexService To LangChain Components

**Files:**
- Modify: `backend/app/services/index_service.py`
- Modify: `backend/app/api/workspace.py`
- Modify: `backend/tests/test_index_service.py`
- Modify: `backend/tests/test_workspace_api.py`

- [ ] **Step 1: Write failing index service tests for LangChain documents**

In `backend/tests/test_index_service.py`, replace vector chunk assertions with document assertions using a fake workspace vector store:

```python
class RecordingWorkspaceVectorStore:
    def __init__(self, *, fail_upsert: bool = False, fail_delete_collection: bool = False) -> None:
        self.fail_upsert = fail_upsert
        self.fail_delete_collection = fail_delete_collection
        self.upserts: list[tuple[str, list[object]]] = []
        self.deleted_artifacts: list[tuple[str, str]] = []
        self.deleted_collections: list[str] = []
        self.collections: set[str] = set()

    def upsert_documents(self, collection_name: str, documents: list[object]) -> None:
        if self.fail_upsert:
            from app.repositories.vector_store import VectorStoreError

            raise VectorStoreError("upsert failed")
        self.collections.add(collection_name)
        self.upserts.append((collection_name, documents))

    def delete_artifact_chunks(self, collection_name: str, artifact_id: str) -> None:
        self.deleted_artifacts.append((collection_name, artifact_id))

    def has_searchable_content(self, collection_name: str) -> bool:
        return collection_name in self.collections

    def search(self, collection_name: str, query: str, *, limit: int, metadata_filter=None):
        return []

    def delete_collection(self, collection_name: str) -> None:
        if self.fail_delete_collection:
            from app.repositories.vector_store import VectorStoreError

            raise VectorStoreError("delete failed")
        self.deleted_collections.append(collection_name)
        self.collections.discard(collection_name)

    def list_collections(self) -> list[str]:
        return sorted(self.collections)
```

Update the existing index assertions:

```python
indexed_ids = {
    document.metadata["artifact_id"]
    for _, documents in store.upserts
    for document in documents
}
assert text_source.artifact_id in indexed_ids
assert question_card.front_matter.id in indexed_ids
assert project.front_matter.id in indexed_ids
assert binary_source.artifact_id not in indexed_ids
assert "Do not index" not in {
    document.page_content for _, documents in store.upserts for document in documents
}
```

- [ ] **Step 2: Run index tests to verify failure**

Run:

```sh
cd backend
uv run pytest tests/test_index_service.py tests/test_workspace_api.py::test_workspace_artifact_delete_keeps_file_when_vector_delete_fails -v
```

Expected: FAIL because `IndexService` still expects `VectorChunk` upserts and `delete_document_chunks`.

- [ ] **Step 3: Modify IndexService constructor and dependencies**

In `backend/app/services/index_service.py`, replace the `RagService` import and vector chunk imports with:

```python
from langchain_core.documents import Document

from app.services.artifact_document_service import ArtifactDocumentBuilder, ArtifactTextSplitter
from app.services.workspace_vector_store import WorkspaceVectorStore
```

Change `IndexService.__init__`:

```python
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        vector_store: WorkspaceVectorStore | None = None,
        document_builder: ArtifactDocumentBuilder | None = None,
        text_splitter: ArtifactTextSplitter | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.vector_store = vector_store or WorkspaceVectorStore(settings=self.settings)
        self.document_builder = document_builder or ArtifactDocumentBuilder()
        self.text_splitter = text_splitter or ArtifactTextSplitter()
```

- [ ] **Step 4: Replace artifact chunk building with LangChain documents**

Replace `_build_chunks_for_artifact` with `_build_documents_for_artifact`:

```python
    def _build_documents_for_artifact(
        self, artifact: models.Artifact, workspace: WorkspaceService
    ) -> "_BuildResult":
        if artifact.recovery_required or artifact.processing_status == "needs_recovery":
            return _BuildResult(status="stale", documents=[])
        text = self._read_indexable_text(artifact, workspace)
        if text is None:
            return _BuildResult(status="completed", documents=[])
        document = self.document_builder.build(artifact, text)
        documents = self.text_splitter.split([document])
        return _BuildResult(status="completed", documents=documents)
```

Update call sites:

```python
build = self._build_documents_for_artifact(artifact, workspace)
...
if build.documents:
    self.vector_store.upsert_documents(new_collection, build.documents)
```

Update `index_artifact` to delete and upsert artifact chunks:

```python
self.vector_store.delete_artifact_chunks(target_collection, artifact.id)
if build.documents:
    self.vector_store.upsert_documents(target_collection, build.documents)
```

Replace `_BuildResult`:

```python
class _BuildResult:
    def __init__(self, *, status: str, documents: list[Document]) -> None:
        self.status = status
        self.documents = documents
```

- [ ] **Step 5: Modify workspace artifact deletion**

In `backend/app/api/workspace.py`, replace:

```python
index_service.vector_store.delete_document_chunks(target_collection, artifact_id)
```

with:

```python
index_service.vector_store.delete_artifact_chunks(target_collection, artifact_id)
```

- [ ] **Step 6: Run index and workspace API tests**

Run:

```sh
cd backend
uv run pytest tests/test_index_service.py tests/test_workspace_api.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit Task 5**

Run:

```sh
git add backend/app/services/index_service.py backend/app/api/workspace.py backend/tests/test_index_service.py backend/tests/test_workspace_api.py
git commit -m "refactor: index workspace artifacts with langchain"
```

Expected: commit succeeds.

---

### Task 6: Add Retrieval Query Planner And Postprocessor

**Files:**
- Create: `backend/app/services/retrieval_query_planner.py`
- Create: `backend/app/services/retrieval_postprocessor.py`
- Create: `backend/tests/test_retrieval_query_planner.py`
- Create: `backend/tests/test_retrieval_postprocessor.py`

- [ ] **Step 1: Write failing query planner tests**

Create `backend/tests/test_retrieval_query_planner.py`:

```python
from app.services.retrieval_query_planner import RetrievalQueryPlanner, RetrievalRequest


def test_question_generation_plan_prefers_interview_material() -> None:
    plan = RetrievalQueryPlanner().plan(
        RetrievalRequest(
            purpose="question_generation",
            query="字节后端岗位 JD 关注 Redis 高并发",
            mode="comprehensive",
            limit=4,
        )
    )

    assert plan.semantic_query == "字节后端岗位 JD 关注 Redis 高并发"
    assert plan.candidate_limit == 12
    assert plan.final_limit == 4
    assert plan.artifact_kinds == ("question_bank", "knowledge", "project", "high_frequency")


def test_project_deep_dive_plan_filters_projects_first() -> None:
    plan = RetrievalQueryPlanner().plan(
        RetrievalRequest(
            purpose="question_generation",
            query="订单缓存项目",
            mode="project_deep_dive",
            limit=4,
        )
    )

    assert plan.artifact_kinds == ("project", "knowledge", "practice")
    assert "项目" in plan.semantic_query


def test_answer_feedback_plan_uses_answer_context() -> None:
    plan = RetrievalQueryPlanner().plan(
        RetrievalRequest(
            purpose="answer_feedback",
            query="Redis 缓存击穿 我使用互斥锁",
            mode="comprehensive",
            limit=4,
        )
    )

    assert plan.artifact_kinds == (
        "knowledge",
        "question_bank",
        "project",
        "high_frequency",
        "practice",
    )
    assert plan.score_threshold == 0.25
```

- [ ] **Step 2: Write failing postprocessor tests**

Create `backend/tests/test_retrieval_postprocessor.py`:

```python
from app.services.retrieval_postprocessor import RetrievalPostProcessor
from app.services.retrieval_query_planner import RetrievalQueryPlan
from app.services.workspace_vector_store import WorkspaceVectorHit


def plan() -> RetrievalQueryPlan:
    return RetrievalQueryPlan(
        semantic_query="redis",
        artifact_kinds=("knowledge", "question_bank", "project"),
        candidate_limit=10,
        final_limit=3,
        score_threshold=0.5,
        max_per_artifact=1,
        purpose="question_generation",
    )


def hit(content: str, score: float, artifact_id: str, kind: str) -> WorkspaceVectorHit:
    return WorkspaceVectorHit(
        content=content,
        score=score,
        metadata={"artifact_id": artifact_id, "artifact_kind": kind, "source_type": "artifact"},
    )


def test_postprocessor_filters_low_scores_and_caps_per_artifact() -> None:
    hits = [
        hit("a1 first", 0.9, "a1", "knowledge"),
        hit("a1 second", 0.8, "a1", "knowledge"),
        hit("a2", 0.7, "a2", "project"),
        hit("low", 0.2, "a3", "question_bank"),
    ]

    processed = RetrievalPostProcessor().process(hits, plan())

    assert [item.content for item in processed] == ["a1 first", "a2"]


def test_postprocessor_keeps_multiple_kinds_when_available() -> None:
    hits = [
        hit("k1", 0.9, "a1", "knowledge"),
        hit("k2", 0.89, "a2", "knowledge"),
        hit("q1", 0.88, "a3", "question_bank"),
        hit("p1", 0.87, "a4", "project"),
    ]

    processed = RetrievalPostProcessor().process(hits, plan())

    assert {item.metadata["artifact_kind"] for item in processed} == {
        "knowledge",
        "question_bank",
        "project",
    }
```

- [ ] **Step 3: Run planner and postprocessor tests to verify failure**

Run:

```sh
cd backend
uv run pytest tests/test_retrieval_query_planner.py tests/test_retrieval_postprocessor.py -v
```

Expected: FAIL because the modules do not exist.

- [ ] **Step 4: Implement retrieval query planner**

Create `backend/app/services/retrieval_query_planner.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


RetrievalPurpose = Literal[
    "question_generation",
    "answer_feedback",
    "follow_up_feedback",
    "generic",
]


@dataclass(frozen=True)
class RetrievalRequest:
    purpose: RetrievalPurpose
    query: str
    mode: str = "comprehensive"
    limit: int = 4


@dataclass(frozen=True)
class RetrievalQueryPlan:
    semantic_query: str
    artifact_kinds: tuple[str, ...]
    candidate_limit: int
    final_limit: int
    score_threshold: float
    max_per_artifact: int
    purpose: RetrievalPurpose


class RetrievalQueryPlanner:
    def plan(self, request: RetrievalRequest) -> RetrievalQueryPlan:
        query = request.query.strip()
        final_limit = max(1, request.limit)
        if request.mode == "project_deep_dive":
            return RetrievalQueryPlan(
                semantic_query=f"projects 项目 项目经历 {query}".strip(),
                artifact_kinds=("project", "knowledge", "practice"),
                candidate_limit=final_limit * 3,
                final_limit=final_limit,
                score_threshold=0.25,
                max_per_artifact=2,
                purpose=request.purpose,
            )
        if request.purpose == "answer_feedback":
            return RetrievalQueryPlan(
                semantic_query=query,
                artifact_kinds=("knowledge", "question_bank", "project", "high_frequency", "practice"),
                candidate_limit=final_limit * 3,
                final_limit=final_limit,
                score_threshold=0.25,
                max_per_artifact=2,
                purpose=request.purpose,
            )
        if request.purpose == "follow_up_feedback":
            return RetrievalQueryPlan(
                semantic_query=query,
                artifact_kinds=("question_bank", "practice", "knowledge"),
                candidate_limit=final_limit * 3,
                final_limit=final_limit,
                score_threshold=0.25,
                max_per_artifact=2,
                purpose=request.purpose,
            )
        return RetrievalQueryPlan(
            semantic_query=query,
            artifact_kinds=("question_bank", "knowledge", "project", "high_frequency"),
            candidate_limit=final_limit * 3,
            final_limit=final_limit,
            score_threshold=0.25,
            max_per_artifact=2,
            purpose=request.purpose,
        )
```

- [ ] **Step 5: Implement retrieval postprocessor**

Create `backend/app/services/retrieval_postprocessor.py`:

```python
from __future__ import annotations

from app.services.retrieval_query_planner import RetrievalQueryPlan
from app.services.workspace_vector_store import WorkspaceVectorHit


class RetrievalPostProcessor:
    def process(
        self,
        hits: list[WorkspaceVectorHit],
        plan: RetrievalQueryPlan,
    ) -> list[WorkspaceVectorHit]:
        filtered = [hit for hit in hits if hit.score >= plan.score_threshold]
        filtered.sort(key=lambda hit: hit.score, reverse=True)

        per_artifact: dict[str, int] = {}
        selected: list[WorkspaceVectorHit] = []
        for hit in filtered:
            artifact_id = str(hit.metadata.get("artifact_id") or hit.metadata.get("source_id") or "")
            count = per_artifact.get(artifact_id, 0)
            if artifact_id and count >= plan.max_per_artifact:
                continue
            selected.append(hit)
            if artifact_id:
                per_artifact[artifact_id] = count + 1
            if len(selected) >= plan.final_limit:
                break

        return self._prefer_kind_diversity(selected, filtered, plan)

    def _prefer_kind_diversity(
        self,
        selected: list[WorkspaceVectorHit],
        filtered: list[WorkspaceVectorHit],
        plan: RetrievalQueryPlan,
    ) -> list[WorkspaceVectorHit]:
        if len(selected) < plan.final_limit:
            return selected
        selected_kinds = {str(hit.metadata.get("artifact_kind", "")) for hit in selected}
        for kind in plan.artifact_kinds:
            if kind in selected_kinds:
                continue
            replacement = next(
                (
                    hit
                    for hit in filtered
                    if hit.metadata.get("artifact_kind") == kind and hit not in selected
                ),
                None,
            )
            if replacement is None:
                continue
            selected[-1] = replacement
            selected.sort(key=lambda hit: hit.score, reverse=True)
            selected_kinds = {str(hit.metadata.get("artifact_kind", "")) for hit in selected}
        return selected[: plan.final_limit]
```

- [ ] **Step 6: Run planner and postprocessor tests**

Run:

```sh
cd backend
uv run pytest tests/test_retrieval_query_planner.py tests/test_retrieval_postprocessor.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit Task 6**

Run:

```sh
git add backend/app/services/retrieval_query_planner.py backend/app/services/retrieval_postprocessor.py backend/tests/test_retrieval_query_planner.py backend/tests/test_retrieval_postprocessor.py
git commit -m "feat: add workspace retrieval planning"
```

Expected: commit succeeds.

---

### Task 7: Wire Workspace Retrieval And Context Assembly Into Interviews

**Files:**
- Create: `backend/app/services/context_assembler.py`
- Create: `backend/tests/test_context_assembler.py`
- Modify: `backend/app/services/workspace_retrieval_service.py`
- Modify: `backend/app/services/interview_service.py`
- Modify: `backend/tests/test_interviews.py`

- [ ] **Step 1: Write failing context assembler tests**

Create `backend/tests/test_context_assembler.py`:

```python
from app.services.context_assembler import ContextAssembler


def test_context_assembler_keeps_direct_context_first_and_applies_budget() -> None:
    assembler = ContextAssembler(max_characters=80)

    context = assembler.assemble(
        direct_context=["[候选人画像]\nJava 后端"],
        project_context=["[项目材料]\n订单缓存项目" * 5],
        retrieved_context=["[检索片段]\nRedis 缓存击穿"],
    )

    assert context[0].startswith("[候选人画像]")
    assert sum(len(item) for item in context) <= 80
```

- [ ] **Step 2: Write failing workspace retrieval service tests**

Add to `backend/tests/test_interviews.py` or create a focused test file:

```python
from app.services.retrieval_query_planner import RetrievalRequest


def test_workspace_retrieval_service_uses_query_plan(client, monkeypatch) -> None:
    captured_requests: list[RetrievalRequest] = []

    class FakePlanner:
        def plan(self, request):
            captured_requests.append(request)
            from app.services.retrieval_query_planner import RetrievalQueryPlan

            return RetrievalQueryPlan(
                semantic_query=request.query,
                artifact_kinds=("knowledge",),
                candidate_limit=6,
                final_limit=2,
                score_threshold=0.0,
                max_per_artifact=1,
                purpose=request.purpose,
            )

    class FakeStore:
        def has_searchable_content(self, collection_name):
            return True

        def search(self, collection_name, query, *, limit, metadata_filter=None):
            from app.services.workspace_vector_store import WorkspaceVectorHit

            return [
                WorkspaceVectorHit(
                    content="Redis cache stampede",
                    score=0.9,
                    metadata={"artifact_id": "a1", "artifact_kind": "knowledge", "source_type": "artifact"},
                )
            ]

    from app.services.workspace_retrieval_service import WorkspaceRetrievalService

    with client.app.state.session_factory() as session:
        service = WorkspaceRetrievalService(
            vector_store=FakeStore(),
            query_planner=FakePlanner(),
        )
        hits = service.search(
            session,
            RetrievalRequest(
                purpose="question_generation",
                query="Redis",
                mode="comprehensive",
                limit=2,
            ),
        )

    assert hits[0]["content"] == "Redis cache stampede"
    assert captured_requests[0].purpose == "question_generation"
```

- [ ] **Step 3: Run context and retrieval tests to verify failure**

Run:

```sh
cd backend
uv run pytest tests/test_context_assembler.py tests/test_interviews.py::test_workspace_retrieval_service_uses_query_plan -v
```

Expected: FAIL because `ContextAssembler` does not exist and `WorkspaceRetrievalService.search` still accepts a plain query string.

- [ ] **Step 4: Implement context assembler**

Create `backend/app/services/context_assembler.py`:

```python
from __future__ import annotations


class ContextAssembler:
    def __init__(self, *, max_characters: int = 12000) -> None:
        self.max_characters = max_characters

    def assemble(
        self,
        *,
        direct_context: list[str],
        project_context: list[str],
        retrieved_context: list[str],
    ) -> list[str]:
        selected: list[str] = []
        used = 0
        for item in [*direct_context, *project_context, *retrieved_context]:
            if not item:
                continue
            remaining = self.max_characters - used
            if remaining <= 0:
                break
            clipped = item if len(item) <= remaining else item[:remaining].rstrip()
            if clipped:
                selected.append(clipped)
                used += len(clipped)
        return selected
```

- [ ] **Step 5: Update WorkspaceRetrievalService**

Modify `backend/app/services/workspace_retrieval_service.py`:

```python
from __future__ import annotations

import logging

from qdrant_client.http.models import FieldCondition, Filter, MatchAny
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.repositories.vector_store import VectorStoreUnavailable
from app.repositories.workspace_settings_repository import WorkspaceSettingsRepository
from app.services.retrieval_postprocessor import RetrievalPostProcessor
from app.services.retrieval_query_planner import (
    RetrievalQueryPlanner,
    RetrievalRequest,
)
from app.services.workspace_vector_store import WorkspaceVectorStore


logger = logging.getLogger(__name__)


class WorkspaceRetrievalService:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        vector_store: WorkspaceVectorStore | None = None,
        query_planner: RetrievalQueryPlanner | None = None,
        postprocessor: RetrievalPostProcessor | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.vector_store = vector_store or WorkspaceVectorStore(settings=self.settings)
        self.query_planner = query_planner or RetrievalQueryPlanner()
        self.postprocessor = postprocessor or RetrievalPostProcessor()
        self.settings_repository = WorkspaceSettingsRepository()

    def search(self, session: Session, request: RetrievalRequest) -> list[dict[str, object]]:
        workspace_settings = self.settings_repository.get_or_create(session)
        collection = workspace_settings.active_collection or self.settings.qdrant_collection
        try:
            if not self.vector_store.has_searchable_content(collection):
                return []
            plan = self.query_planner.plan(request)
            raw_hits = self.vector_store.search(
                collection,
                plan.semantic_query,
                limit=plan.candidate_limit,
                metadata_filter=self._metadata_filter(plan.artifact_kinds),
            )
            hits = self.postprocessor.process(raw_hits, plan)
        except VectorStoreUnavailable as exc:
            logger.info("Workspace retrieval unavailable: %s", exc)
            return []
        return [
            {
                "content": hit.content,
                "score": hit.score,
                "source_type": str(hit.metadata.get("source_type", "artifact")),
                "source_id": str(hit.metadata.get("artifact_id") or hit.metadata.get("source_id") or ""),
                "artifact_kind": str(hit.metadata.get("artifact_kind", "")),
                "relative_path": str(hit.metadata.get("relative_path", "")),
            }
            for hit in hits
        ]

    def _metadata_filter(self, artifact_kinds: tuple[str, ...]) -> Filter | None:
        if not artifact_kinds:
            return None
        return Filter(
            must=[
                FieldCondition(
                    key="metadata.artifact_kind",
                    match=MatchAny(any=list(artifact_kinds)),
                )
            ]
        )
```

- [ ] **Step 6: Update InterviewService to pass retrieval requests**

In `backend/app/services/interview_service.py`, remove `RagService` import, constructor argument, and `self.rag_service`.

Add imports:

```python
from app.services.context_assembler import ContextAssembler
from app.services.retrieval_query_planner import RetrievalRequest
```

Add constructor dependency:

```python
        context_assembler: ContextAssembler | None = None,
```

Set:

```python
        self.context_assembler = context_assembler or ContextAssembler()
```

Update `_answer_context`:

```python
        direct_context = self._direct_workspace_context(session)
        project_context = self._project_context(session)
        context_hits = self.retrieval_service.search(
            session,
            RetrievalRequest(
                purpose="answer_feedback",
                query=_answer_context_query(
                    target_company=config.target_company,
                    target_role=config.target_role,
                    job_description=config.job_description,
                    extra_prompt=config.extra_prompt,
                    question=question,
                    answer=answer,
                ),
                mode=config.mode,
                limit=4,
            ),
        )
        retrieved_context = [self._retrieved_context_text(hit) for hit in context_hits]
        return self.context_assembler.assemble(
            direct_context=direct_context,
            project_context=project_context,
            retrieved_context=[
                f"[本题考察点]\n围绕当前题目识别考察点：{question}",
                *retrieved_context,
            ],
        )
```

Update `_question_context`:

```python
        direct_context = self._direct_workspace_context(session)
        project_context = self._project_context(session) if mode == "project_deep_dive" else []
        context_hits = self.retrieval_service.search(
            session,
            RetrievalRequest(
                purpose="question_generation",
                query=query,
                mode=mode,
                limit=4,
            ),
        )
        retrieved_context = [self._retrieved_context_text(hit) for hit in context_hits]
        context = self.context_assembler.assemble(
            direct_context=direct_context,
            project_context=project_context,
            retrieved_context=retrieved_context,
        )
        return context, context_hits
```

- [ ] **Step 7: Update interview tests that monkeypatch retrieval**

In `backend/tests/test_interviews.py`, update monkeypatched `capture_search` functions from:

```python
def capture_search(_self, _session, query: str, limit: int):
    assert limit == 4
```

to:

```python
def capture_search(_self, _session, request):
    assert request.limit == 4
    query = request.query
```

When assertions inspect the query list, append `request.query`.

- [ ] **Step 8: Run interview and context tests**

Run:

```sh
cd backend
uv run pytest tests/test_context_assembler.py tests/test_interviews.py -v
```

Expected: PASS.

- [ ] **Step 9: Commit Task 7**

Run:

```sh
git add backend/app/services/context_assembler.py backend/app/services/workspace_retrieval_service.py backend/app/services/interview_service.py backend/tests/test_context_assembler.py backend/tests/test_interviews.py
git commit -m "feat: plan workspace retrieval context"
```

Expected: commit succeeds.

---

### Task 8: Remove Legacy RagService From Memory And Finish Full Verification

**Files:**
- Modify: `backend/app/services/memory_service.py`
- Modify: `backend/tests/test_memory.py`
- Modify: `backend/tests/test_qdrant_store.py`
- Modify: `README.md`
- Modify: `docs/workbench-architecture.md`
- Modify: `docs/knowledge-data-flow.md`

- [ ] **Step 1: Write failing memory test for no legacy vector indexing**

In `backend/tests/test_memory.py`, add:

```python
def test_finish_does_not_index_legacy_report_or_memory_vectors(client: TestClient, monkeypatch) -> None:
    calls: list[str] = []

    def fail_upsert(*_args, **_kwargs):
        calls.append("upsert")
        raise AssertionError("finish should not write report or memory vectors")

    monkeypatch.setattr(
        "app.repositories.qdrant_store.QdrantVectorStore.upsert_chunks",
        fail_upsert,
        raising=False,
    )
    config = {
        "target_company": "OpenAI",
        "target_role": "Backend Engineer",
        "job_description": "Build AI app infrastructure.",
        "extra_prompt": "Focus on weakness reinforcement.",
        "language": "zh-CN",
        "mode": "weakness_reinforcement",
        "chat_model_provider": "openai",
        "chat_model": "gpt-4.1-mini",
        "target_rounds": 1,
    }
    created = client.post("/api/interview-sessions", json=config).json()
    session_id = created["session"]["id"]
    client.post(
        f"/api/interview-sessions/{session_id}/answer",
        json={"answer": "I use tests and clear services."},
    )

    finished = client.post(f"/api/interview-sessions/{session_id}/finish")

    assert finished.status_code == 200
    assert calls == []
```

- [ ] **Step 2: Run memory test to verify failure**

Run:

```sh
cd backend
uv run pytest tests/test_memory.py::test_finish_does_not_index_legacy_report_or_memory_vectors -v
```

Expected: FAIL while `MemoryService` still uses `RagService` or legacy vector indexing.

- [ ] **Step 3: Remove RagService dependency and vector indexing from MemoryService**

In `backend/app/services/memory_service.py`, remove imports:

```python
from app.repositories.vector_store import VectorChunk, stable_vector_id
from app.services.rag_service import RagService
```

Change constructor from:

```python
        rag_service: RagService | None = None,
```

to no `rag_service` argument.

Remove:

```python
self.rag_service = rag_service or RagService(self.settings)
```

Replace the call in `finish_session`:

```python
self.index_report_and_memory(session, report, memory_files)
```

with:

```python
for memory_file in memory_files:
    memory_file.last_indexed_at = None
```

Delete the entire `index_report_and_memory` method.

- [ ] **Step 4: Delete `backend/app/services/rag_service.py`**

Run:

```sh
git rm backend/app/services/rag_service.py
```

Expected: git stages the deletion. If imports remain, `rg "RagService|rag_service"` should show only documentation or old committed spec text.

- [ ] **Step 5: Update Qdrant tests to reflect administrative wrapper usage**

Keep `backend/tests/test_qdrant_store.py` only if `backend/app/repositories/qdrant_store.py` remains. Remove tests that assert document-specific repository semantics if the repository is no longer used by application code. Keep tests for:

```python
def test_stable_vector_id_is_deterministic_and_parseable() -> None:
    first = stable_vector_id("artifact", "artifact-1", 0)
    second = stable_vector_id("artifact", "artifact-1", 0)

    assert first == second
```

If `qdrant_store.py` is deleted, remove `backend/tests/test_qdrant_store.py` and rely on `backend/tests/test_workspace_vector_store.py`.

- [ ] **Step 6: Update docs**

In `docs/knowledge-data-flow.md`, update the Mermaid section so the RAG section names LangChain components:

```text
读取可索引 artifact 正文 -> LangChain Markdown/递归切块 -> LangChain embedding -> LangChain QdrantVectorStore 写入 -> WorkspaceRetrievalService 生成 query plan -> LangChain retriever 查询 active collection -> Auto Reign 后处理和上下文预算 -> 注入面试 prompt
```

In `docs/workbench-architecture.md`, add one bullet under “入库与检索”:

```markdown
RAG 组件层使用 LangChain 的 splitter、embedding、Qdrant vectorstore 和 retriever；workspace 协议、provenance、active collection、可索引规则、上下文优先级和 prompt 安全边界仍由 Auto Reign 应用代码控制。
```

In `README.md`, remove references to `/api/documents` and `/api/rag/search`. Add:

```markdown
资料入库统一通过 workspace API 完成：上传资料使用 `POST /api/workspace/materials/upload`，学习笔记使用 `POST /api/workspace/learning-notes/stream`，真实面试记录使用 `POST /api/workspace/real-interview-records`。面试检索只读取 workspace artifact active collection。
```

- [ ] **Step 7: Run focused tests**

Run:

```sh
cd backend
uv run pytest tests/test_memory.py tests/test_legacy_rag_removed.py tests/test_workspace_vector_store.py tests/test_index_service.py tests/test_interviews.py -v
```

Expected: PASS.

- [ ] **Step 8: Run full backend validation**

Run:

```sh
cd backend
uv run pytest -v
uv run ruff check .
```

Expected: both commands pass.

- [ ] **Step 9: Run frontend and compose validation**

Run:

```sh
cd frontend
npm test
npm run build

cd ..
docker compose config
```

Expected: all commands pass.

- [ ] **Step 10: Commit Task 8**

Run:

```sh
git add backend/app/services/memory_service.py backend/tests/test_memory.py README.md docs/workbench-architecture.md docs/knowledge-data-flow.md
git add -u backend/app/services backend/tests
git commit -m "refactor: finish workspace-only langchain rag"
```

Expected: commit succeeds.

---

## Final Verification Checklist

- [ ] `rg "RagService|/api/rag|/api/documents|DocumentService|DocumentRepository|DocumentChunkRepository" backend/app backend/tests frontend/src` returns no application references.
- [ ] `rg "documents|document_chunks" backend/app/db backend/alembic backend/tests` only returns historical migration definitions, downgrade recreation in `20260625_0005`, or tests explicitly asserting legacy removal.
- [ ] `cd backend && uv run pytest -v` passes.
- [ ] `cd backend && uv run ruff check .` passes.
- [ ] `cd frontend && npm test` passes.
- [ ] `cd frontend && npm run build` passes.
- [ ] `docker compose config` passes.
- [ ] `README.md`, `docs/workbench-architecture.md`, and `docs/knowledge-data-flow.md` all describe workspace-only RAG with LangChain component boundaries.
