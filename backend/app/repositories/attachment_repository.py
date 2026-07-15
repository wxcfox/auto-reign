from __future__ import annotations

from collections.abc import Collection, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models


class AttachmentRepository:
    def create_draft(
        self,
        session: Session,
        *,
        attachment_id: str,
        user_id: int,
        original_filename: str,
        object_key: str,
        parsed_object_key: str | None,
        mime_type: str,
        size_bytes: int,
        content_hash: str,
        parsed_size_bytes: int | None,
        parsed_content_hash: str | None,
    ) -> models.Attachment:
        attachment = models.Attachment(
            id=attachment_id,
            user_id=user_id,
            message_id=None,
            original_filename=original_filename,
            object_key=object_key,
            parsed_object_key=parsed_object_key,
            mime_type=mime_type,
            size_bytes=size_bytes,
            content_hash=content_hash,
            parsed_size_bytes=parsed_size_bytes,
            parsed_content_hash=parsed_content_hash,
        )
        session.add(attachment)
        return attachment

    def get(
        self,
        session: Session,
        *,
        user_id: int,
        attachment_id: str,
    ) -> models.Attachment | None:
        return session.scalar(
            select(models.Attachment).where(
                models.Attachment.id == attachment_id,
                models.Attachment.user_id == user_id,
            )
        )

    def lock_drafts(
        self,
        session: Session,
        *,
        user_id: int,
        attachment_ids: list[str],
    ) -> list[models.Attachment]:
        if not attachment_ids:
            return []
        locked = list(
            session.scalars(
                select(models.Attachment)
                .where(
                    models.Attachment.user_id == user_id,
                    models.Attachment.id.in_(attachment_ids),
                    models.Attachment.message_id.is_(None),
                )
                .order_by(models.Attachment.id)
                .with_for_update()
            )
        )
        by_id = {attachment.id: attachment for attachment in locked}
        return [
            by_id[attachment_id]
            for attachment_id in attachment_ids
            if attachment_id in by_id
        ]

    def get_draft_for_update(
        self,
        session: Session,
        *,
        user_id: int,
        attachment_id: str,
    ) -> models.Attachment | None:
        return session.scalar(
            select(models.Attachment)
            .where(
                models.Attachment.id == attachment_id,
                models.Attachment.user_id == user_id,
                models.Attachment.message_id.is_(None),
            )
            .with_for_update()
        )

    def list_unbound(
        self,
        session: Session,
        *,
        user_id: int,
    ) -> list[models.Attachment]:
        return list(
            session.scalars(
                select(models.Attachment)
                .where(
                    models.Attachment.user_id == user_id,
                    models.Attachment.message_id.is_(None),
                )
                .order_by(models.Attachment.created_at, models.Attachment.id)
            )
        )

    def list_for_messages(
        self,
        session: Session,
        *,
        user_id: int,
        message_ids: Collection[str],
    ) -> list[models.Attachment]:
        if not message_ids:
            return []
        return list(
            session.scalars(
                select(models.Attachment)
                .where(
                    models.Attachment.user_id == user_id,
                    models.Attachment.message_id.in_(message_ids),
                )
                .order_by(models.Attachment.created_at, models.Attachment.id)
            )
        )

    def bind_to_message(
        self,
        session: Session,
        *,
        user_id: int,
        attachments: Sequence[models.Attachment],
        message_id: str,
    ) -> None:
        if any(
            attachment.user_id != user_id or attachment.message_id is not None
            for attachment in attachments
        ):
            raise ValueError("attachment_not_ready")
        for attachment in attachments:
            attachment.message_id = message_id
        session.flush()

    def delete_draft(
        self,
        session: Session,
        *,
        user_id: int,
        attachment: models.Attachment,
    ) -> None:
        if attachment.user_id != user_id or attachment.message_id is not None:
            raise ValueError("attachment_not_ready")
        session.delete(attachment)
        session.flush()
