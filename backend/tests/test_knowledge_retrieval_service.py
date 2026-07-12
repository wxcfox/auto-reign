from __future__ import annotations

from dataclasses import replace
import json

import pytest
from fastapi import HTTPException

from app.repositories.vector_store import VectorStoreUnavailable
from app.schemas.knowledge_collections import KnowledgeCollectionConfig
from app.services.knowledge_document_service import KnowledgeDocumentService
from app.services.knowledge_retrieval_service import (
    KnowledgeRetrievalService,
    KnowledgeSource,
    serialize_knowledge_result,
)
from app.services.knowledge_scope_service import (
    ReadyDocumentScope,
    ResolvedCollectionScope,
)
from app.services.knowledge_vector_store import (
    DocumentGeneration,
    KnowledgeVectorHit,
)
from app.services.token_counter import RuntimeTokenCounter
from app.storage import ObjectStoreUnavailable, StoredObject
from tests.fake_object_store import FakeObjectStore


class RecordingVectorStore:
    def __init__(self) -> None:
        self.results: dict[str, list[KnowledgeVectorHit]] = {}
        self.search_calls: list[tuple[str, tuple[DocumentGeneration, ...], int]] = []
        self.error: Exception | None = None

    def search(
        self,
        query: str,
        *,
        scopes: list[DocumentGeneration],
        limit: int,
    ) -> list[KnowledgeVectorHit]:
        if self.error is not None:
            raise self.error
        assert scopes
        self.search_calls.append((query, tuple(scopes), limit))
        return list(self.results.get(scopes[0].collection_id, ()))[:limit]


class CorruptMetadataObjectStore(FakeObjectStore):
    def __init__(self, corruption: str) -> None:
        super().__init__()
        self.corruption = corruption

    def get(self, key: str) -> StoredObject:
        stored = super().get(key)
        if self.corruption == "metadata_key_mismatch":
            metadata = replace(stored.metadata, key=f"{key}-other")
        elif self.corruption == "metadata_size_mismatch":
            metadata = replace(
                stored.metadata,
                size_bytes=stored.metadata.size_bytes + 1,
            )
        else:
            return stored
        return replace(stored, metadata=metadata)


@pytest.fixture
def token_counter() -> RuntimeTokenCounter:
    return RuntimeTokenCounter(image_input_token_reserve=1_024)


def _document(
    *,
    document_id: str = "doc-1",
    collection_id: str = "collection-1",
    owner_user_id: int = 7,
    generation: int = 2,
    content_hash: str = "sha256-current",
    filename: str = "guide.md",
    parsed_object_key: str | None = None,
) -> ReadyDocumentScope:
    return ReadyDocumentScope(
        collection_id=collection_id,
        owner_user_id=owner_user_id,
        document_id=document_id,
        index_generation=generation,
        content_hash=content_hash,
        parsed_object_key=(
            parsed_object_key
            or KnowledgeDocumentService.parsed_key(
                owner_user_id,
                collection_id,
                document_id,
                generation,
            )
        ),
        filename=filename,
    )


def _scope(
    *documents: ReadyDocumentScope,
    collection_id: str | None = None,
    owner_user_id: int | None = None,
    top_k: int = 8,
    score_threshold: float | None = None,
) -> ResolvedCollectionScope:
    first = documents[0] if documents else None
    resolved_collection_id = collection_id or (
        first.collection_id if first is not None else "collection-empty"
    )
    resolved_owner_id = (
        owner_user_id
        if owner_user_id is not None
        else (first.owner_user_id if first is not None else 7)
    )
    return ResolvedCollectionScope(
        collection_id=resolved_collection_id,
        owner_user_id=resolved_owner_id,
        config=KnowledgeCollectionConfig(
            top_k=top_k,
            score_threshold=score_threshold,
        ),
        documents=tuple(documents),
    )


def _source(
    document: ReadyDocumentScope,
    *,
    content: str,
    chunk_index: int | None = None,
    score: float | None = None,
) -> KnowledgeSource:
    return KnowledgeSource(
        document_id=document.document_id,
        collection_id=document.collection_id,
        filename=document.filename,
        index_generation=document.index_generation,
        content_hash=document.content_hash,
        chunk_index=chunk_index,
        score=score,
        content=content,
    )


def _put_text(store: FakeObjectStore, document: ReadyDocumentScope, text: str) -> None:
    store.put(document.parsed_object_key, text.encode("utf-8"), if_none_match=True)


def _hit(
    document: ReadyDocumentScope,
    text: str,
    *,
    start: int,
    end: int,
    chunk_index: int = 0,
    score: float = 0.9,
    content: object | None = None,
    metadata_overrides: dict[str, object] | None = None,
) -> KnowledgeVectorHit:
    metadata: dict[str, object] = {
        "collection_id": document.collection_id,
        "owner_user_id": document.owner_user_id,
        "document_id": document.document_id,
        "index_generation": document.index_generation,
        "content_hash": document.content_hash,
        "filename": document.filename,
        "chunk_index": chunk_index,
        "source_start": start,
        "source_end": end,
    }
    metadata.update(metadata_overrides or {})
    return KnowledgeVectorHit(
        content=text[start:end] if content is None else content,  # type: ignore[arg-type]
        score=score,
        metadata=metadata,
    )


def _retriever(
    object_store: FakeObjectStore,
    vector_store: RecordingVectorStore,
    token_counter: RuntimeTokenCounter,
    *,
    max_results: int = 20,
    max_query_chars: int = 2_000,
) -> KnowledgeRetrievalService:
    return KnowledgeRetrievalService(
        object_store=object_store,
        vector_store=vector_store,
        token_counter=token_counter,
        max_results=max_results,
        max_query_chars=max_query_chars,
        max_parsed_chars=20_000,
    )


def _budget(
    counter: RuntimeTokenCounter,
    *,
    call_id: str,
    mode: str,
    sources: list[KnowledgeSource],
) -> int:
    return counter.count_tool_result(
        call_id=call_id,
        content=serialize_knowledge_result(mode, sources),
    )


def test_empty_scope_returns_a_budgeted_empty_result_without_external_io(
    token_counter,
) -> None:
    object_store = FakeObjectStore()
    vector_store = RecordingVectorStore()
    call_id = "call-empty"
    expected = serialize_knowledge_result("direct", [])
    available = token_counter.count_tool_result(call_id=call_id, content=expected)

    result = _retriever(object_store, vector_store, token_counter).search(
        call_id=call_id,
        query="anything",
        scopes=[],
        available_tokens=available,
    )

    assert result.mode == "direct"
    assert result.sources == []
    assert result.content == expected
    assert object_store.get_calls == []
    assert vector_store.search_calls == []


def test_collection_group_without_ready_documents_is_treated_as_empty(
    token_counter,
) -> None:
    object_store = FakeObjectStore()
    vector_store = RecordingVectorStore()
    call_id = "call-empty-group"
    available = _budget(
        token_counter,
        call_id=call_id,
        mode="direct",
        sources=[],
    )

    result = _retriever(object_store, vector_store, token_counter).search(
        call_id=call_id,
        query="anything",
        scopes=[_scope(collection_id="collection-empty")],
        available_tokens=available,
    )

    assert result.sources == []
    assert object_store.get_calls == []
    assert vector_store.search_calls == []


def test_empty_result_envelope_is_checked_before_object_or_vector_io(
    token_counter,
) -> None:
    object_store = FakeObjectStore()
    vector_store = RecordingVectorStore()
    document = _document()
    _put_text(object_store, document, "full source")
    call_id = "call-no-envelope-budget"
    minimum = _budget(
        token_counter,
        call_id=call_id,
        mode="direct",
        sources=[],
    )

    with pytest.raises(HTTPException) as error:
        _retriever(object_store, vector_store, token_counter).search(
            call_id=call_id,
            query="source",
            scopes=[_scope(document)],
            available_tokens=minimum - 1,
        )

    assert error.value.status_code == 400
    assert error.value.detail["code"] == "context_too_large"
    assert object_store.get_calls == []
    assert vector_store.search_calls == []


@pytest.mark.parametrize(
    ("query", "expected_code"),
    [
        ("", "knowledge_query_empty"),
        (" \n\t ", "knowledge_query_empty"),
        ("x" * 13, "knowledge_query_too_long"),
    ],
)
def test_query_is_validated_before_direct_or_rag_io(
    token_counter,
    query,
    expected_code,
) -> None:
    object_store = FakeObjectStore()
    vector_store = RecordingVectorStore()
    document = _document()
    _put_text(object_store, document, "small complete source")

    with pytest.raises(HTTPException) as error:
        _retriever(
            object_store,
            vector_store,
            token_counter,
            max_query_chars=12,
        ).search(
            call_id="call-invalid-query",
            query=query,
            scopes=[_scope(document)],
            available_tokens=10_000,
        )

    assert error.value.detail["code"] == expected_code
    assert object_store.get_calls == []
    assert vector_store.search_calls == []


def test_direct_returns_the_complete_authoritative_source_at_exact_budget(
    token_counter,
) -> None:
    object_store = FakeObjectStore()
    vector_store = RecordingVectorStore()
    document = _document(generation=4, content_hash="sha256-original")
    text = '# Cache\nExact source with "quotes" and 中文。'
    _put_text(object_store, document, text)
    expected_source = _source(document, content=text)
    call_id = "call-direct-" + ("x" * 80)
    available = _budget(
        token_counter,
        call_id=call_id,
        mode="direct",
        sources=[expected_source],
    )

    result = _retriever(object_store, vector_store, token_counter).search(
        call_id=call_id,
        query="  cache  ",
        scopes=[_scope(document)],
        available_tokens=available,
    )

    assert result.mode == "direct"
    assert result.sources == [expected_source]
    assert result.content == serialize_knowledge_result("direct", [expected_source])
    assert token_counter.count_tool_result(call_id=call_id, content=result.content) == available
    payload = json.loads(result.content)
    assert payload["sources"][0]["index_generation"] == 4
    assert payload["sources"][0]["content_hash"] == "sha256-original"
    assert object_store.get_calls
    assert set(object_store.get_calls) == {document.parsed_object_key}
    assert vector_store.search_calls == []


def test_rag_uses_qdrant_only_to_locate_an_authoritative_parsed_slice(
    token_counter,
) -> None:
    object_store = FakeObjectStore()
    vector_store = RecordingVectorStore()
    document = _document()
    text = ("preface " * 80) + "authoritative cache chunk" + (" tail" * 80)
    _put_text(object_store, document, text)
    start = text.index("authoritative cache chunk")
    end = start + len("authoritative cache chunk")
    vector_store.results[document.collection_id] = [
        _hit(document, text, start=start, end=end, chunk_index=5, score=0.91)
    ]
    expected_source = _source(
        document,
        content=text[start:end],
        chunk_index=5,
        score=0.91,
    )
    call_id = "call-rag"
    available = _budget(
        token_counter,
        call_id=call_id,
        mode="rag",
        sources=[expected_source],
    )
    assert available < _budget(
        token_counter,
        call_id=call_id,
        mode="direct",
        sources=[_source(document, content=text)],
    )

    result = _retriever(object_store, vector_store, token_counter).search(
        call_id=call_id,
        query="  cache penetration  ",
        scopes=[_scope(document, top_k=3)],
        available_tokens=available,
    )

    assert result.mode == "rag"
    assert result.sources == [expected_source]
    assert vector_store.search_calls[0][0] == "cache penetration"
    assert vector_store.search_calls[0][2] == 3
    assert object_store.get_calls
    assert set(object_store.get_calls) == {document.parsed_object_key}
    assert token_counter.count_tool_result(call_id=call_id, content=result.content) == available


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (-1, 2),
        (0, 0),
        (3, 2),
        (0, 10_000),
        (True, 2),
        (0, True),
        ("0", 2),
    ],
)
def test_rag_rejects_invalid_source_offsets(
    token_counter,
    start,
    end,
) -> None:
    object_store = FakeObjectStore()
    vector_store = RecordingVectorStore()
    document = _document()
    text = "0123456789" * 200
    _put_text(object_store, document, text)
    vector_store.results[document.collection_id] = [
        _hit(
            document,
            text,
            start=0,
            end=2,
            content="01",
            metadata_overrides={"source_start": start, "source_end": end},
        )
    ]
    available = _budget(
        token_counter,
        call_id="call-invalid-offset",
        mode="rag",
        sources=[_source(document, content="01", chunk_index=0, score=0.9)],
    )

    with pytest.raises(HTTPException) as error:
        _retriever(object_store, vector_store, token_counter).search(
            call_id="call-invalid-offset",
            query="digits",
            scopes=[_scope(document)],
            available_tokens=available,
        )

    assert error.value.detail["code"] == "knowledge_unavailable"


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("collection_id", "other-collection"),
        ("owner_user_id", 999),
        ("document_id", "other-document"),
        ("index_generation", 99),
        ("content_hash", "other-hash"),
    ],
)
def test_rag_hit_must_match_the_resolved_five_tuple(
    token_counter,
    field,
    invalid_value,
) -> None:
    object_store = FakeObjectStore()
    vector_store = RecordingVectorStore()
    document = _document()
    text = "large authoritative source " * 100
    _put_text(object_store, document, text)
    vector_store.results[document.collection_id] = [
        _hit(
            document,
            text,
            start=0,
            end=20,
            metadata_overrides={field: invalid_value},
        )
    ]
    expected_content = text[:20]
    available = _budget(
        token_counter,
        call_id="call-invalid-scope",
        mode="rag",
        sources=[
            _source(
                document,
                content=expected_content,
                chunk_index=0,
                score=0.9,
            )
        ],
    )

    with pytest.raises(HTTPException) as error:
        _retriever(object_store, vector_store, token_counter).search(
            call_id="call-invalid-scope",
            query="source",
            scopes=[_scope(document)],
            available_tokens=available,
        )

    assert error.value.detail["code"] == "knowledge_unavailable"


def test_rag_rejects_generated_or_stale_content_instead_of_returning_it(
    token_counter,
) -> None:
    object_store = FakeObjectStore()
    vector_store = RecordingVectorStore()
    document = _document()
    text = "authoritative source " * 120
    _put_text(object_store, document, text)
    vector_store.results[document.collection_id] = [
        _hit(
            document,
            text,
            start=0,
            end=20,
            content="generated summary",
        )
    ]
    available = _budget(
        token_counter,
        call_id="call-stale-content",
        mode="rag",
        sources=[
            _source(
                document,
                content=text[:20],
                chunk_index=0,
                score=0.9,
            )
        ],
    )

    with pytest.raises(HTTPException) as error:
        _retriever(object_store, vector_store, token_counter).search(
            call_id="call-stale-content",
            query="source",
            scopes=[_scope(document)],
            available_tokens=available,
        )

    assert error.value.status_code == 503
    assert error.value.detail["code"] == "knowledge_unavailable"


@pytest.mark.parametrize(
    ("metadata_overrides", "content", "score"),
    [
        ({"filename": "forged.md"}, "authoritative source", 0.9),
        ({"chunk_index": -1}, "authoritative source", 0.9),
        ({"chunk_index": True}, "authoritative source", 0.9),
        ({}, b"authoritative source", 0.9),
        ({}, "authoritative source", float("nan")),
        ({}, "authoritative source", True),
    ],
)
def test_rag_rejects_malformed_non_authoritative_hit_fields(
    token_counter,
    metadata_overrides,
    content,
    score,
) -> None:
    object_store = FakeObjectStore()
    vector_store = RecordingVectorStore()
    document = _document()
    text = "authoritative source" + (" tail" * 200)
    _put_text(object_store, document, text)
    vector_store.results[document.collection_id] = [
        _hit(
            document,
            text,
            start=0,
            end=len("authoritative source"),
            content=content,
            score=score,
            metadata_overrides=metadata_overrides,
        )
    ]
    available = _budget(
        token_counter,
        call_id="call-malformed-hit",
        mode="rag",
        sources=[
            _source(
                document,
                content="authoritative source",
                chunk_index=0,
                score=0.9,
            )
        ],
    )

    with pytest.raises(HTTPException) as error:
        _retriever(object_store, vector_store, token_counter).search(
            call_id="call-malformed-hit",
            query="source",
            scopes=[_scope(document)],
            available_tokens=available,
        )

    assert error.value.detail["code"] == "knowledge_unavailable"


@pytest.mark.parametrize(
    "corruption",
    [
        "missing",
        "unavailable",
        "invalid_utf8",
        "empty",
        "metadata_key_mismatch",
        "metadata_size_mismatch",
        "oversized",
    ],
)
def test_corrupt_direct_source_never_falls_back_to_rag(
    token_counter,
    corruption,
) -> None:
    object_store = (
        CorruptMetadataObjectStore(corruption)
        if corruption.startswith("metadata_")
        else FakeObjectStore()
    )
    vector_store = RecordingVectorStore()
    document = _document()
    if corruption == "unavailable":
        object_store.get_error = ObjectStoreUnavailable("temporarily unavailable")
    elif corruption != "missing":
        if corruption == "invalid_utf8":
            content = b"\xff"
        elif corruption == "empty":
            content = b""
        elif corruption == "oversized":
            content = b"x" * 20_001
        else:
            content = b"valid parsed source"
        object_store.put(
            document.parsed_object_key,
            content,
            if_none_match=True,
        )

    with pytest.raises(HTTPException) as error:
        _retriever(object_store, vector_store, token_counter).search(
            call_id="call-corrupt-direct",
            query="source",
            scopes=[_scope(document)],
            available_tokens=10_000,
        )

    assert error.value.status_code == 503
    assert error.value.detail["code"] == "knowledge_unavailable"
    assert vector_store.search_calls == []


def test_rag_fails_when_no_candidate_fits_the_remaining_budget(
    token_counter,
) -> None:
    object_store = FakeObjectStore()
    vector_store = RecordingVectorStore()
    document = _document()
    text = "authoritative source " * 300
    _put_text(object_store, document, text)
    vector_store.results[document.collection_id] = [
        _hit(
            document,
            text,
            start=0,
            end=len("authoritative source"),
        )
    ]
    call_id = "call-no-candidate-budget"
    available = _budget(
        token_counter,
        call_id=call_id,
        mode="direct",
        sources=[],
    )

    with pytest.raises(HTTPException) as error:
        _retriever(object_store, vector_store, token_counter).search(
            call_id=call_id,
            query="source",
            scopes=[_scope(document)],
            available_tokens=available,
        )

    assert error.value.status_code == 400
    assert error.value.detail["code"] == "context_too_large"


def test_noncanonical_parsed_pointer_fails_before_object_io_or_rag(
    token_counter,
) -> None:
    object_store = FakeObjectStore()
    vector_store = RecordingVectorStore()
    canonical = _document()
    document = replace(
        canonical,
        parsed_object_key=("users/999/knowledge/other-collection/other-document/parsed/2"),
    )

    with pytest.raises(HTTPException) as error:
        _retriever(object_store, vector_store, token_counter).search(
            call_id="call-cross-tenant-key",
            query="source",
            scopes=[_scope(document)],
            available_tokens=10_000,
        )

    assert error.value.detail["code"] == "knowledge_unavailable"
    assert object_store.get_calls == []
    assert vector_store.search_calls == []


def test_multiple_collections_apply_each_top_k_then_global_limit_and_stable_ties(
    token_counter,
) -> None:
    object_store = FakeObjectStore()
    vector_store = RecordingVectorStore()
    document_b = _document(
        document_id="doc-b",
        collection_id="collection-b",
        content_hash="hash-b",
    )
    document_a = _document(
        document_id="doc-a",
        collection_id="collection-a",
        content_hash="hash-a",
    )
    text_a = "alpha authoritative" + (" a" * 1_000)
    text_b = "beta authoritative" + (" b" * 1_000)
    _put_text(object_store, document_a, text_a)
    _put_text(object_store, document_b, text_b)
    hit_a = _hit(
        document_a,
        text_a,
        start=0,
        end=len("alpha authoritative"),
        chunk_index=3,
        score=0.8,
    )
    hit_b = _hit(
        document_b,
        text_b,
        start=0,
        end=len("beta authoritative"),
        chunk_index=2,
        score=0.8,
    )
    second_b = _hit(
        document_b,
        text_b,
        start=len("beta "),
        end=len("beta authoritative"),
        chunk_index=4,
        score=0.7,
    )
    vector_store.results[document_b.collection_id] = [hit_b, second_b]
    vector_store.results[document_a.collection_id] = [hit_a]
    expected = [
        _source(
            document_a,
            content="alpha authoritative",
            chunk_index=3,
            score=0.8,
        ),
        _source(
            document_b,
            content="beta authoritative",
            chunk_index=2,
            score=0.8,
        ),
    ]
    call_id = "call-multiple"
    available = _budget(
        token_counter,
        call_id=call_id,
        mode="rag",
        sources=expected,
    )

    result = _retriever(
        object_store,
        vector_store,
        token_counter,
        max_results=2,
    ).search(
        call_id=call_id,
        query="authoritative",
        # Reversed input order makes the tie-break contract observable.
        scopes=[
            _scope(document_b, top_k=2),
            _scope(document_a, top_k=1),
        ],
        available_tokens=available,
    )

    assert result.sources == expected
    assert [(call[1][0].collection_id, call[2]) for call in vector_store.search_calls] == [
        ("collection-b", 2),
        ("collection-a", 1),
    ]
    assert len(result.sources) == 2


def test_score_threshold_is_taken_from_the_turn_scope_snapshot(
    token_counter,
) -> None:
    object_store = FakeObjectStore()
    vector_store = RecordingVectorStore()
    document = _document()
    text = "threshold source" + (" tail" * 300)
    _put_text(object_store, document, text)
    vector_store.results[document.collection_id] = [
        _hit(
            document,
            text,
            start=0,
            end=len("threshold source"),
            score=0.49,
        )
    ]
    call_id = "call-threshold"
    available = max(
        300,
        _budget(
            token_counter,
            call_id=call_id,
            mode="rag",
            sources=[],
        ),
    )

    result = _retriever(object_store, vector_store, token_counter).search(
        call_id=call_id,
        query="threshold",
        scopes=[_scope(document, score_threshold=0.5)],
        available_tokens=available,
    )

    assert result.mode == "rag"
    assert result.sources == []


def test_below_threshold_hit_still_must_match_the_authoritative_slice(
    token_counter,
) -> None:
    object_store = FakeObjectStore()
    vector_store = RecordingVectorStore()
    document = _document()
    text = "threshold source" + (" tail" * 300)
    _put_text(object_store, document, text)
    vector_store.results[document.collection_id] = [
        _hit(
            document,
            text,
            start=0,
            end=len("threshold source"),
            content="stale projection",
            score=0.49,
        )
    ]
    available = _budget(
        token_counter,
        call_id="call-threshold-corrupt",
        mode="direct",
        sources=[],
    )

    with pytest.raises(HTTPException) as error:
        _retriever(object_store, vector_store, token_counter).search(
            call_id="call-threshold-corrupt",
            query="threshold",
            scopes=[_scope(document, score_threshold=0.5)],
            available_tokens=available,
        )

    assert error.value.detail["code"] == "knowledge_unavailable"


def test_vector_failure_is_not_reported_as_an_empty_result(token_counter) -> None:
    object_store = FakeObjectStore()
    vector_store = RecordingVectorStore()
    vector_store.error = VectorStoreUnavailable("qdrant unavailable")
    document = _document()
    _put_text(object_store, document, "large source " * 300)
    call_id = "call-vector-error"
    available = _budget(
        token_counter,
        call_id=call_id,
        mode="direct",
        sources=[],
    )

    with pytest.raises(HTTPException) as error:
        _retriever(object_store, vector_store, token_counter).search(
            call_id=call_id,
            query="source",
            scopes=[_scope(document)],
            available_tokens=available,
        )

    assert error.value.status_code == 503
    assert error.value.detail["code"] == "knowledge_unavailable"
