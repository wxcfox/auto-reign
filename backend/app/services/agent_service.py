from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from types import MappingProxyType
from typing import TypeAlias

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import (
    bad_request,
    conflict,
    forbidden,
    not_found,
    service_unavailable,
)
from app.core.model_providers import find_chat_provider
from app.db import models
from app.repositories.knowledge_document_repository import (
    KnowledgeDocumentRepository,
)
from app.repositories.resource_repository import ResourceRepository
from app.schemas.agents import (
    AgentConfig,
    AgentCreateRequest,
    AgentListResponse,
    AgentPutRequest,
    AgentResponse,
)
from app.schemas.knowledge_collections import KnowledgeCollectionConfig
from app.schemas.modeling import ModelRef
from app.schemas.resources import ResourceDeleteResponse, ResourceListScope
from app.schemas.workspaces import WorkspaceConfig
from app.services.config_service import default_chat_model


JsonScalar: TypeAlias = str | int | float | bool | None
FrozenJsonValue: TypeAlias = (
    JsonScalar
    | tuple["FrozenJsonValue", ...]
    | Mapping[str, "FrozenJsonValue"]
)


@dataclass(frozen=True)
class ResolvedAgentHome:
    workspace_id: str
    owner_user_id: int
    initial_agents_md: str
    config_json: Mapping[str, FrozenJsonValue]
    updated_at: datetime


@dataclass(frozen=True)
class ResolvedKnowledgeScope:
    collection_id: str
    owner_user_id: int
    document_ids: tuple[str, ...] | None
    config_json: Mapping[str, FrozenJsonValue]
    updated_at: datetime


@dataclass(frozen=True)
class ResolvedAgentConfig:
    agent_id: str
    owner_user_id: int
    system_prompt: str
    default_model: ModelRef | None
    home_workspace: ResolvedAgentHome | None
    knowledge_scopes: tuple[ResolvedKnowledgeScope, ...]
    config_json: Mapping[str, FrozenJsonValue]
    updated_at: datetime
    config_hash: str


@dataclass(frozen=True)
class ResolvedAgent:
    id: str
    name: str
    config: ResolvedAgentConfig
    updated_at: datetime
    config_hash: str


def freeze_json(value: object) -> FrozenJsonValue:
    """Return a normalized, recursively immutable JSON projection."""
    normalized = _normalize_json(value)
    return _freeze_normalized_json(normalized)


class AgentService:
    def __init__(
        self,
        resources: ResourceRepository | None = None,
        knowledge_documents: KnowledgeDocumentRepository | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.resources = resources or ResourceRepository()
        self.knowledge_documents = (
            knowledge_documents or KnowledgeDocumentRepository()
        )
        self.settings = settings or get_settings()

    @staticmethod
    def plain_chat_agent() -> ResolvedAgent:
        """Return the capability-free configuration used by plain LLM chats."""
        config = ResolvedAgentConfig(
            agent_id="",
            owner_user_id=0,
            system_prompt="",
            default_model=None,
            home_workspace=None,
            knowledge_scopes=(),
            config_json=MappingProxyType({}),
            updated_at=datetime.min,
            config_hash="plain-chat-v1",
        )
        return ResolvedAgent(
            id="",
            name="No agent",
            config=config,
            updated_at=datetime.min,
            config_hash=config.config_hash,
        )

    def list_agents(
        self,
        session: Session,
        *,
        actor: models.User,
        scope: ResourceListScope = "visible",
        include_inactive: bool = False,
    ) -> AgentListResponse:
        if include_inactive and scope == "visible":
            raise bad_request(
                "resource_scope_invalid",
                "Inactive resources require an owned or global scope.",
            )
        if include_inactive and scope == "global" and actor.role != "admin":
            raise forbidden(
                "admin_required",
                "Administrator access is required.",
            )
        resources = self.resources.list_visible(
            session,
            user_id=actor.id,
            resource_type="agent",
            scope=scope,
            include_inactive=include_inactive,
        )
        return AgentListResponse(
            agents=[self._response(resource, actor) for resource in resources]
        )

    def get_agent(
        self,
        session: Session,
        *,
        actor: models.User,
        agent_id: str,
    ) -> AgentResponse:
        resource = self.resources.get_visible(
            session,
            user_id=actor.id,
            resource_id=agent_id,
            resource_type="agent",
        )
        if resource is None:
            raise self._not_found()
        return self._response(resource, actor)

    def create_private(
        self,
        session: Session,
        *,
        actor: models.User,
        payload: AgentCreateRequest,
    ) -> AgentResponse:
        resource = self._create(session, owner_id=actor.id, payload=payload)
        return self._response(resource, actor)

    def create_global(
        self,
        session: Session,
        *,
        actor: models.User,
        payload: AgentCreateRequest,
    ) -> AgentResponse:
        if actor.role != "admin":
            raise forbidden(
                "admin_required",
                "Administrator access is required.",
            )
        resource = self._create(session, owner_id=0, payload=payload)
        return self._response(resource, actor)

    def put_agent(
        self,
        session: Session,
        *,
        actor: models.User,
        agent_id: str,
        payload: AgentPutRequest,
    ) -> AgentResponse:
        resource = self.resources.get_for_update(
            session,
            resource_id=agent_id,
            resource_type="agent",
        )
        self._ensure_manageable(resource, actor)
        assert resource is not None

        self._validate_configured_model(payload.config.default_model)
        self._validate_references(
            session,
            owner_id=resource.user_id,
            config=payload.config,
        )
        resource.name = payload.name
        resource.config_json = payload.config.model_dump(mode="json")
        resource.is_active = payload.is_active
        resource.updated_at = models._now()
        self._flush_with_name_conflict(session)
        return self._response(resource, actor)

    def delete_agent(
        self,
        session: Session,
        *,
        actor: models.User,
        agent_id: str,
    ) -> ResourceDeleteResponse:
        resource = self.resources.get_for_update(
            session,
            resource_id=agent_id,
            resource_type="agent",
        )
        self._ensure_manageable(resource, actor)
        assert resource is not None
        self.resources.soft_delete(session, resource)
        return ResourceDeleteResponse(id=resource.id, status="deleted")

    def resolve_for_turn(
        self,
        session: Session,
        *,
        user_id: int,
        agent_id: str,
    ) -> ResolvedAgent:
        resource = self.resources.get_visible_for_update(
            session,
            user_id=user_id,
            resource_id=agent_id,
            resource_type="agent",
        )
        if resource is None:
            raise conflict("agent_unavailable", "Agent is unavailable.")
        config = AgentConfig.model_validate(resource.config_json)
        resources_by_id, _documents_by_id, reference_configs = (
            self._lock_and_validate_references(
                session,
                owner_id=resource.user_id,
                config=config,
            )
        )
        normalized_agent, frozen_agent = _normalized_frozen_mapping(
            config.model_dump(mode="json", exclude_none=False)
        )

        resolved_home: ResolvedAgentHome | None = None
        normalized_home_snapshot: dict[str, object] | None = None
        if config.home_workspace_id is not None:
            workspace = resources_by_id[config.home_workspace_id]
            workspace_config = reference_configs[workspace.id]
            assert isinstance(workspace_config, WorkspaceConfig)
            normalized_workspace, frozen_workspace = _normalized_frozen_mapping(
                workspace_config.model_dump(mode="json", exclude_none=False)
            )
            resolved_home = ResolvedAgentHome(
                workspace_id=workspace.id,
                owner_user_id=workspace.user_id,
                initial_agents_md=workspace_config.initial_agents_md,
                config_json=frozen_workspace,
                updated_at=workspace.updated_at,
            )
            normalized_home_snapshot = {
                "workspace_id": workspace.id,
                "owner_user_id": workspace.user_id,
                "updated_at": workspace.updated_at.isoformat(),
                "config": normalized_workspace,
            }

        resolved_scopes: list[ResolvedKnowledgeScope] = []
        normalized_scope_snapshots: list[dict[str, object]] = []
        for scope in config.knowledge_scopes:
            collection = resources_by_id[scope.collection_id]
            collection_config = reference_configs[collection.id]
            assert isinstance(collection_config, KnowledgeCollectionConfig)
            normalized_collection, frozen_collection = _normalized_frozen_mapping(
                collection_config.model_dump(mode="json", exclude_none=False)
            )
            document_ids = (
                tuple(scope.document_ids)
                if scope.document_ids is not None
                else None
            )
            resolved_scopes.append(
                ResolvedKnowledgeScope(
                    collection_id=collection.id,
                    owner_user_id=collection.user_id,
                    document_ids=document_ids,
                    config_json=frozen_collection,
                    updated_at=collection.updated_at,
                )
            )
            normalized_scope_snapshots.append(
                {
                    "collection_id": collection.id,
                    "owner_user_id": collection.user_id,
                    "updated_at": collection.updated_at.isoformat(),
                    "config": normalized_collection,
                    "document_ids": (
                        list(document_ids) if document_ids is not None else None
                    ),
                }
            )

        expanded_payload = {
            "agent": {
                "id": resource.id,
                "owner_user_id": resource.user_id,
                "updated_at": resource.updated_at.isoformat(),
                "config": normalized_agent,
            },
            "home": normalized_home_snapshot,
            "knowledge_scopes": normalized_scope_snapshots,
        }
        canonical = json.dumps(
            expanded_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        config_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        resolved_config = ResolvedAgentConfig(
            agent_id=resource.id,
            owner_user_id=resource.user_id,
            system_prompt=config.system_prompt,
            default_model=config.default_model,
            home_workspace=resolved_home,
            knowledge_scopes=tuple(resolved_scopes),
            config_json=frozen_agent,
            updated_at=resource.updated_at,
            config_hash=config_hash,
        )
        return ResolvedAgent(
            id=resource.id,
            name=resource.name,
            config=resolved_config,
            updated_at=resource.updated_at,
            config_hash=config_hash,
        )

    def resolve_model(
        self,
        *,
        agent: ResolvedAgent | None,
        conversation_override: ModelRef | None,
    ) -> ModelRef:
        selected = conversation_override or (agent.config.default_model if agent else None)
        if selected is None:
            default = default_chat_model(self.settings)
            if default is None:
                raise self._runtime_model_unavailable()
            selected = ModelRef.model_validate(default)
        provider = find_chat_provider(self.settings, selected.provider)
        if provider is None or selected.model not in provider.models:
            raise self._runtime_model_unavailable()
        return selected

    def _create(
        self,
        session: Session,
        *,
        owner_id: int,
        payload: AgentCreateRequest,
    ) -> models.Resource:
        self._validate_configured_model(payload.config.default_model)
        try:
            resource = self.resources.create(
                session,
                owner_id=owner_id,
                resource_type="agent",
                name=payload.name,
                config_json=payload.config.model_dump(mode="json"),
            )
        except IntegrityError as error:
            raise self._name_conflict() from error
        self._validate_references(
            session,
            owner_id=owner_id,
            config=payload.config,
        )
        return resource

    def _validate_references(
        self,
        session: Session,
        *,
        owner_id: int,
        config: AgentConfig,
    ) -> None:
        self._lock_and_validate_references(
            session,
            owner_id=owner_id,
            config=config,
        )

    def _lock_and_validate_references(
        self,
        session: Session,
        *,
        owner_id: int,
        config: AgentConfig,
    ) -> tuple[
        dict[str, models.Resource],
        dict[str, models.KnowledgeDocument],
        dict[str, WorkspaceConfig | KnowledgeCollectionConfig],
    ]:
        expected_types: dict[str, str] = {}
        requested_resource_ids: list[str] = []
        if config.home_workspace_id is not None:
            expected_types[config.home_workspace_id] = "workspace"
            requested_resource_ids.append(config.home_workspace_id)
        for scope in config.knowledge_scopes:
            if scope.collection_id in expected_types:
                raise self._reference_invalid()
            expected_types[scope.collection_id] = "knowledge_collection"
            requested_resource_ids.append(scope.collection_id)

        resources_by_id: dict[str, models.Resource] = {}
        if requested_resource_ids:
            locked_resources = list(
                session.scalars(
                    select(models.Resource)
                    .where(models.Resource.id.in_(sorted(requested_resource_ids)))
                    .order_by(models.Resource.id)
                    .with_for_update()
                )
            )
            if {resource.id for resource in locked_resources} != set(
                requested_resource_ids
            ):
                raise self._reference_invalid()
            resources_by_id = {
                resource.id: resource for resource in locked_resources
            }

        for resource_id, expected_type in expected_types.items():
            resource = resources_by_id[resource_id]
            if (
                resource.resource_type != expected_type
                or not resource.is_active
                or resource.deleted_at is not None
                or not self._owner_is_allowed(owner_id, resource.user_id)
            ):
                raise self._reference_invalid()

        reference_configs: dict[
            str, WorkspaceConfig | KnowledgeCollectionConfig
        ] = {}
        try:
            for resource_id, expected_type in expected_types.items():
                resource = resources_by_id[resource_id]
                if expected_type == "workspace":
                    reference_configs[resource_id] = WorkspaceConfig.model_validate(
                        resource.config_json
                    )
                else:
                    reference_configs[resource_id] = (
                        KnowledgeCollectionConfig.model_validate(
                            resource.config_json
                        )
                    )
        except ValidationError:
            raise self._reference_invalid() from None

        requested_documents: list[tuple[str, str]] = []
        for scope in config.knowledge_scopes:
            if scope.document_ids is not None:
                requested_documents.extend(
                    (document_id, scope.collection_id)
                    for document_id in scope.document_ids
                )
        document_ids = [document_id for document_id, _ in requested_documents]
        if len(document_ids) != len(set(document_ids)):
            raise self._reference_invalid()
        if not document_ids:
            return resources_by_id, {}, reference_configs

        documents_by_id: dict[str, models.KnowledgeDocument] = {}
        scopes_by_collection = {
            scope.collection_id: scope for scope in config.knowledge_scopes
        }
        for collection_id in sorted(scopes_by_collection):
            scope = scopes_by_collection[collection_id]
            if scope.document_ids is None:
                continue
            collection = resources_by_id[collection_id]
            requested_ids = tuple(sorted(scope.document_ids))
            locked_documents = self.knowledge_documents.lock_active_references(
                session,
                collection_id=collection_id,
                owner_user_id=collection.user_id,
                document_ids=requested_ids,
            )
            if {document.id for document in locked_documents} != set(requested_ids):
                raise self._reference_invalid()
            documents_by_id.update(
                {document.id: document for document in locked_documents}
            )
        return resources_by_id, documents_by_id, reference_configs

    def _validate_configured_model(self, model: ModelRef | None) -> None:
        if model is None:
            return
        provider = find_chat_provider(self.settings, model.provider)
        if provider is None or model.model not in provider.models:
            raise bad_request(
                "model_unavailable",
                "The selected model is unavailable.",
            )

    @staticmethod
    def _owner_is_allowed(agent_owner_id: int, resource_owner_id: int) -> bool:
        if agent_owner_id == 0:
            return resource_owner_id == 0
        return resource_owner_id in {0, agent_owner_id}

    @staticmethod
    def _can_manage(resource: models.Resource, actor: models.User) -> bool:
        return resource.user_id == actor.id or (
            resource.user_id == 0 and actor.role == "admin"
        )

    def _ensure_manageable(
        self,
        resource: models.Resource | None,
        actor: models.User,
    ) -> None:
        if (
            resource is None
            or resource.deleted_at is not None
            or not self._can_manage(resource, actor)
        ):
            raise self._not_found()

    @staticmethod
    def _response(
        resource: models.Resource,
        actor: models.User,
    ) -> AgentResponse:
        return AgentResponse(
            id=resource.id,
            name=resource.name,
            scope="global" if resource.user_id == 0 else "private",
            can_manage=AgentService._can_manage(resource, actor),
            is_active=resource.is_active,
            config=AgentConfig.model_validate(resource.config_json),
            created_at=resource.created_at,
            updated_at=resource.updated_at,
        )

    @staticmethod
    def _flush_with_name_conflict(session: Session) -> None:
        try:
            session.flush()
        except IntegrityError as error:
            raise AgentService._name_conflict() from error

    @staticmethod
    def _not_found():
        return not_found("resource_not_found", "Agent not found.")

    @staticmethod
    def _name_conflict():
        return conflict(
            "resource_name_taken",
            "An Agent with this name already exists.",
        )

    @staticmethod
    def _reference_invalid():
        return bad_request(
            "resource_reference_invalid",
            "An Agent resource reference is invalid.",
        )

    @staticmethod
    def _runtime_model_unavailable():
        return service_unavailable(
            "model_unavailable",
            "The selected model is unavailable.",
        )


def _normalize_json(value: object) -> object:
    try:
        canonical = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return json.loads(canonical)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("value must be valid JSON") from error


def _freeze_normalized_json(value: object) -> FrozenJsonValue:
    if isinstance(value, dict):
        return MappingProxyType(
            {
                str(key): _freeze_normalized_json(item)
                for key, item in value.items()
            }
        )
    if isinstance(value, list):
        return tuple(_freeze_normalized_json(item) for item in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError("normalized value is not JSON")


def _normalized_frozen_mapping(
    value: object,
) -> tuple[dict[str, object], Mapping[str, FrozenJsonValue]]:
    normalized = _normalize_json(value)
    if not isinstance(normalized, dict):
        raise ValueError("JSON object is required")
    frozen = _freeze_normalized_json(normalized)
    if not isinstance(frozen, Mapping):
        raise AssertionError("frozen JSON object is required")
    return normalized, frozen
