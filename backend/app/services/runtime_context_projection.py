from __future__ import annotations

import base64
from binascii import Error as Base64Error

from app.repositories.subtask_context_repository import (
    SubtaskRuntimeContextProjection,
)
from app.services.json_safety import (
    MAX_JSON_STRING_CHARS,
    JsonSafetyError,
    canonical_json,
)
from app.services.runtime_types import (
    RuntimeImageContext,
    RuntimeSelectedDocumentsContext,
    RuntimeTextContext,
    RuntimeUserContext,
)
from app.services.upload_validation_service import SUPPORTED_IMAGE_MIME_TYPES


class TaskExecutionError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def project_runtime_contexts(
    row: SubtaskRuntimeContextProjection,
    *,
    is_current: bool,
) -> tuple[RuntimeUserContext, ...]:
    if not isinstance(row.name, str) or not row.name or len(row.name) > 255:
        raise TaskExecutionError("context_invalid")
    if row.context_type == "selected_documents":
        return _selected_documents(row, is_current=is_current)
    if row.context_type not in {"attachment", "knowledge_base"}:
        raise TaskExecutionError("context_invalid")

    contexts: list[RuntimeUserContext] = []
    if row.extracted_text is not None:
        if not isinstance(row.extracted_text, str):
            raise TaskExecutionError("context_invalid")
        if len(row.extracted_text) > MAX_JSON_STRING_CHARS:
            raise TaskExecutionError("context_too_large")
        contexts.append(
            RuntimeTextContext(
                context_id=row.id,
                source_type=row.context_type,
                name=row.name,
                text=row.extracted_text,
            )
        )
    if row.context_type == "attachment" and row.image_base64 is not None:
        _validate_image(row)
        assert isinstance(row.mime_type, str)
        assert isinstance(row.image_base64, str)
        contexts.append(
            RuntimeImageContext(
                context_id=row.id,
                name=row.name,
                mime_type=row.mime_type,
                image_base64=row.image_base64,
            )
        )
    if not contexts:
        raise TaskExecutionError("context_invalid")
    return tuple(contexts)


def _selected_documents(
    row: SubtaskRuntimeContextProjection,
    *,
    is_current: bool,
) -> tuple[RuntimeUserContext, ...]:
    if not is_current:
        return ()
    reference = row.type_data
    if not isinstance(reference, dict):
        raise TaskExecutionError("context_invalid")
    knowledge_id = reference.get("knowledge_id")
    document_ids = reference.get("document_ids")
    if (
        not isinstance(knowledge_id, str)
        or not knowledge_id.strip()
        or not isinstance(document_ids, list)
        or not document_ids
        or any(
            not isinstance(document_id, str) or not document_id.strip()
            for document_id in document_ids
        )
        or len(document_ids) != len(set(document_ids))
    ):
        raise TaskExecutionError("context_invalid")
    try:
        canonical_json(
            {"knowledge_id": knowledge_id, "document_ids": document_ids}
        )
    except JsonSafetyError as error:
        code = (
            "context_too_large"
            if error.args and error.args[0] in {"json_size_limit", "json_string_limit"}
            else "context_invalid"
        )
        raise TaskExecutionError(code) from None
    return (
        RuntimeSelectedDocumentsContext(
            context_id=row.id,
            name=row.name,
            knowledge_id=knowledge_id,
            document_ids=tuple(document_ids),
        ),
    )


def _validate_image(row: SubtaskRuntimeContextProjection) -> None:
    if (
        not isinstance(row.mime_type, str)
        or row.mime_type not in SUPPORTED_IMAGE_MIME_TYPES
        or not isinstance(row.image_base64, str)
        or not row.image_base64
    ):
        raise TaskExecutionError("context_invalid")
    try:
        if len(row.image_base64) % 4 != 0:
            raise TaskExecutionError("context_invalid")
        decoded = base64.b64decode(row.image_base64, validate=True)
    except (Base64Error, ValueError):
        raise TaskExecutionError("context_invalid") from None
    if base64.b64encode(decoded).decode("ascii") != row.image_base64:
        raise TaskExecutionError("context_invalid")
