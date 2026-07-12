from __future__ import annotations

from collections.abc import Iterator
import json
import logging
from threading import Lock
from typing import Any, Protocol

from anyio import CancelScope
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool
from starlette.types import Send

from app.services.agent_runtime import RuntimeTerminalError
from app.services.attachment_runtime_loader import AttachmentRuntimeError
from app.services.generation_service import PreparedGenerationError


logger = logging.getLogger(__name__)


class GenerationEventLike(Protocol):
    event: str
    data: dict[str, object]


class _CloseOnceEvents:
    def __init__(self, events: Iterator[GenerationEventLike]) -> None:
        self._events = events
        self._closed = False
        self._lock = Lock()

    def __iter__(self) -> "_CloseOnceEvents":
        return self

    def __next__(self) -> GenerationEventLike:
        with self._lock:
            if self._closed:
                raise StopIteration
            return next(self._events)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            _close_events_safely(self._events)


class GenerationStreamingResponse(StreamingResponse):
    def __init__(self, events: Iterator[GenerationEventLike]) -> None:
        self._generation_events = _CloseOnceEvents(events)
        super().__init__(
            stream_generation_events(self._generation_events),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    async def stream_response(self, send: Send) -> None:
        try:
            await super().stream_response(send)
        finally:
            with CancelScope(shield=True):
                await run_in_threadpool(
                    _close_events_safely,
                    self._generation_events,
                )


def sse_event(event: str, data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


def http_error_payload(error: HTTPException) -> dict[str, Any]:
    if isinstance(error.detail, dict):
        code = error.detail.get("code")
        message = error.detail.get("message")
        if isinstance(code, str) and code and isinstance(message, str) and message:
            return {
                "code": code,
                "message": message,
                "status_code": error.status_code,
            }
    return {
        "code": "request_failed",
        "message": "Request failed.",
        "status_code": error.status_code,
    }


def stream_generation_events(
    events: Iterator[GenerationEventLike],
) -> Iterator[str]:
    try:
        for item in events:
            yield sse_event(item.event, item.data)
    except PreparedGenerationError as error:
        if isinstance(error.cause, AttachmentRuntimeError):
            messages = {
                "attachment_unavailable": "Attachment content is unavailable.",
                "attachment_corrupt": "Attachment content failed integrity validation.",
            }
            code = (
                error.cause.code
                if error.cause.code in messages
                else "attachment_unavailable"
            )
            payload = {
                "code": code,
                "message": messages[code],
                "status_code": 503,
            }
        elif isinstance(error.cause, HTTPException):
            payload = http_error_payload(error.cause)
        elif isinstance(error.cause, RuntimeTerminalError):
            payload = {
                "code": error.cause.code,
                "message": error.cause.public_message,
                "status_code": error.cause.status_code,
            }
        else:
            payload = {
                "code": "provider_call_failed",
                "message": "The model request failed.",
                "status_code": 502,
            }
        yield sse_event(
            "error",
            {
                **payload,
                "conversation_id": error.conversation_id,
                "assistant_message_id": error.assistant_message_id,
            },
        )
    except HTTPException as error:
        yield sse_event("error", http_error_payload(error))
    except Exception:
        yield sse_event(
            "error",
            {
                "code": "request_failed",
                "message": "Request failed.",
                "status_code": 500,
            },
        )
    finally:
        _close_events_safely(events)


def _close_events_safely(events: object) -> None:
    close = getattr(events, "close", None)
    if not callable(close):
        return
    try:
        close()
    except BaseException as error:
        logger.warning(
            "generation_stream_close_failed",
            extra={
                "exception_type": type(error).__name__,
                "error_code": "generation_stream_close_failed",
            },
            exc_info=False,
        )
