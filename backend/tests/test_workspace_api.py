import json
import re
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import select

from app.db import models
from app.db.session import session_scope
from app.repositories.vector_store import VectorStoreUnavailable
from app.services.artifact_service import ArtifactService
from app.services.index_service import IndexService
from app.services.workspace_service import WorkspaceService


def _register(client, username: str = "alice") -> str:
    response = client.post(
        "/api/auth/register",
        json={"username": username, "password": "correct horse battery staple"},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _workspace_artifacts(client, user_id: int = 1) -> ArtifactService:
    workspace = WorkspaceService(client.app.state.settings.data_dir / "users" / str(user_id) / "workspace")
    workspace.initialize()
    return ArtifactService(workspace)


def _workspace_service(client, user_id: int = 1) -> WorkspaceService:
    workspace = WorkspaceService(client.app.state.settings.data_dir / "users" / str(user_id) / "workspace")
    workspace.initialize()
    return workspace


def _disable_index_rebuild(monkeypatch) -> None:
    class NoopVectorStore:
        def delete_artifact_chunks(self, *args, **kwargs) -> None:
            return None

    class NoopIndexService:
        settings = SimpleNamespace(qdrant_collection="auto_reign_test")
        vector_store = NoopVectorStore()

        def rebuild_index(self, *args, **kwargs) -> str:
            return "noop"

    monkeypatch.setattr("app.api.workspace.IndexService", NoopIndexService)


class RecordingWorkspaceVectorStore:
    def __init__(self) -> None:
        self.upserts: list[tuple[str, list[object]]] = []
        self.deleted_collections: list[str] = []
        self.collections: set[str] = set()

    def prepare_documents(self, documents: list[object]) -> None:
        del documents

    def upsert_documents(self, collection_name: str, documents: list[object]) -> None:
        self.collections.add(collection_name)
        self.upserts.append((collection_name, documents))

    def delete_artifact_chunks(self, collection_name: str, artifact_id: str) -> None:
        del collection_name, artifact_id

    def delete_collection(self, collection_name: str) -> None:
        self.deleted_collections.append(collection_name)
        self.collections.discard(collection_name)

    def list_collections(self) -> list[str]:
        return sorted(self.collections)


def _sse_result(body: str) -> dict[str, object]:
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


def test_workspace_status_is_initialized_on_startup(client) -> None:
    token = _register(client)

    response = client.get("/api/workspace", headers=_auth(token))

    assert response.status_code == 200
    body = response.json()
    assert body["language"] == "zh-CN"
    assert body["schema_version"] == 1
    assert body["artifact_count"] == 0
    settings = client.app.state.settings
    assert (settings.data_dir / "users" / "1" / "workspace" / "workspace.md").exists()
    assert (settings.data_dir / "users" / "1" / "workspace" / "manifest.md").exists()
    assert (settings.data_dir / "users" / "1" / "workspace" / "raw").exists()
    assert (settings.data_dir / "users" / "1" / "workspace" / "extracted").exists()
    assert not (settings.data_dir / "users" / "1" / "workspace" / "sources").exists()
    assert not (settings.data_dir / "uploads").exists()


def test_workspace_rebuild_projection_endpoint(client) -> None:
    token = _register(client)
    artifacts = _workspace_artifacts(client)
    artifacts.create_markdown("knowledge/api.md", kind="knowledge", body="# API\n")

    response = client.post("/api/workspace/rebuild-projection", headers=_auth(token))

    assert response.status_code == 200
    assert response.json()["artifact_count"] == 2


def test_workspace_preparation_tasks_parse_review_status(client) -> None:
    token = _register(client)
    artifacts = _workspace_artifacts(client)
    artifacts.create_markdown(
        "review/status.md",
        kind="review_status",
        body=(
            "# 复习状态\n\n"
            "## 当前重点\n\n"
            "1. MySQL：用 30 秒说清 redo/binlog 两阶段提交。\n"
            "2. Spring：复述 Bean 生命周期。\n"
            "3. Redis：说明缓存击穿治理。\n"
            "4. JVM：复述类加载流程。\n"
        ),
        evidence_refs=["practice:abc"],
    )
    client.post("/api/workspace/rebuild-projection", headers=_auth(token))

    response = client.get("/api/workspace/preparation-tasks", headers=_auth(token))

    assert response.status_code == 200
    body = response.json()
    assert [task["title"] for task in body["tasks"]] == [
        "MySQL：用 30 秒说清 redo/binlog 两阶段提交。",
        "Spring：复述 Bean 生命周期。",
        "Redis：说明缓存击穿治理。",
    ]
    assert body["tasks"][0]["source_artifact_id"]
    assert body["tasks"][0]["source_relative_path"] == "review/status.md"
    assert body["tasks"][0]["reason"] == "来自复习状态"


def test_workspace_upload_materials_endpoint(client, monkeypatch) -> None:
    _disable_index_rebuild(monkeypatch)
    token = _register(client)
    response = client.post(
        "/api/workspace/materials/upload",
        files={"files": ("redis.md", b"# Redis\n\ncache", "text/markdown")},
        headers=_auth(token),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["sources"][0]["duplicate"] is False
    status = client.get("/api/workspace", headers=_auth(token)).json()
    assert status["artifact_count"] == 3


def test_workspace_artifacts_include_source_display_name(client, monkeypatch) -> None:
    _disable_index_rebuild(monkeypatch)
    token = _register(client)
    _workspace_artifacts(client).create_markdown(
        "knowledge/redis.md",
        kind="knowledge",
        body="# Redis\n",
    )
    client.post("/api/workspace/rebuild-projection", headers=_auth(token))
    upload = client.post(
        "/api/workspace/materials/upload",
        files={"files": ("resume-final.md", b"# Resume\n\nBackend work", "text/markdown")},
        headers=_auth(token),
    )
    assert upload.status_code == 200

    artifacts = client.get("/api/workspace/artifacts", headers=_auth(token)).json()["artifacts"]
    source = next(artifact for artifact in artifacts if artifact["kind"] == "source")
    knowledge = next(artifact for artifact in artifacts if artifact["kind"] == "knowledge")
    manifest = next(artifact for artifact in artifacts if artifact["kind"] == "manifest")

    assert source["display_name"] == "resume-final.md"
    assert source["owner"] == "sources"
    assert source["created_at"]
    assert source["updated_at"]
    assert source["relative_path"].startswith(f"raw/{source['id']}-")
    assert knowledge["display_name"] == knowledge["relative_path"].split("/")[-1]
    assert knowledge["owner"] == "knowledge"
    assert manifest["relative_path"] == "manifest.md"
    assert manifest["allowed_operations"] == ["replace_body"]


def test_workspace_upload_schedules_background_index_rebuild(client, monkeypatch) -> None:
    token = _register(client)
    calls: list[tuple[object, object, object, int, str]] = []

    class RecordingIndexService:
        def rebuild_index(
            self,
            session_factory,
            workspace,
            repository,
            *,
            user_id: int,
            qdrant_prefix: str,
        ) -> str:
            calls.append((session_factory, workspace, repository, user_id, qdrant_prefix))
            return "rebuilt"

    monkeypatch.setattr("app.api.workspace.IndexService", RecordingIndexService)

    response = client.post(
        "/api/workspace/materials/upload",
        files={"files": ("redis.md", b"# Redis\n\ncache", "text/markdown")},
        headers=_auth(token),
    )

    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0][0] is client.app.state.session_factory
    assert calls[0][1].root == _workspace_service(client).root
    assert calls[0][3] == 1
    assert calls[0][4] == "auto_reign_user_1"


def test_workspace_rebuild_index_uses_user_active_collection(client, monkeypatch) -> None:
    token = _register(client)
    artifacts = _workspace_artifacts(client)
    artifacts.create_markdown("knowledge/index-me.md", kind="knowledge", body="# Index\n\nbody")
    client.post("/api/workspace/rebuild-projection", headers=_auth(token))
    store = RecordingWorkspaceVectorStore()
    store.collections.update({"auto_reign_user_1__old", "auto_reign_user_1__orphan"})
    with session_scope(client.app.state.session_factory) as session:
        user = session.get(models.User, 1)
        user.settings_json = {**user.settings_json, "active_collection": "auto_reign_user_1__old"}

    monkeypatch.setattr(
        "app.api.workspace.IndexService",
        lambda: IndexService(vector_store=store),
    )

    response = client.post("/api/workspace/rebuild-index", headers=_auth(token))

    assert response.status_code == 200
    body = response.json()
    assert body["collection"].startswith("auto_reign_user_1__")
    assert body["collection"] != "auto_reign_user_1__old"
    assert store.upserts
    assert "auto_reign_user_1__old" in store.deleted_collections
    assert "auto_reign_user_1__orphan" in store.deleted_collections
    with session_scope(client.app.state.session_factory) as session:
        user = session.get(models.User, 1)
        assert user.settings_json["active_collection"] == body["collection"]


def test_record_learning_note_creates_knowledge_artifact(client, monkeypatch) -> None:
    token = _register(client)
    calls: list[tuple[object, object, object]] = []

    class RecordingIndexService:
        def rebuild_index(
            self,
            session_factory,
            workspace,
            repository,
            *,
            user_id: int,
            qdrant_prefix: str,
        ) -> str:
            del user_id, qdrant_prefix
            calls.append((session_factory, workspace, repository))
            return "rebuilt"

    monkeypatch.setattr("app.api.workspace.IndexService", RecordingIndexService)

    response = client.post(
        "/api/workspace/learning-notes/stream",
        json={
            "text": "今天学习了 Redis 缓存穿透、布隆过滤器和空值缓存。",
            "language": "zh-CN",
            "provider": "qwen",
            "model": "qwen3.7-plus",
        },
        headers=_auth(token),
    )

    assert response.status_code == 200
    body = _sse_result(response.text)
    assert body["source"]["duplicate"] is False
    assert re.match(
        r"raw/\d{4}-\d{2}-\d{2}-learning-notes\.md",
        body["source"]["relative_path"],
    )
    assert body["artifact"]["kind"] == "knowledge"
    assert body["summary"]["title"]
    assert "Redis" in body["summary"]["summary"]
    assert "我的理解" in body["card_markdown"]
    assert "原始记录已保存" not in body["card_markdown"]

    artifacts = client.get("/api/workspace/artifacts", headers=_auth(token)).json()["artifacts"]
    kinds = {artifact["kind"] for artifact in artifacts}
    assert {"source", "knowledge", "review_status"}.issubset(kinds)

    detail = client.get(
        f"/api/workspace/artifacts/{body['artifact']['id']}",
        headers=_auth(token),
    ).json()
    assert "我的理解" in detail["body"]
    assert "修正/补充" in detail["body"]
    assert "30 秒面试说法" in detail["body"]
    assert "易混点" in detail["body"]
    assert "追问" in detail["body"]
    assert "缓存穿透" in detail["body"]
    assert "原始记录已保存" not in detail["body"]
    status = next(artifact for artifact in artifacts if artifact["kind"] == "review_status")
    status_detail = client.get(
        f"/api/workspace/artifacts/{status['id']}",
        headers=_auth(token),
    ).json()
    assert "## 最近整理" in status_detail["body"]
    assert body["summary"]["title"] in status_detail["body"]
    assert len(calls) == 1


def test_learning_note_stream_creates_learning_conversation(client, monkeypatch) -> None:
    token = _register(client)

    class RecordingIndexService:
        def rebuild_index(
            self,
            session_factory,
            workspace,
            repository,
            *,
            user_id: int,
            qdrant_prefix: str,
        ) -> str:
            del session_factory, workspace, repository, user_id, qdrant_prefix
            return "rebuilt"

    monkeypatch.setattr("app.api.workspace.IndexService", RecordingIndexService)

    response = client.post(
        "/api/workspace/learning-notes/stream",
        json={
            "text": "今天学习了 Redis 缓存穿透。",
            "language": "zh-CN",
            "provider": "qwen",
            "model": "qwen3.7-plus",
        },
        headers=_auth(token),
    )

    assert response.status_code == 200
    body = _sse_result(response.text)
    assert body["conversation_id"]

    with session_scope(client.app.state.session_factory) as session:
        conversation = session.get(models.Conversation, body["conversation_id"])
        assert conversation is not None
        assert conversation.user_id == 1
        assert conversation.kind == "learning"
        messages = list(
            session.scalars(
                select(models.Message)
                .where(models.Message.conversation_id == conversation.id)
                .order_by(models.Message.sequence)
            )
        )
        assert [message.role for message in messages] == ["user", "assistant"]
        assert "缓存穿透" in messages[0].content
        assert messages[1].metadata_json["artifact_path"] == body["artifact"]["relative_path"]


def test_learning_note_stream_appends_to_existing_learning_conversation(client, monkeypatch) -> None:
    token = _register(client)

    class RecordingIndexService:
        def rebuild_index(
            self,
            session_factory,
            workspace,
            repository,
            *,
            user_id: int,
            qdrant_prefix: str,
        ) -> str:
            del session_factory, workspace, repository, user_id, qdrant_prefix
            return "rebuilt"

    monkeypatch.setattr("app.api.workspace.IndexService", RecordingIndexService)

    first = client.post(
        "/api/workspace/learning-notes/stream",
        json={"text": "第一次学习 Redis 缓存穿透。", "language": "zh-CN"},
        headers=_auth(token),
    )
    first_body = _sse_result(first.text)

    second = client.post(
        "/api/workspace/learning-notes/stream",
        json={
            "conversation_id": first_body["conversation_id"],
            "text": "第二次学习布隆过滤器。",
            "language": "zh-CN",
        },
        headers=_auth(token),
    )

    assert second.status_code == 200
    second_body = _sse_result(second.text)
    assert second_body["conversation_id"] == first_body["conversation_id"]

    with session_scope(client.app.state.session_factory) as session:
        messages = list(
            session.scalars(
                select(models.Message)
                .where(models.Message.conversation_id == first_body["conversation_id"])
                .order_by(models.Message.sequence)
            )
        )
        assert [message.role for message in messages] == [
            "user",
            "assistant",
            "user",
            "assistant",
        ]
        assert "第二次学习布隆过滤器" in messages[2].content


def test_learning_note_stream_rejects_unknown_conversation_before_persisting(client, monkeypatch) -> None:
    token = _register(client)

    class FailingModelService:
        def stream_learning_note_summary(self, *args, **kwargs):
            raise AssertionError("model should not be called when conversation is unknown")

    monkeypatch.setattr("app.api.workspace.ModelService", FailingModelService)

    response = client.post(
        "/api/workspace/learning-notes/stream",
        json={
            "conversation_id": "missing-session",
            "text": "这条学习记录不应该落盘。",
            "language": "zh-CN",
        },
        headers=_auth(token),
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "learning_session_not_found"
    assert client.get("/api/workspace", headers=_auth(token)).json()["artifact_count"] == 0


def test_record_learning_note_merges_cards_with_same_topic(client, monkeypatch) -> None:
    token = _register(client)

    class FixedModelService:
        def stream_learning_note_summary(
            self,
            text: str,
            *,
            language: str = "zh-CN",
            provider: str | None = None,
            model: str | None = None,
        ):
            yield (
                "# Redis 缓存穿透\n\n"
                f"## 摘要\n\n已整理：{text}\n\n"
                "## 关键点\n\n- 布隆过滤器和空值缓存是常见治理方案。\n\n"
                "## 面试表达\n\n- 先说明风险，再说明布隆过滤器和空值缓存的取舍。\n\n"
                "## 可追问问题\n\n- 布隆过滤器误判会带来什么影响？"
            )

    class RecordingIndexService:
        def rebuild_index(
            self,
            session_factory,
            workspace,
            repository,
            *,
            user_id: int,
            qdrant_prefix: str,
        ) -> str:
            del session_factory, workspace, repository, user_id, qdrant_prefix
            return "rebuilt"

    monkeypatch.setattr("app.api.workspace.ModelService", FixedModelService)
    monkeypatch.setattr("app.api.workspace.IndexService", RecordingIndexService)

    first_response = client.post(
        "/api/workspace/learning-notes/stream",
        json={
            "text": "第一次：缓存穿透可以用布隆过滤器挡住不存在的 key。",
            "language": "zh-CN",
        },
        headers=_auth(token),
    )
    second_response = client.post(
        "/api/workspace/learning-notes/stream",
        json={
            "text": "第二次：空值缓存也可以降低数据库压力。",
            "language": "zh-CN",
        },
        headers=_auth(token),
    )
    first = _sse_result(first_response.text)
    second = _sse_result(second_response.text)

    artifacts = client.get("/api/workspace/artifacts", headers=_auth(token)).json()["artifacts"]
    knowledge_artifacts = [artifact for artifact in artifacts if artifact["kind"] == "knowledge"]
    source_artifacts = [artifact for artifact in artifacts if artifact["kind"] == "source"]

    assert first["artifact"]["id"] == second["artifact"]["id"]
    assert first["artifact"]["relative_path"] == "knowledge/redis-缓存穿透.md"
    assert len(knowledge_artifacts) == 1
    assert len(source_artifacts) == 1
    assert re.match(
        r"raw/\d{4}-\d{2}-\d{2}-learning-notes\.md",
        source_artifacts[0]["relative_path"],
    )

    detail = client.get(
        f"/api/workspace/artifacts/{second['artifact']['id']}",
        headers=_auth(token),
    ).json()
    assert detail["revision"] == 2
    assert "第一次：缓存穿透" in detail["body"]
    assert "第二次：空值缓存" in detail["body"]
    assert detail["body"].count("# Redis 缓存穿透") == 1
    assert detail["body"].count("- 我的理解") == 2


def test_record_learning_note_stream_emits_deltas_and_result(client, monkeypatch) -> None:
    token = _register(client)
    calls: list[tuple[object, object, object]] = []

    class RecordingIndexService:
        def rebuild_index(
            self,
            session_factory,
            workspace,
            repository,
            *,
            user_id: int,
            qdrant_prefix: str,
        ) -> str:
            del user_id, qdrant_prefix
            calls.append((session_factory, workspace, repository))
            return "rebuilt"

    monkeypatch.setattr("app.api.workspace.IndexService", RecordingIndexService)

    response = client.post(
        "/api/workspace/learning-notes/stream",
        json={
            "text": "今天学习了 MySQL 索引覆盖和回表优化。",
            "language": "zh-CN",
            "provider": "qwen",
            "model": "qwen3.7-plus",
        },
        headers=_auth(token),
    )

    assert response.status_code == 200
    body = response.text
    assert "event: delta" in body
    assert "event: result" in body
    assert "MySQL" in body

    artifacts = client.get("/api/workspace/artifacts", headers=_auth(token)).json()["artifacts"]
    kinds = {artifact["kind"] for artifact in artifacts}
    assert {"source", "knowledge"}.issubset(kinds)
    assert len(calls) == 1


def test_record_real_interview_archives_extracts_and_updates_status(
    client,
    monkeypatch,
) -> None:
    token = _register(client)
    calls: list[tuple[object, object, object]] = []

    class RecordingIndexService:
        def rebuild_index(
            self,
            session_factory,
            workspace,
            repository,
            *,
            user_id: int,
            qdrant_prefix: str,
        ) -> str:
            del user_id, qdrant_prefix
            calls.append((session_factory, workspace, repository))
            return "rebuilt"

    monkeypatch.setattr("app.api.workspace.IndexService", RecordingIndexService)

    response = client.post(
        "/api/workspace/real-interview-records",
        json={
            "text": (
                "面试官：Redis 缓存击穿怎么处理？\n"
                "我：只说了加锁，没答好降级预案。\n"
                "面试官：MySQL redo log 和 binlog 为什么要两阶段提交？\n"
                "我：这个不会。\n"
            ),
            "language": "zh-CN",
        },
        headers=_auth(token),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["raw_artifact"]["kind"] == "interview_record"
    assert re.match(
        r"raw/\d{8}-\d{6}-\d{6}-real-interview\.md",
        body["raw_artifact"]["relative_path"],
    )
    assert body["questions"] == [
        "Redis 缓存击穿怎么处理？",
        "MySQL redo log 和 binlog 为什么要两阶段提交？",
    ]
    assert "没答好降级预案" in body["weak_points"][0]
    assert body["high_frequency_artifact"]["relative_path"] == "review/high-frequency.md"
    assert body["status_artifact"]["relative_path"] == "review/status.md"

    raw_detail = client.get(
        f"/api/workspace/artifacts/{body['raw_artifact']['id']}",
        headers=_auth(token),
    ).json()
    assert "## 原始记录" in raw_detail["body"]
    assert "Redis 缓存击穿怎么处理？" in raw_detail["body"]

    high_frequency_detail = client.get(
        f"/api/workspace/artifacts/{body['high_frequency_artifact']['id']}",
        headers=_auth(token),
    ).json()
    assert "## 真实面试高频问题" in high_frequency_detail["body"]
    assert "Redis 缓存击穿怎么处理？" in high_frequency_detail["body"]
    assert "## 暴露问题" in high_frequency_detail["body"]
    assert "这个不会" in high_frequency_detail["body"]
    status_detail = client.get(
        f"/api/workspace/artifacts/{body['status_artifact']['id']}",
        headers=_auth(token),
    ).json()
    assert "## 当前重点" in status_detail["body"]
    assert "Redis 缓存击穿怎么处理？" in status_detail["body"]

    tasks = client.get("/api/workspace/preparation-tasks", headers=_auth(token)).json()["tasks"]
    assert len(tasks) == 3
    assert "Redis 缓存击穿怎么处理？" in tasks[0]["title"]
    assert "降级预案" in tasks[1]["title"]
    assert len(calls) == 1


def test_learning_note_stream_prompt_uses_requested_language_headings() -> None:
    prompt = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "prompts"
        / "learning_note_summary_stream.md"
    ).read_text(encoding="utf-8")

    assert "language == \"zh-CN\"" in prompt
    assert "## 摘要" in prompt
    assert "## 关键点" in prompt
    assert "## 面试表达" in prompt
    assert "## 可追问问题" in prompt
    assert "## Summary" in prompt


def test_workspace_artifact_read_and_replace_body(client) -> None:
    token = _register(client)
    artifacts = _workspace_artifacts(client)
    artifacts.create_markdown("knowledge/edit.md", kind="knowledge", body="# Edit\n\nold")
    client.post("/api/workspace/rebuild-projection", headers=_auth(token))
    listed = client.get("/api/workspace/artifacts", headers=_auth(token)).json()["artifacts"]
    artifact_id = listed[0]["id"]
    assert listed[0]["allowed_operations"] == ["replace_body"]

    detail = client.get(f"/api/workspace/artifacts/{artifact_id}", headers=_auth(token)).json()
    assert detail["body"] == "# Edit\n\nold"

    response = client.put(
        f"/api/workspace/artifacts/{artifact_id}/body",
        json={"expected_revision": 1, "body": "# Edit\n\nnew"},
        headers=_auth(token),
    )

    assert response.status_code == 200
    assert response.json()["revision"] == 2
    assert artifacts.read_markdown("knowledge/edit.md").body == "# Edit\n\nnew"

    conflict = client.put(
        f"/api/workspace/artifacts/{artifact_id}/body",
        json={"expected_revision": 1, "body": "# stale"},
        headers=_auth(token),
    )
    assert conflict.status_code == 409


def test_workspace_artifact_delete_removes_file_and_projection(client) -> None:
    token = _register(client)
    artifacts = _workspace_artifacts(client)
    workspace = _workspace_service(client)
    artifacts.create_markdown("knowledge/delete-me.md", kind="knowledge", body="# Delete\n")
    client.post("/api/workspace/rebuild-projection", headers=_auth(token))
    listed = client.get("/api/workspace/artifacts", headers=_auth(token)).json()["artifacts"]
    artifact_id = next(artifact for artifact in listed if artifact["kind"] == "knowledge")["id"]
    artifact_path = workspace.resolve_path("knowledge/delete-me.md")

    response = client.delete(f"/api/workspace/artifacts/{artifact_id}", headers=_auth(token))

    assert response.status_code == 200
    assert response.json() == {"id": artifact_id, "status": "deleted"}
    assert not artifact_path.exists()
    assert client.get(f"/api/workspace/artifacts/{artifact_id}", headers=_auth(token)).status_code == 404
    remaining = client.get("/api/workspace/artifacts", headers=_auth(token)).json()["artifacts"]
    assert [artifact["kind"] for artifact in remaining] == ["manifest"]


def test_workspace_source_delete_removes_original_and_sidecar(client, monkeypatch) -> None:
    _disable_index_rebuild(monkeypatch)
    token = _register(client)
    upload = client.post(
        "/api/workspace/materials/upload",
        files={"files": ("source.txt", b"source", "text/plain")},
        headers=_auth(token),
    ).json()
    source_id = upload["sources"][0]["artifact_id"]
    source = next(
        artifact
        for artifact in client.get("/api/workspace/artifacts", headers=_auth(token)).json()["artifacts"]
        if artifact["id"] == source_id
    )
    source_path = _workspace_service(client).resolve_path(source["relative_path"])
    sidecar_path = source_path.with_name(f"{source_path.name}.meta.json")

    response = client.delete(f"/api/workspace/artifacts/{source_id}", headers=_auth(token))

    assert response.status_code == 200
    assert not source_path.exists()
    assert not sidecar_path.exists()


def test_workspace_artifact_delete_keeps_file_when_vector_delete_fails(
    client, monkeypatch
) -> None:
    token = _register(client)
    artifacts = _workspace_artifacts(client)
    workspace = _workspace_service(client)
    artifacts.create_markdown("knowledge/keep-me.md", kind="knowledge", body="# Keep\n")
    client.post("/api/workspace/rebuild-projection", headers=_auth(token))
    listed = client.get("/api/workspace/artifacts", headers=_auth(token)).json()["artifacts"]
    artifact_id = listed[0]["id"]
    artifact_path = workspace.resolve_path("knowledge/keep-me.md")

    class FailingVectorStore:
        def delete_artifact_chunks(self, collection_name: str, artifact_id: str) -> None:
            raise VectorStoreUnavailable(f"{collection_name}:{artifact_id}")

    class FailingIndexService:
        settings = SimpleNamespace(qdrant_collection="auto_reign_test")
        vector_store = FailingVectorStore()

    monkeypatch.setattr("app.api.workspace.IndexService", FailingIndexService)

    response = client.delete(f"/api/workspace/artifacts/{artifact_id}", headers=_auth(token))

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "vector_delete_failed"
    assert artifact_path.exists()
    assert client.get(f"/api/workspace/artifacts/{artifact_id}", headers=_auth(token)).status_code == 200


def test_workspace_artifact_permissions_are_enforced(client, monkeypatch) -> None:
    _disable_index_rebuild(monkeypatch)
    token = _register(client)
    upload = client.post(
        "/api/workspace/materials/upload",
        files={"files": ("source.txt", b"source", "text/plain")},
        headers=_auth(token),
    ).json()
    source_id = upload["sources"][0]["artifact_id"]

    response = client.put(
        f"/api/workspace/artifacts/{source_id}/body",
        json={"expected_revision": 1, "body": "changed"},
        headers=_auth(token),
    )

    assert response.status_code == 403


def test_workspace_legacy_plan_artifact_is_not_editable(client) -> None:
    token = _register(client)
    artifacts = _workspace_artifacts(client)
    artifacts.create_markdown("state/plan.md", kind="plan", body="# Plan\n\n- a\n")
    client.post("/api/workspace/rebuild-projection", headers=_auth(token))
    plan = next(
        artifact
        for artifact in client.get("/api/workspace/artifacts", headers=_auth(token)).json()[
            "artifacts"
        ]
        if artifact["kind"] == "plan"
    )

    response = client.put(
        f"/api/workspace/artifacts/{plan['id']}/body",
        json={"expected_revision": 1, "body": "# Plan\n\n- a\n- b\n- c\n- d\n"},
        headers=_auth(token),
    )

    assert response.status_code == 400


def test_health_includes_workspace_without_exposing_paths(client) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["workspace"]["initialized"] is False
    assert "path" not in body["workspace"]
