from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
import json

from fastapi import HTTPException
from sqlalchemy.exc import OperationalError

from app.schemas.knowledge_collections import KnowledgeCollectionConfig
from app.services.agent_service import (
    ResolvedAgentConfig,
    ResolvedKnowledgeScope,
    freeze_json,
)
from app.services.knowledge_document_service import KnowledgeDocumentService
from app.services.knowledge_retrieval_service import (
    KnowledgeRetrievalService,
    KnowledgeSearchResult,
    KnowledgeSource,
)
from app.services.knowledge_scope_service import (
    ReadyDocumentScope,
    ResolvedCollectionScope,
)
from app.services.runtime_types import CapabilityContext, ToolCall
from app.services.token_counter import RuntimeTokenCounter
from app.tools.knowledge import KnowledgeCapabilityProvider
from tests.fake_object_store import FakeObjectStore
from tests.fakes import FakeKnowledgeVectorStore


class RecordingSession:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


class RecordingSessionFactory:
    def __init__(self) -> None:
        self.sessions: list[RecordingSession] = []

    def __call__(self) -> RecordingSession:
        session = RecordingSession()
        self.sessions.append(session)
        return session


class RecordingScopeService:
    def __init__(self, resolved: list[ResolvedCollectionScope] | None = None) -> None:
        self.resolved = [] if resolved is None else resolved
        self.calls: list[tuple[RecordingSession, int, tuple[ResolvedKnowledgeScope, ...]]] = []
        self.error: Exception | None = None

    def resolve(
        self,
        session,
        *,
        user_id: int,
        knowledge_scopes: tuple[ResolvedKnowledgeScope, ...],
    ) -> list[ResolvedCollectionScope]:
        self.calls.append((session, user_id, knowledge_scopes))
        if self.error is not None:
            raise self.error
        return self.resolved


class RecordingRetrieval:
    def __init__(self, result: KnowledgeSearchResult) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []
        self.scope_service: RecordingScopeService | None = None
        self.error: Exception | None = None

    def search(self, **kwargs) -> KnowledgeSearchResult:
        assert self.scope_service is not None and self.scope_service.calls
        assert self.scope_service.calls[-1][0].closed is True
        self.calls.append(dict(kwargs))
        if self.error is not None:
            raise self.error
        return self.result


def _knowledge_scope() -> ResolvedKnowledgeScope:
    config = freeze_json(
        KnowledgeCollectionConfig().model_dump(mode="json", exclude_none=False)
    )
    assert isinstance(config, Mapping)
    return ResolvedKnowledgeScope(
        collection_id="collection-1",
        owner_user_id=0,
        document_ids=("document-1",),
        config_json=config,
        updated_at=datetime.now(UTC),
    )


def _agent_config(*, with_knowledge: bool) -> ResolvedAgentConfig:
    config = freeze_json({})
    assert isinstance(config, Mapping)
    return ResolvedAgentConfig(
        agent_id="agent-1",
        owner_user_id=0,
        system_prompt="Help the user.",
        default_model=None,
        home_workspace=None,
        knowledge_scopes=((_knowledge_scope(),) if with_knowledge else ()),
        config_json=config,
        updated_at=datetime.now(UTC),
        config_hash="agent-config-hash",
    )


def _context(
    factory: RecordingSessionFactory,
    *,
    with_knowledge: bool = True,
    token_budget: int = 4_321,
) -> CapabilityContext:
    return CapabilityContext(
        user_id=7,
        agent_config=_agent_config(with_knowledge=with_knowledge),
        session_factory=factory,  # type: ignore[arg-type]
        token_budget=token_budget,
    )


def _source() -> KnowledgeSource:
    return KnowledgeSource(
        document_id="document-1",
        collection_id="collection-1",
        filename="policy.md",
        index_generation=3,
        content_hash="sha256-current",
        chunk_index=2,
        score=0.91,
        content='Exact source with "quotes" and 中文。',
    )


def _provider(
    *,
    result: KnowledgeSearchResult | None = None,
    scopes: list[ResolvedCollectionScope] | None = None,
) -> tuple[
    KnowledgeCapabilityProvider,
    RecordingScopeService,
    RecordingRetrieval,
]:
    resolved_scopes = scopes
    if resolved_scopes is None:
        resolved_scopes = [
            ResolvedCollectionScope(
                collection_id="collection-1",
                owner_user_id=0,
                config=KnowledgeCollectionConfig(),
                documents=(
                    ReadyDocumentScope(
                        collection_id="collection-1",
                        owner_user_id=0,
                        document_id="document-1",
                        index_generation=3,
                        content_hash="sha256-current",
                        parsed_object_key=KnowledgeDocumentService.parsed_key(
                            0,
                            "collection-1",
                            "document-1",
                            3,
                        ),
                        filename="policy.md",
                    ),
                ),
            )
        ]
    scope_service = RecordingScopeService(resolved_scopes)
    retrieval = RecordingRetrieval(
        result
        or KnowledgeSearchResult(
            mode="rag",
            sources=[_source()],
            content='{"escaped":"\\"exact\\" and 中文"}',
        )
    )
    retrieval.scope_service = scope_service
    provider = KnowledgeCapabilityProvider(
        scope_service=scope_service,  # type: ignore[arg-type]
        retrieval=retrieval,  # type: ignore[arg-type]
    )
    return provider, scope_service, retrieval


def test_search_knowledge_exposes_only_query_for_a_bound_agent() -> None:
    provider, _scope_service, _retrieval = _provider()
    factory = RecordingSessionFactory()
    context = _context(factory)

    definition = provider.tool_definitions(context)[0]

    assert definition.name == "search_knowledge"
    assert definition.input_schema == {
        "type": "object",
        "properties": {"query": {"type": "string", "minLength": 1}},
        "required": ["query"],
        "additionalProperties": False,
    }
    assert provider.prompt_modules(context) == ("knowledge_base",)


def test_provider_exposes_no_prompt_or_tool_without_a_bound_scope() -> None:
    provider, scope_service, retrieval = _provider()
    factory = RecordingSessionFactory()
    context = _context(factory, with_knowledge=False)

    assert provider.prompt_modules(context) == ()
    assert provider.tool_definitions(context) == ()
    result = provider.execute(
        ToolCall(
            id="call-hidden",
            name="search_knowledge",
            arguments={"query": "private policy"},
        ),
        context,
    )

    assert result.is_error is True
    assert json.loads(result.content)["code"] == "tool_not_found"
    assert scope_service.calls == []
    assert retrieval.calls == []
    assert factory.sessions == []


def test_execute_uses_current_user_and_frozen_scopes_then_closes_db_session() -> None:
    provider, scope_service, retrieval = _provider()
    factory = RecordingSessionFactory()
    context = _context(factory)
    call = ToolCall(
        id="call-1",
        name="search_knowledge",
        arguments={"query": "policy"},
    )

    result = provider.execute(call, context)

    session, user_id, scopes = scope_service.calls[0]
    assert user_id == context.user_id == 7
    assert scopes is context.agent_config.knowledge_scopes
    assert session.committed is True
    assert session.closed is True
    assert session.rolled_back is False
    assert retrieval.calls == [
        {
            "call_id": call.id,
            "query": "policy",
            "scopes": scope_service.resolved,
            "available_tokens": context.token_budget,
        }
    ]
    assert retrieval.calls[0]["scopes"]
    assert result.content is retrieval.result.content
    assert result.metadata == {
        "tool": "search_knowledge",
        "mode": "rag",
        "sources": [
            {
                "document_id": "document-1",
                "collection_id": "collection-1",
                "filename": "policy.md",
                "index_generation": 3,
                "content_hash": "sha256-current",
                "chunk_index": 2,
                "score": 0.91,
            }
        ],
    }


def test_extra_arguments_cannot_expand_agent_scope_or_reach_services() -> None:
    provider, scope_service, retrieval = _provider()
    factory = RecordingSessionFactory()
    result = provider.execute(
        ToolCall(
            id="call-extra",
            name="search_knowledge",
            arguments={
                "query": "policy",
                "collection_id": "collection-other",
                "document_ids": ["document-secret"],
            },
        ),
        _context(factory),
    )

    assert result.is_error is True
    assert json.loads(result.content) == {
        "code": "knowledge_request_invalid",
        "message": "Knowledge query is invalid.",
    }
    assert "collection-other" not in result.content
    assert "document-secret" not in result.content
    assert scope_service.calls == []
    assert retrieval.calls == []


def test_http_failure_is_structured_without_leaking_internal_detail() -> None:
    provider, scope_service, retrieval = _provider()
    factory = RecordingSessionFactory()
    retrieval.error = HTTPException(
        status_code=503,
        detail={
            "code": "knowledge_unavailable",
            "message": "private object-store endpoint and credential detail",
        },
    )
    context = _context(factory)

    result = provider.execute(
        ToolCall(
            id="call-error",
            name="search_knowledge",
            arguments={"query": "policy"},
        ),
        context,
    )

    assert scope_service.calls
    assert result.is_error is True
    assert json.loads(result.content) == {
        "code": "knowledge_unavailable",
        "message": "Knowledge is unavailable.",
    }
    assert "private object-store" not in result.content


def test_scope_database_failure_rolls_back_and_returns_structured_error() -> None:
    provider, scope_service, retrieval = _provider()
    factory = RecordingSessionFactory()
    scope_service.error = OperationalError(
        "SELECT private_table",
        {"password": "database-secret"},
        RuntimeError("private database endpoint"),
    )

    result = provider.execute(
        ToolCall(
            id="call-database-error",
            name="search_knowledge",
            arguments={"query": "policy"},
        ),
        _context(factory),
    )

    assert result.is_error is True
    assert json.loads(result.content) == {
        "code": "knowledge_unavailable",
        "message": "Knowledge is unavailable.",
    }
    assert "private" not in result.content
    assert "database-secret" not in result.content
    assert retrieval.calls == []
    assert factory.sessions[0].rolled_back is True
    assert factory.sessions[0].closed is True


def test_corrupt_direct_source_returns_error_without_rag_fallback() -> None:
    factory = RecordingSessionFactory()
    document = ReadyDocumentScope(
        collection_id="collection-1",
        owner_user_id=7,
        document_id="document-1",
        index_generation=1,
        content_hash="sha256-current",
        parsed_object_key=KnowledgeDocumentService.parsed_key(
            7,
            "collection-1",
            "document-1",
            1,
        ),
        filename="policy.md",
    )
    scopes = [
        ResolvedCollectionScope(
            collection_id="collection-1",
            owner_user_id=7,
            config=KnowledgeCollectionConfig(),
            documents=(document,),
        )
    ]
    vector_store = FakeKnowledgeVectorStore()
    scope_service = RecordingScopeService(scopes)
    retrieval = KnowledgeRetrievalService(
        object_store=FakeObjectStore(),
        vector_store=vector_store,
        token_counter=RuntimeTokenCounter(image_input_token_reserve=1_024),
    )
    provider = KnowledgeCapabilityProvider(
        scope_service=scope_service,  # type: ignore[arg-type]
        retrieval=retrieval,
    )

    result = provider.execute(
        ToolCall(
            id="call-corrupt",
            name="search_knowledge",
            arguments={"query": "policy"},
        ),
        _context(factory, token_budget=10_000),
    )

    assert result.is_error is True
    assert json.loads(result.content)["code"] == "knowledge_unavailable"
    assert vector_store.search_calls == []
