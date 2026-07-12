from collections.abc import Collection

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models
from app.schemas.resources import ResourceListScope


class ResourceRepository:
    def list_visible(
        self,
        session: Session,
        *,
        user_id: int,
        resource_type: str,
        scope: ResourceListScope = "visible",
        include_inactive: bool = False,
        include_unavailable: bool = False,
        resource_ids: Collection[str] | None = None,
    ) -> list[models.Resource]:
        if resource_ids is not None and not resource_ids:
            return []
        owner_filter = {
            "visible": models.Resource.user_id.in_([0, user_id]),
            "owned": models.Resource.user_id == user_id,
            "global": models.Resource.user_id == 0,
        }[scope]
        filters = [
            models.Resource.resource_type == resource_type,
            owner_filter,
        ]
        if not include_unavailable:
            filters.append(models.Resource.deleted_at.is_(None))
            if not include_inactive:
                filters.append(models.Resource.is_active.is_(True))
        if resource_ids is not None:
            filters.append(models.Resource.id.in_(resource_ids))
        return list(
            session.scalars(
                select(models.Resource)
                .where(*filters)
                .order_by(
                    models.Resource.user_id,
                    models.Resource.name,
                    models.Resource.id,
                )
            )
        )

    def get_visible(
        self,
        session: Session,
        *,
        user_id: int,
        resource_id: str,
        resource_type: str,
        include_unavailable: bool = False,
    ) -> models.Resource | None:
        filters = [
            models.Resource.id == resource_id,
            models.Resource.resource_type == resource_type,
            models.Resource.user_id.in_([0, user_id]),
        ]
        if not include_unavailable:
            filters.extend(
                [
                    models.Resource.is_active.is_(True),
                    models.Resource.deleted_at.is_(None),
                ]
            )
        return session.scalar(select(models.Resource).where(*filters))

    def get_visible_for_update(
        self,
        session: Session,
        *,
        user_id: int,
        resource_id: str,
        resource_type: str,
    ) -> models.Resource | None:
        return session.scalar(
            select(models.Resource)
            .where(
                models.Resource.id == resource_id,
                models.Resource.resource_type == resource_type,
                models.Resource.user_id.in_([0, user_id]),
                models.Resource.is_active.is_(True),
                models.Resource.deleted_at.is_(None),
            )
            .with_for_update()
        )

    def get_for_update(
        self,
        session: Session,
        *,
        resource_id: str,
        resource_type: str,
    ) -> models.Resource | None:
        return session.scalar(
            select(models.Resource)
            .where(
                models.Resource.id == resource_id,
                models.Resource.resource_type == resource_type,
            )
            .with_for_update()
        )

    def create(
        self,
        session: Session,
        *,
        owner_id: int,
        resource_type: str,
        name: str,
        config_json: dict[str, object],
    ) -> models.Resource:
        resource = models.Resource(
            user_id=owner_id,
            resource_type=resource_type,
            name=name,
            config_json=config_json,
        )
        session.add(resource)
        session.flush()
        return resource

    def list_active_agents(self, session: Session) -> list[models.Resource]:
        return list(
            session.scalars(
                select(models.Resource).where(
                    models.Resource.resource_type == "agent",
                    models.Resource.is_active.is_(True),
                    models.Resource.deleted_at.is_(None),
                )
            )
        )

    def soft_delete(self, session: Session, resource: models.Resource) -> None:
        resource.is_active = False
        resource.deleted_at = models._now()
        resource.updated_at = resource.deleted_at
        session.flush()
