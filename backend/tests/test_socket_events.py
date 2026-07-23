import asyncio

import httpx
import pytest
from pydantic import ValidationError
import socketio

from app.core.config import Settings
from app.core.socketio import (
    configure_socketio_manager,
    create_socketio_server,
    shutdown_socketio_server,
)
from app.schemas.socket_events import (
    ChatCancelAck,
    ChatBlockUpdatedPayload,
    ChatChunkPayload,
    ChatRetryPayload,
    ChatRetryAck,
    ChatSendAck,
    ChatSendPayload,
    SocketErrorAck,
    TaskJoinAck,
    TaskJoinPayload,
    TaskLeaveAck,
)


def test_socket_input_payloads_forbid_extra_fields_and_non_positive_ids() -> None:
    with pytest.raises(ValidationError):
        TaskJoinPayload.model_validate({"task_id": 1, "unexpected": True})
    with pytest.raises(ValidationError):
        TaskJoinPayload.model_validate({"task_id": 0})
    with pytest.raises(ValidationError):
        TaskJoinPayload.model_validate({"task_id": True})
    with pytest.raises(ValidationError):
        ChatRetryPayload.model_validate({"task_id": 1, "subtask_id": -1})


def test_task_join_accepts_initial_and_reconnect_cursors() -> None:
    assert TaskJoinPayload(task_id=7).after_message_id is None
    assert TaskJoinPayload(task_id=7, after_message_id=0).after_message_id == 0
    assert TaskJoinPayload(task_id=7, after_message_id=8).after_message_id == 8
    with pytest.raises(ValidationError):
        TaskJoinPayload.model_validate({"task_id": 7, "after_message_id": -1})


def test_chat_send_converts_to_the_single_http_domain_request() -> None:
    payload = ChatSendPayload(
        task_id=7,
        message="hello",
        context_ids=[2, 3],
    )

    request = payload.to_request()

    assert request.task_id == 7
    assert request.message == "hello"
    assert request.context_ids == [2, 3]
    with pytest.raises(ValidationError):
        ChatSendPayload(message="hello", context_ids=[2, 2])
    with pytest.raises(ValidationError):
        ChatSendPayload(message="")
    with pytest.raises(ValidationError):
        ChatSendPayload.model_validate({"message": b"\xff"})


def test_chunk_uses_canonical_block_ids_and_js_offsets() -> None:
    payload = ChatChunkPayload(
        task_id=1,
        subtask_id=2,
        generation_id="generation-1",
        block_id="block:1",
        block_offset=2,
        offset=4,
        content="😀",
    )
    assert payload.block_offset == 2
    assert payload.offset == 4
    with pytest.raises(ValidationError):
        ChatChunkPayload(
            task_id=1,
            subtask_id=2,
            generation_id="generation-1",
            block_id="not allowed/",
            block_offset=0,
            offset=0,
            content="x",
        )


def test_block_update_is_exact() -> None:
    value = ChatBlockUpdatedPayload(
        task_id=1,
        subtask_id=2,
        generation_id="generation-1",
        block_id="block.1",
        status="done",
        content="answer",
    )
    assert value.content == "answer"
    with pytest.raises(ValidationError):
        ChatBlockUpdatedPayload.model_validate(
            {**value.model_dump(), "unknown": "secret"}
        )


def test_socketio_factory_uses_only_the_supplied_redis_decision() -> None:
    settings = Settings(_env_file=None)

    degraded = create_socketio_server(settings, redis_available=False)
    distributed = create_socketio_server(settings, redis_available=True)

    assert isinstance(degraded.manager, socketio.AsyncManager)
    assert not isinstance(degraded.manager, socketio.AsyncRedisManager)
    assert isinstance(distributed.manager, socketio.AsyncRedisManager)
    assert degraded.eio.ping_interval == settings.socketio_ping_interval_seconds
    assert degraded.eio.ping_timeout == settings.socketio_ping_timeout_seconds
    assert degraded.eio.max_http_buffer_size == 1_000_000


def test_socketio_factory_enforces_same_origin_by_default() -> None:
    server = create_socketio_server(Settings(_env_file=None), redis_available=False)
    environ = {
        "wsgi.url_scheme": "http",
        "HTTP_HOST": "127.0.0.1:8000",
    }

    policy = server.eio.cors_allowed_origins

    assert callable(policy)
    assert policy("http://127.0.0.1:8000", environ) is True
    assert policy("http://localhost:3000", environ) is True
    assert policy("https://hostile.example", environ) is False
    assert policy("http://localhost.evil.example:3000", environ) is False
    assert policy("http://user@localhost:3000", environ) is False
    assert policy("http://localhost:3000/path", environ) is False
    assert policy("http://localhost:99999", environ) is False


def test_engineio_handshake_origin_policy_and_lifespan_restart() -> None:
    async def scenario() -> None:
        settings = Settings(_env_file=None)
        server = create_socketio_server(settings, redis_available=False)
        application = socketio.ASGIApp(server, socketio_path="socket.io")
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1:8000",
        ) as client:
            accepted = await client.get(
                "/socket.io/?EIO=4&transport=polling",
                headers={"Origin": "http://localhost:3000"},
            )
            rejected = await client.get(
                "/socket.io/?EIO=4&transport=polling",
                headers={"Origin": "https://hostile.example"},
            )
            assert accepted.status_code == 200
            assert rejected.status_code == 400
            assert server.manager_initialized is True

            await asyncio.wait_for(shutdown_socketio_server(server), timeout=2)
            assert server.manager_initialized is False
            configure_socketio_manager(
                server,
                settings,
                redis_available=False,
            )

            restarted = await client.get(
                "/socket.io/?EIO=4&transport=polling",
                headers={"Origin": "http://127.0.0.1:3100"},
            )
            assert restarted.status_code == 200
            assert server.manager_initialized is True
            await asyncio.wait_for(shutdown_socketio_server(server), timeout=2)

    asyncio.run(scenario())


def test_redis_manager_shutdown_closes_owned_resources_once() -> None:
    class CloseProbe:
        def __init__(self) -> None:
            self.close_calls = 0

        async def aclose(self) -> None:
            self.close_calls += 1

    async def scenario() -> None:
        settings = Settings(_env_file=None)
        server = create_socketio_server(settings, redis_available=True)
        manager = server.manager
        assert isinstance(manager, socketio.AsyncRedisManager)
        listener = asyncio.create_task(asyncio.Event().wait())
        pubsub = CloseProbe()
        redis = CloseProbe()
        manager.thread = listener
        manager.pubsub = pubsub
        manager.redis = redis
        manager.connected = True
        server.manager_initialized = True

        await asyncio.wait_for(shutdown_socketio_server(server), timeout=2)
        await asyncio.wait_for(shutdown_socketio_server(server), timeout=2)

        assert listener.done() and listener.cancelled()
        assert pubsub.close_calls == 1
        assert redis.close_calls == 1
        assert isinstance(server.manager, socketio.AsyncManager)
        assert not isinstance(server.manager, socketio.AsyncRedisManager)
        assert server.manager_initialized is False

    asyncio.run(scenario())


@pytest.mark.parametrize("blocked_stage", ["socket", "pubsub"])
def test_socketio_shutdown_cancellation_drains_and_resets(
    blocked_stage: str,
) -> None:
    class CloseProbe:
        def __init__(self, *, blocked: bool = False) -> None:
            self.close_calls = 0
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            if not blocked:
                self.release.set()

        async def aclose(self) -> None:
            self.close_calls += 1
            self.started.set()
            await self.release.wait()

    class SocketProbe:
        def __init__(self, *, blocked: bool) -> None:
            self.close_calls = 0
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            if not blocked:
                self.release.set()

        async def close(self, **_kwargs: object) -> None:
            self.close_calls += 1
            self.started.set()
            await self.release.wait()

    async def scenario() -> None:
        settings = Settings(_env_file=None)
        server = create_socketio_server(settings, redis_available=True)
        manager = server.manager
        assert isinstance(manager, socketio.AsyncRedisManager)
        listener = asyncio.create_task(asyncio.Event().wait())
        socket = SocketProbe(blocked=blocked_stage == "socket")
        pubsub = CloseProbe(blocked=blocked_stage == "pubsub")
        redis = CloseProbe()
        server.eio.sockets["sid"] = socket  # type: ignore[assignment]
        manager.thread = listener
        manager.pubsub = pubsub
        manager.redis = redis
        manager.connected = True
        server.manager_initialized = True

        shutdown = asyncio.create_task(shutdown_socketio_server(server))
        blocker = socket if blocked_stage == "socket" else pubsub
        await asyncio.wait_for(blocker.started.wait(), timeout=2)
        shutdown.cancel()
        await asyncio.sleep(0)
        assert shutdown.done() is False
        blocker.release.set()

        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(shutdown, timeout=2)

        assert socket.close_calls == 1
        assert listener.done()
        assert pubsub.close_calls == 1
        assert redis.close_calls == 1
        assert server.eio.sockets == {}
        assert isinstance(server.manager, socketio.AsyncManager)
        assert not isinstance(server.manager, socketio.AsyncRedisManager)
        assert server.manager_initialized is False
        assert server.eio.start_service_task is True
        assert server.eio.service_task_event is None
        assert server.eio.service_task_handle is None

    asyncio.run(scenario())


def test_ack_models_are_exact_and_validate_every_identifier() -> None:
    assert TaskJoinAck(task_id=1, subtasks=[]).model_dump(mode="json") == {
        "task_id": 1,
        "subtasks": [],
        "streaming": None,
    }
    assert TaskLeaveAck(task_id=1).task_id == 1
    assert ChatSendAck(task_id=1, subtask_id=2, message_id=3).message_id == 3
    assert ChatCancelAck(task_id=1, subtask_id=2, accepted=True).accepted is True
    assert ChatRetryAck(task_id=1, subtask_id=2).subtask_id == 2
    assert SocketErrorAck(error={"code": "request_failed"}).model_dump() == {
        "error": {"code": "request_failed"}
    }

    exact_cases = (
        (TaskJoinAck, {"task_id": 1, "subtasks": []}),
        (TaskLeaveAck, {"task_id": 1}),
        (ChatSendAck, {"task_id": 1, "subtask_id": 2, "message_id": 3}),
        (ChatCancelAck, {"task_id": 1, "subtask_id": 2, "accepted": True}),
        (ChatRetryAck, {"task_id": 1, "subtask_id": 2}),
        (SocketErrorAck, {"error": {"code": "request_failed"}}),
    )
    for model, payload in exact_cases:
        with pytest.raises(ValidationError):
            model.model_validate({**payload, "unexpected": "private"})

    with pytest.raises(ValidationError):
        ChatSendAck.model_validate(
            {"task_id": 1, "subtask_id": 2, "message_id": 3, "prompt": "secret"}
        )
    with pytest.raises(ValidationError):
        ChatCancelAck(task_id=1, subtask_id=0, accepted=True)
    with pytest.raises(ValidationError):
        SocketErrorAck.model_validate(
            {"error": {"code": "request_failed", "message": "private data"}}
        )
    with pytest.raises(ValidationError):
        SocketErrorAck(error={"code": "private prompt"})
