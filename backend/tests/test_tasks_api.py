from __future__ import annotations

from collections.abc import Iterator

from fastapi import FastAPI, Header
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.api.dependencies import get_current_user
from app.api.tasks import router
from app.core.config import Settings
from app.db import models
from app.repositories.task_repository import TaskRepository
from app.schemas.agents import AgentConfig
from app.schemas.modeling import ModelRef
from app.services.task_service import TaskService, TaskServiceError


def _settings(*, openai_api_key: str | None = "test-key") -> Settings:
    return Settings(
        openai_api_key=openai_api_key,
        openai_chat_models="gpt-5",
        default_chat_provider="openai",
        deepseek_api_key=None,
        qwen_api_key=None,
    )


@pytest.fixture
def api(tmp_path) -> Iterator[tuple[TestClient, sessionmaker[Session]]]:
    engine = create_engine(f"sqlite:///{tmp_path / 'tasks-api.db'}")
    models.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        session.add_all(
            [
                models.User(
                    id=user_id,
                    username=f"user-{user_id}",
                    password_hash="unused",
                    display_name=f"User {user_id}",
                    role="user",
                    is_active=True,
                    token_version=1,
                    settings_json={},
                )
                for user_id in (1, 2)
            ]
        )
    app = FastAPI()
    app.include_router(router)
    app.state.session_factory = factory
    app.state.settings = _settings()
    app.state.task_service = TaskService()

    def current_user(x_test_user: int = Header(default=1)) -> models.User:
        with factory() as session:
            user = session.get(models.User, x_test_user)
            assert user is not None
            session.expunge(user)
            return user

    app.dependency_overrides[get_current_user] = current_user
    try:
        with TestClient(app) as client:
            yield client, factory
    finally:
        engine.dispose()


def _seed_task(factory: sessionmaker[Session]) -> tuple[int, int]:
    with factory.begin() as session:
        agent = models.Resource(
            id="agent-1",
            user_id=1,
            resource_type="agent",
            name="Helpful",
            config_json=AgentConfig(system_prompt="Help the user.").model_dump(mode="json"),
        )
        task = models.Task(
            user_id=1,
            agent_id=agent.id,
            name="First task",
            status="COMPLETED",
            model_override_json={"provider": "openai", "model": "gpt-5"},
        )
        inactive = models.Task(user_id=1, name="hidden", status="COMPLETED", is_active=False)
        other = models.Task(user_id=2, name="other", status="COMPLETED")
        session.add_all([agent, task, inactive, other])
        session.flush()
        user = models.Subtask(
            user_id=1,
            task_id=task.id,
            role="USER",
            message_id=1,
            prompt="latest prompt",
            status="COMPLETED",
            progress=100,
        )
        assistant = models.Subtask(
            user_id=1,
            task_id=task.id,
            role="ASSISTANT",
            message_id=2,
            parent_id=1,
            status="COMPLETED",
            progress=100,
            result={
                "messages_chain": [
                    {
                        "role": "assistant",
                        "content": "first",
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "function": {
                                    "name": "lookup",
                                    "arguments": "{}",
                                },
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "content": "safe tool",
                        "tool_call_id": "call-1",
                        "name": "lookup",
                    },
                    {"role": "assistant", "content": "final"},
                ]
            },
        )
        session.add_all([user, assistant])
        session.flush()
        failed = models.Subtask(
            user_id=1,
            task_id=task.id,
            role="ASSISTANT",
            message_id=3,
            parent_id=2,
            status="FAILED",
            progress=100,
            result={
                "value": "partial",
                "messages_chain": [
                    {
                        "role": "tool",
                        "content": "SECRET_CHAIN",
                        "nested": {"secret": "SECRET_NESTED_CHAIN"},
                    }
                ],
                "blocks": [{"content": "SECRET_BLOCK"}],
                "sources": [{"url": "SECRET_SOURCE"}],
                "runtime": {"metadata": "SECRET_METADATA"},
            },
        )
        pending = models.Subtask(
            user_id=1,
            task_id=task.id,
            role="ASSISTANT",
            message_id=4,
            parent_id=3,
            status="PENDING",
            progress=0,
            result={"messages_chain": [{"role": "assistant", "content": "SECRET_PENDING"}]},
        )
        running = models.Subtask(
            user_id=1,
            task_id=task.id,
            role="ASSISTANT",
            message_id=5,
            parent_id=4,
            status="RUNNING",
            progress=50,
            result={"messages_chain": [{"role": "assistant", "content": "SECRET_RUNNING"}]},
        )
        cancelled = models.Subtask(
            user_id=1,
            task_id=task.id,
            role="ASSISTANT",
            message_id=6,
            parent_id=5,
            status="CANCELLED",
            progress=100,
            result={
                "value": "cancelled partial",
                "messages_chain": [{"role": "tool", "content": "SECRET_CANCELLED"}],
                "blocks": [{"content": "SECRET_CANCELLED_BLOCK"}],
            },
        )
        context = models.SubtaskContext(
            user_id=1,
            subtask_id=assistant.id,
            context_type="attachment",
            name="brief.txt",
            status="ready",
            binary_data=b"hidden",
            extracted_text="hidden",
            image_base64="hidden",
            text_length=6,
            mime_type="text/plain",
            file_size=6,
            type_data={"kind": "note"},
        )
        session.add_all([failed, pending, running, cancelled, context])
        session.flush()
        return task.id, failed.message_id


def test_list_and_detail_project_task_history_safely(
    api: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, factory = api
    task_id, _ = _seed_task(factory)
    with factory.begin() as session:
        private_agent = models.Resource(
            id="private-agent",
            user_id=2,
            resource_type="agent",
            name="Other user's agent",
            config_json={},
        )
        unavailable = models.Task(
            user_id=1,
            agent_id=private_agent.id,
            name="Unavailable agent task",
            status="COMPLETED",
        )
        session.add_all([private_agent, unavailable])
        session.flush()

    listed = client.get("/api/tasks")
    assert listed.status_code == 200
    item = next(item for item in listed.json()["tasks"] if item["id"] == task_id)
    assert item["href"] == f"/chat?task={task_id}"
    assert item["last_message"] == "latest prompt"
    assert item["agent"] == {"id": "agent-1", "name": "Helpful", "is_available": True}
    assert item["model_override"] == {"provider": "openai", "model": "gpt-5"}
    unavailable_item = next(item for item in listed.json()["tasks"] if item["id"] == unavailable.id)
    assert unavailable_item["agent"] == {
        "id": "private-agent",
        "name": "Unavailable agent",
        "is_available": False,
    }

    detail = client.get(f"/api/tasks/{task_id}")
    assert detail.status_code == 200
    subtasks = detail.json()["subtasks"]
    assert [item["message_id"] for item in subtasks] == [1, 2, 3, 4, 5, 6]
    assert [item["role"] for item in subtasks[1]["result"]["messages_chain"]] == [
        "assistant",
        "tool",
        "assistant",
    ]
    failed_result = subtasks[2]["result"]
    assert failed_result == {
        "value": "partial",
        "messages_chain": [{"role": "assistant", "content": "partial"}],
    }
    assert all(
        secret not in str(detail.json())
        for secret in (
            "SECRET_CHAIN",
            "SECRET_NESTED_CHAIN",
            "SECRET_BLOCK",
            "SECRET_SOURCE",
            "SECRET_METADATA",
        )
    )
    assert subtasks[3]["result"] is None
    assert subtasks[4]["result"] is None
    assert subtasks[5]["result"] == {
        "value": "cancelled partial",
        "messages_chain": [{"role": "assistant", "content": "cancelled partial"}],
    }
    assert "SECRET_PENDING" not in str(detail.json())
    assert "SECRET_RUNNING" not in str(detail.json())
    assert "SECRET_CANCELLED" not in str(detail.json())
    assert {"binary_data", "image_base64", "extracted_text", "error_message"}.isdisjoint(
        subtasks[1]["contexts"][0]
    )


def test_incremental_rename_delete_and_model_guards(
    api: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, factory = api
    task_id, failed_message_id = _seed_task(factory)

    with factory() as session:
        incremental = TaskService().list_subtasks_after(
            session, user_id=1, task_id=task_id, after_message_id=1
        )
        assert [item.message_id for item in incremental] == [2, failed_message_id, 4, 5, 6]
        with pytest.raises(TaskServiceError, match="task_not_found"):
            TaskService().list_subtasks_after(
                session, user_id=2, task_id=task_id, after_message_id=None
            )

    renamed = client.patch(f"/api/tasks/{task_id}", json={"name": " Renamed "})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Renamed"
    valid_model = client.put(
        f"/api/tasks/{task_id}/model",
        json={"model_override": {"provider": "openai", "model": "gpt-5"}},
    )
    assert valid_model.status_code == 200
    changed = client.put(f"/api/tasks/{task_id}/model", json={"model_override": None})
    assert changed.status_code == 200
    assert changed.json()["model_override"] is None

    invalid_provider = client.put(
        f"/api/tasks/{task_id}/model",
        json={"model_override": {"provider": "other", "model": "gpt-5"}},
    )
    assert invalid_provider.status_code == 503
    assert invalid_provider.json()["detail"]["code"] == "model_unavailable"
    invalid_model = client.put(
        f"/api/tasks/{task_id}/model",
        json={"model_override": {"provider": "openai", "model": "unknown"}},
    )
    assert invalid_model.status_code == 503
    assert invalid_model.json()["detail"]["code"] == "model_unavailable"

    client.app.state.settings = _settings(openai_api_key=None)
    no_fallback = client.put(f"/api/tasks/{task_id}/model", json={"model_override": None})
    assert no_fallback.status_code == 503
    assert no_fallback.json()["detail"]["code"] == "model_unavailable"
    client.app.state.settings = _settings()

    with factory.begin() as session:
        task = session.get(models.Task, task_id)
        assert task is not None
        task.status = "RUNNING"
    running = client.put(
        f"/api/tasks/{task_id}/model",
        json={"model_override": {"provider": "openai", "model": "gpt-5"}},
    )
    assert running.status_code == 409
    assert running.json()["detail"]["code"] == "task_running"
    running_delete = client.delete(f"/api/tasks/{task_id}")
    assert running_delete.status_code == 409
    assert running_delete.json()["detail"]["code"] == "task_running"
    assert (
        client.patch(
            f"/api/tasks/{task_id}", json={"name": "x"}, headers={"X-Test-User": "2"}
        ).status_code
        == 404
    )

    with factory.begin() as session:
        task = session.get(models.Task, task_id)
        assert task is not None
        task.status = "COMPLETED"
    deleted = client.delete(f"/api/tasks/{task_id}")
    assert deleted.status_code == 204
    assert client.get(f"/api/tasks/{task_id}").status_code == 404


class _TransitioningTaskRepository(TaskRepository):
    def set_model_override_if_terminal(
        self,
        session: Session,
        *,
        user_id: int,
        task_id: int,
        model_override: ModelRef | None,
    ) -> bool:
        task = session.get(models.Task, task_id)
        assert task is not None
        task.status = "RUNNING"
        session.flush()
        return super().set_model_override_if_terminal(
            session,
            user_id=user_id,
            task_id=task_id,
            model_override=model_override,
        )


def test_model_update_rechecks_terminal_status_in_conditional_update(
    api: tuple[TestClient, sessionmaker[Session]],
) -> None:
    _client, factory = api
    task_id, _ = _seed_task(factory)
    with factory.begin() as session:
        service = TaskService(
            repository=_TransitioningTaskRepository(),
        )
        with pytest.raises(TaskServiceError, match="task_running"):
            service.set_model_override(
                session,
                user_id=1,
                task_id=task_id,
                model_override=ModelRef(provider="openai", model="gpt-5"),
            )
        task = session.get(models.Task, task_id)
        assert task is not None
        assert task.model_override_json == {"provider": "openai", "model": "gpt-5"}


def test_rename_uses_latest_prompt_query_not_full_subtask_projection(
    api: tuple[TestClient, sessionmaker[Session]], monkeypatch: pytest.MonkeyPatch
) -> None:
    _client, factory = api
    task_id, _ = _seed_task(factory)
    repository = TaskRepository()

    def fail_if_history_is_loaded(*_args: object, **_kwargs: object) -> list[models.Subtask]:
        raise AssertionError("rename must not load all subtasks")

    monkeypatch.setattr(repository, "list_subtasks", fail_if_history_is_loaded)
    with factory.begin() as session:
        response = TaskService(
            repository=repository,
        ).rename_task(session, user_id=1, task_id=task_id, name=" Renamed ")

    assert response.name == "Renamed"
    assert response.last_message == "latest prompt"


def test_task_brief_and_owner_check_never_load_subtask_history(
    api: tuple[TestClient, sessionmaker[Session]], monkeypatch: pytest.MonkeyPatch
) -> None:
    _client, factory = api
    task_id, _ = _seed_task(factory)
    repository = TaskRepository()

    def fail_if_history_is_loaded(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("lightweight Task reads must not load Subtasks")

    monkeypatch.setattr(repository, "list_subtasks", fail_if_history_is_loaded)
    service = TaskService(repository=repository)
    with factory.begin() as session:
        service.require_task_owner(session, user_id=1, task_id=task_id)
        brief = service.get_task_brief(session, user_id=1, task_id=task_id)
        with pytest.raises(TaskServiceError, match="task_not_found"):
            service.require_task_owner(session, user_id=2, task_id=task_id)

    assert brief.id == task_id
    assert brief.last_message == brief.name


def test_task_routes_have_no_stream_or_send_endpoints(
    api: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = api
    for path in (
        "/api/tasks",
        "/api/tasks/1/stream",
        "/api/tasks/1/retry",
        "/api/tasks/1/retry/stream",
        "/api/tasks/stream",
    ):
        assert client.post(path).status_code in {404, 405}

    assert not any("stream" in route.path or "retry" in route.path for route in router.routes)
    assert not any(
        route.path == "/api/tasks" and "POST" in route.methods for route in router.routes
    )
