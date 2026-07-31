from __future__ import annotations

import json

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import session_scope
from app.repositories.vector_store import VectorStoreUnavailable
from app.services.knowledge_retrieval_service import (
    KnowledgeRetrievalService,
    knowledge_result_status,
)
from app.services.knowledge_scope_service import KnowledgeScopeService
from app.services.runtime_types import (
    CapabilityContext,
    ToolCall,
    ToolDefinition,
    ToolResult,
)
from app.storage.object_store import ObjectStoreError


class SearchKnowledgeArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)


class _ScopeUnavailable(Exception):
    """Knowledge scope resolution failed before any retrieval happened."""


# Each code names one failure class the model can act on differently: a budget
# limit, a bad argument, an unavailable retriever, unreadable source content or
# an unusable scope. None of them mean "the knowledge holds no match" - that is
# a successful result with status ``no_match``.
_PUBLIC_ERROR_MESSAGES = {
    "context_too_large": "The knowledge result exceeds the remaining context budget.",
    "knowledge_query_empty": "Knowledge query is required.",
    "knowledge_query_too_long": "Knowledge query is too long.",
    "knowledge_retriever_unavailable": "Knowledge retrieval is unavailable.",
    "knowledge_content_unavailable": "Knowledge content is unavailable.",
    "knowledge_scope_unavailable": "The knowledge scope is unavailable.",
    "knowledge_unavailable": "Knowledge is unavailable.",
}


class KnowledgeCapabilityProvider:
    def __init__(
        self,
        *,
        scope_service: KnowledgeScopeService,
        retrieval: KnowledgeRetrievalService,
    ) -> None:
        self.scope_service = scope_service
        self.retrieval = retrieval

    def prompt_modules(self, context: CapabilityContext) -> tuple[str, ...]:
        if not context.agent_config.knowledge_scopes:
            return ()
        return ("knowledge_base",)

    def tool_definitions(
        self,
        context: CapabilityContext,
    ) -> tuple[ToolDefinition, ...]:
        if not context.agent_config.knowledge_scopes:
            return ()
        return (
            ToolDefinition(
                name="search_knowledge",
                description=(
                    "Search the read-only knowledge collections bound to this "
                    "Agent, listed by name in [CAPABILITY_SOURCES]. They hold "
                    "reference material the user curated and indexed ahead of "
                    "time - documents, manuals, notes, study or course "
                    "material, specifications - retrieved by content rather "
                    "than by path. Use it when answering needs that reference "
                    'material. Returns {"status":"hit","sources":[...]} with '
                    'authoritative source excerpts, or '
                    '{"status":"no_match","sources":[]} when nothing in scope '
                    "matches; an empty match is a valid finding, not a "
                    'failure. On a hit, `mode` reports coverage: "direct" '
                    "returned the full text of every in-scope document, while "
                    '"rag" returned only the excerpts matching this query, so '
                    "absent content is not proven absent. A returned `code` "
                    "means the knowledge backend failed and says nothing about "
                    "whether the material exists."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "minLength": 1,
                            "description": (
                                "Search terms expected to appear in the source "
                                "text. Use one topic per call rather than the "
                                "user's full sentence."
                            ),
                        }
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            ),
        )

    def execute(self, call: ToolCall, context: CapabilityContext) -> ToolResult:
        if call.name != "search_knowledge" or not context.agent_config.knowledge_scopes:
            return self._error(
                call,
                "tool_not_found",
                "The requested tool is unavailable.",
            )
        try:
            query = SearchKnowledgeArguments.model_validate(call.arguments).query
            try:
                with session_scope(context.session_factory) as session:
                    scopes = self.scope_service.resolve(
                        session,
                        user_id=context.user_id,
                        knowledge_scopes=context.agent_config.knowledge_scopes,
                    )
            except HTTPException as error:
                # Scope resolution owns tenant isolation and resource
                # references. Its failures are never a retrieval outcome.
                raise _ScopeUnavailable() from error

            result = self.retrieval.search(
                call_id=call.id,
                query=query,
                scopes=scopes,
                available_tokens=context.token_budget,
            )
            return ToolResult(
                call_id=call.id,
                content=result.content,
                metadata={
                    "tool": call.name,
                    "mode": result.mode,
                    "status": knowledge_result_status(result.sources),
                    "sources": [
                        {
                            "document_id": source.document_id,
                            "collection_id": source.collection_id,
                            "filename": source.filename,
                            "index_generation": source.index_generation,
                            "content_hash": source.content_hash,
                            "chunk_index": source.chunk_index,
                            "score": source.score,
                        }
                        for source in result.sources
                    ],
                },
            )
        except ValidationError:
            return self._error(
                call,
                "knowledge_request_invalid",
                "Knowledge query is invalid.",
            )
        except _ScopeUnavailable:
            return self._error(
                call,
                "knowledge_scope_unavailable",
                _PUBLIC_ERROR_MESSAGES["knowledge_scope_unavailable"],
            )
        except HTTPException as error:
            detail = error.detail if isinstance(error.detail, dict) else {}
            code = detail.get("code")
            public_code = (
                code
                if isinstance(code, str) and code in _PUBLIC_ERROR_MESSAGES
                else "knowledge_unavailable"
            )
            return self._error(
                call,
                public_code,
                _PUBLIC_ERROR_MESSAGES[public_code],
            )
        except (ObjectStoreError, VectorStoreUnavailable, SQLAlchemyError):
            return self._error(
                call,
                "knowledge_unavailable",
                "Knowledge is unavailable.",
            )

    @staticmethod
    def _error(call: ToolCall, code: str, message: str) -> ToolResult:
        return ToolResult(
            call_id=call.id,
            content=json.dumps(
                {"code": code, "message": message},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            is_error=True,
            metadata={"tool": call.name, "code": code},
        )
