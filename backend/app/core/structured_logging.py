from collections.abc import Callable
from datetime import UTC, datetime
import json
import logging
import math
import re
import sys
from typing import Final

from app.core.request_context import get_request_id


_STRUCTURED_STRING_FIELDS: Final = frozenset(
    {
        "http_method",
        "http_path",
        "exception_type",
        "provider",
        "model",
        "error_code",
        "agent_id",
        "workspace_id",
        "collection_id",
        "document_id",
        "tool_name",
        "retrieval_mode",
        "provider_request_id",
        "provider_status",
    }
)
_STRUCTURED_INTEGER_FIELDS: Final = frozenset(
    {
        "status_code",
        "task_id",
        "subtask_id",
        "message_id",
        "context_id",
        "index_generation",
        "call_index",
        "input_tokens",
        "output_tokens",
    }
)
_STRUCTURED_FLOAT_FIELDS: Final = frozenset({"duration_ms"})
_STRUCTURED_FIELDS: Final = tuple(
    sorted(
        _STRUCTURED_STRING_FIELDS
        | _STRUCTURED_INTEGER_FIELDS
        | _STRUCTURED_FLOAT_FIELDS
    )
)
_SAFE_EVENT = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_SAFE_LOGGER = re.compile(r"^[A-Za-z0-9_.-]{1,256}$")
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_SAFE_EXCEPTION_TYPE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,127}$")
_SAFE_LEVELS: Final = frozenset({"debug", "info", "warning", "error", "critical"})
_MAX_STRING_LENGTH: Final = 512
_THIRD_PARTY_LOGGERS: Final = (
    "boto3",
    "botocore",
    "openai",
    "httpx",
    "httpcore",
    "urllib3",
)
_HANDLER_MARKER: Final = "_auto_reign_json_handler"


def _safe_string(value: object) -> str | None:
    if not isinstance(value, str) or not value or len(value) > _MAX_STRING_LENGTH:
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return None
    return value


def _safe_request_id(value: object) -> str | None:
    if isinstance(value, str) and _SAFE_REQUEST_ID.fullmatch(value):
        return value
    return None


def _safe_structured_value(field: str, value: object) -> object | None:
    if field in _STRUCTURED_STRING_FIELDS:
        return _safe_string(value)
    if field in _STRUCTURED_INTEGER_FIELDS:
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        if field == "status_code" and not 100 <= value <= 599:
            return None
        if field in {"task_id", "subtask_id", "message_id", "context_id"} and not (
            1 <= value <= 2**63 - 1
        ):
            return None
        if field == "index_generation" and not 1 <= value <= 2_147_483_647:
            return None
        if field == "call_index" and not 1 <= value <= 2_147_483_647:
            return None
        if field in {"input_tokens", "output_tokens"} and not 0 <= value <= 2**63 - 1:
            return None
        return value
    if field in _STRUCTURED_FLOAT_FIELDS:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        try:
            converted = float(value)
        except (OverflowError, TypeError, ValueError):
            return None
        return converted if math.isfinite(converted) and converted >= 0 else None
    return None


class JsonFormatter(logging.Formatter):
    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        super().__init__()
        self.clock = clock or (lambda: datetime.now(UTC))

    def format(self, record: logging.LogRecord) -> str:
        logger_name = (
            record.name
            if isinstance(record.name, str) and _SAFE_LOGGER.fullmatch(record.name)
            else "unknown"
        )
        is_application = isinstance(record.name, str) and (
            record.name == "app" or record.name.startswith("app.")
        )
        raw_event = record.msg if isinstance(record.msg, str) else ""
        if is_application:
            event = (
                raw_event
                if not record.args and _SAFE_EVENT.fullmatch(raw_event)
                else "application_log_invalid"
            )
        else:
            event = "third_party_log"

        level = record.levelname.lower()
        payload: dict[str, object] = {
            "timestamp": self.clock().isoformat(),
            "level": level if level in _SAFE_LEVELS else "unknown",
            "logger": logger_name,
            "event": event,
        }
        exc_info = record.exc_info
        if isinstance(exc_info, tuple) and exc_info[0] is not None:
            raw_exception_type = exc_info[0].__name__
            exception_type = (
                raw_exception_type
                if _SAFE_EXCEPTION_TYPE.fullmatch(raw_exception_type)
                else None
            )
            if exception_type is not None:
                payload["exception_type"] = exception_type
        elif is_application:
            raw_exception_type = getattr(record, "exception_type", None)
            exception_type = (
                raw_exception_type
                if isinstance(raw_exception_type, str)
                and _SAFE_EXCEPTION_TYPE.fullmatch(raw_exception_type)
                else None
            )
            if exception_type is not None:
                payload["exception_type"] = exception_type

        if is_application:
            request_id = _safe_request_id(get_request_id()) or _safe_request_id(
                getattr(record, "request_id", None)
            )
            if request_id is not None:
                payload["request_id"] = request_id
            for field in _STRUCTURED_FIELDS:
                if field == "exception_type":
                    continue
                value = _safe_structured_value(field, getattr(record, field, None))
                if value is not None:
                    payload[field] = value
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(*, level: str) -> None:
    root = logging.getLogger()
    configured = any(
        getattr(handler, _HANDLER_MARKER, False) for handler in root.handlers
    )
    if not configured:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        setattr(handler, _HANDLER_MARKER, True)
        root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper()))
    for logger_name in ("uvicorn", "uvicorn.error"):
        logger = logging.getLogger(logger_name)
        logger.handlers.clear()
        logger.propagate = True
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers.clear()
    access_logger.propagate = False
    access_logger.disabled = True
    for logger_name in _THIRD_PARTY_LOGGERS:
        logging.getLogger(logger_name).setLevel(logging.WARNING)
