import asyncio
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from unittest.mock import AsyncMock, Mock, sentinel

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.db import session as session_module
from app.db.models import UTCDateTime
from app.db.session import create_engine_for_settings

STORAGE_ENVIRONMENT_VARIABLES = (
    "DATABASE_URL",
    "QDRANT_URL",
    "QDRANT_COLLECTION",
)


@pytest.fixture
def main_module(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OBJECT_STORE_BACKEND", "local")
    monkeypatch.setenv(
        "OBJECT_STORE_LOCAL_ROOT",
        str(tmp_path / "module-import-objects"),
    )
    monkeypatch.setenv("QDRANT_URL", ":memory:")
    monkeypatch.setenv("QWEN_API_KEY", "test-qwen-key")
    get_settings.cache_clear()
    module = import_module("app.main")
    root = getattr(module.fastapi_app.state.object_store, "root", None)
    if root is not None:
        assert root != (Path("data") / "objects").resolve()
    yield module
    get_settings.cache_clear()


def _mock_app_settings(init_data_dir: Path) -> Mock:
    return Mock(
        init_data_dir=init_data_dir,
        log_level="INFO",
        chat_context_token_budget=32_000,
        tool_result_token_reserve=4_096,
        image_input_token_reserve=4_096,
        attachment_max_bytes=20 * 1024 * 1024,
        attachment_max_parsed_chars=1_000_000,
        attachment_max_decompressed_bytes=32 * 1024 * 1024,
        attachment_max_pdf_pages=500,
        agent_home_max_file_bytes=1_000_000,
        knowledge_max_parsed_chars=2_000_000,
        knowledge_max_decompressed_bytes=64 * 1024 * 1024,
        knowledge_max_pdf_pages=1_000,
        knowledge_worker_processing_timeout_seconds=300,
        knowledge_worker_poll_interval_seconds=1.0,
        knowledge_max_results=30,
        knowledge_max_query_chars=2_000,
        runtime_max_tool_rounds=8,
        redis_url="redis://127.0.0.1:16379/0",
        chat_stream_ttl_seconds=3600,
        chat_stream_key_prefix="test:chat",
        socketio_ping_interval_seconds=25,
        socketio_ping_timeout_seconds=20,
        model_request_timeout_seconds=30,
    )


def _mock_realtime_backend() -> Mock:
    backend = Mock(
        redis_available=False,
        backend="memory",
        degraded=True,
        close_calls=0,
    )
    backend.stream_store = Mock()
    backend.aclose = Mock()

    async def close() -> None:
        backend.close_calls += 1

    backend.aclose.side_effect = close
    return backend


def _install_realtime_builder(monkeypatch, main_module) -> Mock:
    backend = _mock_realtime_backend()

    async def build(_settings):
        return backend

    monkeypatch.setattr(main_module, "build_chat_realtime", build)
    return backend


def test_settings_exposes_database_and_qdrant_configuration(monkeypatch) -> None:
    for variable in STORAGE_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    settings = Settings(_env_file=None)

    assert settings.database_url == (
        "mysql+pymysql://auto_reign:auto_reign@127.0.0.1:13306/auto_reign"
    )
    assert settings.qdrant_url == "http://127.0.0.1:16333"
    assert settings.qdrant_collection == "auto_reign_default"
    assert settings.init_data_dir == Path("init_data")


def test_settings_does_not_expose_legacy_storage_configuration(monkeypatch) -> None:
    for variable in STORAGE_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    settings = Settings(_env_file=None)

    legacy_attributes = (
        "sqlite_" + "path",
        "chroma_" + "dir",
        "default_" + "collection",
    )
    assert all(not hasattr(settings, attribute) for attribute in legacy_attributes)


def test_create_engine_uses_database_url_and_pool_pre_ping() -> None:
    settings = Settings(
        _env_file=None,
        database_url="mysql+pymysql://user:password@database.example/auto_reign",
    )

    engine = create_engine_for_settings(settings)

    try:
        assert engine.url.drivername == "mysql+pymysql"
        assert engine.url.host == "database.example"
        assert engine.pool._pre_ping is True
    finally:
        engine.dispose()


def test_create_engine_sets_read_committed_for_mysql(monkeypatch) -> None:
    captured: dict[str, object] = {}
    expected_engine = object()

    def fake_create_engine(database_url: str, **kwargs: object) -> object:
        captured["database_url"] = database_url
        captured.update(kwargs)
        return expected_engine

    monkeypatch.setattr(session_module, "create_engine", fake_create_engine)
    settings = Settings(
        _env_file=None,
        database_url="mysql+pymysql://user:password@database.example/auto_reign",
    )

    engine = session_module.create_engine_for_settings(settings)

    assert engine is expected_engine
    assert captured["isolation_level"] == "READ COMMITTED"
    assert captured["pool_pre_ping"] is True


def test_sqlite_keeps_its_existing_test_connection_configuration() -> None:
    settings = Settings(_env_file=None, database_url="sqlite://")

    engine = create_engine_for_settings(settings)

    try:
        assert engine.dialect.name == "sqlite"
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT 1").scalar_one() == 1
    finally:
        engine.dispose()


def test_app_bootstraps_before_startup_and_shutdown_disposes_engine(
    monkeypatch,
    main_module,
) -> None:
    engine = Mock()
    init_data_dir = Path("test-init-data")
    settings = _mock_app_settings(init_data_dir)
    calls: list[str] = []
    runtime = Mock()
    execution_service = Mock()
    execution_service.recover_interrupted.side_effect = lambda: calls.append("recover")
    engine.dispose.side_effect = lambda: calls.append("dispose")

    def record_bootstrap(session_factory, *, init_data_dir: Path) -> None:
        assert session_factory is sentinel.session_factory
        assert init_data_dir == Path("test-init-data")
        calls.append("bootstrap")

    monkeypatch.setattr(main_module, "create_engine_for_settings", lambda _settings: engine)
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    monkeypatch.setattr(
        main_module,
        "make_session_factory",
        lambda _engine: sentinel.session_factory,
    )
    monkeypatch.setattr(
        main_module,
        "bootstrap_application",
        record_bootstrap,
    )
    monkeypatch.setattr(main_module, "AgentRuntime", Mock(return_value=runtime))
    monkeypatch.setattr(
        main_module,
        "build_object_store",
        Mock(return_value=sentinel.object_store),
    )
    monkeypatch.setattr(
        main_module,
        "TaskExecutionService",
        Mock(return_value=execution_service),
    )
    realtime = _install_realtime_builder(monkeypatch, main_module)

    app = main_module.create_app(
        knowledge_retriever_factory_override=sentinel.knowledge_vector_store,
        start_background_workers=False,
    )
    original_namespace_shutdown = app.state.chat_namespace.shutdown

    async def shutdown_namespace() -> None:
        calls.append("namespace_shutdown")
        await original_namespace_shutdown()

    async def shutdown_socket(_server) -> None:
        calls.append("socket_shutdown")

    async def close_realtime() -> None:
        realtime.close_calls += 1
        calls.append("realtime_close")

    app.state.chat_namespace.shutdown = shutdown_namespace
    monkeypatch.setattr(main_module, "shutdown_socketio_server", shutdown_socket)
    realtime.aclose.side_effect = close_realtime

    with TestClient(app):
        calls.append("yield")
        assert calls == ["bootstrap", "recover", "yield"]
        assert app.state.task_execution_service is execution_service
        assert app.state.chat_realtime is realtime
        assert app.state.chat_stream_store is realtime.stream_store
        assert app.state.chat_emitter.stream_store is realtime.stream_store
        assert app.state.object_store is sentinel.object_store
        assert app.state.agent_home_service.store is sentinel.object_store
        assert app.state.agent_home_service.max_file_bytes == 1_000_000
        assert app.state.agent_runtime is runtime
        assert app.state.context_assembler.token_budget == 32_000
        runtime.stream_turn.assert_not_called()

    assert calls == [
        "bootstrap",
        "recover",
        "yield",
        "namespace_shutdown",
        "socket_shutdown",
        "realtime_close",
        "dispose",
    ]
    execution_service.recover_interrupted.assert_called_once_with()
    assert realtime.close_calls == 1
    engine.dispose.assert_called_once_with()

    prepared_send = Mock(
        task_id=7,
        user_subtask_id=10,
        user_message_id=1,
        assistant_subtask_id=11,
    )
    prepared_retry = Mock(
        task_id=7,
        user_subtask_id=None,
        user_message_id=None,
        assistant_subtask_id=12,
    )
    execution_service.prepare_send.return_value = prepared_send
    execution_service.prepare_retry.return_value = prepared_retry
    app.state.chat_namespace.get_session = AsyncMock(  # type: ignore[method-assign]
        return_value={"user_id": 3}
    )
    app.state.chat_namespace._enter_room = AsyncMock()
    scheduled: list[object] = []

    def schedule(prepared: object) -> None:
        scheduled.append(prepared)
        app.state.chat_namespace._execution_metadata.pop(
            prepared.assistant_subtask_id  # type: ignore[attr-defined]
        )

    app.state.chat_namespace._schedule_execution = schedule

    with TestClient(app):
        assert app.state.chat_namespace._shutting_down is False
        sent = asyncio.run(
            app.state.chat_namespace.on_chat_send(
                "sid",
                {"task_id": 7, "message": "again", "context_ids": []},
            )
        )
        retried = asyncio.run(
            app.state.chat_namespace.on_chat_retry(
                "sid",
                {"task_id": 7, "subtask_id": 12},
            )
        )
        assert sent == {"task_id": 7, "subtask_id": 10, "message_id": 1}
        assert retried == {"task_id": 7, "subtask_id": 12}

    assert scheduled == [prepared_send, prepared_retry]
    assert calls[-6:] == [
        "bootstrap",
        "recover",
        "namespace_shutdown",
        "socket_shutdown",
        "realtime_close",
        "dispose",
    ]
    assert execution_service.recover_interrupted.call_count == 2
    assert realtime.close_calls == 2
    assert engine.dispose.call_count == 2

    async def cancel_during_namespace_drain() -> list[str]:
        context = app.router.lifespan_context(app)
        await context.__aenter__()
        started = asyncio.Event()
        release = asyncio.Event()

        async def blocked_shutdown() -> None:
            calls.append("namespace_drain_started")
            started.set()
            await release.wait()
            calls.append("namespace_drain_finished")

        app.state.chat_namespace.shutdown = blocked_shutdown
        call_offset = len(calls)
        exiting = asyncio.create_task(context.__aexit__(None, None, None))
        await asyncio.wait_for(started.wait(), timeout=2)
        exiting.cancel()
        await asyncio.sleep(0)
        assert exiting.done() is False
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(exiting, timeout=2)
        return calls[call_offset:]

    assert asyncio.run(cancel_during_namespace_drain()) == [
        "namespace_drain_started",
        "namespace_drain_finished",
        "socket_shutdown",
        "realtime_close",
        "dispose",
    ]
    assert realtime.close_calls == 3
    assert engine.dispose.call_count == 3


def test_app_disposes_engine_when_bootstrap_fails(monkeypatch, main_module) -> None:
    engine = Mock()
    settings = _mock_app_settings(Path("invalid-init-data"))
    execution_service = Mock()
    monkeypatch.setattr(main_module, "create_engine_for_settings", lambda _settings: engine)
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    monkeypatch.setattr(
        main_module,
        "make_session_factory",
        lambda _engine: sentinel.session_factory,
    )
    monkeypatch.setattr(
        main_module,
        "TaskExecutionService",
        Mock(return_value=execution_service),
    )
    monkeypatch.setattr(
        main_module,
        "build_object_store",
        Mock(return_value=sentinel.object_store),
    )
    _install_realtime_builder(monkeypatch, main_module)
    monkeypatch.setattr(
        main_module,
        "bootstrap_application",
        Mock(side_effect=RuntimeError("bootstrap failed")),
    )

    app = main_module.create_app(
        knowledge_retriever_factory_override=sentinel.knowledge_vector_store,
        start_background_workers=False,
    )
    with pytest.raises(RuntimeError, match="bootstrap failed"), TestClient(app):
        pass

    execution_service.recover_interrupted.assert_not_called()
    engine.dispose.assert_called_once_with()


def test_app_disposes_engine_when_generation_recovery_fails(
    monkeypatch,
    main_module,
) -> None:
    engine = Mock()
    settings = _mock_app_settings(Path("test-init-data"))
    calls: list[str] = []
    execution_service = Mock()

    def fail_recovery() -> None:
        calls.append("recover")
        raise RuntimeError("recovery failed")

    execution_service.recover_interrupted.side_effect = fail_recovery
    engine.dispose.side_effect = lambda: calls.append("dispose")
    monkeypatch.setattr(main_module, "create_engine_for_settings", lambda _settings: engine)
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    monkeypatch.setattr(
        main_module,
        "make_session_factory",
        lambda _engine: sentinel.session_factory,
    )
    monkeypatch.setattr(
        main_module,
        "bootstrap_application",
        lambda *_args, **_kwargs: calls.append("bootstrap"),
    )
    monkeypatch.setattr(
        main_module,
        "TaskExecutionService",
        Mock(return_value=execution_service),
    )
    monkeypatch.setattr(
        main_module,
        "build_object_store",
        Mock(return_value=sentinel.object_store),
    )
    _install_realtime_builder(monkeypatch, main_module)

    app = main_module.create_app(
        knowledge_retriever_factory_override=sentinel.knowledge_vector_store,
        start_background_workers=False,
    )
    with pytest.raises(RuntimeError, match="recovery failed"), TestClient(app):
        pass

    assert calls == ["bootstrap", "recover", "dispose"]
    engine.dispose.assert_called_once_with()


def test_lifespan_starts_and_stops_knowledge_worker_in_locked_order(
    monkeypatch,
    main_module,
) -> None:
    engine = Mock()
    settings = _mock_app_settings(Path("test-init-data"))
    calls: list[str] = []
    execution_service = Mock()
    worker = Mock()
    execution_service.recover_interrupted.side_effect = lambda: calls.append(
        "recover"
    )
    worker.start.side_effect = lambda: calls.append("knowledge_start")
    worker.stop.side_effect = lambda *, timeout: calls.append(
        f"knowledge_stop:{timeout}"
    )
    engine.dispose.side_effect = lambda: calls.append("dispose")
    monkeypatch.setattr(main_module, "create_engine_for_settings", lambda _settings: engine)
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    monkeypatch.setattr(
        main_module,
        "make_session_factory",
        lambda _engine: sentinel.session_factory,
    )
    monkeypatch.setattr(
        main_module,
        "bootstrap_application",
        lambda *_args, **_kwargs: calls.append("bootstrap"),
    )
    monkeypatch.setattr(
        main_module,
        "TaskExecutionService",
        Mock(return_value=execution_service),
    )
    monkeypatch.setattr(
        main_module,
        "KnowledgeIndexWorker",
        Mock(return_value=worker),
    )
    _install_realtime_builder(monkeypatch, main_module)
    monkeypatch.setattr(
        main_module,
        "build_object_store",
        Mock(return_value=sentinel.object_store),
    )

    app = main_module.create_app(
        knowledge_retriever_factory_override=sentinel.knowledge_vector_store,
    )
    with TestClient(app):
        calls.append("yield")
        assert calls == ["bootstrap", "recover", "knowledge_start", "yield"]

    assert calls == [
        "bootstrap",
        "recover",
        "knowledge_start",
        "yield",
        "knowledge_stop:5",
        "dispose",
    ]


def test_lifespan_disposes_engine_without_stopping_when_worker_start_fails(
    monkeypatch,
    main_module,
) -> None:
    engine = Mock()
    settings = _mock_app_settings(Path("test-init-data"))
    calls: list[str] = []
    execution_service = Mock()
    worker = Mock()
    execution_service.recover_interrupted.side_effect = lambda: calls.append(
        "recover"
    )

    def fail_start() -> None:
        calls.append("knowledge_start")
        raise RuntimeError("worker start failed")

    worker.start.side_effect = fail_start
    engine.dispose.side_effect = lambda: calls.append("dispose")
    monkeypatch.setattr(main_module, "create_engine_for_settings", lambda _settings: engine)
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    monkeypatch.setattr(
        main_module,
        "make_session_factory",
        lambda _engine: sentinel.session_factory,
    )
    monkeypatch.setattr(
        main_module,
        "bootstrap_application",
        lambda *_args, **_kwargs: calls.append("bootstrap"),
    )
    monkeypatch.setattr(
        main_module,
        "TaskExecutionService",
        Mock(return_value=execution_service),
    )
    monkeypatch.setattr(
        main_module,
        "KnowledgeIndexWorker",
        Mock(return_value=worker),
    )
    _install_realtime_builder(monkeypatch, main_module)
    monkeypatch.setattr(
        main_module,
        "build_object_store",
        Mock(return_value=sentinel.object_store),
    )

    app = main_module.create_app(
        knowledge_retriever_factory_override=sentinel.knowledge_vector_store,
    )
    with pytest.raises(RuntimeError, match="worker start failed"), TestClient(app):
        pass

    assert calls == ["bootstrap", "recover", "knowledge_start", "dispose"]
    worker.stop.assert_not_called()
    engine.dispose.assert_called_once_with()


def test_lifespan_waits_for_timed_out_worker_before_disposing_engine(
    monkeypatch,
    main_module,
) -> None:
    engine = Mock()
    settings = _mock_app_settings(Path("test-init-data"))
    calls: list[str] = []
    execution_service = Mock()
    worker = Mock()

    def stop(*, timeout) -> None:
        calls.append(f"stop:{timeout}")
        if timeout == 5:
            raise main_module.KnowledgeWorkerStopTimeout("still running")

    worker.stop.side_effect = stop
    engine.dispose.side_effect = lambda: calls.append("dispose")
    monkeypatch.setattr(main_module, "create_engine_for_settings", lambda _settings: engine)
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    monkeypatch.setattr(
        main_module,
        "make_session_factory",
        lambda _engine: sentinel.session_factory,
    )
    monkeypatch.setattr(main_module, "bootstrap_application", Mock())
    monkeypatch.setattr(
        main_module,
        "TaskExecutionService",
        Mock(return_value=execution_service),
    )
    monkeypatch.setattr(
        main_module,
        "KnowledgeIndexWorker",
        Mock(return_value=worker),
    )
    _install_realtime_builder(monkeypatch, main_module)
    monkeypatch.setattr(
        main_module,
        "build_object_store",
        Mock(return_value=sentinel.object_store),
    )
    app = main_module.create_app(
        knowledge_retriever_factory_override=sentinel.knowledge_vector_store,
    )

    with pytest.raises(main_module.KnowledgeWorkerStopTimeout, match="still running"):
        with TestClient(app):
            pass

    assert calls == ["stop:5", "stop:None", "dispose"]


def test_utc_datetime_normalizes_aware_values_to_naive_utc_for_storage() -> None:
    value = datetime(2026, 6, 22, 8, 30, tzinfo=UTC)

    assert UTCDateTime().process_bind_param(value, None) == datetime(2026, 6, 22, 8, 30)


def test_utc_datetime_attaches_utc_to_naive_database_values() -> None:
    stored_value = datetime(2026, 6, 22, 8, 30)

    assert UTCDateTime().process_result_value(stored_value, None) == datetime(
        2026,
        6,
        22,
        8,
        30,
        tzinfo=UTC,
    )
