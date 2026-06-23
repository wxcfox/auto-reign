import re
from pathlib import Path
from types import SimpleNamespace

from app.repositories.vector_store import VectorStoreUnavailable


def test_workspace_status_is_initialized_on_startup(client) -> None:
    response = client.get("/api/workspace")

    assert response.status_code == 200
    body = response.json()
    assert body["language"] == "zh-CN"
    assert body["schema_version"] == 1
    assert body["artifact_count"] == 0


def test_workspace_rebuild_projection_endpoint(client) -> None:
    artifacts = client.app.state.artifact_service
    artifacts.create_markdown("knowledge/api.md", kind="knowledge", body="# API\n")

    response = client.post("/api/workspace/rebuild-projection")

    assert response.status_code == 200
    assert response.json()["artifact_count"] == 1


def test_workspace_preparation_tasks_parse_review_status(client) -> None:
    artifacts = client.app.state.artifact_service
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
    client.post("/api/workspace/rebuild-projection")

    response = client.get("/api/workspace/preparation-tasks")

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


def test_workspace_upload_materials_endpoint(client) -> None:
    response = client.post(
        "/api/workspace/materials/upload",
        files={"files": ("redis.md", b"# Redis\n\ncache", "text/markdown")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["sources"][0]["duplicate"] is False
    status = client.get("/api/workspace").json()
    assert status["artifact_count"] == 2


def test_workspace_artifacts_include_source_display_name(client) -> None:
    client.app.state.artifact_service.create_markdown(
        "knowledge/redis.md",
        kind="knowledge",
        body="# Redis\n",
    )
    client.post("/api/workspace/rebuild-projection")
    upload = client.post(
        "/api/workspace/materials/upload",
        files={"files": ("resume-final.md", b"# Resume\n\nBackend work", "text/markdown")},
    )
    assert upload.status_code == 200

    artifacts = client.get("/api/workspace/artifacts").json()["artifacts"]
    source = next(artifact for artifact in artifacts if artifact["kind"] == "source")
    knowledge = next(artifact for artifact in artifacts if artifact["kind"] == "knowledge")

    assert source["display_name"] == "resume-final.md"
    assert source["owner"] == "sources"
    assert source["created_at"]
    assert source["updated_at"]
    assert source["relative_path"].startswith(f"sources/documents/{source['id']}-")
    assert knowledge["display_name"] == knowledge["relative_path"].split("/")[-1]
    assert knowledge["owner"] == "knowledge"


def test_workspace_upload_schedules_background_index_rebuild(client, monkeypatch) -> None:
    calls: list[tuple[object, object, object]] = []

    class RecordingIndexService:
        def rebuild_index(self, session_factory, workspace, repository) -> str:
            calls.append((session_factory, workspace, repository))
            return "rebuilt"

    monkeypatch.setattr("app.api.workspace.IndexService", RecordingIndexService)

    response = client.post(
        "/api/workspace/materials/upload",
        files={"files": ("redis.md", b"# Redis\n\ncache", "text/markdown")},
    )

    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0][0] is client.app.state.session_factory
    assert calls[0][1] is client.app.state.workspace_service


def test_record_learning_note_creates_knowledge_artifact(client, monkeypatch) -> None:
    calls: list[tuple[object, object, object]] = []

    class RecordingIndexService:
        def rebuild_index(self, session_factory, workspace, repository) -> str:
            calls.append((session_factory, workspace, repository))
            return "rebuilt"

    monkeypatch.setattr("app.api.workspace.IndexService", RecordingIndexService)

    response = client.post(
        "/api/workspace/learning-notes",
        json={
            "text": "今天学习了 Redis 缓存穿透、布隆过滤器和空值缓存。",
            "language": "zh-CN",
            "provider": "qwen",
            "model": "qwen3.7-plus",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["source"]["duplicate"] is False
    assert re.match(r"inbox/\d{4}-\d{2}-\d{2}\.md", body["source"]["relative_path"])
    assert body["artifact"]["kind"] == "knowledge"
    assert body["summary"]["title"]
    assert "Redis" in body["summary"]["summary"]
    assert "我的理解" in body["card_markdown"]
    assert "原始记录已保存" not in body["card_markdown"]

    artifacts = client.get("/api/workspace/artifacts").json()["artifacts"]
    kinds = {artifact["kind"] for artifact in artifacts}
    assert {"source", "knowledge", "review_status"}.issubset(kinds)

    detail = client.get(f"/api/workspace/artifacts/{body['artifact']['id']}").json()
    assert "我的理解" in detail["body"]
    assert "修正/补充" in detail["body"]
    assert "30 秒面试说法" in detail["body"]
    assert "易混点" in detail["body"]
    assert "追问" in detail["body"]
    assert "缓存穿透" in detail["body"]
    assert "原始记录已保存" not in detail["body"]
    status = next(artifact for artifact in artifacts if artifact["kind"] == "review_status")
    status_detail = client.get(f"/api/workspace/artifacts/{status['id']}").json()
    assert "## 最近整理" in status_detail["body"]
    assert body["summary"]["title"] in status_detail["body"]
    assert len(calls) == 1


def test_record_learning_note_merges_cards_with_same_topic(client, monkeypatch) -> None:
    class FixedModelService:
        def summarize_learning_note(
            self,
            text: str,
            *,
            language: str = "zh-CN",
            provider: str | None = None,
            model: str | None = None,
        ):
            from app.services.model_service import LearningNoteSummaryResult

            return LearningNoteSummaryResult(
                title="Redis 缓存穿透",
                summary=f"已整理：{text}",
                key_points=["布隆过滤器和空值缓存是常见治理方案。"],
                interview_takeaways=["先说明风险，再说明布隆过滤器和空值缓存的取舍。"],
                follow_up_questions=["布隆过滤器误判会带来什么影响？"],
            )

    class RecordingIndexService:
        def rebuild_index(self, session_factory, workspace, repository) -> str:
            return "rebuilt"

    monkeypatch.setattr("app.api.workspace.ModelService", FixedModelService)
    monkeypatch.setattr("app.api.workspace.IndexService", RecordingIndexService)

    first = client.post(
        "/api/workspace/learning-notes",
        json={
            "text": "第一次：缓存穿透可以用布隆过滤器挡住不存在的 key。",
            "language": "zh-CN",
        },
    ).json()
    second = client.post(
        "/api/workspace/learning-notes",
        json={
            "text": "第二次：空值缓存也可以降低数据库压力。",
            "language": "zh-CN",
        },
    ).json()

    artifacts = client.get("/api/workspace/artifacts").json()["artifacts"]
    knowledge_artifacts = [artifact for artifact in artifacts if artifact["kind"] == "knowledge"]
    source_artifacts = [artifact for artifact in artifacts if artifact["kind"] == "source"]

    assert first["artifact"]["id"] == second["artifact"]["id"]
    assert first["artifact"]["relative_path"] == "knowledge/redis-缓存穿透.md"
    assert len(knowledge_artifacts) == 1
    assert len(source_artifacts) == 1
    assert source_artifacts[0]["relative_path"].startswith("inbox/")

    detail = client.get(f"/api/workspace/artifacts/{second['artifact']['id']}").json()
    assert detail["revision"] == 2
    assert "第一次：缓存穿透" in detail["body"]
    assert "第二次：空值缓存" in detail["body"]
    assert detail["body"].count("# Redis 缓存穿透") == 1
    assert detail["body"].count("- 我的理解") == 2


def test_record_learning_note_stream_emits_deltas_and_result(client, monkeypatch) -> None:
    calls: list[tuple[object, object, object]] = []

    class RecordingIndexService:
        def rebuild_index(self, session_factory, workspace, repository) -> str:
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
    )

    assert response.status_code == 200
    body = response.text
    assert "event: delta" in body
    assert "event: result" in body
    assert "MySQL" in body

    artifacts = client.get("/api/workspace/artifacts").json()["artifacts"]
    kinds = {artifact["kind"] for artifact in artifacts}
    assert {"source", "knowledge"}.issubset(kinds)
    assert len(calls) == 1


def test_record_real_interview_archives_extracts_and_updates_status(
    client,
    monkeypatch,
) -> None:
    calls: list[tuple[object, object, object]] = []

    class RecordingIndexService:
        def rebuild_index(self, session_factory, workspace, repository) -> str:
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
    )

    assert response.status_code == 200
    body = response.json()
    assert body["raw_artifact"]["kind"] == "interview_record"
    assert body["raw_artifact"]["relative_path"].startswith("raw/")
    assert body["questions"] == [
        "Redis 缓存击穿怎么处理？",
        "MySQL redo log 和 binlog 为什么要两阶段提交？",
    ]
    assert "没答好降级预案" in body["weak_points"][0]
    assert body["high_frequency_artifact"]["relative_path"] == "review/high-frequency.md"
    assert body["status_artifact"]["relative_path"] == "review/status.md"

    raw_detail = client.get(f"/api/workspace/artifacts/{body['raw_artifact']['id']}").json()
    assert "## 原始记录" in raw_detail["body"]
    assert "Redis 缓存击穿怎么处理？" in raw_detail["body"]

    high_frequency_detail = client.get(
        f"/api/workspace/artifacts/{body['high_frequency_artifact']['id']}"
    ).json()
    assert "## 真实面试高频问题" in high_frequency_detail["body"]
    assert "Redis 缓存击穿怎么处理？" in high_frequency_detail["body"]
    assert "## 暴露问题" in high_frequency_detail["body"]
    assert "这个不会" in high_frequency_detail["body"]
    status_detail = client.get(f"/api/workspace/artifacts/{body['status_artifact']['id']}").json()
    assert "## 当前重点" in status_detail["body"]
    assert "Redis 缓存击穿怎么处理？" in status_detail["body"]

    tasks = client.get("/api/workspace/preparation-tasks").json()["tasks"]
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
    artifacts = client.app.state.artifact_service
    artifacts.create_markdown("knowledge/edit.md", kind="knowledge", body="# Edit\n\nold")
    client.post("/api/workspace/rebuild-projection")
    listed = client.get("/api/workspace/artifacts").json()["artifacts"]
    artifact_id = listed[0]["id"]
    assert listed[0]["allowed_operations"] == ["replace_body"]

    detail = client.get(f"/api/workspace/artifacts/{artifact_id}").json()
    assert detail["body"] == "# Edit\n\nold"

    response = client.put(
        f"/api/workspace/artifacts/{artifact_id}/body",
        json={"expected_revision": 1, "body": "# Edit\n\nnew"},
    )

    assert response.status_code == 200
    assert response.json()["revision"] == 2
    assert artifacts.read_markdown("knowledge/edit.md").body == "# Edit\n\nnew"

    conflict = client.put(
        f"/api/workspace/artifacts/{artifact_id}/body",
        json={"expected_revision": 1, "body": "# stale"},
    )
    assert conflict.status_code == 409


def test_workspace_artifact_delete_removes_file_and_projection(client) -> None:
    artifacts = client.app.state.artifact_service
    workspace = client.app.state.workspace_service
    artifacts.create_markdown("knowledge/delete-me.md", kind="knowledge", body="# Delete\n")
    client.post("/api/workspace/rebuild-projection")
    listed = client.get("/api/workspace/artifacts").json()["artifacts"]
    artifact_id = listed[0]["id"]
    artifact_path = workspace.resolve_path("knowledge/delete-me.md")

    response = client.delete(f"/api/workspace/artifacts/{artifact_id}")

    assert response.status_code == 200
    assert response.json() == {"id": artifact_id, "status": "deleted"}
    assert not artifact_path.exists()
    assert client.get(f"/api/workspace/artifacts/{artifact_id}").status_code == 404
    assert client.get("/api/workspace/artifacts").json()["artifacts"] == []


def test_workspace_source_delete_removes_original_and_sidecar(client) -> None:
    upload = client.post(
        "/api/workspace/materials/upload",
        files={"files": ("source.txt", b"source", "text/plain")},
    ).json()
    source_id = upload["sources"][0]["artifact_id"]
    source = next(
        artifact
        for artifact in client.get("/api/workspace/artifacts").json()["artifacts"]
        if artifact["id"] == source_id
    )
    source_path = client.app.state.workspace_service.resolve_path(source["relative_path"])
    sidecar_path = source_path.with_name(f"{source_path.name}.meta.json")

    response = client.delete(f"/api/workspace/artifacts/{source_id}")

    assert response.status_code == 200
    assert not source_path.exists()
    assert not sidecar_path.exists()


def test_workspace_artifact_delete_keeps_file_when_vector_delete_fails(
    client, monkeypatch
) -> None:
    artifacts = client.app.state.artifact_service
    workspace = client.app.state.workspace_service
    artifacts.create_markdown("knowledge/keep-me.md", kind="knowledge", body="# Keep\n")
    client.post("/api/workspace/rebuild-projection")
    listed = client.get("/api/workspace/artifacts").json()["artifacts"]
    artifact_id = listed[0]["id"]
    artifact_path = workspace.resolve_path("knowledge/keep-me.md")

    class FailingVectorStore:
        def delete_document_chunks(self, collection_name: str, document_id: str) -> None:
            raise VectorStoreUnavailable(f"{collection_name}:{document_id}")

    class FailingIndexService:
        settings = SimpleNamespace(qdrant_collection="auto_reign_test")
        vector_store = FailingVectorStore()

    monkeypatch.setattr("app.api.workspace.IndexService", FailingIndexService)

    response = client.delete(f"/api/workspace/artifacts/{artifact_id}")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "vector_delete_failed"
    assert artifact_path.exists()
    assert client.get(f"/api/workspace/artifacts/{artifact_id}").status_code == 200


def test_workspace_artifact_permissions_are_enforced(client) -> None:
    upload = client.post(
        "/api/workspace/materials/upload",
        files={"files": ("source.txt", b"source", "text/plain")},
    ).json()
    source_id = upload["sources"][0]["artifact_id"]

    response = client.put(
        f"/api/workspace/artifacts/{source_id}/body",
        json={"expected_revision": 1, "body": "changed"},
    )

    assert response.status_code == 403


def test_workspace_legacy_plan_artifact_is_not_editable(client) -> None:
    artifacts = client.app.state.artifact_service
    artifacts.create_markdown("state/plan.md", kind="plan", body="# Plan\n\n- a\n")
    client.post("/api/workspace/rebuild-projection")
    plan = client.get("/api/workspace/artifacts").json()["artifacts"][0]

    response = client.put(
        f"/api/workspace/artifacts/{plan['id']}/body",
        json={"expected_revision": 1, "body": "# Plan\n\n- a\n- b\n- c\n- d\n"},
    )

    assert response.status_code == 400


def test_health_includes_workspace_without_exposing_paths(client) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["workspace"]["initialized"] is True
    assert "path" not in body["workspace"]
