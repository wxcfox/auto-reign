from __future__ import annotations

from datetime import datetime
from typing import Any


def artifact_processing_status(artifact: Any) -> str:
    return _str_status(artifact, "processing_status", "completed")


def artifact_index_status(artifact: Any) -> str:
    return _str_status(artifact, "index_status", "pending")


def artifact_recovery_required(artifact: Any) -> bool:
    value = _status_json(artifact).get("recovery_required")
    return bool(value)


def artifact_recovery_reason(artifact: Any) -> str | None:
    return _optional_str(_status_json(artifact).get("recovery_reason"))


def artifact_language(artifact: Any) -> str:
    return _str_metadata(artifact, "language", "zh-CN")


def artifact_source_refs(artifact: Any) -> list[str]:
    return _str_list(_metadata_json(artifact).get("source_refs"))


def artifact_evidence_refs(artifact: Any) -> list[str]:
    return _str_list(_metadata_json(artifact).get("evidence_refs"))


def artifact_source_filename(artifact: Any) -> str | None:
    return _optional_str(_metadata_json(artifact).get("source_filename"))


def artifact_media_type(artifact: Any) -> str | None:
    return _optional_str(_metadata_json(artifact).get("media_type"))


def artifact_size_bytes(artifact: Any) -> int | None:
    value = _metadata_json(artifact).get("size_bytes")
    return value if isinstance(value, int) else None


def artifact_source_type(artifact: Any) -> str:
    return _str_metadata(artifact, "source_type", "upload")


def artifact_origin(artifact: Any) -> str:
    return _str_metadata(artifact, "origin", "llm")


def artifact_edited_by(artifact: Any) -> str:
    return _str_metadata(artifact, "edited_by", "system")


def artifact_uploaded_at(artifact: Any) -> datetime | None:
    return _optional_datetime(_metadata_json(artifact).get("uploaded_at"))


def artifact_status_json(
    *,
    processing_status: str = "completed",
    index_status: str = "pending",
    recovery_required: bool = False,
    recovery_reason: str | None = None,
) -> dict[str, object]:
    return {
        "processing_status": processing_status,
        "index_status": index_status,
        "recovery_required": recovery_required,
        "recovery_reason": recovery_reason,
    }


def artifact_metadata_json(
    *,
    source_refs: list[str] | None = None,
    evidence_refs: list[str] | None = None,
    language: str = "zh-CN",
    source_filename: str | None = None,
    media_type: str | None = None,
    size_bytes: int | None = None,
    source_type: str | None = None,
    origin: str = "llm",
    edited_by: str = "system",
    uploaded_at: datetime | None = None,
) -> dict[str, object]:
    return {
        "source_refs": source_refs or [],
        "evidence_refs": evidence_refs or [],
        "language": language,
        "source_filename": source_filename,
        "media_type": media_type,
        "size_bytes": size_bytes,
        "source_type": source_type,
        "origin": origin,
        "edited_by": edited_by,
        "uploaded_at": _datetime_json(uploaded_at),
    }


def with_index_status(artifact: Any, index_status: str) -> dict[str, object]:
    status = dict(_status_json(artifact))
    status["index_status"] = index_status
    return status


def _status_json(artifact: Any) -> dict[str, object]:
    value = getattr(artifact, "status_json", None)
    return value if isinstance(value, dict) else {}


def _metadata_json(artifact: Any) -> dict[str, object]:
    value = getattr(artifact, "metadata_json", None)
    return value if isinstance(value, dict) else {}


def _str_status(artifact: Any, key: str, default: str) -> str:
    value = _status_json(artifact).get(key)
    return value if isinstance(value, str) and value else default


def _str_metadata(artifact: Any, key: str, default: str) -> str:
    value = _metadata_json(artifact).get(key)
    return value if isinstance(value, str) and value else default


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _optional_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _datetime_json(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")
