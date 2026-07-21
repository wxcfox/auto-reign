from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.errors import service_unavailable
from app.repositories.knowledge_document_repository import (
    KnowledgeDocumentRepository,
    ReadyDocumentFilter,
)
from app.schemas.knowledge_collections import KnowledgeCollectionConfig
from app.services.agent_service import ResolvedKnowledgeScope
from app.services.knowledge_document_service import KnowledgeDocumentService


@dataclass(frozen=True)
class ReadyDocumentScope:
    collection_id: str
    owner_user_id: int
    document_id: str
    index_generation: int
    content_hash: str
    parsed_object_key: str
    filename: str


@dataclass(frozen=True)
class ResolvedCollectionScope:
    collection_id: str
    owner_user_id: int
    config: KnowledgeCollectionConfig
    documents: tuple[ReadyDocumentScope, ...]


class KnowledgeScopeService:
    def __init__(
        self,
        repository: KnowledgeDocumentRepository | None = None,
    ) -> None:
        self.repository = repository or KnowledgeDocumentRepository()

    def resolve(
        self,
        session: Session,
        *,
        user_id: int,
        knowledge_scopes: tuple[ResolvedKnowledgeScope, ...],
    ) -> list[ResolvedCollectionScope]:
        configured = self._validate_configured_scopes(
            user_id=user_id,
            knowledge_scopes=knowledge_scopes,
        )
        if not configured:
            return []

        documents = self.repository.list_ready_for_scopes(
            session,
            scopes=tuple(
                ReadyDocumentFilter(
                    collection_id=scope.collection_id,
                    owner_user_id=scope.owner_user_id,
                    document_ids=scope.document_ids,
                )
                for scope, _config in configured
            ),
        )

        by_pair: dict[tuple[str, int], list[ReadyDocumentScope]] = {
            (scope.collection_id, scope.owner_user_id): [] for scope, _config in configured
        }
        seen_document_ids: set[str] = set()
        configured_by_pair = {
            (scope.collection_id, scope.owner_user_id): (scope, config)
            for scope, config in configured
        }
        for document in documents:
            pair = (document.collection_id, document.user_id)
            configured_scope = configured_by_pair.get(pair)
            if configured_scope is None:
                raise self._unavailable()
            scope, config = configured_scope
            if (
                document.id in seen_document_ids
                or not document.is_active
                or document.status != "ready"
                or document.retriever_type != config.retriever_type
                or (scope.document_ids is not None and document.id not in scope.document_ids)
            ):
                raise self._unavailable()
            ready = self._project_document(document)
            seen_document_ids.add(document.id)
            by_pair[pair].append(ready)

        resolved: list[ResolvedCollectionScope] = []
        for scope, config in configured:
            ready_documents = tuple(
                sorted(
                    by_pair[(scope.collection_id, scope.owner_user_id)],
                    key=lambda item: item.document_id,
                )
            )
            if not ready_documents:
                continue
            resolved.append(
                ResolvedCollectionScope(
                    collection_id=scope.collection_id,
                    owner_user_id=scope.owner_user_id,
                    config=config,
                    documents=ready_documents,
                )
            )
        return resolved

    def _validate_configured_scopes(
        self,
        *,
        user_id: int,
        knowledge_scopes: tuple[ResolvedKnowledgeScope, ...],
    ) -> list[tuple[ResolvedKnowledgeScope, KnowledgeCollectionConfig]]:
        if type(user_id) is not int or user_id <= 0:
            raise self._unavailable()

        seen_collections: set[str] = set()
        seen_document_ids: set[str] = set()
        validated: list[tuple[ResolvedKnowledgeScope, KnowledgeCollectionConfig]] = []
        for scope in knowledge_scopes:
            if (
                not isinstance(scope.collection_id, str)
                or not scope.collection_id
                or type(scope.owner_user_id) is not int
                or scope.owner_user_id not in {0, user_id}
                or scope.collection_id in seen_collections
            ):
                raise self._unavailable()
            if scope.document_ids is not None:
                if (
                    not scope.document_ids
                    or any(
                        not isinstance(document_id, str) or not document_id
                        for document_id in scope.document_ids
                    )
                    or len(scope.document_ids) != len(set(scope.document_ids))
                    or bool(set(scope.document_ids) & seen_document_ids)
                ):
                    raise self._unavailable()
            try:
                config = KnowledgeCollectionConfig.model_validate(scope.config_json)
            except (TypeError, ValueError, ValidationError):
                raise self._unavailable() from None
            seen_collections.add(scope.collection_id)
            seen_document_ids.update(scope.document_ids or ())
            validated.append((scope, config))
        return validated

    @classmethod
    def _project_document(cls, document) -> ReadyDocumentScope:
        if (
            not isinstance(document.id, str)
            or not document.id
            or not isinstance(document.collection_id, str)
            or not document.collection_id
            or type(document.user_id) is not int
            or document.user_id < 0
            or type(document.index_generation) is not int
            or document.index_generation < 1
            or not isinstance(document.content_hash, str)
            or not document.content_hash
            or not isinstance(document.name, str)
            or not document.name
            or not isinstance(document.parsed_object_key, str)
            or not document.parsed_object_key
        ):
            raise cls._unavailable()
        expected_key = KnowledgeDocumentService.parsed_key(
            document.user_id,
            document.collection_id,
            document.id,
            document.index_generation,
        )
        if document.parsed_object_key != expected_key:
            raise cls._unavailable()
        return ReadyDocumentScope(
            collection_id=document.collection_id,
            owner_user_id=document.user_id,
            document_id=document.id,
            index_generation=document.index_generation,
            content_hash=document.content_hash,
            parsed_object_key=expected_key,
            filename=document.name,
        )

    @staticmethod
    def _unavailable():
        return service_unavailable(
            "knowledge_unavailable",
            "Knowledge content is unavailable.",
        )
