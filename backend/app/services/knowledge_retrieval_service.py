from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Protocol

from app.core.errors import bad_request, service_unavailable
from app.core.limits import (
    DEFAULT_KNOWLEDGE_MAX_QUERY_CHARS,
    DEFAULT_KNOWLEDGE_MAX_PARSED_CHARS,
    DEFAULT_KNOWLEDGE_MAX_RESULTS,
)
from app.repositories.vector_store import VectorStoreUnavailable
from app.services.knowledge_document_service import (
    KnowledgeContentUnavailable,
    KnowledgeDocumentService,
    read_parsed_text,
)
from app.services.knowledge_scope_service import (
    ReadyDocumentScope,
    ResolvedCollectionScope,
)
from app.services.knowledge_retrievers import (
    DocumentGeneration,
    KnowledgeRetriever,
    KnowledgeRetrieverHit,
    RetrieverType,
)
from app.services.token_counter import RuntimeTokenCounter
from app.storage.object_store import ObjectStore


@dataclass(frozen=True)
class KnowledgeSource:
    document_id: str
    collection_id: str
    filename: str
    index_generation: int
    content_hash: str
    chunk_index: int | None
    score: float | None
    content: str
    retrieval_mode: str | None = None
    vector_score: float | None = None
    keyword_score: float | None = None
    fused_score: float | None = None


@dataclass(frozen=True)
class KnowledgeSearchResult:
    mode: str
    sources: list[KnowledgeSource]
    content: str


@dataclass(frozen=True)
class _Candidate:
    source: KnowledgeSource
    owner_user_id: int
    source_start: int
    source_end: int


class _KnowledgeSearchRetrieverFactory(Protocol):
    def get(self, retriever_type: RetrieverType) -> KnowledgeRetriever: ...


def serialize_knowledge_result(
    mode: str,
    sources: list[KnowledgeSource],
) -> str:
    return json.dumps(
        {
            "type": "untrusted_knowledge_sources",
            "mode": mode,
            "sources": [
                {
                    "document_id": source.document_id,
                    "collection_id": source.collection_id,
                    "filename": source.filename,
                    "index_generation": source.index_generation,
                    "content_hash": source.content_hash,
                    "chunk_index": source.chunk_index,
                    "score": source.score,
                    "retrieval_mode": source.retrieval_mode,
                    "vector_score": source.vector_score,
                    "keyword_score": source.keyword_score,
                    "fused_score": source.fused_score,
                    "content": source.content,
                }
                for source in sources
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


class KnowledgeRetrievalService:
    def __init__(
        self,
        *,
        object_store: ObjectStore,
        retriever_factory: _KnowledgeSearchRetrieverFactory,
        token_counter: RuntimeTokenCounter,
        max_results: int = DEFAULT_KNOWLEDGE_MAX_RESULTS,
        max_query_chars: int = DEFAULT_KNOWLEDGE_MAX_QUERY_CHARS,
        max_parsed_chars: int = DEFAULT_KNOWLEDGE_MAX_PARSED_CHARS,
    ) -> None:
        if type(max_results) is not int or max_results < 1:
            raise ValueError("max_results must be positive")
        if type(max_query_chars) is not int or max_query_chars < 1:
            raise ValueError("max_query_chars must be positive")
        if type(max_parsed_chars) is not int or max_parsed_chars < 1:
            raise ValueError("max_parsed_chars must be positive")
        self.object_store = object_store
        self.retriever_factory = retriever_factory
        self.token_counter = token_counter
        self.max_results = max_results
        self.max_query_chars = max_query_chars
        self.max_parsed_chars = max_parsed_chars

    def search(
        self,
        *,
        call_id: str,
        query: str,
        scopes: list[ResolvedCollectionScope],
        available_tokens: int,
    ) -> KnowledgeSearchResult:
        normalized_query = self._normalize_query(query)
        if type(available_tokens) is not int or available_tokens < 0:
            raise bad_request(
                "context_too_large",
                "No context budget remains for the knowledge result.",
            )
        self._validate_scopes(scopes)
        self._ensure_fits(
            call_id=call_id,
            content=serialize_knowledge_result("direct", []),
            available_tokens=available_tokens,
        )

        documents = [document for group in scopes for document in group.documents]
        if not documents:
            content = serialize_knowledge_result("direct", [])
            return KnowledgeSearchResult(mode="direct", sources=[], content=content)

        parsed_cache: dict[
            tuple[str, int, str, int, str],
            str,
        ] = {}
        complete_sources: list[KnowledgeSource] = []
        direct_fits = True
        for document in documents:
            text = self._read_authoritative(document, parsed_cache)
            source = self._source(document, content=text)
            candidate = [*complete_sources, source]
            if not self._fits(
                call_id=call_id,
                content=serialize_knowledge_result("direct", candidate),
                available_tokens=available_tokens,
            ):
                direct_fits = False
                break
            complete_sources.append(source)
        if direct_fits and len(complete_sources) == len(documents):
            content = serialize_knowledge_result("direct", complete_sources)
            return KnowledgeSearchResult(
                mode="direct",
                sources=complete_sources,
                content=content,
            )

        candidates = self._retrieve_candidates(
            query=normalized_query,
            scopes=scopes,
            parsed_cache=parsed_cache,
        )
        candidates.sort(key=self._candidate_sort_key)
        capped = candidates[: self.max_results]
        selected = self._fit_candidates(
            capped,
            call_id=call_id,
            available_tokens=available_tokens,
        )
        content = serialize_knowledge_result("rag", selected)
        self._ensure_fits(
            call_id=call_id,
            content=content,
            available_tokens=available_tokens,
        )
        return KnowledgeSearchResult(mode="rag", sources=selected, content=content)

    def _retrieve_candidates(
        self,
        *,
        query: str,
        scopes: list[ResolvedCollectionScope],
        parsed_cache: dict[tuple[str, int, str, int, str], str],
    ) -> list[_Candidate]:
        candidates: list[_Candidate] = []
        seen_chunks: set[tuple[str, int, str, int, str, int, int, int]] = set()
        for group in scopes:
            if not group.documents:
                continue
            generations = [self._generation(item) for item in group.documents]
            documents_by_scope = {self._document_key(item): item for item in group.documents}
            try:
                hits = self.retriever_factory.get(group.config.retriever_type).retrieve(
                    query,
                    scopes=generations,
                    mode=group.config.retrieval_mode,
                    limit=group.config.top_k,
                    vector_weight=group.config.vector_weight,
                    keyword_weight=group.config.keyword_weight,
                )
                if len(hits) > group.config.top_k:
                    raise VectorStoreUnavailable(
                        "Knowledge retriever result count exceeded limit"
                    )
                for hit in hits:
                    candidate = self._validate_hit(
                        hit,
                        documents_by_scope=documents_by_scope,
                        score_threshold=group.config.score_threshold,
                        expected_mode=group.config.retrieval_mode,
                        parsed_cache=parsed_cache,
                    )
                    if candidate is None:
                        continue
                    source = candidate.source
                    chunk_key = (
                        source.collection_id,
                        candidate.owner_user_id,
                        source.document_id,
                        source.index_generation,
                        source.content_hash,
                        source.chunk_index,
                        candidate.source_start,
                        candidate.source_end,
                    )
                    if chunk_key in seen_chunks:
                        raise VectorStoreUnavailable(
                            "Knowledge retriever result contained a duplicate chunk"
                        )
                    seen_chunks.add(chunk_key)
                    candidates.append(candidate)
            except Exception as error:
                raise self._unavailable("Knowledge retrieval is unavailable.") from error
        return candidates

    def _validate_hit(
        self,
        hit: KnowledgeRetrieverHit,
        *,
        documents_by_scope: dict[tuple[str, int, str, int, str], ReadyDocumentScope],
        score_threshold: float,
        expected_mode: str,
        parsed_cache: dict[tuple[str, int, str, int, str], str],
    ) -> _Candidate | None:
        try:
            metadata = hit.metadata
            hit_content = hit.content
            raw_score = hit.score
        except AttributeError as error:
            raise VectorStoreUnavailable(
                "Knowledge retriever result payload is invalid"
            ) from error
        if not isinstance(metadata, dict):
            raise VectorStoreUnavailable("Knowledge retriever result payload is invalid")
        if hit.retrieval_mode != expected_mode:
            raise VectorStoreUnavailable("Knowledge retriever returned the wrong mode")
        string_fields = (
            "collection_id",
            "document_id",
            "content_hash",
            "filename",
        )
        integer_fields = (
            "owner_user_id",
            "index_generation",
            "chunk_index",
            "source_start",
            "source_end",
        )
        if (
            any(
                not isinstance(metadata.get(field), str) or not metadata[field]
                for field in string_fields
            )
            or any(type(metadata.get(field)) is not int for field in integer_fields)
            or metadata["owner_user_id"] < 0
            or metadata["index_generation"] < 1
            or metadata["chunk_index"] < 0
            or metadata["source_start"] < 0
            or metadata["source_end"] <= metadata["source_start"]
            or not isinstance(hit_content, str)
            or not hit_content.strip()
            or isinstance(raw_score, bool)
            or not isinstance(raw_score, (int, float))
        ):
            raise VectorStoreUnavailable("Knowledge retriever result payload is invalid")
        score = float(raw_score)
        if not math.isfinite(score) or not 0.0 <= score <= 1.0:
            raise VectorStoreUnavailable("Knowledge retriever score is invalid")
        component_scores = (hit.vector_score, hit.keyword_score, hit.fused_score)
        if any(
            value is not None
            and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or not 0.0 <= float(value) <= 1.0
            )
            for value in component_scores
        ):
            raise VectorStoreUnavailable("Knowledge retriever component score is invalid")
        if (
            (expected_mode == "vector" and hit.vector_score != score)
            or (expected_mode == "keyword" and hit.keyword_score != score)
            or (expected_mode == "hybrid" and hit.fused_score != score)
        ):
            raise VectorStoreUnavailable("Knowledge retriever score provenance is invalid")
        document_key = (
            metadata["collection_id"],
            metadata["owner_user_id"],
            metadata["document_id"],
            metadata["index_generation"],
            metadata["content_hash"],
        )
        document = documents_by_scope.get(document_key)
        if document is None or metadata["filename"] != document.filename:
            raise VectorStoreUnavailable(
                "Knowledge retriever result escaped the resolved scope"
            )
        text = self._read_authoritative(document, parsed_cache)
        start = metadata["source_start"]
        end = metadata["source_end"]
        if end > len(text) or hit_content != text[start:end]:
            raise VectorStoreUnavailable(
                "Knowledge retriever result does not match its source"
            )
        if score < score_threshold:
            return None
        return _Candidate(
            source=self._source(
                document,
                content=text[start:end],
                chunk_index=metadata["chunk_index"],
                score=score,
                retrieval_mode=hit.retrieval_mode,
                vector_score=hit.vector_score,
                keyword_score=hit.keyword_score,
                fused_score=hit.fused_score,
            ),
            owner_user_id=document.owner_user_id,
            source_start=start,
            source_end=end,
        )

    def _read_authoritative(
        self,
        document: ReadyDocumentScope,
        cache: dict[tuple[str, int, str, int, str], str],
    ) -> str:
        key = self._document_key(document)
        cached = cache.get(key)
        if cached is not None:
            return cached
        expected_key = KnowledgeDocumentService.parsed_key(
            document.owner_user_id,
            document.collection_id,
            document.document_id,
            document.index_generation,
        )
        if document.parsed_object_key != expected_key:
            raise self._unavailable("Knowledge content is unavailable.")
        try:
            text = read_parsed_text(
                self.object_store,
                object_key=expected_key,
                max_parsed_chars=self.max_parsed_chars,
            )
        except KnowledgeContentUnavailable as error:
            raise self._unavailable("Knowledge content is unavailable.") from error
        cache[key] = text
        return text

    def _fit_candidates(
        self,
        candidates: list[_Candidate],
        *,
        call_id: str,
        available_tokens: int,
    ) -> list[KnowledgeSource]:
        selected: list[KnowledgeSource] = []
        for candidate in candidates:
            next_sources = [*selected, candidate.source]
            if self._fits(
                call_id=call_id,
                content=serialize_knowledge_result("rag", next_sources),
                available_tokens=available_tokens,
            ):
                selected = next_sources
        if candidates and not selected:
            raise bad_request(
                "context_too_large",
                "No retrieved source fits the remaining context budget.",
            )
        return selected

    def _validate_scopes(self, scopes: list[ResolvedCollectionScope]) -> None:
        seen_collections: set[str] = set()
        seen_documents: set[str] = set()
        for group in scopes:
            if (
                not isinstance(group.collection_id, str)
                or not group.collection_id
                or type(group.owner_user_id) is not int
                or group.owner_user_id < 0
                or group.collection_id in seen_collections
            ):
                raise self._unavailable("Knowledge content is unavailable.")
            seen_collections.add(group.collection_id)
            for document in group.documents:
                if (
                    document.collection_id != group.collection_id
                    or document.owner_user_id != group.owner_user_id
                    or document.document_id in seen_documents
                    or not isinstance(document.document_id, str)
                    or not document.document_id
                    or type(document.index_generation) is not int
                    or document.index_generation < 1
                    or not isinstance(document.content_hash, str)
                    or not document.content_hash
                    or not isinstance(document.filename, str)
                    or not document.filename
                ):
                    raise self._unavailable("Knowledge content is unavailable.")
                expected_key = KnowledgeDocumentService.parsed_key(
                    document.owner_user_id,
                    document.collection_id,
                    document.document_id,
                    document.index_generation,
                )
                if document.parsed_object_key != expected_key:
                    raise self._unavailable("Knowledge content is unavailable.")
                seen_documents.add(document.document_id)

    @staticmethod
    def _generation(document: ReadyDocumentScope) -> DocumentGeneration:
        return DocumentGeneration(
            collection_id=document.collection_id,
            owner_user_id=document.owner_user_id,
            document_id=document.document_id,
            index_generation=document.index_generation,
            content_hash=document.content_hash,
        )

    @staticmethod
    def _document_key(
        document: ReadyDocumentScope,
    ) -> tuple[str, int, str, int, str]:
        return (
            document.collection_id,
            document.owner_user_id,
            document.document_id,
            document.index_generation,
            document.content_hash,
        )

    @staticmethod
    def _source(
        document: ReadyDocumentScope,
        *,
        content: str,
        chunk_index: int | None = None,
        score: float | None = None,
        retrieval_mode: str | None = None,
        vector_score: float | None = None,
        keyword_score: float | None = None,
        fused_score: float | None = None,
    ) -> KnowledgeSource:
        return KnowledgeSource(
            document_id=document.document_id,
            collection_id=document.collection_id,
            filename=document.filename,
            index_generation=document.index_generation,
            content_hash=document.content_hash,
            chunk_index=chunk_index,
            score=score,
            retrieval_mode=retrieval_mode,
            vector_score=vector_score,
            keyword_score=keyword_score,
            fused_score=fused_score,
            content=content,
        )

    @staticmethod
    def _candidate_sort_key(candidate: _Candidate) -> tuple[object, ...]:
        source = candidate.source
        assert source.score is not None
        assert source.chunk_index is not None
        return (
            -source.score,
            source.collection_id,
            source.document_id,
            source.index_generation,
            source.chunk_index,
            candidate.source_start,
            candidate.source_end,
            source.content_hash,
        )

    def _fits(
        self,
        *,
        call_id: str,
        content: str,
        available_tokens: int,
    ) -> bool:
        return (
            self.token_counter.count_tool_result(
                call_id=call_id,
                content=content,
            )
            <= available_tokens
        )

    def _ensure_fits(
        self,
        *,
        call_id: str,
        content: str,
        available_tokens: int,
    ) -> None:
        if not self._fits(
            call_id=call_id,
            content=content,
            available_tokens=available_tokens,
        ):
            raise bad_request(
                "context_too_large",
                "No context budget remains for the knowledge result.",
            )

    def _normalize_query(self, query: str) -> str:
        if not isinstance(query, str) or not query.strip():
            raise bad_request(
                "knowledge_query_empty",
                "Knowledge query is required.",
            )
        normalized = query.strip()
        if len(normalized) > self.max_query_chars:
            raise bad_request(
                "knowledge_query_too_long",
                "Knowledge query is too long.",
            )
        return normalized

    @staticmethod
    def _unavailable(message: str):
        return service_unavailable("knowledge_unavailable", message)
