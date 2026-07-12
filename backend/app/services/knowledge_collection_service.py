from typing import Literal

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import bad_request, conflict, forbidden, not_found
from app.db import models
from app.repositories.resource_repository import ResourceRepository
from app.schemas.agents import AgentConfig
from app.schemas.knowledge_collections import (
    KnowledgeCollectionConfig,
    KnowledgeCollectionCreateRequest,
    KnowledgeCollectionListResponse,
    KnowledgeCollectionPutRequest,
    KnowledgeCollectionResponse,
)
from app.schemas.resources import ResourceDeleteResponse, ResourceListScope


class KnowledgeCollectionService:
    def __init__(self, resources: ResourceRepository | None = None) -> None:
        self.resources = resources or ResourceRepository()

    def list_resources(
        self,
        session: Session,
        *,
        actor: models.User,
        scope: ResourceListScope = "visible",
        include_inactive: bool = False,
    ) -> KnowledgeCollectionListResponse:
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
            resource_type="knowledge_collection",
            scope=scope,
            include_inactive=include_inactive,
        )
        return KnowledgeCollectionListResponse(
            collections=[self._response(item, actor) for item in resources]
        )

    def get_resource(
        self,
        session: Session,
        *,
        actor: models.User,
        resource_id: str,
    ) -> KnowledgeCollectionResponse:
        resource = self.resources.get_visible(
            session,
            user_id=actor.id,
            resource_id=resource_id,
            resource_type="knowledge_collection",
        )
        if resource is None:
            raise self._not_found()
        return self._response(resource, actor)

    def require_visible(
        self,
        session: Session,
        *,
        user_id: int,
        collection_id: str,
    ) -> models.Resource:
        resource = self.resources.get_visible(
            session,
            user_id=user_id,
            resource_id=collection_id,
            resource_type="knowledge_collection",
        )
        if resource is None:
            raise not_found(
                "knowledge_collection_not_found",
                "Collection not found.",
            )
        return resource

    def require_manageable(
        self,
        session: Session,
        *,
        actor: models.User,
        collection_id: str,
    ) -> models.Resource:
        resource = self.resources.get_for_update(
            session,
            resource_id=collection_id,
            resource_type="knowledge_collection",
        )
        if (
            resource is None
            or resource.deleted_at is not None
            or not resource.is_active
            or not self._can_manage(resource, actor)
        ):
            raise not_found(
                "knowledge_collection_not_found",
                "Collection not found.",
            )
        return resource

    def create_private(
        self,
        session: Session,
        *,
        actor: models.User,
        payload: KnowledgeCollectionCreateRequest,
    ) -> KnowledgeCollectionResponse:
        resource = self._create(
            session,
            owner_id=actor.id,
            payload=payload,
        )
        return self._response(resource, actor)

    def create_global(
        self,
        session: Session,
        *,
        actor: models.User,
        payload: KnowledgeCollectionCreateRequest,
    ) -> KnowledgeCollectionResponse:
        if actor.role != "admin":
            raise forbidden(
                "admin_required",
                "Administrator access is required.",
            )
        resource = self._create(session, owner_id=0, payload=payload)
        return self._response(resource, actor)

    def put_resource(
        self,
        session: Session,
        *,
        actor: models.User,
        resource_id: str,
        payload: KnowledgeCollectionPutRequest,
    ) -> KnowledgeCollectionResponse:
        return self._put_resource(
            session,
            actor=actor,
            resource_id=resource_id,
            payload=payload,
            authority_scope="private",
        )

    def put_global_resource(
        self,
        session: Session,
        *,
        actor: models.User,
        resource_id: str,
        payload: KnowledgeCollectionPutRequest,
    ) -> KnowledgeCollectionResponse:
        self._require_admin(actor)
        return self._put_resource(
            session,
            actor=actor,
            resource_id=resource_id,
            payload=payload,
            authority_scope="global",
        )

    def _put_resource(
        self,
        session: Session,
        *,
        actor: models.User,
        resource_id: str,
        payload: KnowledgeCollectionPutRequest,
        authority_scope: Literal["private", "global"],
    ) -> KnowledgeCollectionResponse:
        resource = self.resources.get_for_update(
            session,
            resource_id=resource_id,
            resource_type="knowledge_collection",
        )
        self._ensure_mutation_authority(
            resource,
            actor,
            authority_scope=authority_scope,
        )
        assert resource is not None

        if not payload.is_active and (
            self._agent_references(session, resource_id=resource.id)
            or self._documents_block_collection_change(
                session,
                collection_id=resource.id,
            )
        ):
            raise conflict(
                "resource_in_use",
                "Knowledge Collection has active references or documents.",
            )

        resource.name = payload.name
        resource.config_json = payload.config.model_dump(mode="json")
        resource.is_active = payload.is_active
        resource.updated_at = models._now()
        self._flush_with_name_conflict(session)
        return self._response(resource, actor)

    def delete_resource(
        self,
        session: Session,
        *,
        actor: models.User,
        resource_id: str,
    ) -> ResourceDeleteResponse:
        return self._delete_resource(
            session,
            actor=actor,
            resource_id=resource_id,
            authority_scope="private",
        )

    def delete_global_resource(
        self,
        session: Session,
        *,
        actor: models.User,
        resource_id: str,
    ) -> ResourceDeleteResponse:
        self._require_admin(actor)
        return self._delete_resource(
            session,
            actor=actor,
            resource_id=resource_id,
            authority_scope="global",
        )

    def _delete_resource(
        self,
        session: Session,
        *,
        actor: models.User,
        resource_id: str,
        authority_scope: Literal["private", "global"],
    ) -> ResourceDeleteResponse:
        resource = self.resources.get_for_update(
            session,
            resource_id=resource_id,
            resource_type="knowledge_collection",
        )
        self._ensure_mutation_authority(
            resource,
            actor,
            authority_scope=authority_scope,
        )
        assert resource is not None

        if self._agent_references(
            session,
            resource_id=resource.id,
        ) or self._documents_block_collection_change(
            session,
            collection_id=resource.id,
        ):
            raise conflict(
                "resource_in_use",
                "Knowledge Collection has active references or documents.",
            )
        self.resources.soft_delete(session, resource)
        return ResourceDeleteResponse(id=resource.id, status="deleted")

    def _create(
        self,
        session: Session,
        *,
        owner_id: int,
        payload: KnowledgeCollectionCreateRequest,
    ) -> models.Resource:
        try:
            return self.resources.create(
                session,
                owner_id=owner_id,
                resource_type="knowledge_collection",
                name=payload.name,
                config_json=payload.config.model_dump(mode="json"),
            )
        except IntegrityError as error:
            raise self._name_conflict() from error

    def _agent_references(self, session: Session, *, resource_id: str) -> bool:
        for agent in self.resources.list_active_agents(session):
            config = AgentConfig.model_validate(agent.config_json)
            if any(
                scope.collection_id == resource_id
                for scope in config.knowledge_scopes
            ):
                return True
        return False

    @staticmethod
    def _documents_block_collection_change(
        session: Session,
        *,
        collection_id: str,
    ) -> bool:
        return (
            session.scalar(
                select(models.KnowledgeDocument.id)
                .where(
                    models.KnowledgeDocument.collection_id == collection_id,
                    or_(
                        models.KnowledgeDocument.is_active.is_(True),
                        and_(
                            models.KnowledgeDocument.is_active.is_(False),
                            models.KnowledgeDocument.error_code.in_(
                                [
                                    "knowledge_cleanup_pending",
                                    "knowledge_cleanup_failed",
                                ]
                            ),
                        ),
                    ),
                )
                .limit(1)
            )
            is not None
        )

    @staticmethod
    def _can_manage(resource: models.Resource, actor: models.User) -> bool:
        return resource.user_id == actor.id or (
            resource.user_id == 0 and actor.role == "admin"
        )

    def _ensure_mutation_authority(
        self,
        resource: models.Resource | None,
        actor: models.User,
        *,
        authority_scope: Literal["private", "global"],
    ) -> None:
        has_authority = resource is not None and (
            resource.user_id == actor.id
            if authority_scope == "private"
            else resource.user_id == 0 and actor.role == "admin"
        )
        if (
            resource is None
            or resource.deleted_at is not None
            or not has_authority
        ):
            raise self._not_found()

    @staticmethod
    def _require_admin(actor: models.User) -> None:
        if actor.role != "admin":
            raise forbidden(
                "admin_required",
                "Administrator access is required.",
            )

    @staticmethod
    def _flush_with_name_conflict(session: Session) -> None:
        try:
            session.flush()
        except IntegrityError as error:
            raise KnowledgeCollectionService._name_conflict() from error

    @staticmethod
    def _response(
        resource: models.Resource,
        actor: models.User,
    ) -> KnowledgeCollectionResponse:
        return KnowledgeCollectionResponse(
            id=resource.id,
            name=resource.name,
            scope="global" if resource.user_id == 0 else "private",
            can_manage=KnowledgeCollectionService._can_manage(resource, actor),
            is_active=resource.is_active,
            config=KnowledgeCollectionConfig.model_validate(resource.config_json),
            created_at=resource.created_at,
            updated_at=resource.updated_at,
        )

    @staticmethod
    def _not_found():
        return not_found("resource_not_found", "Knowledge Collection not found.")

    @staticmethod
    def _name_conflict():
        return conflict(
            "resource_name_taken",
            "A Knowledge Collection with this name already exists.",
        )
