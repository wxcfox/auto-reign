import logging
from time import monotonic
from typing import Any

from fastapi import Request
from fastapi.responses import PlainTextResponse
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.request_context import (
    bind_request_id,
    is_safe_request_id,
    normalize_request_id,
    reset_request_id,
)


request_logger = logging.getLogger("app.request")
_REQUEST_ID_STATE_KEY = "_auto_reign_request_id"


def _route_template(scope: Scope) -> str:
    route = scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str) and path.startswith("/") and len(path) <= 512:
        return path
    return "unmatched"


async def unhandled_exception_handler(
    request: Request,
    _error: Exception,
) -> Response:
    state = request.scope.get("state")
    request_id = (
        state.get(_REQUEST_ID_STATE_KEY) if isinstance(state, dict) else None
    )
    headers = (
        {"X-Request-ID": request_id} if is_safe_request_id(request_id) else None
    )
    return PlainTextResponse(
        "Internal Server Error",
        status_code=500,
        headers=headers,
    )


class RequestLoggingMiddleware:
    def __init__(self, app: ASGIApp, logger: logging.Logger | None = None) -> None:
        self.app = app
        self.logger = logger or request_logger

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = normalize_request_id(Headers(scope=scope).get("x-request-id"))
        state = scope.get("state")
        if not isinstance(state, dict):
            state = {}
            scope["state"] = state
        state[_REQUEST_ID_STATE_KEY] = request_id
        token = bind_request_id(request_id)
        started = monotonic()
        status_code = 500
        exception_type: str | None = None

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception as error:
            exception_type = type(error).__name__
            raise
        finally:
            extra: dict[str, Any] = {
                "request_id": request_id,
                "http_method": scope.get("method", "UNKNOWN"),
                "http_path": _route_template(scope),
                "status_code": status_code,
                "duration_ms": round((monotonic() - started) * 1000, 2),
            }
            if exception_type is not None:
                extra["exception_type"] = exception_type
            self.logger.info("http_request_complete", extra=extra)
            reset_request_id(token)
