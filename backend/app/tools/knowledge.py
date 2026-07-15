from __future__ import annotations

import json

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import session_scope
from app.repositories.vector_store import VectorStoreUnavailable
from app.services.knowledge_retrieval_service import KnowledgeRetrievalService
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


_PUBLIC_ERROR_MESSAGES = {
    "context_too_large": "The knowledge result exceeds the remaining context budget.",
    "knowledge_query_empty": "Knowledge query is required.",
    "knowledge_query_too_long": "Knowledge query is too long.",
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
                    "Search the read-only knowledge sources bound to this Agent."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "minLength": 1}
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
            with session_scope(context.session_factory) as session:
                scopes = self.scope_service.resolve(
                    session,
                    user_id=context.user_id,
                    knowledge_scopes=context.agent_config.knowledge_scopes,
                )

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
