import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Event
from time import monotonic
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import event, select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError

from app.core.config import Settings
from app.db import models
from app.db.session import create_engine_for_settings, make_session_factory
from app.repositories.task_repository import TaskRepository
from app.schemas.agents import AgentConfig, AgentPutRequest, KnowledgeScope
from app.schemas.chat import ChatSendRequest
from app.services.agent_service import AgentService
from app.services.knowledge_collection_service import KnowledgeCollectionService
from app.services.task_execution_service import TaskExecutionService
from app.services.workspace_resource_service import WorkspaceResourceService


_LOCK_WAIT_QUERY = text(
    """
    SELECT 1
    FROM performance_schema.data_lock_waits AS lock_wait
    JOIN performance_schema.threads AS requesting_thread
      ON requesting_thread.THREAD_ID = lock_wait.REQUESTING_THREAD_ID
    JOIN performance_schema.threads AS blocking_thread
      ON blocking_thread.THREAD_ID = lock_wait.BLOCKING_THREAD_ID
    WHERE requesting_thread.PROCESSLIST_ID = :requesting_connection_id
      AND blocking_thread.PROCESSLIST_ID = :blocking_connection_id
    LIMIT 1
    """
)

_LOCK_WAIT_PREFLIGHT_QUERY = text(
    """
    SELECT lock_wait.REQUESTING_THREAD_ID, lock_wait.BLOCKING_THREAD_ID
    FROM performance_schema.data_lock_waits AS lock_wait
    JOIN performance_schema.threads AS requesting_thread
      ON requesting_thread.THREAD_ID = lock_wait.REQUESTING_THREAD_ID
    JOIN performance_schema.threads AS blocking_thread
      ON blocking_thread.THREAD_ID = lock_wait.BLOCKING_THREAD_ID
    LIMIT 0
    """
)


@dataclass(frozen=True)
class _RaceState:
    user_id: int
    agent_id: str
    agent_name: str
    resource_id: str


class _UnsafeDisposableDatabaseError(ValueError):
    pass


def _normalized_host(host: str | None) -> str:
    normalized = (host or "").casefold().rstrip(".")
    if normalized in {"localhost", "127.0.0.1", "::1"}:
        return "loopback"
    return normalized


def _database_identity(url: URL) -> tuple[str, int, str | None]:
    return (
        _normalized_host(url.host),
        url.port or 3306,
        url.database.casefold() if url.database is not None else None,
    )


def _validate_disposable_mysql_url(
    explicit_url: str,
    *,
    default_database_url: str,
) -> URL:
    try:
        parsed_url = make_url(explicit_url)
    except ArgumentError as error:
        raise ValueError(
            "MYSQL_RESOURCE_RACE_DATABASE_URL is not a valid database URL"
        ) from error
    if not parsed_url.drivername.startswith("mysql"):
        raise ValueError("MYSQL_RESOURCE_RACE_DATABASE_URL must use a MySQL driver")
    if not parsed_url.database:
        raise ValueError(
            "MYSQL_RESOURCE_RACE_DATABASE_URL must name a disposable database"
        )
    if parsed_url.database.casefold() in {
        "information_schema",
        "mysql",
        "performance_schema",
        "sys",
    }:
        raise ValueError(
            "MYSQL_RESOURCE_RACE_DATABASE_URL must not name a system database"
        )
    if not parsed_url.database.casefold().endswith("_test"):
        raise _UnsafeDisposableDatabaseError(
            "MYSQL_RESOURCE_RACE_DATABASE_URL database name must end with _test"
        )

    try:
        default_url = make_url(default_database_url)
    except ArgumentError as error:
        raise _UnsafeDisposableDatabaseError(
            "cannot prove the disposable schema differs because DATABASE_URL is invalid"
        ) from error
    if not default_url.database:
        raise _UnsafeDisposableDatabaseError(
            "cannot prove the disposable schema differs because DATABASE_URL has no "
            "database name"
        )
    same_endpoint = _database_identity(parsed_url) == _database_identity(default_url)
    same_schema_name = (
        parsed_url.database.casefold() == default_url.database.casefold()
    )
    if same_endpoint or same_schema_name:
        raise _UnsafeDisposableDatabaseError(
            "the disposable race database must use a different schema name "
            "from DATABASE_URL"
        )
    return parsed_url


def _disposable_mysql_url() -> URL:
    if os.environ.get("RUN_MYSQL_INTEGRATION") != "1":
        pytest.skip("requires RUN_MYSQL_INTEGRATION=1")
    explicit_url = os.environ.get("MYSQL_RESOURCE_RACE_DATABASE_URL")
    if not explicit_url:
        pytest.fail(
            "RUN_MYSQL_INTEGRATION=1 requires an explicit disposable "
            "MYSQL_RESOURCE_RACE_DATABASE_URL"
        )
    try:
        return _validate_disposable_mysql_url(
            explicit_url,
            default_database_url=Settings(_env_file=None).database_url,
        )
    except _UnsafeDisposableDatabaseError as error:
        pytest.fail(str(error))
    except ValueError as error:
        pytest.fail(str(error))


def test_integration_flag_requires_explicit_resource_race_url(monkeypatch) -> None:
    monkeypatch.setenv("RUN_MYSQL_INTEGRATION", "1")
    monkeypatch.delenv("MYSQL_RESOURCE_RACE_DATABASE_URL", raising=False)

    with pytest.raises(pytest.fail.Exception, match="explicit disposable"):
        _disposable_mysql_url()


def _setup_state(session_factory, *, resource_type: str) -> _RaceState:
    suffix = uuid4().hex[:8]
    with session_factory() as session:
        user = models.User(
            username=f"race-{suffix}",
            password_hash="not-used",
            display_name="Race User",
            role="user",
            is_active=True,
            token_version=1,
            settings_json={},
        )
        session.add(user)
        session.flush()
        resource = models.Resource(
            user_id=user.id,
            resource_type=resource_type,
            name=f"race-{resource_type}-{suffix}",
            config_json=(
                {
                    "workspace_type": "agent_home",
                    "initial_agents_md": "# Race",
                }
                if resource_type == "workspace"
                else {}
            ),
        )
        agent = models.Resource(
            user_id=user.id,
            resource_type="agent",
            name=f"race-agent-{resource_type}-{suffix}",
            config_json=AgentConfig(system_prompt="Race.").model_dump(mode="json"),
        )
        session.add_all([resource, agent])
        session.commit()
        return _RaceState(
            user_id=user.id,
            agent_id=agent.id,
            agent_name=agent.name,
            resource_id=resource.id,
        )


def _agent_put(state: _RaceState, *, resource_type: str) -> AgentPutRequest:
    return AgentPutRequest(
        name=f"race-agent-{resource_type}-{state.agent_id[:8]}",
        config=AgentConfig(
            system_prompt="Race.",
            home_workspace_id=(
                state.resource_id if resource_type == "workspace" else None
            ),
            knowledge_scopes=(
                [KnowledgeScope(collection_id=state.resource_id)]
                if resource_type == "knowledge_collection"
                else []
            ),
        ),
        is_active=True,
    )


def _delete_resource(session, *, actor, state: _RaceState, resource_type: str):
    if resource_type == "workspace":
        return WorkspaceResourceService().delete_resource(
            session,
            actor=actor,
            resource_id=state.resource_id,
        )
    return KnowledgeCollectionService().delete_resource(
        session,
        actor=actor,
        resource_id=state.resource_id,
    )


def _wait_for_mysql_lock_evidence(
    session,
    *,
    requesting_connection_id: int,
    blocking_connection_id: int,
    binding_finished: Event,
) -> None:
    deadline = monotonic() + 15
    while True:
        try:
            evidence = session.scalar(
                _LOCK_WAIT_QUERY,
                {
                    "requesting_connection_id": requesting_connection_id,
                    "blocking_connection_id": blocking_connection_id,
                },
            )
        except SQLAlchemyError as error:
            raise AssertionError(
                "MySQL integration prerequisite failed: the test user must be able "
                "to inspect performance_schema.data_lock_waits and threads."
            ) from error
        if evidence == 1:
            return
        if binding_finished.is_set():
            raise AssertionError(
                "Binder finished before MySQL reported it waiting on the deleter lock."
            )
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise AssertionError(
                "MySQL integration prerequisite failed: performance_schema did not "
                "expose the binder-to-deleter row-lock wait within 15 seconds; ensure "
                "Performance Schema lock instrumentation and cross-session visibility "
                "are enabled."
            )
        binding_finished.wait(timeout=min(0.05, remaining))


def _race_bind_commit_then_delete_scan(
    session_factory,
    settings: Settings,
    *,
    state: _RaceState,
    resource_type: str,
) -> tuple[str, str]:
    delete_auth_read = Event()
    bind_committed = Event()

    def delete_worker() -> str:
        try:
            with session_factory() as session:
                actor = session.scalar(
                    select(models.User).where(models.User.id == state.user_id)
                )
                assert actor is not None
                delete_auth_read.set()
                assert bind_committed.wait(timeout=30)
                try:
                    _delete_resource(
                        session,
                        actor=actor,
                        state=state,
                        resource_type=resource_type,
                    )
                    session.commit()
                except HTTPException as error:
                    session.rollback()
                    return error.detail["code"]
                return "deleted"
        finally:
            delete_auth_read.set()

    def bind_worker() -> str:
        try:
            assert delete_auth_read.wait(timeout=30)
            with session_factory() as session:
                actor = session.get(models.User, state.user_id)
                assert actor is not None
                AgentService(settings=settings).put_agent(
                    session,
                    actor=actor,
                    agent_id=state.agent_id,
                    payload=_agent_put(state, resource_type=resource_type),
                )
                session.commit()
            return "bound"
        finally:
            bind_committed.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        delete_future = pool.submit(delete_worker)
        bind_future = pool.submit(bind_worker)
        return bind_future.result(timeout=60), delete_future.result(timeout=60)


def _race_delete_lock_then_bind_wait(
    session_factory,
    settings: Settings,
    *,
    state: _RaceState,
    resource_type: str,
) -> tuple[str, str]:
    delete_locked = Event()
    target_for_update_reached = Event()
    binding_finished = Event()
    connection_ids: dict[str, int] = {}

    def delete_worker() -> str:
        try:
            with session_factory() as session:
                actor = session.scalar(
                    select(models.User).where(models.User.id == state.user_id)
                )
                assert actor is not None
                deleter_connection_id = session.scalar(
                    text("SELECT CONNECTION_ID()")
                )
                assert deleter_connection_id is not None
                _delete_resource(
                    session,
                    actor=actor,
                    state=state,
                    resource_type=resource_type,
                )
                delete_locked.set()
                assert target_for_update_reached.wait(timeout=30)
                assert not binding_finished.is_set()
                binder_connection_id = connection_ids.get("binder")
                if binder_connection_id is None:
                    raise AssertionError(
                        "Binder failed before exposing its MySQL connection id."
                    )
                _wait_for_mysql_lock_evidence(
                    session,
                    requesting_connection_id=binder_connection_id,
                    blocking_connection_id=deleter_connection_id,
                    binding_finished=binding_finished,
                )
                session.commit()
            return "deleted"
        finally:
            delete_locked.set()

    def bind_worker() -> str:
        try:
            assert delete_locked.wait(timeout=30)
            with session_factory() as session:

                @event.listens_for(session, "do_orm_execute")
                def observe_target_lock(orm_execute_state) -> None:
                    statement = orm_execute_state.statement
                    if (
                        orm_execute_state.is_select
                        and statement._for_update_arg is not None
                        and "resources.id IN" in str(statement)
                    ):
                        target_for_update_reached.set()

                actor = session.get(models.User, state.user_id)
                assert actor is not None
                binder_connection_id = session.scalar(
                    text("SELECT CONNECTION_ID()")
                )
                assert binder_connection_id is not None
                connection_ids["binder"] = binder_connection_id
                try:
                    AgentService(settings=settings).put_agent(
                        session,
                        actor=actor,
                        agent_id=state.agent_id,
                        payload=_agent_put(state, resource_type=resource_type),
                    )
                    session.commit()
                except HTTPException as error:
                    session.rollback()
                    return error.detail["code"]
                return "bound"
        finally:
            target_for_update_reached.set()
            binding_finished.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        delete_future = pool.submit(delete_worker)
        bind_future = pool.submit(bind_worker)
        return delete_future.result(timeout=60), bind_future.result(timeout=60)


def _agent_references_resource(
    config: AgentConfig,
    *,
    resource_id: str,
) -> bool:
    return config.home_workspace_id == resource_id or any(
        scope.collection_id == resource_id for scope in config.knowledge_scopes
    )


def _run_resource_race(resource_type: str, ordering: str) -> None:
    url = _disposable_mysql_url()
    settings = Settings(_env_file=None, database_url=url.render_as_string(False))
    engine = create_engine_for_settings(settings)
    schema_managed = False
    try:
        with engine.connect() as connection:
            actual_database = connection.scalar(text("SELECT DATABASE()"))
            if actual_database != url.database:
                pytest.fail(
                    "MYSQL_RESOURCE_RACE_DATABASE_URL resolved to an unexpected database"
                )
            if ordering == "delete_first":
                performance_schema_enabled = connection.scalar(
                    text("SELECT @@performance_schema")
                )
                if performance_schema_enabled != 1:
                    pytest.fail(
                        "MySQL integration prerequisite failed: Performance Schema "
                        "must be enabled for the delete-first lock proof."
                    )
                try:
                    connection.execute(_LOCK_WAIT_PREFLIGHT_QUERY)
                except SQLAlchemyError as error:
                    pytest.fail(
                        "MySQL integration prerequisite failed: the test user must "
                        "be able to inspect performance_schema.data_lock_waits and "
                        f"threads ({type(error).__name__})."
                    )
        schema_managed = True
        models.Base.metadata.drop_all(engine)
        models.Base.metadata.create_all(engine)
        session_factory = make_session_factory(engine)
        state = _setup_state(
            session_factory,
            resource_type=resource_type,
        )
        if ordering == "bind_first":
            assert _race_bind_commit_then_delete_scan(
                session_factory,
                settings,
                state=state,
                resource_type=resource_type,
            ) == ("bound", "resource_in_use")
        else:
            assert _race_delete_lock_then_bind_wait(
                session_factory,
                settings,
                state=state,
                resource_type=resource_type,
            ) == ("deleted", "resource_reference_invalid")

        with session_factory() as session:
            resource = session.get(models.Resource, state.resource_id)
            agent = session.get(models.Resource, state.agent_id)
            assert resource is not None
            assert agent is not None
            config = AgentConfig.model_validate(agent.config_json)
            if ordering == "bind_first":
                assert resource.is_active is True
                assert resource.deleted_at is None
                assert _agent_references_resource(
                    config,
                    resource_id=resource.id,
                )
            else:
                assert resource.is_active is False
                assert resource.deleted_at is not None
                assert agent.name == state.agent_name
                assert not _agent_references_resource(
                    config,
                    resource_id=resource.id,
                )
                active_agents = session.scalars(
                    select(models.Resource).where(
                        models.Resource.resource_type == "agent",
                        models.Resource.is_active.is_(True),
                        models.Resource.deleted_at.is_(None),
                    )
                )
                assert all(
                    not _agent_references_resource(
                        AgentConfig.model_validate(active_agent.config_json),
                        resource_id=resource.id,
                    )
                    for active_agent in active_agents
                )
    finally:
        try:
            if schema_managed:
                models.Base.metadata.drop_all(engine)
        finally:
            engine.dispose()


class _NoopRuntime:
    def prepare_turn(self, turn):
        return turn

    def stream_turn(self, _turn, *, observer):
        del observer
        raise AssertionError("prepare/delete race must not invoke the runtime")


def _task_execution_service(
    session_factory,
    settings: Settings,
) -> TaskExecutionService:
    return TaskExecutionService(
        session_factory=session_factory,
        runtime=_NoopRuntime(),
        agent_service=AgentService(settings=settings),
        settings=settings,
    )


def _race_prepare_lock_then_delete_wait(
    session_factory,
    settings: Settings,
    *,
    state: _RaceState,
) -> tuple[tuple[str, str], set[int]]:
    prepare_locked = Event()
    delete_target_reached = Event()
    release_prepare = Event()
    delete_finished = Event()
    connection_ids: dict[str, int] = {}

    def prepare_worker() -> str:
        prepare_session = session_factory()
        try:
            connection_id = prepare_session.scalar(text("SELECT CONNECTION_ID()"))
            assert connection_id is not None
            connection_ids["prepare"] = connection_id

            @event.listens_for(prepare_session, "after_flush")
            def pause_with_agent_lock(_session, _flush_context) -> None:
                if prepare_locked.is_set():
                    return
                prepare_locked.set()
                assert release_prepare.wait(timeout=30)

            _task_execution_service(
                lambda: prepare_session,
                settings,
            ).prepare_send(
                user_id=state.user_id,
                request=ChatSendRequest(
                    message="prepare wins",
                    agent_id=state.agent_id,
                ),
            )
            return "prepared"
        finally:
            try:
                prepare_session.close()
            finally:
                prepare_locked.set()

    def delete_worker() -> str:
        try:
            with session_factory() as session:
                actor = session.scalar(
                    select(models.User).where(models.User.id == state.user_id)
                )
                assert actor is not None
                connection_id = session.scalar(text("SELECT CONNECTION_ID()"))
                assert connection_id is not None
                connection_ids["delete"] = connection_id
                assert prepare_locked.wait(timeout=30)

                @event.listens_for(session, "do_orm_execute")
                def observe_agent_lock(orm_execute_state) -> None:
                    statement = orm_execute_state.statement
                    if (
                        orm_execute_state.is_select
                        and statement._for_update_arg is not None
                        and "resources" in str(statement)
                    ):
                        delete_target_reached.set()

                AgentService(settings=settings).delete_agent(
                    session,
                    actor=actor,
                    agent_id=state.agent_id,
                )
                session.commit()
            return "deleted"
        finally:
            delete_target_reached.set()
            delete_finished.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        prepare_future = pool.submit(prepare_worker)
        delete_future = pool.submit(delete_worker)
        try:
            assert prepare_locked.wait(timeout=30)
            assert delete_target_reached.wait(timeout=30)
            with session_factory() as observer:
                observer_id = observer.scalar(text("SELECT CONNECTION_ID()"))
                assert observer_id is not None
                connection_ids["observer"] = observer_id
                prepare_id = connection_ids.get("prepare")
                delete_id = connection_ids.get("delete")
                assert prepare_id is not None and delete_id is not None
                assert len({prepare_id, delete_id, observer_id}) == 3
                _wait_for_mysql_lock_evidence(
                    observer,
                    requesting_connection_id=delete_id,
                    blocking_connection_id=prepare_id,
                    binding_finished=delete_finished,
                )
        finally:
            release_prepare.set()
        result = (
            prepare_future.result(timeout=60),
            delete_future.result(timeout=60),
        )
    return result, set(connection_ids.values())


def _race_delete_lock_then_prepare_wait(
    session_factory,
    settings: Settings,
    *,
    state: _RaceState,
) -> tuple[tuple[str, str], set[int]]:
    delete_locked = Event()
    prepare_target_reached = Event()
    release_delete = Event()
    prepare_finished = Event()
    connection_ids: dict[str, int] = {}

    def delete_worker() -> str:
        try:
            with session_factory() as session:
                actor = session.scalar(
                    select(models.User).where(models.User.id == state.user_id)
                )
                assert actor is not None
                connection_id = session.scalar(text("SELECT CONNECTION_ID()"))
                assert connection_id is not None
                connection_ids["delete"] = connection_id
                AgentService(settings=settings).delete_agent(
                    session,
                    actor=actor,
                    agent_id=state.agent_id,
                )
                delete_locked.set()
                assert release_delete.wait(timeout=30)
                session.commit()
            return "deleted"
        finally:
            delete_locked.set()

    def prepare_worker() -> str:
        prepare_session = session_factory()
        try:
            assert delete_locked.wait(timeout=30)
            connection_id = prepare_session.scalar(text("SELECT CONNECTION_ID()"))
            assert connection_id is not None
            connection_ids["prepare"] = connection_id

            @event.listens_for(prepare_session, "do_orm_execute")
            def observe_agent_lock(orm_execute_state) -> None:
                statement = orm_execute_state.statement
                if (
                    orm_execute_state.is_select
                    and statement._for_update_arg is not None
                    and "resources" in str(statement)
                ):
                    prepare_target_reached.set()

            try:
                _task_execution_service(
                    lambda: prepare_session,
                    settings,
                ).prepare_send(
                    user_id=state.user_id,
                    request=ChatSendRequest(
                        message="delete wins",
                        agent_id=state.agent_id,
                    ),
                )
            except HTTPException as error:
                return error.detail["code"]
            return "prepared"
        finally:
            try:
                prepare_session.close()
            finally:
                prepare_target_reached.set()
                prepare_finished.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        delete_future = pool.submit(delete_worker)
        prepare_future = pool.submit(prepare_worker)
        try:
            assert delete_locked.wait(timeout=30)
            assert prepare_target_reached.wait(timeout=30)
            with session_factory() as observer:
                observer_id = observer.scalar(text("SELECT CONNECTION_ID()"))
                assert observer_id is not None
                connection_ids["observer"] = observer_id
                prepare_id = connection_ids.get("prepare")
                delete_id = connection_ids.get("delete")
                assert prepare_id is not None and delete_id is not None
                assert len({prepare_id, delete_id, observer_id}) == 3
                _wait_for_mysql_lock_evidence(
                    observer,
                    requesting_connection_id=prepare_id,
                    blocking_connection_id=delete_id,
                    binding_finished=prepare_finished,
                )
        finally:
            release_delete.set()
        result = (
            delete_future.result(timeout=60),
            prepare_future.result(timeout=60),
        )
    return result, set(connection_ids.values())


def _run_agent_delete_and_prepare_race(ordering: str) -> None:
    url = _disposable_mysql_url()
    settings = Settings(
        _env_file=None,
        database_url=url.render_as_string(False),
        qwen_api_key="race-qwen-key",
        qwen_chat_models="qwen3.7-plus",
        default_chat_provider="qwen",
    )
    engine = create_engine_for_settings(settings)
    schema_managed = False
    try:
        with engine.connect() as connection:
            actual_database = connection.scalar(text("SELECT DATABASE()"))
            if actual_database != url.database:
                pytest.fail(
                    "MYSQL_RESOURCE_RACE_DATABASE_URL resolved to an unexpected database"
                )
            performance_schema_enabled = connection.scalar(
                text("SELECT @@performance_schema")
            )
            if performance_schema_enabled != 1:
                pytest.fail(
                    "MySQL integration prerequisite failed: Performance Schema "
                    "must be enabled for the task preparation/delete lock proof."
                )
            try:
                connection.execute(_LOCK_WAIT_PREFLIGHT_QUERY)
            except SQLAlchemyError as error:
                pytest.fail(
                    "MySQL integration prerequisite failed: the test user must "
                    "be able to inspect performance_schema.data_lock_waits and "
                    f"threads ({type(error).__name__})."
                )
        schema_managed = True
        models.Base.metadata.drop_all(engine)
        models.Base.metadata.create_all(engine)
        session_factory = make_session_factory(engine)
        state = _setup_state(session_factory, resource_type="workspace")

        if ordering == "prepare_first":
            result, connection_ids = _race_prepare_lock_then_delete_wait(
                session_factory,
                settings,
                state=state,
            )
            assert result == ("prepared", "deleted")
        else:
            result, connection_ids = _race_delete_lock_then_prepare_wait(
                session_factory,
                settings,
                state=state,
            )
            assert result == ("deleted", "agent_unavailable")
        assert len(connection_ids) == 3

        with session_factory() as session:
            agent = session.get(models.Resource, state.agent_id)
            tasks = list(session.scalars(select(models.Task)))
            subtasks = list(
                session.scalars(
                    select(models.Subtask).order_by(models.Subtask.message_id)
                )
            )
            assert agent is not None
            assert agent.is_active is False
            assert agent.deleted_at is not None
            if ordering == "prepare_first":
                assert len(tasks) == 1
                assert [(item.role, item.status) for item in subtasks] == [
                    ("USER", "COMPLETED"),
                    ("ASSISTANT", "PENDING"),
                ]
                TaskRepository().recover_interrupted(session)
                task_id = tasks[0].id
                session.commit()
            else:
                assert tasks == []
                assert subtasks == []
                task_id = None

        if task_id is not None:
            with session_factory() as recovered_session:
                recovered_task = recovered_session.get(
                    models.Task,
                    task_id,
                )
                recovered_assistant = recovered_session.scalar(
                    select(models.Subtask).where(
                        models.Subtask.user_id == state.user_id,
                        models.Subtask.task_id == task_id,
                        models.Subtask.role == "ASSISTANT",
                    )
                )
                assert recovered_task is not None
                assert recovered_assistant is not None
                assert recovered_task.status == "FAILED"
                assert recovered_assistant.status == "FAILED"
                assert recovered_assistant.error_message == "generation_interrupted"
            with pytest.raises(HTTPException) as unavailable:
                _task_execution_service(session_factory, settings).prepare_send(
                    user_id=state.user_id,
                    request=ChatSendRequest(
                        message="later turn",
                        task_id=task_id,
                    ),
                )
            assert unavailable.value.detail["code"] == "agent_unavailable"
    finally:
        try:
            if schema_managed:
                models.Base.metadata.drop_all(engine)
        finally:
            engine.dispose()


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("LOCALHOST.", "loopback"),
        ("LocalHost", "loopback"),
        ("DB.EXAMPLE.COM.", "db.example.com"),
    ],
)
def test_disposable_database_guard_normalizes_host_case_and_trailing_dot(
    host: str,
    expected: str,
) -> None:
    assert _normalized_host(host) == expected


@pytest.mark.parametrize(
    ("explicit_url", "default_url"),
    [
        (
            "mysql+pymysql://user:pass@LOCALHOST./AUTO_REIGN",
            "mysql+pymysql://user:pass@127.0.0.1:3306/auto_reign",
        ),
        (
            "mysql+pymysql://user:pass@/auto_reign"
            "?unix_socket=%2Ftmp%2Fmysql.sock",
            "mysql+pymysql://user:pass@db.example.com/auto_reign",
        ),
        (
            "mysql+pymysql://user:pass@race.example.com/Race_Schema",
            "mysql+pymysql://user:pass@production.example.com/race_schema",
        ),
    ],
)
def test_disposable_database_guard_rejects_default_schema_aliases(
    explicit_url: str,
    default_url: str,
) -> None:
    with pytest.raises(_UnsafeDisposableDatabaseError):
        _validate_disposable_mysql_url(
            explicit_url,
            default_database_url=default_url,
        )


def test_disposable_database_guard_accepts_a_distinct_schema_name() -> None:
    parsed = _validate_disposable_mysql_url(
        "mysql+pymysql://user:pass@LOCALHOST./auto_reign_resource_race_test",
        default_database_url=(
            "mysql+pymysql://user:pass@127.0.0.1:3306/auto_reign"
        ),
    )

    assert parsed.database == "auto_reign_resource_race_test"


@pytest.mark.parametrize(
    "default_url",
    [
        "not a database URL",
        "mysql+pymysql://user:pass@localhost",
    ],
)
def test_disposable_database_guard_fails_closed_for_unverifiable_default_url(
    default_url: str,
) -> None:
    with pytest.raises(_UnsafeDisposableDatabaseError):
        _validate_disposable_mysql_url(
            "mysql+pymysql://user:pass@localhost/auto_reign_resource_race_test",
            default_database_url=default_url,
        )


def test_disposable_database_guard_rejects_non_test_schema() -> None:
    with pytest.raises(_UnsafeDisposableDatabaseError, match="end with _test"):
        _validate_disposable_mysql_url(
            "mysql+pymysql://user:pass@localhost/production",
            default_database_url=(
                "mysql+pymysql://user:pass@localhost/auto_reign"
            ),
        )


@pytest.mark.parametrize("ordering", ["bind_first", "delete_first"])
@pytest.mark.parametrize("resource_type", ["workspace", "knowledge_collection"])
def test_agent_binding_and_resource_delete_are_linearized(
    resource_type: str,
    ordering: str,
) -> None:
    _run_resource_race(resource_type, ordering)


@pytest.mark.parametrize("ordering", ["prepare_first", "delete_first"])
def test_agent_delete_and_prepare_turn_are_linearized(ordering: str) -> None:
    _run_agent_delete_and_prepare_race(ordering)
