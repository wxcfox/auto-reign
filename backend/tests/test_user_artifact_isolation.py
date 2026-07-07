import inspect

from app.db.session import session_scope
from app.repositories.artifact_repository import ArtifactRepository
from app.services.workspace_service import WorkspaceService


def _register(client, username: str) -> str:
    return client.post(
        "/api/auth/register",
        json={"username": username, "password": "correct horse battery staple"},
    ).json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _sse_result(body: str) -> dict[str, object]:
    import json

    for frame in body.strip().split("\n\n"):
        lines = frame.splitlines()
        if "event: result" not in lines:
            continue
        data = "\n".join(
            line.removeprefix("data:").strip()
            for line in lines
            if line.startswith("data:")
        )
        return json.loads(data)
    raise AssertionError("SSE response did not include a result event.")


def test_users_can_create_same_learning_artifact_path_without_collision(
    client,
    monkeypatch,
) -> None:
    class NoopIndexService:
        def rebuild_index(self, *args, **kwargs) -> str:
            return "noop"

    monkeypatch.setattr("app.api.workspace.IndexService", NoopIndexService)
    alice = _register(client, "alice")
    bob = _register(client, "bob")

    first = client.post(
        "/api/workspace/learning-notes/stream",
        json={"text": "今天学习了 MySQL 覆盖索引。", "language": "zh-CN"},
        headers=_auth(alice),
    )
    second = client.post(
        "/api/workspace/learning-notes/stream",
        json={"text": "今天学习了 MySQL 覆盖索引。", "language": "zh-CN"},
        headers=_auth(bob),
    )

    assert first.status_code == 200
    assert second.status_code == 200
    first_artifact = _sse_result(first.text)["artifact"]
    second_artifact = _sse_result(second.text)["artifact"]

    assert first_artifact["relative_path"] == second_artifact["relative_path"]
    assert first_artifact["id"] != second_artifact["id"]

    repository = ArtifactRepository()
    with session_scope(client.app.state.session_factory) as session:
        alice_artifact = repository.get(
            session,
            user_id=1,
            artifact_id=str(first_artifact["id"]),
        )
        bob_artifact = repository.get(
            session,
            user_id=2,
            artifact_id=str(second_artifact["id"]),
        )
        assert alice_artifact is not None
        assert bob_artifact is not None
        assert alice_artifact.user_id == 1
        assert bob_artifact.user_id == 2
        assert alice_artifact.relative_path == bob_artifact.relative_path
        assert repository.get(
            session,
            user_id=1,
            artifact_id=str(second_artifact["id"]),
        ) is None
        assert repository.get(
            session,
            user_id=2,
            artifact_id=str(first_artifact["id"]),
        ) is None


def test_user_cannot_read_other_users_artifact(client, monkeypatch) -> None:
    class NoopIndexService:
        def rebuild_index(self, *args, **kwargs) -> str:
            return "noop"

    monkeypatch.setattr("app.api.workspace.IndexService", NoopIndexService)
    alice = _register(client, "alice")
    bob = _register(client, "bob")
    response = client.post(
        "/api/workspace/learning-notes/stream",
        json={"text": "今天学习了 Redis 缓存穿透。", "language": "zh-CN"},
        headers=_auth(alice),
    )
    artifact_id = _sse_result(response.text)["artifact"]["id"]

    forbidden = client.get(f"/api/workspace/artifacts/{artifact_id}", headers=_auth(bob))

    assert forbidden.status_code == 404


def test_rebuild_projection_requires_keyword_only_user_id() -> None:
    signature = inspect.signature(WorkspaceService.rebuild_projection)

    assert signature.parameters["user_id"].kind is inspect.Parameter.KEYWORD_ONLY


def test_artifact_repository_requires_keyword_only_user_boundaries() -> None:
    methods = [
        ArtifactRepository.get,
        ArtifactRepository.get_by_relative_path,
        ArtifactRepository.get_source_by_content_hash,
        ArtifactRepository.list,
    ]

    for method in methods:
        signature = inspect.signature(method)
        assert signature.parameters["user_id"].kind is inspect.Parameter.KEYWORD_ONLY
