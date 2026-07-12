from datetime import UTC, datetime
from io import StringIO
import json
import logging
import math
from uuid import UUID

from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from app.core.request_context import (
    get_request_id,
    normalize_request_id,
    request_id_context,
)
from app.core.structured_logging import JsonFormatter, configure_logging
from app.middleware.request_logging import (
    RequestLoggingMiddleware,
    unhandled_exception_handler,
)


FIXED_TIME = datetime(2026, 7, 13, 8, 0, tzinfo=UTC)


def _test_app(stream: StringIO) -> FastAPI:
    logger = logging.getLogger(f"app.test.request.{id(stream)}")
    logger.handlers.clear()
    logger.propagate = False
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter(clock=lambda: FIXED_TIME))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    app = FastAPI()
    app.add_exception_handler(Exception, unhandled_exception_handler)
    app.add_middleware(RequestLoggingMiddleware, logger=logger)

    @app.post("/echo/{item_id}")
    async def echo(item_id: str, request: Request) -> Response:
        await request.json()
        assert get_request_id() is not None
        return Response(
            content=json.dumps({"ok": True, "item_id": item_id}),
            media_type="application/json",
            headers={"X-Request-ID": "downstream-value"},
        )

    @app.get("/number/{item_id}")
    async def number(item_id: int) -> dict[str, int]:
        return {"item_id": item_id}

    return app


def test_request_logging_uses_route_template_and_omits_request_data() -> None:
    stream = StringIO()
    with TestClient(_test_app(stream)) as client:
        response = client.post(
            "/echo/private-item-id?token=query-secret",
            headers={"X-Request-ID": "request-123"},
            json={
                "password": "super-secret-password",
                "message": "private user message",
                "rag_chunk": "private retrieved text",
            },
        )

    event = json.loads(stream.getvalue())
    assert response.headers.get_list("X-Request-ID") == ["request-123"]
    assert event == {
        "timestamp": FIXED_TIME.isoformat(),
        "level": "info",
        "logger": event["logger"],
        "event": "http_request_complete",
        "request_id": "request-123",
        "http_method": "POST",
        "http_path": "/echo/{item_id}",
        "status_code": 200,
        "duration_ms": event["duration_ms"],
    }
    assert isinstance(event["duration_ms"], float)
    assert math.isfinite(event["duration_ms"])
    serialized = stream.getvalue()
    for secret in (
        "private-item-id",
        "query-secret",
        "downstream-value",
        "super-secret-password",
        "private user message",
        "private retrieved text",
    ):
        assert secret not in serialized
    assert get_request_id() is None


def test_invalid_request_id_is_replaced_with_uuid() -> None:
    stream = StringIO()
    with TestClient(_test_app(stream)) as client:
        response = client.post(
            "/echo/value",
            headers={"X-Request-ID": "bad id with spaces"},
            json={},
        )

    generated = response.headers["X-Request-ID"]
    assert str(UUID(generated)) == generated
    assert json.loads(stream.getvalue())["request_id"] == generated


def test_request_id_normalization_rejects_control_unicode_and_overlong_values() -> None:
    for value in ("line\nbreak", "请求标识", "x" * 129, ""):
        generated = normalize_request_id(value)
        assert str(UUID(generated)) == generated


def test_unmatched_route_never_logs_raw_path() -> None:
    stream = StringIO()
    with TestClient(_test_app(stream)) as client:
        response = client.get("/not-found/private-secret")

    assert response.status_code == 404
    event = json.loads(stream.getvalue())
    assert event["http_path"] == "unmatched"
    assert "private-secret" not in stream.getvalue()


def test_validation_error_response_keeps_request_id_and_route_template() -> None:
    stream = StringIO()
    with TestClient(_test_app(stream)) as client:
        response = client.get(
            "/number/not-an-integer",
            headers={"X-Request-ID": "request-validation-1"},
        )

    assert response.status_code == 422
    assert response.headers.get_list("X-Request-ID") == ["request-validation-1"]
    event = json.loads(stream.getvalue())
    assert event["http_path"] == "/number/{item_id}"
    assert event["status_code"] == 422
    assert "not-an-integer" not in stream.getvalue()


def test_unhandled_error_response_keeps_request_id_without_leaking_error() -> None:
    stream = StringIO()
    app = _test_app(stream)

    @app.get("/boom/{item_id}")
    async def boom(item_id: str) -> None:
        raise RuntimeError(f"private exception {item_id}")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(
            "/boom/private-item",
            headers={"X-Request-ID": "request-error-1"},
        )

    assert response.status_code == 500
    assert response.headers.get_list("X-Request-ID") == ["request-error-1"]
    assert response.text == "Internal Server Error"
    event = json.loads(stream.getvalue())
    assert event["http_path"] == "/boom/{item_id}"
    assert event["status_code"] == 500
    assert event["exception_type"] == "RuntimeError"
    assert "private-item" not in stream.getvalue()
    assert "private exception" not in stream.getvalue()


def test_streaming_response_keeps_context_until_body_completion(
    monkeypatch,
) -> None:
    stream = StringIO()
    app = _test_app(stream)
    observed: list[str | None] = []
    ticks = iter((10.0, 10.025))
    monkeypatch.setattr(
        "app.middleware.request_logging.monotonic",
        ticks.__next__,
    )

    @app.get("/stream/{item_id}")
    async def streaming(item_id: str) -> StreamingResponse:
        async def body():
            observed.append(get_request_id())
            yield b"one"
            observed.append(get_request_id())
            yield b"two"

        return StreamingResponse(body())

    with TestClient(app) as client:
        response = client.get(
            "/stream/private-item",
            headers={"X-Request-ID": "request-stream-1"},
        )

    assert response.content == b"onetwo"
    assert response.headers.get_list("X-Request-ID") == ["request-stream-1"]
    assert observed == ["request-stream-1", "request-stream-1"]
    assert get_request_id() is None
    event = json.loads(stream.getvalue())
    assert event["http_path"] == "/stream/{item_id}"
    assert event["duration_ms"] == 25.0


def test_third_party_logger_message_url_headers_and_exception_are_not_rendered() -> None:
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter(clock=lambda: FIXED_TIME))
    logger = logging.getLogger("botocore.endpoint")
    previous_handlers = logger.handlers[:]
    previous_propagate = logger.propagate
    previous_level = logger.level
    logger.handlers[:] = [handler]
    logger.propagate = False
    logger.setLevel(logging.WARNING)
    try:
        try:
            raise RuntimeError("secret response body")
        except RuntimeError:
            logger.warning(
                "POST https://secret.example/path Authorization=Bearer-secret body=%s",
                "private-body",
                exc_info=True,
            )
    finally:
        logger.handlers[:] = previous_handlers
        logger.propagate = previous_propagate
        logger.setLevel(previous_level)

    payload = json.loads(stream.getvalue())
    assert payload == {
        "timestamp": FIXED_TIME.isoformat(),
        "level": "warning",
        "logger": "botocore.endpoint",
        "event": "third_party_log",
        "exception_type": "RuntimeError",
    }
    serialized = stream.getvalue()
    for secret in (
        "secret.example",
        "Bearer-secret",
        "private-body",
        "secret response body",
    ):
        assert secret not in serialized


def test_application_formatter_rejects_dynamic_message_and_unsafe_extra_values() -> None:
    record = logging.LogRecord(
        name="app.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="dynamic %s",
        args=("private-body",),
        exc_info=None,
    )
    record.http_path = "/safe/{item_id}"
    record.status_code = True
    record.index_generation = 0
    record.duration_ms = float("nan")
    record.model = "x" * 513
    record.error_code = "bad\nvalue"

    payload = json.loads(JsonFormatter(clock=lambda: FIXED_TIME).format(record))

    assert payload == {
        "timestamp": FIXED_TIME.isoformat(),
        "level": "info",
        "logger": "app.test",
        "event": "application_log_invalid",
        "http_path": "/safe/{item_id}",
    }
    assert "private-body" not in json.dumps(payload)


def test_formatter_rejects_unsafe_logger_name_and_request_id() -> None:
    record = logging.LogRecord(
        name="app.secret\nlogger",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="safe_event",
        args=(),
        exc_info=None,
    )
    record.request_id = "unsafe request id"

    payload = json.loads(JsonFormatter(clock=lambda: FIXED_TIME).format(record))

    assert payload["logger"] == "unknown"
    assert "request_id" not in payload
    assert "secret" not in json.dumps(payload)


def test_formatter_prefers_bound_request_id_over_invalid_record_extra() -> None:
    record = logging.LogRecord(
        name="app.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="safe_event",
        args=(),
        exc_info=None,
    )
    record.request_id = "unsafe request id"
    record.status_code = 10**5000
    record.index_generation = 2_147_483_648
    record.duration_ms = 10**5000

    with request_id_context("bound-123"):
        payload = json.loads(JsonFormatter(clock=lambda: FIXED_TIME).format(record))

    assert payload["request_id"] == "bound-123"
    assert "status_code" not in payload
    assert "index_generation" not in payload
    assert "duration_ms" not in payload


def test_configure_logging_is_idempotent_and_preserves_existing_handlers() -> None:
    root = logging.getLogger()
    previous_root_level = root.level
    existing = logging.NullHandler()
    uvicorn_logger = logging.getLogger("uvicorn")
    uvicorn_error_logger = logging.getLogger("uvicorn.error")
    uvicorn_access_logger = logging.getLogger("uvicorn.access")
    previous_uvicorn = (
        uvicorn_logger.handlers[:],
        uvicorn_logger.propagate,
        uvicorn_error_logger.handlers[:],
        uvicorn_error_logger.propagate,
        uvicorn_access_logger.handlers[:],
        uvicorn_access_logger.propagate,
        uvicorn_access_logger.disabled,
    )
    uvicorn_logger.handlers[:] = [logging.NullHandler()]
    uvicorn_error_logger.handlers[:] = [logging.NullHandler()]
    uvicorn_access_logger.handlers[:] = [logging.NullHandler()]
    root.addHandler(existing)
    try:
        configure_logging(level="INFO")
        configure_logging(level="WARNING")
        managed = [
            handler
            for handler in root.handlers
            if getattr(handler, "_auto_reign_json_handler", False)
        ]
        assert existing in root.handlers
        assert len(managed) == 1
        assert root.level == logging.WARNING
        assert uvicorn_logger.handlers == []
        assert uvicorn_logger.propagate is True
        assert uvicorn_error_logger.handlers == []
        assert uvicorn_error_logger.propagate is True
        assert uvicorn_access_logger.handlers == []
        assert uvicorn_access_logger.propagate is False
        assert uvicorn_access_logger.disabled is True
    finally:
        root.removeHandler(existing)
        root.setLevel(previous_root_level)
        (
            uvicorn_logger.handlers[:],
            uvicorn_logger.propagate,
            uvicorn_error_logger.handlers[:],
            uvicorn_error_logger.propagate,
            uvicorn_access_logger.handlers[:],
            uvicorn_access_logger.propagate,
            uvicorn_access_logger.disabled,
        ) = previous_uvicorn
