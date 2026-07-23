from __future__ import annotations

import asyncio
from collections.abc import Coroutine
import inspect
from urllib.parse import urlsplit

import socketio

from app.core.config import Settings


def create_socketio_server(
    settings: Settings,
    *,
    redis_available: bool = False,
) -> socketio.AsyncServer:
    manager = (
        socketio.AsyncRedisManager(settings.redis_url)
        if redis_available
        else None
    )
    options: dict[str, object] = {}
    if manager is not None:
        options["client_manager"] = manager
    return socketio.AsyncServer(
        async_mode="asgi",
        cors_allowed_origins=_socket_origin_allowed,
        ping_interval=settings.socketio_ping_interval_seconds,
        ping_timeout=settings.socketio_ping_timeout_seconds,
        max_http_buffer_size=1_000_000,
        **options,
    )


def configure_socketio_manager(
    server: socketio.AsyncServer,
    settings: Settings,
    *,
    redis_available: bool,
) -> None:
    """Select the manager from the already-completed realtime backend probe.

    Lifespan startup runs before Engine.IO accepts a connection, so replacing the
    initial in-memory manager here cannot race manager initialization.
    """

    if server.manager_initialized:
        raise RuntimeError("socketio_manager_already_initialized")
    manager = (
        socketio.AsyncRedisManager(settings.redis_url)
        if redis_available
        else socketio.AsyncManager()
    )
    manager.set_server(server)
    server.manager = manager


async def shutdown_socketio_server(server: socketio.AsyncServer) -> None:
    """Close Socket.IO-owned resources and make the server restartable."""

    await _await_shutdown_completion(_shutdown_socketio_server(server))


async def _shutdown_socketio_server(server: socketio.AsyncServer) -> None:
    """Run every teardown stage and retain the first stage failure."""

    first_error: BaseException | None = None
    try:
        sockets = list(server.eio.sockets.values())
        server.eio.sockets = {}
        manager = server.manager
        listener: asyncio.Task[object] | None = None
        resources: list[object] = []
        if isinstance(manager, socketio.AsyncRedisManager):
            raw_listener = getattr(manager, "thread", None)
            listener = (
                raw_listener if isinstance(raw_listener, asyncio.Task) else None
            )
            manager.thread = None
            seen_resources: set[int] = set()
            for name in ("pubsub", "redis"):
                resource = getattr(manager, name, None)
                setattr(manager, name, None)
                if resource is None or id(resource) in seen_resources:
                    continue
                seen_resources.add(id(resource))
                resources.append(resource)
            manager.connected = False

        if sockets:
            # Engine.IO's bulk ``disconnect()`` waits for each polling client's
            # outbound queue to be consumed. During ASGI shutdown no client may be
            # polling anymore, so that wait can deadlock the lifespan. Close every
            # socket without waiting for its queue, then stop the service task.
            results = await asyncio.gather(
                *(
                    client.close(
                        wait=False,
                        reason=server.eio.reason.SERVER_DISCONNECT,
                    )
                    for client in sockets
                ),
                return_exceptions=True,
            )
            first_error = next(
                (
                    result
                    for result in results
                    if isinstance(result, BaseException)
                ),
                None,
            )
        try:
            await server.shutdown()
        except BaseException as error:
            if first_error is None:
                first_error = error

        if listener is not None:
            if not listener.done():
                listener.cancel()
            results = await asyncio.gather(listener, return_exceptions=True)
            if (
                first_error is None
                and results
                and isinstance(results[0], BaseException)
                and not isinstance(results[0], asyncio.CancelledError)
            ):
                first_error = results[0]
        for resource in resources:
            try:
                await _close_async_resource(resource)
            except BaseException as error:
                if first_error is None:
                    first_error = error
    finally:
        _reset_socketio_server(server)

    if first_error is not None:
        raise first_error


def _reset_socketio_server(server: socketio.AsyncServer) -> None:
    server.environ.clear()
    fresh_manager = socketio.AsyncManager()
    fresh_manager.set_server(server)
    server.manager = fresh_manager
    server.manager_initialized = False
    server.eio.start_service_task = True
    server.eio.service_task_event = None
    server.eio.service_task_handle = None


async def _await_shutdown_completion(
    cleanup: Coroutine[object, object, None],
) -> None:
    """Do not let caller cancellation interrupt manager/resource reset."""
    task = asyncio.create_task(cleanup)
    cancellation: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as error:
            if cancellation is None:
                cancellation = error
        except BaseException as error:
            if cancellation is None:
                raise
            cancellation.add_note(
                f"Socket.IO cleanup failed with {type(error).__name__}"
            )
            raise cancellation from None
    if cancellation is not None:
        try:
            task.result()
        except BaseException as error:
            cancellation.add_note(
                f"Socket.IO cleanup failed with {type(error).__name__}"
            )
        raise cancellation
    task.result()


async def _close_async_resource(resource: object) -> None:
    close = getattr(resource, "aclose", None)
    if not callable(close):
        close = getattr(resource, "close", None)
    if not callable(close):
        return
    result = close()
    if inspect.isawaitable(result):
        await result


def _socket_origin_allowed(origin: str | None, environ: dict[str, object]) -> bool:
    if origin is None:
        return True
    if not isinstance(origin, str) or not origin or "," in origin:
        return False
    try:
        parsed = urlsplit(origin)
        port = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.hostname is None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        return False
    request_origins = _request_origins(environ)
    if origin in request_origins:
        return True
    hostname = parsed.hostname.lower()
    return hostname in {"localhost", "127.0.0.1", "::1"} and (
        port is None or 1 <= port <= 65_535
    )


def _request_origins(environ: dict[str, object]) -> set[str]:
    scheme = environ.get("wsgi.url_scheme")
    host = environ.get("HTTP_HOST")
    origins: set[str] = set()
    if isinstance(scheme, str) and isinstance(host, str):
        origins.add(f"{scheme}://{host}")
        forwarded_scheme = environ.get("HTTP_X_FORWARDED_PROTO", scheme)
        forwarded_host = environ.get("HTTP_X_FORWARDED_HOST", host)
        if isinstance(forwarded_scheme, str) and isinstance(forwarded_host, str):
            origins.add(
                f"{forwarded_scheme.split(',')[0].strip()}://"
                f"{forwarded_host.split(',')[0].strip()}"
            )
    return origins
