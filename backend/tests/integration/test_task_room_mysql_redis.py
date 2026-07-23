from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import socket
from types import MappingProxyType
from urllib.parse import urlparse
from uuid import uuid4

from alembic import command
from alembic.config import Config
import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, URL, make_url
import socketio
import uvicorn

from app.core.auth import create_access_token
from app.core.config import get_settings
from app.db import models
from app.services.agent_runtime import PreparedRuntimeTurn, RuntimeTurn
from app.services.runtime_types import (
    AssistantMessageEvent,
    ProviderCallMetrics,
    RuntimeEvent,
    TextDeltaEvent,
    ToolCall,
    ToolResult,
    ToolResultEvent,
    ToolStartEvent,
)
from app.services.tool_registry import ToolRegistrySnapshot


ALEMBIC_INI = Path(__file__).parents[2] / "alembic.ini"
DATABASE_SUFFIX = "_migration_test"
TERMINAL_STATUSES = {"COMPLETED", "FAILED", "CANCELLED"}


def _dedicated_mysql_url() -> URL:
    if os.environ.get("RUN_MYSQL_INTEGRATION") != "1":
        pytest.skip("requires RUN_MYSQL_INTEGRATION=1")
    raw_url = os.environ.get("MYSQL_MIGRATION_DATABASE_URL")
    if not raw_url:
        pytest.fail(
            "RUN_MYSQL_INTEGRATION=1 requires explicit "
            "MYSQL_MIGRATION_DATABASE_URL"
        )
    url = make_url(raw_url)
    if not url.drivername.startswith("mysql") or not url.database:
        pytest.fail("MYSQL_MIGRATION_DATABASE_URL must name a MySQL database")
    if not url.database.casefold().endswith(DATABASE_SUFFIX):
        pytest.fail(
            "MYSQL_MIGRATION_DATABASE_URL must name a dedicated database ending "
            f"with {DATABASE_SUFFIX}"
        )
    configured = os.environ.get("DATABASE_URL")
    if configured and _database_identity(url) == _database_identity(make_url(configured)):
        pytest.fail("Task room integration database must differ from DATABASE_URL")
    return url


def test_integration_flag_requires_explicit_task_room_mysql_url(monkeypatch) -> None:
    monkeypatch.setenv("RUN_MYSQL_INTEGRATION", "1")
    monkeypatch.delenv("MYSQL_MIGRATION_DATABASE_URL", raising=False)

    with pytest.raises(pytest.fail.Exception, match="requires explicit"):
        _dedicated_mysql_url()


def _database_identity(url: URL) -> tuple[str | None, int, str | None]:
    host = (url.host or "").casefold().rstrip(".")
    if host in {"localhost", "127.0.0.1", "::1"}:
        host = "loopback"
    return host, url.port or 3306, url.database.casefold() if url.database else None


def _task_room_redis_url() -> str:
    if os.environ.get("RUN_MYSQL_INTEGRATION") != "1":
        pytest.skip("requires RUN_MYSQL_INTEGRATION=1")
    raw_url = os.environ.get("REDIS_URL")
    if not raw_url:
        pytest.fail("RUN_MYSQL_INTEGRATION=1 requires explicit REDIS_URL")
    parsed = urlparse(raw_url)
    if parsed.scheme not in {"redis", "rediss"} or not parsed.hostname:
        pytest.fail("REDIS_URL must be a standalone Redis URL")
    if parsed.path != "/15" or parsed.params or parsed.query or parsed.fragment:
        pytest.fail("Task room integration requires REDIS_URL database /15")
    return raw_url


def test_integration_flag_requires_explicit_task_room_redis_url(monkeypatch) -> None:
    monkeypatch.setenv("RUN_MYSQL_INTEGRATION", "1")
    monkeypatch.delenv("REDIS_URL", raising=False)

    with pytest.raises(pytest.fail.Exception, match="requires explicit REDIS_URL"):
        _task_room_redis_url()


@pytest.fixture
def redis_url() -> str:
    return _task_room_redis_url()


class DeterministicRuntime:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self.gate_reached = asyncio.Event()
        self.release_gate = asyncio.Event()

    def prepare_turn(self, turn: RuntimeTurn) -> PreparedRuntimeTurn:
        return PreparedRuntimeTurn(
            context=turn.context,
            agent_prompt=turn.agent_prompt,
            provider=turn.provider,
            model=turn.model,
            turns=turn.turns,
            tool_registry=ToolRegistrySnapshot(
                specs=MappingProxyType({}),
                prompt_modules=(),
            ),
        )

    def stream_turn(
        self,
        turn: PreparedRuntimeTurn,
        *,
        observer: Callable[[ProviderCallMetrics], None],
    ) -> Iterator[RuntimeEvent]:
        del observer
        prompt = turn.turns[-1].user.text
        if prompt == "use tool":
            call = ToolCall(
                id="call.integration.1",
                name="integration_lookup",
                arguments={"query": "mysql redis"},
            )
            yield AssistantMessageEvent(content=None, tool_calls=(call,))
            yield ToolStartEvent(call=call)
            yield ToolResultEvent(
                call=call,
                result=ToolResult(call_id=call.id, content="integration hit"),
            )
            yield TextDeltaEvent(content="final answer")
            yield AssistantMessageEvent(content="final answer")
            return

        if prompt == "gate reconnect":
            yield TextDeltaEvent(content="A😀")
            self._loop.call_soon_threadsafe(self.gate_reached.set)
            waiter = asyncio.run_coroutine_threadsafe(
                self.release_gate.wait(), self._loop
            )
            waiter.result(timeout=20)
            yield AssistantMessageEvent(content="A😀")
            return

        yield TextDeltaEvent(content="deterministic answer")
        yield AssistantMessageEvent(content="deterministic answer")

    def release(self) -> None:
        self.release_gate.set()


@dataclass(frozen=True, slots=True)
class MysqlAppHandle:
    url: str
    token: str
    other_token: str
    runtime: DeterministicRuntime
    app: object
    sio: socketio.AsyncServer

    async def wait_for_terminal(self, task_id: int) -> dict[str, object]:
        deadline = asyncio.get_running_loop().time() + 10
        while asyncio.get_running_loop().time() < deadline:
            detail = await self.get_task(task_id)
            if detail["status"] in TERMINAL_STATUSES:
                return detail
            await asyncio.sleep(0.02)
        raise AssertionError(f"Task {task_id} did not reach a terminal state")

    async def get_task(self, task_id: int) -> dict[str, object]:
        async with httpx.AsyncClient(base_url=self.url) as client:
            response = await client.get(
                f"/api/tasks/{task_id}",
                headers={"Authorization": f"Bearer {self.token}"},
            )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert isinstance(payload, dict)
        return payload


class MysqlAppFactory:
    def __init__(self, *, database_url: URL, redis_url: str, root: Path) -> None:
        self.database_url = database_url
        self.redis_url = redis_url
        self.root = root

    @asynccontextmanager
    async def start(self) -> AsyncIterator[MysqlAppHandle]:
        from app import main as main_module
        from tests.fakes import FakeKnowledgeVectorStore

        prefix = get_settings().chat_stream_key_prefix
        redis: Redis | None = None
        runtime: DeterministicRuntime | None = None
        listener: socket.socket | None = None
        server: uvicorn.Server | None = None
        server_task: asyncio.Task[None] | None = None
        app_engine: Engine | None = None
        primary_error: BaseException | None = None
        cleanup_errors: list[tuple[str, BaseException]] = []

        try:
            config = Config(str(ALEMBIC_INI))
            _clear_dedicated_migration_database(self.database_url)
            _run_alembic(command.upgrade, config, "head")

            redis = Redis.from_url(self.redis_url, decode_responses=True)
            await _delete_redis_prefix(redis, prefix)

            app = main_module.create_app(
                knowledge_retriever_factory_override=FakeKnowledgeVectorStore(),
                start_background_workers=False,
            )
            app_engine = app.state.session_factory.kw["bind"]
            runtime = DeterministicRuntime(asyncio.get_running_loop())
            app.state.task_execution_service.runtime = runtime
            token, other_token = _create_test_users(app.state.session_factory)
            asgi_app = socketio.ASGIApp(
                app.state.socket_server,
                other_asgi_app=app,
                socketio_path="socket.io",
            )
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen(128)
            listener.setblocking(False)
            port = listener.getsockname()[1]
            server = uvicorn.Server(
                uvicorn.Config(
                    asgi_app,
                    host="127.0.0.1",
                    port=port,
                    log_level="warning",
                    lifespan="on",
                )
            )
            server_task = asyncio.create_task(server.serve(sockets=[listener]))
            await _wait_for_server(server, server_task)
            yield MysqlAppHandle(
                url=f"http://127.0.0.1:{port}",
                token=token,
                other_token=other_token,
                runtime=runtime,
                app=app,
                sio=app.state.socket_server,
            )
        except BaseException as error:
            primary_error = error
            raise
        finally:
            if runtime is not None:
                _run_cleanup_step(
                    cleanup_errors,
                    "runtime gate release",
                    runtime.release,
                )
            if server is not None:
                server.should_exit = True
            if server_task is not None:
                try:
                    await asyncio.wait_for(server_task, timeout=15)
                except BaseException as error:
                    cleanup_errors.append(("uvicorn shutdown", error))
            if listener is not None:
                _run_cleanup_step(
                    cleanup_errors,
                    "listening socket close",
                    listener.close,
                )
            if app_engine is not None:
                _run_cleanup_step(
                    cleanup_errors,
                    "application engine dispose",
                    app_engine.dispose,
                )
            if redis is not None:
                try:
                    await _delete_redis_prefix(redis, prefix)
                except BaseException as error:
                    cleanup_errors.append(("Redis prefix cleanup", error))
                try:
                    await redis.aclose()
                except BaseException as error:
                    cleanup_errors.append(("Redis client close", error))
            try:
                _clear_dedicated_migration_database(self.database_url)
            except BaseException as error:
                cleanup_errors.append(("dedicated database cleanup", error))
            _finish_cleanup(cleanup_errors, primary_error=primary_error)


@pytest.fixture
def mysql_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, redis_url: str) -> MysqlAppFactory:
    database_url = _dedicated_mysql_url()
    prefix = f"auto_reign:task14:{uuid4().hex}"
    monkeypatch.setenv(
        "DATABASE_URL", database_url.render_as_string(hide_password=False)
    )
    monkeypatch.setenv("REDIS_URL", redis_url)
    monkeypatch.setenv("CHAT_STREAM_KEY_PREFIX", prefix)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OBJECT_STORE_BACKEND", "local")
    monkeypatch.setenv("OBJECT_STORE_LOCAL_ROOT", str(tmp_path / "objects"))
    monkeypatch.setenv("QDRANT_URL", ":memory:")
    monkeypatch.setenv("QDRANT_COLLECTION", "task14")
    monkeypatch.setenv("QWEN_API_KEY", "task14-no-network")
    monkeypatch.setenv("JWT_SECRET_KEY", "task14-integration-secret")
    get_settings.cache_clear()
    yield MysqlAppFactory(
        database_url=database_url,
        redis_url=redis_url,
        root=tmp_path,
    )
    get_settings.cache_clear()


def _create_test_users(session_factory) -> tuple[str, str]:
    with session_factory.begin() as session:
        owner = models.User(
            username=f"task14-owner-{uuid4().hex}",
            password_hash="unused",
            display_name="Task 14 Owner",
            role="user",
            is_active=True,
            token_version=1,
            settings_json={},
        )
        other = models.User(
            username=f"task14-other-{uuid4().hex}",
            password_hash="unused",
            display_name="Task 14 Other",
            role="user",
            is_active=True,
            token_version=1,
            settings_json={},
        )
        session.add_all([owner, other])
        session.flush()
        owner_identity = owner.username, owner.id, owner.token_version
        other_identity = other.username, other.id, other.token_version
    return (
        create_access_token(*owner_identity),
        create_access_token(*other_identity),
    )


async def _wait_for_server(
    server: uvicorn.Server,
    server_task: asyncio.Task[None],
) -> None:
    deadline = asyncio.get_running_loop().time() + 15
    while not server.started:
        if server_task.done():
            server_task.result()
            raise AssertionError("uvicorn exited before startup")
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("uvicorn did not start")
        await asyncio.sleep(0.02)


def _run_alembic(
    operation: Callable[[Config, str], None],
    config: Config,
    revision: str,
) -> None:
    """Prevent Alembic's fileConfig from disabling unrelated test loggers."""
    root = logging.getLogger()
    root_state = root.handlers[:], root.level, root.disabled
    logger_states = {
        name: (logger.handlers[:], logger.level, logger.propagate, logger.disabled)
        for name, logger in logging.Logger.manager.loggerDict.items()
        if isinstance(logger, logging.Logger)
    }
    try:
        operation(config, revision)
    finally:
        root.handlers[:], root.level, root.disabled = root_state
        for name, state in logger_states.items():
            logger = logging.getLogger(name)
            logger.handlers[:], logger.level, logger.propagate, logger.disabled = state


def _run_cleanup_step(
    errors: list[tuple[str, BaseException]],
    label: str,
    operation: Callable[[], object],
) -> None:
    try:
        operation()
    except BaseException as error:
        errors.append((label, error))


def _finish_cleanup(
    errors: list[tuple[str, BaseException]],
    *,
    primary_error: BaseException | None,
) -> None:
    if not errors:
        return
    if primary_error is not None:
        for label, error in errors:
            primary_error.add_note(
                f"Task room integration cleanup failed at {label}: "
                f"{type(error).__name__}"
            )
        return
    if len(errors) == 1:
        raise errors[0][1]
    raise BaseExceptionGroup(
        "Task room integration cleanup failures",
        [error for _label, error in errors],
    )


def _clear_dedicated_migration_database(database_url: URL) -> None:
    if not database_url.database or not database_url.database.casefold().endswith(
        DATABASE_SUFFIX
    ):
        raise AssertionError("refusing to clear a non-dedicated database")
    engine = create_engine(database_url)
    try:
        models.Base.metadata.drop_all(engine)
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
    finally:
        engine.dispose()


async def _delete_redis_prefix(redis: Redis, prefix: str) -> None:
    batch: list[str] = []
    async for key in redis.scan_iter(match=f"{prefix}:*"):
        batch.append(key)
        if len(batch) == 100:
            await redis.delete(*batch)
            batch.clear()
    if batch:
        await redis.delete(*batch)


async def _wait_for(
    predicate: Callable[[], bool],
    *,
    description: str,
    timeout: float = 10,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError(f"Timed out waiting for {description}")
        await asyncio.sleep(0.01)


async def _connect(url: str, token: str) -> socketio.AsyncClient:
    client = socketio.AsyncClient(reconnection=False)
    await client.connect(
        url,
        auth={"token": token},
        namespaces=["/chat"],
        socketio_path="socket.io",
        transports=["websocket"],
    )
    return client


def test_cleanup_steps_continue_and_annotate_the_primary_error() -> None:
    cleanup_errors: list[tuple[str, BaseException]] = []
    completed: list[str] = []

    def fail() -> None:
        raise OSError("cleanup failed")

    _run_cleanup_step(cleanup_errors, "first", fail)
    _run_cleanup_step(cleanup_errors, "second", lambda: completed.append("second"))
    primary = RuntimeError("original test failure")

    _finish_cleanup(cleanup_errors, primary_error=primary)

    assert completed == ["second"]
    assert primary.__notes__ == [
        "Task room integration cleanup failed at first: OSError"
    ]


def test_cleanup_errors_are_aggregated_without_a_primary_error() -> None:
    first = OSError("first")
    second = RuntimeError("second")

    with pytest.raises(BaseExceptionGroup) as raised:
        _finish_cleanup(
            [("first", first), ("second", second)],
            primary_error=None,
        )

    assert raised.value.exceptions == (first, second)


def test_task_room_persists_and_recovers_tool_blocks(mysql_app: MysqlAppFactory) -> None:
    async def scenario() -> None:
        async with mysql_app.start() as handle:
            client = socketio.AsyncClient(reconnection=False)
            ack_received = False
            events: list[tuple[str, bool, dict[str, object]]] = []

            def record(name: str) -> Callable[[dict[str, object]], None]:
                def handler(payload: dict[str, object]) -> None:
                    events.append((name, ack_received, payload))

                return handler

            client.on("chat:start", record("start"), namespace="/chat")
            client.on("chat:block_created", record("created"), namespace="/chat")
            client.on("chat:block_updated", record("updated"), namespace="/chat")
            client.on("chat:done", record("done"), namespace="/chat")
            try:
                await client.connect(
                    handle.url,
                    auth={"token": handle.token},
                    namespaces=["/chat"],
                    socketio_path="socket.io",
                    transports=["websocket"],
                )
                ack = await client.call(
                    "chat:send",
                    {"message": "use tool", "context_ids": []},
                    namespace="/chat",
                    timeout=5,
                )
                ack_received = True
                assert ack["task_id"] > 0
                assert ack["subtask_id"] > 0
                detail = await handle.wait_for_terminal(ack["task_id"])
                await _wait_for(
                    lambda: any(name == "done" for name, _after, _data in events),
                    description="chat:done",
                )
                assistant = detail["subtasks"][-1]
                chain = assistant["result"]["messages_chain"]
                assert chain[-1]["role"] == "assistant"
                assert any(item["role"] == "tool" for item in chain)
                assert events
                assert all(after_ack for _name, after_ack, _payload in events)
                names = [name for name, _after, _payload in events]
                assert names.index("start") < names.index("created") < names.index("updated")
                assert names.index("updated") < names.index("done")
                assert any(
                    name == "updated" and payload.get("status") == "done"
                    for name, _after, payload in events
                )
                persisted_tool = next(
                    block
                    for block in assistant["result"]["blocks"]
                    if block["type"] == "tool"
                )
                assert persisted_tool["tool_output"] == "integration hit"
            finally:
                if client.connected:
                    await client.disconnect()

    asyncio.run(scenario())


def test_reconnect_join_recovers_active_utf16_stream(mysql_app: MysqlAppFactory) -> None:
    async def scenario() -> None:
        async with mysql_app.start() as handle:
            first = await _connect(handle.url, handle.token)
            seen_blocks: list[dict[str, object]] = []
            seen_chunks: list[dict[str, object]] = []
            first.on(
                "chat:block_created",
                lambda payload: seen_blocks.append(payload),
                namespace="/chat",
            )
            first.on(
                "chat:chunk",
                lambda payload: seen_chunks.append(payload),
                namespace="/chat",
            )
            second: socketio.AsyncClient | None = None
            try:
                ack = await first.call(
                    "chat:send",
                    {"message": "gate reconnect", "context_ids": []},
                    namespace="/chat",
                    timeout=5,
                )
                await _wait_for(
                    lambda: bool(seen_blocks and seen_chunks),
                    description="initial streamed text block",
                )
                await asyncio.wait_for(handle.runtime.gate_reached.wait(), timeout=5)
                real_block_id = seen_blocks[0]["block"]["id"]
                assert real_block_id == seen_chunks[0]["block_id"]
                await first.disconnect()

                second = await _connect(handle.url, handle.token)
                join = await second.call(
                    "task:join",
                    {
                        "task_id": ack["task_id"],
                        "after_message_id": ack["message_id"],
                    },
                    namespace="/chat",
                    timeout=5,
                )
                assert join["task_id"] == ack["task_id"]
                assert [item["role"] for item in join["subtasks"]] == ["ASSISTANT"]
                streaming = join["streaming"]
                assert streaming["cached_content"] == "A😀"
                assert streaming["offset"] == 3
                assert streaming["blocks"] == [
                        {
                            "id": real_block_id,
                            "type": "text",
                            "content": "",
                        "status": "streaming",
                        "timestamp": streaming["blocks"][0]["timestamp"],
                    }
                ]
                assert streaming["generation_id"] == seen_chunks[0]["generation_id"]
                handle.runtime.release()
                detail = await handle.wait_for_terminal(ack["task_id"])
                assert detail["subtasks"][-1]["result"]["value"] == "A😀"
            finally:
                handle.runtime.release()
                if first.connected:
                    await first.disconnect()
                if second is not None and second.connected:
                    await second.disconnect()

    asyncio.run(scenario())


def test_other_user_join_is_denied_and_never_enters_task_room(
    mysql_app: MysqlAppFactory,
) -> None:
    async def scenario() -> None:
        async with mysql_app.start() as handle:
            owner = await _connect(handle.url, handle.token)
            other = await _connect(handle.url, handle.other_token)
            try:
                ack = await owner.call(
                    "chat:send",
                    {"message": "gate reconnect", "context_ids": []},
                    namespace="/chat",
                    timeout=5,
                )
                await asyncio.wait_for(handle.runtime.gate_reached.wait(), timeout=5)
                denied = await other.call(
                    "task:join",
                    {"task_id": ack["task_id"], "after_message_id": None},
                    namespace="/chat",
                    timeout=5,
                )
                assert denied == {"error": {"code": "access_denied"}}
                room = f"task:{ack['task_id']}"
                participants = {
                    sid
                    for sid, _engine_sid in handle.sio.manager.get_participants(
                        "/chat", room
                    )
                }
                assert owner.get_sid("/chat") in participants
                assert other.get_sid("/chat") not in participants
            finally:
                handle.runtime.release()
                if owner.connected:
                    await owner.disconnect()
                if other.connected:
                    await other.disconnect()

    asyncio.run(scenario())
