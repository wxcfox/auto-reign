from __future__ import annotations

from dataclasses import dataclass
import hashlib
import logging
import re
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker

from app.core.limits import DEFAULT_ATTACHMENT_MAX_BYTES
from app.db.session import session_scope
from app.repositories.attachment_repository import AttachmentRepository
from app.schemas.attachments import AttachmentContentDTO, AttachmentDraftDTO
from app.services.extraction_service import ExtractionError, ExtractionService
from app.storage.object_store import (
    ObjectConflict,
    ObjectNotFound,
    ObjectStore,
    ObjectStoreError,
    ObjectStoreUnavailable,
    ObjectTooLarge,
)


logger = logging.getLogger(__name__)


class AttachmentServiceError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class _OriginalSnapshot:
    id: str
    filename: str
    object_key: str
    mime_type: str
    size_bytes: int
    content_hash: str
    message_id: str | None


def sanitize_filename(filename: str) -> str:
    basename = filename.replace("\\", "/").rsplit("/", 1)[-1]
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", basename).strip("._-")
    safe_name = safe_name or "attachment"
    if safe_name.lower() == "parsed.txt":
        safe_name = f"source-{safe_name}"
    return safe_name


class AttachmentService:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        store: ObjectStore,
        extraction: ExtractionService | None = None,
        repository: AttachmentRepository | None = None,
        max_bytes: int = DEFAULT_ATTACHMENT_MAX_BYTES,
    ) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.session_factory = session_factory
        self.store = store
        self.extraction = extraction or ExtractionService()
        self.repository = repository or AttachmentRepository()
        self.max_bytes = max_bytes

    def create_draft_committed(
        self,
        *,
        user_id: int,
        filename: str,
        media_type: str,
        content: bytes,
    ) -> AttachmentDraftDTO:
        if user_id <= 0:
            raise ValueError("user_id must be positive")
        if len(content) > self.max_bytes:
            raise ExtractionError(
                "extraction_too_large",
                "attachment exceeds size limit",
            )

        parsed = self.extraction.extract_required(filename, media_type, content)
        attachment_id = str(uuid4())
        prefix = f"users/{user_id}/attachments/{attachment_id}"
        object_key = f"{prefix}/{sanitize_filename(filename)}"
        parsed_object_key = f"{prefix}/parsed.txt" if parsed.text is not None else None
        parsed_bytes = parsed.text.encode("utf-8") if parsed.text is not None else None
        attempted_keys: list[str] = []

        try:
            self._put_new(
                key=object_key,
                content=content,
                attempted_keys=attempted_keys,
            )
            if parsed_object_key is not None:
                assert parsed_bytes is not None
                self._put_new(
                    key=parsed_object_key,
                    content=parsed_bytes,
                    attempted_keys=attempted_keys,
                )

            with session_scope(self.session_factory) as session:
                attachment = self.repository.create_draft(
                    session,
                    attachment_id=attachment_id,
                    user_id=user_id,
                    original_filename=filename,
                    object_key=object_key,
                    parsed_object_key=parsed_object_key,
                    mime_type=parsed.mime_type,
                    size_bytes=len(content),
                    content_hash=hashlib.sha256(content).hexdigest(),
                    parsed_size_bytes=(
                        len(parsed_bytes) if parsed_bytes is not None else None
                    ),
                    parsed_content_hash=(
                        hashlib.sha256(parsed_bytes).hexdigest()
                        if parsed_bytes is not None
                        else None
                    ),
                )
                session.flush()
                dto = AttachmentDraftDTO.from_model(attachment)
            return dto
        except Exception:
            self._best_effort_delete(
                attachment_id=attachment_id,
                attempted_keys=attempted_keys,
            )
            raise

    def list_drafts(self, *, user_id: int) -> tuple[AttachmentDraftDTO, ...]:
        with session_scope(self.session_factory) as session:
            return tuple(
                AttachmentDraftDTO.from_model(row)
                for row in self.repository.list_unbound(session, user_id=user_id)
            )

    def read_original(
        self,
        *,
        user_id: int,
        attachment_id: str,
    ) -> AttachmentContentDTO:
        with session_scope(self.session_factory) as session:
            row = self.repository.get(
                session,
                user_id=user_id,
                attachment_id=attachment_id,
            )
            if row is None:
                raise AttachmentServiceError(
                    "attachment_not_found",
                    "attachment was not found",
                )
            snapshot = _OriginalSnapshot(
                id=row.id,
                filename=row.original_filename,
                object_key=row.object_key,
                mime_type=row.mime_type,
                size_bytes=row.size_bytes,
                content_hash=row.content_hash,
                message_id=row.message_id,
            )

        try:
            stored = self.store.get(snapshot.object_key)
        except ObjectTooLarge as exc:
            raise AttachmentServiceError(
                "attachment_corrupt",
                "attachment content failed integrity validation",
            ) from exc
        except (ObjectNotFound, ObjectStoreUnavailable) as exc:
            raise AttachmentServiceError(
                "attachment_unavailable",
                "attachment content is unavailable",
            ) from exc
        except ObjectStoreError as exc:
            raise AttachmentServiceError(
                "attachment_unavailable",
                "attachment content is unavailable",
            ) from exc

        actual_hash = hashlib.sha256(stored.data).hexdigest()
        if (
            len(stored.data) != snapshot.size_bytes
            or stored.metadata.size_bytes != snapshot.size_bytes
            or actual_hash != snapshot.content_hash
        ):
            raise AttachmentServiceError(
                "attachment_corrupt",
                "attachment content failed integrity validation",
            )
        return AttachmentContentDTO(
            id=snapshot.id,
            filename=snapshot.filename,
            mime_type=snapshot.mime_type,
            size_bytes=snapshot.size_bytes,
            message_id=snapshot.message_id,
            content=stored.data,
        )

    def delete_draft(self, *, user_id: int, attachment_id: str) -> None:
        with session_scope(self.session_factory) as session:
            row = self.repository.get_draft_for_update(
                session,
                user_id=user_id,
                attachment_id=attachment_id,
            )
            if row is None:
                raise AttachmentServiceError(
                    "attachment_not_ready",
                    "attachment is unavailable or already bound",
                )
            keys = [row.object_key]
            if row.parsed_object_key is not None:
                keys.append(row.parsed_object_key)
            for key in keys:
                self.store.delete(key)
            self.repository.delete_draft(
                session,
                user_id=user_id,
                attachment=row,
            )

    def _put_new(
        self,
        *,
        key: str,
        content: bytes,
        attempted_keys: list[str],
    ) -> None:
        attempted_keys.append(key)
        try:
            self.store.put(key, content, if_none_match=True)
        except ObjectConflict:
            attempted_keys.pop()
            raise

    def _best_effort_delete(
        self,
        *,
        attachment_id: str,
        attempted_keys: list[str],
    ) -> None:
        for key in reversed(attempted_keys):
            try:
                self.store.delete(key)
            except Exception as cleanup_error:
                logger.warning(
                    "attachment_compensation_failed",
                    extra={
                        "attachment_id": attachment_id,
                        "exception_type": type(cleanup_error).__name__,
                        "error_code": "attachment_compensation_failed",
                    },
                    exc_info=False,
                )
