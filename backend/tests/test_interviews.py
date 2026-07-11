import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.db import models
from app.db.session import session_scope
from app.repositories.artifact_repository import ArtifactRepository
from app.repositories.vector_store import VectorStoreUnavailable
from app.services.artifact_service import ArtifactService
from app.services.retrieval_query_planner import RetrievalRequest
from app.services.workspace_service import WorkspaceService
from tests.sse import post_sse, sse_error


DEFAULT_QWEN_CONFIG = {
    "target_company": "",
    "target_role": "",
    "job_description": "",
    "extra_prompt": "",
    "language": "en",
    "mode": "comprehensive",
    "chat_model_provider": "qwen",
    "chat_model": "qwen3.7-plus",
    "target_rounds": 3,
}


CONFIG = {
    "target_company": "OpenAI",
    "target_role": "Backend Engineer",
    "job_description": "Build reliable AI application backends.",
    "extra_prompt": "Focus on RAG and FastAPI.",
    "language": "en",
    "mode": "comprehensive",
    "chat_model_provider": "qwen",
    "chat_model": "qwen3.7-plus",
    "target_rounds": 3,
}


def _register(client: TestClient, username: str = "alice") -> dict[str, str]:
    response = client.post(
        "/api/auth/register",
        json={"username": username, "password": "correct horse battery staple"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture(autouse=True)
def auth_headers(client: TestClient) -> dict[str, str]:
    headers = _register(client)
    client.headers.update(headers)
    return headers


def _artifact_service_for_user(user_id: int = 1) -> ArtifactService:
    workspace = WorkspaceService(get_settings().data_dir / "users" / str(user_id) / "workspace")
    workspace.initialize()
    return ArtifactService(workspace)


def _rebuild_projection(client: TestClient, user_id: int = 1) -> None:
    artifact_service = _artifact_service_for_user(user_id)
    with session_scope(client.app.state.session_factory) as session:
        artifact_service.workspace.rebuild_projection(
            session,
            ArtifactRepository(),
            artifact_service,
            user_id=user_id,
        )


def test_get_last_config_defaults_to_qwen(client: TestClient) -> None:
    response = client.get("/api/interview-configs/last")
    assert response.status_code == 200
    body = response.json()
    assert {key: body[key] for key in DEFAULT_QWEN_CONFIG} == DEFAULT_QWEN_CONFIG


def test_save_last_config_and_create_session(client: TestClient) -> None:
    saved = client.put("/api/interview-configs/last", json=CONFIG)
    assert saved.status_code == 200
    loaded = client.get("/api/interview-configs/last")
    assert loaded.status_code == 200
    assert loaded.json()["target_company"] == "OpenAI"
    assert loaded.json()["language"] == "en"

    body = post_sse(client, "/api/interview-sessions/stream", json_body=CONFIG)
    assert body["session"]["status"] == "active"
    assert body["turn"]["round_index"] == 1
    assert body["turn"]["question"]
    assert body["session"]["started_at"].endswith(("Z", "+00:00"))
    assert body["turn"]["created_at"].endswith(("Z", "+00:00"))


def test_stream_create_session_returns_question_delta_and_result(client: TestClient) -> None:
    response = client.post("/api/interview-sessions/stream", json=CONFIG)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    body = response.text
    assert "event: delta" in body
    assert "event: result" in body
    assert '"session"' in body
    assert '"turn"' in body
    assert "Backend Engineer" in body


def test_conversation_history_includes_interview_context(client: TestClient) -> None:
    active = post_sse(
        client,
        "/api/interview-sessions/stream",
        json_body={**CONFIG, "extra_prompt": "Active backend interview", "target_rounds": 2},
    )
    active_session_id = active["session"]["id"]
    completed = post_sse(
        client,
        "/api/interview-sessions/stream",
        json_body={**CONFIG, "extra_prompt": "Completed backend interview", "target_rounds": 1},
    )
    completed_session_id = completed["session"]["id"]
    post_sse(
        client,
        f"/api/interview-sessions/{completed_session_id}/answer/stream",
        json_body={"answer": "I would give a concise architecture answer."},
    )
    post_sse(client, f"/api/interview-sessions/{completed_session_id}/finish/stream")

    listed = client.get("/api/conversations")

    assert listed.status_code == 200
    conversations = listed.json()["conversations"]
    assert [item["id"] for item in conversations] == [completed_session_id, active_session_id]
    assert conversations[0]["kind"] == "interview"
    assert conversations[0]["title"] == "Completed backend interview"
    assert "resumable" not in conversations[0]
    assert conversations[1]["title"] == "Active backend interview"

    detail = client.get(f"/api/conversations/{completed_session_id}").json()
    assert "I would give a concise architecture answer." in [
        message["content"] for message in detail["messages"]
    ]


def test_stream_finish_returns_summary_delta_and_result(client: TestClient) -> None:
    created = post_sse(
        client,
        "/api/interview-sessions/stream",
        json_body={**CONFIG, "target_rounds": 1},
    )
    session_id = created["session"]["id"]
    post_sse(
        client,
        f"/api/interview-sessions/{session_id}/answer/stream",
        json_body={"answer": "I would explain clear service boundaries."},
    )

    response = client.post(f"/api/interview-sessions/{session_id}/finish/stream")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    body = response.text
    assert "event: delta" in body
    assert "event: result" in body
    assert "Backend Engineer interview for OpenAI" in body
    assert '"report"' in body
    assert '"status":"completed"' in body


def test_create_session_skips_rag_when_library_is_empty(client: TestClient, monkeypatch) -> None:
    def fail_embed_query(_self, _text):
        raise AssertionError("embedding should not run for an empty library")

    def fail_search(*_args, **_kwargs):
        raise AssertionError("workspace search should not run for an empty library")

    monkeypatch.setattr(
        "tests.fakes.FakeOpenAIEmbeddings.embed_query",
        fail_embed_query,
    )
    monkeypatch.setattr(
        "app.services.workspace_vector_store.WorkspaceVectorStore.search",
        fail_search,
    )

    body = post_sse(client, "/api/interview-sessions/stream", json_body=CONFIG)
    assert body["turn"]["question"]
    assert body["turn"]["retrieved_context_refs"] == []


def test_create_session_degrades_when_index_refresh_is_unavailable(
    client: TestClient, monkeypatch
) -> None:
    def fail_ensure_current(*_args, **_kwargs):
        raise VectorStoreUnavailable("qdrant unavailable")

    monkeypatch.setattr(
        "app.services.index_service.IndexService.ensure_current",
        fail_ensure_current,
    )

    body = post_sse(client, "/api/interview-sessions/stream", json_body=CONFIG)
    assert body["turn"]["question"]
    assert body["turn"]["retrieved_context_refs"] == []


def test_create_session_uses_workspace_indexed_artifacts(client: TestClient) -> None:
    client.post(
        "/api/workspace/materials/upload",
        files={"files": ("redis.md", b"# Redis\n\nRedis cache stampede", "text/markdown")},
    )
    rebuild = client.post("/api/workspace/rebuild-index")
    assert rebuild.status_code == 200

    created = post_sse(
        client,
        "/api/interview-sessions/stream",
        json_body={
            **CONFIG,
            "target_role": "Redis Backend Engineer",
            "job_description": "Redis cache",
        },
    )

    refs = created["turn"]["retrieved_context_refs"]
    assert refs
    assert refs[0]["source_type"] == "artifact"


def test_create_session_reads_workspace_state_before_question_generation(
    client: TestClient,
    monkeypatch,
) -> None:
    captured_contexts: list[list[str]] = []

    artifacts = _artifact_service_for_user()
    artifacts.create_markdown(
        "profile/candidate.md",
        kind="candidate_profile",
        body="# 候选人画像\n\nJava 后端候选人，做过订单系统。",
    )
    artifacts.create_markdown(
        "profile/target.md",
        kind="target_profile",
        body="# 目标岗位\n\n目标是字节后端，重点关注高并发。",
    )
    artifacts.create_markdown(
        "state/mastery.md",
        kind="mastery",
        body="# 掌握状态\n\n薄弱点：Redis 缓存击穿表达不稳定。",
    )
    artifacts.create_markdown(
        "review/status.md",
        kind="review_status",
        body="# 复习状态\n\n## 当前重点\n\n- 用 30 秒讲清缓存击穿治理。\n",
    )
    _rebuild_projection(client)

    def capture_search(_self, _session, request: RetrievalRequest):
        assert request.purpose == "question_generation"
        assert request.limit == 4
        return [
            {
                "content": "题卡：Redis 缓存击穿需要互斥锁、逻辑过期和降级预案。",
                "score": 0.9,
                "source_type": "artifact",
                "source_id": "knowledge-redis",
            }
        ]

    def capture_stream_question(_self, request):
        captured_contexts.append(request.context)
        yield "请讲讲 Redis 缓存击穿治理。"

    monkeypatch.setattr(
        "app.services.workspace_retrieval_service.WorkspaceRetrievalService.search",
        capture_search,
    )
    monkeypatch.setattr(
        "app.services.model_service.ModelService.stream_question",
        capture_stream_question,
    )

    post_sse(
        client,
        "/api/interview-sessions/stream",
        json_body={**CONFIG, "language": "zh-CN", "target_role": "后端工程师"},
    )
    context_text = "\n".join(captured_contexts[0])
    assert "候选人画像" in context_text
    assert "Java 后端候选人" in context_text
    assert "目标岗位" in context_text
    assert "字节后端" in context_text
    assert "掌握状态" in context_text
    assert "Redis 缓存击穿表达不稳定" in context_text
    assert "复习状态" in context_text
    assert "30 秒讲清缓存击穿治理" in context_text
    assert "题卡：Redis 缓存击穿" in context_text


def test_create_session_auto_indexes_pending_workspace_artifacts(
    client: TestClient, monkeypatch
) -> None:
    class UploadNoopIndexService:
        def rebuild_index(self, *_args, **_kwargs) -> str:
            return "not-built"

    monkeypatch.setattr("app.api.workspace.IndexService", UploadNoopIndexService)
    upload = client.post(
        "/api/workspace/materials/upload",
        files={
            "files": (
                "redis.md",
                b"# Redis\n\nRedis cache stampede and hot key mitigation",
                "text/markdown",
            )
        },
    )
    assert upload.status_code == 200

    listed = client.get("/api/workspace/artifacts").json()["artifacts"]
    assert any(artifact["index_status"] == "pending" for artifact in listed)

    created = post_sse(
        client,
        "/api/interview-sessions/stream",
        json_body={
            **CONFIG,
            "target_role": "Redis Backend Engineer",
            "job_description": "Redis hot key",
        },
    )

    refs = created["turn"]["retrieved_context_refs"]
    assert refs
    assert refs[0]["source_type"] == "artifact"
    indexed = client.get("/api/workspace/artifacts").json()["artifacts"]
    assert any(artifact["index_status"] == "completed" for artifact in indexed)


def test_workspace_retrieval_service_uses_query_plan(client: TestClient) -> None:
    captured_requests: list[RetrievalRequest] = []
    captured_searches: list[dict[str, object]] = []

    class FakePlanner:
        def plan(self, request: RetrievalRequest):
            captured_requests.append(request)
            from app.services.retrieval_query_planner import RetrievalQueryPlan

            return RetrievalQueryPlan(
                semantic_query=f"planned {request.query}",
                artifact_kinds=("knowledge",),
                candidate_limit=6,
                final_limit=2,
                score_threshold=0.0,
                max_per_artifact=1,
                purpose=request.purpose,
            )

    class FakeStore:
        def has_searchable_content(self, collection_name: str) -> bool:
            return True

        def search(self, collection_name: str, query: str, *, limit: int, metadata_filter=None):
            from app.services.workspace_vector_store import WorkspaceVectorHit

            captured_searches.append(
                {
                    "collection_name": collection_name,
                    "query": query,
                    "limit": limit,
                    "metadata_filter": metadata_filter,
                }
            )
            return [
                WorkspaceVectorHit(
                    content="Redis cache stampede",
                    score=0.9,
                    metadata={
                        "artifact_id": "a1",
                        "artifact_kind": "knowledge",
                        "source_type": "artifact",
                        "relative_path": "knowledge/redis.md",
                    },
                )
            ]

    from app.services.workspace_retrieval_service import WorkspaceRetrievalService

    with client.app.state.session_factory() as session:
        service = WorkspaceRetrievalService(
            vector_store=FakeStore(),
            query_planner=FakePlanner(),
            user_id=1,
        )
        hits = service.search(
            session,
            RetrievalRequest(
                purpose="question_generation",
                query="Redis",
                mode="comprehensive",
                limit=2,
            ),
        )

    assert hits == [
        {
            "content": "Redis cache stampede",
            "score": 0.9,
            "source_type": "artifact",
            "source_id": "a1",
            "artifact_kind": "knowledge",
            "relative_path": "knowledge/redis.md",
        }
    ]
    assert captured_requests[0].purpose == "question_generation"
    assert captured_requests[0].query == "Redis"
    assert captured_searches[0]["query"] == "planned Redis"
    assert captured_searches[0]["limit"] == 6
    metadata_filter = captured_searches[0]["metadata_filter"]
    assert metadata_filter.must[0].key == "metadata.artifact_kind"
    assert metadata_filter.must[0].match.any == ["knowledge"]


def test_scoped_workspace_retrieval_without_active_collection_returns_empty(
    client: TestClient,
) -> None:
    class FailingStore:
        def has_searchable_content(self, collection_name: str) -> bool:
            raise AssertionError(f"should not search default collection {collection_name}")

    from app.services.workspace_retrieval_service import WorkspaceRetrievalService

    with client.app.state.session_factory() as session:
        user = session.get(models.User, 1)
        assert user is not None
        user.settings_json = {**(user.settings_json or {}), "active_collection": ""}
        session.commit()

    with client.app.state.session_factory() as session:
        service = WorkspaceRetrievalService(
            vector_store=FailingStore(),
            user_id=1,
        )
        hits = service.search(
            session,
            RetrievalRequest(
                purpose="question_generation",
                query="Redis",
                mode="comprehensive",
                limit=2,
            ),
        )

    assert hits == []


def test_natural_language_target_context_drives_workspace_retrieval(
    client: TestClient,
    monkeypatch,
) -> None:
    queries: list[str] = []
    purposes: list[str] = []

    def capture_search(_self, _session, request: RetrievalRequest):
        assert request.limit == 4
        purposes.append(request.purpose)
        queries.append(request.query)
        return []

    monkeypatch.setattr(
        "app.services.workspace_retrieval_service.WorkspaceRetrievalService.search",
        capture_search,
    )
    config = {
        **DEFAULT_QWEN_CONFIG,
        "extra_prompt": "面试字节后端岗位，JD 关注缓存和高并发。",
        "language": "zh-CN",
    }

    created = post_sse(client, "/api/interview-sessions/stream", json_body=config)
    session_id = created["session"]["id"]
    post_sse(
        client,
        f"/api/interview-sessions/{session_id}/answer/stream",
        json_body={"answer": "我会结合 Redis、限流和服务拆分说明。"},
    )
    post_sse(
        client,
        f"/api/interview-sessions/{session_id}/follow-up-answer/stream",
        json_body={"answer": "我会补充超时、降级和监控告警。"},
    )
    post_sse(client, f"/api/interview-sessions/{session_id}/next-question/stream")

    assert queries[0] == "面试字节后端岗位，JD 关注缓存和高并发。"
    assert "面试字节后端岗位，JD 关注缓存和高并发。" in queries[1]
    assert "我会结合 Redis、限流和服务拆分说明。" in queries[1]
    assert "我会补充超时、降级和监控告警。" in queries[2]
    assert queries[3] == "面试字节后端岗位，JD 关注缓存和高并发。 round 2"
    assert purposes == [
        "question_generation",
        "answer_feedback",
        "follow_up_feedback",
        "question_generation",
    ]


def test_answer_feedback_follow_up_and_next_question(client: TestClient) -> None:
    created = post_sse(client, "/api/interview-sessions/stream", json_body=CONFIG)
    session_id = created["session"]["id"]

    body = post_sse(
        client,
        f"/api/interview-sessions/{session_id}/answer/stream",
        json_body={
            "answer": "I would design services around clear repository and service boundaries."
        },
    )
    assert body["feedback"]
    assert isinstance(body["missing_points"], list)
    assert body["follow_up_question"]
    assert isinstance(body["weaknesses"], list)
    assert isinstance(body["review_suggestions"], list)

    follow_up_body = post_sse(
        client,
        f"/api/interview-sessions/{session_id}/follow-up-answer/stream",
        json_body={"answer": "I would add retries, timeouts, and structured errors."},
    )
    assert follow_up_body["feedback"]
    assert isinstance(follow_up_body["missing_points"], list)
    assert isinstance(follow_up_body["weaknesses"], list)
    assert isinstance(follow_up_body["review_suggestions"], list)

    next_question = post_sse(
        client,
        f"/api/interview-sessions/{session_id}/next-question/stream",
    )
    assert next_question["turn"]["round_index"] == 2


def test_answer_feedback_uses_workspace_retrieval_context(
    client: TestClient,
    monkeypatch,
) -> None:
    from app.schemas.modeling import AnswerEvaluationResult

    captured_contexts: list[list[str]] = []
    captured_queries: list[str] = []
    captured_purposes: list[str] = []
    artifacts = _artifact_service_for_user()
    artifacts.create_markdown(
        "profile/candidate.md",
        kind="candidate_profile",
        body="# 候选人画像\n\n项目：订单缓存系统，使用 Redis 降低数据库压力。",
    )
    artifacts.create_markdown(
        "state/mastery.md",
        kind="mastery",
        body="# 掌握状态\n\n薄弱点：缓存击穿和降级预案讲得不稳定。",
    )
    _rebuild_projection(client)

    def capture_search(_self, _session, request: RetrievalRequest):
        captured_purposes.append(request.purpose)
        captured_queries.append(request.query)
        assert request.limit == 4
        if request.purpose != "answer_feedback":
            return []
        if "Redis cache stampede" in request.query and "I use mutex locks" in request.query:
            return [
                {
                    "content": "Use mutex locks and logical expiration for cache breakdown.",
                    "score": 0.91,
                    "source_type": "artifact",
                    "source_id": "knowledge-redis",
                }
            ]
        return []

    def capture_stream_answer_evaluation(_self, request):
        captured_contexts.append(request.context)
        result = AnswerEvaluationResult(
            feedback="Uses retrieved context.",
            missing_points=[],
            follow_up_question="",
            weaknesses=[],
            review_suggestions=[],
            better_answer="Better: use mutex locks, logical expiration, and a degradation plan.",
            mastery_change="basic -> fluent if the answer includes tradeoffs.",
            should_write_weakness=True,
            should_write_high_frequency=True,
            tested_points=["Redis cache stampede", "degradation plan"],
        )
        yield result.model_dump_json()

    monkeypatch.setattr(
        "app.services.workspace_retrieval_service.WorkspaceRetrievalService.search",
        capture_search,
    )
    monkeypatch.setattr(
        "app.services.model_service.ModelService.stream_answer_evaluation",
        capture_stream_answer_evaluation,
    )

    created = post_sse(
        client,
        "/api/interview-sessions/stream",
        json_body={
            **CONFIG,
            "target_role": "Redis Backend Engineer",
            "job_description": "Redis cache stampede",
        },
    )
    session_id = created["session"]["id"]

    body = post_sse(
        client,
        f"/api/interview-sessions/{session_id}/answer/stream",
        json_body={"answer": "I use mutex locks and logical expiration."},
    )

    assert body["better_answer"].startswith("Better:")
    assert body["mastery_change"] == "basic -> fluent if the answer includes tradeoffs."
    assert body["should_write_weakness"] is True
    assert body["should_write_high_frequency"] is True
    assert body["tested_points"] == ["Redis cache stampede", "degradation plan"]
    context_text = "\n".join(captured_contexts[0])
    assert "候选人画像" in context_text
    assert "订单缓存系统" in context_text
    assert "掌握状态" in context_text
    assert "缓存击穿和降级预案" in context_text
    assert "本题考察点" in context_text
    assert "Use mutex locks and logical expiration for cache breakdown." in context_text
    assert "answer_feedback" in captured_purposes
    assert any("Redis cache stampede" in query for query in captured_queries)

    artifacts = client.get("/api/workspace/artifacts").json()["artifacts"]
    kinds = {artifact["kind"] for artifact in artifacts}
    assert {"practice", "review_status", "high_frequency"}.issubset(kinds)
    practice = next(artifact for artifact in artifacts if artifact["kind"] == "practice")
    practice_detail = client.get(f"/api/workspace/artifacts/{practice['id']}").json()
    assert "I use mutex locks and logical expiration." in practice_detail["body"]
    assert "Uses retrieved context." in practice_detail["body"]
    status = next(artifact for artifact in artifacts if artifact["kind"] == "review_status")
    status_detail = client.get(f"/api/workspace/artifacts/{status['id']}").json()
    assert "## 当前重点" in status_detail["body"]
    assert "## 最近练习" in status_detail["body"]
    assert "- 练习：" in status_detail["body"]
    high_frequency = next(artifact for artifact in artifacts if artifact["kind"] == "high_frequency")
    high_frequency_detail = client.get(f"/api/workspace/artifacts/{high_frequency['id']}").json()
    assert "Redis Backend Engineer" in high_frequency_detail["body"]


def test_answer_feedback_persists_structured_fields_in_session_detail(
    client: TestClient,
    monkeypatch,
) -> None:
    from app.schemas.modeling import AnswerEvaluationResult

    def capture_stream_answer_evaluation(_self, _request):
        result = AnswerEvaluationResult(
            feedback="Good structure.",
            missing_points=["retry boundaries"],
            follow_up_question="",
            weaknesses=["Needs a clearer failure-mode story."],
            review_suggestions=["Prepare one retry incident."],
            better_answer="I would describe the failure mode, retry budget, and rollback path.",
            mastery_change="basic",
            should_write_weakness=True,
            should_write_high_frequency=True,
            tested_points=["failure handling", "retry budget"],
        )
        yield result.model_dump_json()

    monkeypatch.setattr(
        "app.services.model_service.ModelService.stream_answer_evaluation",
        capture_stream_answer_evaluation,
    )

    created = post_sse(client, "/api/interview-sessions/stream", json_body=CONFIG)
    session_id = created["session"]["id"]

    post_sse(
        client,
        f"/api/interview-sessions/{session_id}/answer/stream",
        json_body={"answer": "I add retries and rollbacks."},
    )

    detail = client.get(f"/api/interview-sessions/{session_id}").json()
    turn = detail["turns"][0]
    assert turn["better_answer"] == (
        "I would describe the failure mode, retry budget, and rollback path."
    )
    assert turn["mastery_change"] == "basic"
    assert turn["should_write_weakness"] is True
    assert turn["should_write_high_frequency"] is True
    assert turn["tested_points"] == ["failure handling", "retry budget"]


def test_follow_up_feedback_keeps_structured_fields_separate(
    client: TestClient,
    monkeypatch,
) -> None:
    from app.schemas.modeling import AnswerEvaluationResult

    def capture_stream_answer_evaluation(_self, request):
        if request.question == "What fallback would you add?":
            result = AnswerEvaluationResult(
                feedback="Follow-up feedback.",
                missing_points=["manual fallback"],
                follow_up_question="",
                weaknesses=["Follow-up weakness."],
                review_suggestions=["Review follow-up operations."],
                better_answer="Follow-up better answer.",
                mastery_change="fluent",
                should_write_weakness=False,
                should_write_high_frequency=True,
                tested_points=["follow-up fallback"],
            )
        else:
            result = AnswerEvaluationResult(
                feedback="Main feedback.",
                missing_points=["fallback path"],
                follow_up_question="What fallback would you add?",
                weaknesses=["Main weakness."],
                review_suggestions=["Review main operations."],
                better_answer="Main better answer.",
                mastery_change="basic",
                should_write_weakness=True,
                should_write_high_frequency=False,
                tested_points=["main cache stampede"],
            )
        yield result.model_dump_json()

    monkeypatch.setattr(
        "app.services.model_service.ModelService.stream_answer_evaluation",
        capture_stream_answer_evaluation,
    )

    created = post_sse(client, "/api/interview-sessions/stream", json_body=CONFIG)
    session_id = created["session"]["id"]
    answer = post_sse(
        client,
        f"/api/interview-sessions/{session_id}/answer/stream",
        json_body={"answer": "I would add mutex locks."},
    )
    follow_up = post_sse(
        client,
        f"/api/interview-sessions/{session_id}/follow-up-answer/stream",
        json_body={"answer": "I would add a manual fallback."},
    )

    assert answer["better_answer"] == "Main better answer."
    assert follow_up["better_answer"] == "Follow-up better answer."

    detail = client.get(f"/api/interview-sessions/{session_id}").json()
    turn = detail["turns"][0]
    assert turn["better_answer"] == "Main better answer."
    assert turn["mastery_change"] == "basic"
    assert turn["should_write_weakness"] is True
    assert turn["should_write_high_frequency"] is False
    assert turn["tested_points"] == ["main cache stampede"]
    assert turn["follow_up_better_answer"] == "Follow-up better answer."
    assert turn["follow_up_mastery_change"] == "fluent"
    assert turn["follow_up_should_write_weakness"] is False
    assert turn["follow_up_should_write_high_frequency"] is True
    assert turn["follow_up_tested_points"] == ["follow-up fallback"]

    workspace = get_settings().data_dir / "users" / "1" / "workspace"
    practice_files = list((workspace / "practice").glob("**/*.md"))
    assert len(practice_files) == 1
    practice_text = practice_files[0].read_text(encoding="utf-8")
    assert "**更好的面试说法**：\nMain better answer." in practice_text
    assert "**追问更好的面试说法**：\nFollow-up better answer." in practice_text


def test_weak_answer_feedback_creates_question_bank_entry(
    client: TestClient,
    monkeypatch,
) -> None:
    from app.schemas.modeling import AnswerEvaluationResult

    def stream_weak_answer(_self, _request):
        result = AnswerEvaluationResult(
            feedback="Answer misses the production tradeoffs.",
            missing_points=["hot key fallback", "degradation plan"],
            follow_up_question="How would you degrade when Redis is unavailable?",
            weaknesses=["Cache stampede answer lacks fallback details."],
            review_suggestions=["Review cache stampede operations."],
            better_answer=(
                "Use mutex locks or logical expiration, protect hot keys, and explain "
                "fallback and degradation plans."
            ),
            mastery_change="weak",
            should_write_weakness=True,
            should_write_high_frequency=True,
            tested_points=["cache stampede", "degradation"],
        )
        yield result.model_dump_json()

    monkeypatch.setattr(
        "app.services.model_service.ModelService.stream_answer_evaluation",
        stream_weak_answer,
    )

    created = post_sse(
        client,
        "/api/interview-sessions/stream",
        json_body={
            **CONFIG,
            "language": "zh-CN",
            "target_role": "后端工程师",
            "job_description": "Redis 缓存击穿",
        },
    )
    session_id = created["session"]["id"]

    post_sse(
        client,
        f"/api/interview-sessions/{session_id}/answer/stream",
        json_body={"answer": "我会加锁。"},
    )
    artifacts = client.get("/api/workspace/artifacts").json()["artifacts"]
    question_cards = [artifact for artifact in artifacts if artifact["kind"] == "question_bank"]
    assert len(question_cards) == 1
    assert question_cards[0]["relative_path"].startswith("questions/")
    detail = client.get(f"/api/workspace/artifacts/{question_cards[0]['id']}").json()
    body = detail["body"]
    assert "## 问题：" in body
    assert "### 考察点" in body
    assert "cache stampede" in body
    assert "### 标准回答" in body
    assert "Use mutex locks or logical expiration" in body
    assert "### 结合项目" in body
    assert "### 常见追问" in body
    assert "How would you degrade when Redis is unavailable?" in body
    assert "### 易错点" in body
    assert "hot key fallback" in body
    assert "### 复习状态" in body
    assert "weak" in body


def test_project_deep_dive_includes_project_artifacts_before_question_generation(
    client: TestClient,
    monkeypatch,
) -> None:
    captured_contexts: list[list[str]] = []
    artifacts = _artifact_service_for_user()
    artifacts.create_markdown(
        "projects/order-cache.md",
        kind="project",
        body="# 订单缓存项目\n\n我负责订单缓存一致性、热点 key 保护和降级预案。",
        origin="human",
        edited_by="user",
    )
    _rebuild_projection(client)

    def capture_stream_question(_self, request):
        captured_contexts.append(request.context)
        yield "请结合订单缓存项目讲一次热点 key 保护。"

    monkeypatch.setattr(
        "app.services.model_service.ModelService.stream_question",
        capture_stream_question,
    )

    post_sse(
        client,
        "/api/interview-sessions/stream",
        json_body={
            **CONFIG,
            "language": "zh-CN",
            "mode": "project_deep_dive",
            "target_role": "后端工程师",
        },
    )

    context_text = "\n".join(captured_contexts[0])
    assert "项目材料" in context_text
    assert "订单缓存项目" in context_text
    assert "热点 key 保护" in context_text


def test_stream_answer_feedback_returns_delta_and_result(client: TestClient) -> None:
    created = post_sse(client, "/api/interview-sessions/stream", json_body=CONFIG)
    session_id = created["session"]["id"]

    response = client.post(
        f"/api/interview-sessions/{session_id}/answer/stream",
        json={"answer": "I would design clear service boundaries."},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    body = response.text
    assert "event: delta" in body
    assert "event: result" in body
    assert '"feedback"' in body
    assert "The answer shows" in body

    duplicate = client.post(
        f"/api/interview-sessions/{session_id}/answer/stream",
        json={"answer": "duplicate"},
    )
    assert duplicate.status_code == 409


def test_stream_follow_up_answer_returns_delta_and_result(client: TestClient) -> None:
    created = post_sse(client, "/api/interview-sessions/stream", json_body=CONFIG)
    session_id = created["session"]["id"]
    post_sse(
        client,
        f"/api/interview-sessions/{session_id}/answer/stream",
        json_body={"answer": "I would design clear service boundaries."},
    )

    response = client.post(
        f"/api/interview-sessions/{session_id}/follow-up-answer/stream",
        json={"answer": "I would add retries, timeouts, and dashboards."},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    body = response.text
    assert "event: delta" in body
    assert "event: result" in body
    assert '"feedback"' in body
    assert "The answer shows" in body


def test_stream_next_question_returns_delta_and_result(client: TestClient) -> None:
    created = post_sse(client, "/api/interview-sessions/stream", json_body=CONFIG)
    session_id = created["session"]["id"]
    post_sse(
        client,
        f"/api/interview-sessions/{session_id}/answer/stream",
        json_body={"answer": "I would design clear service boundaries."},
    )

    response = client.post(f"/api/interview-sessions/{session_id}/next-question/stream")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    body = response.text
    assert "event: delta" in body
    assert "event: result" in body
    assert '"turn"' in body
    assert '"round_index":2' in body


def test_next_question_accepts_empty_body_with_non_json_content_type(
    client: TestClient,
) -> None:
    created = post_sse(client, "/api/interview-sessions/stream", json_body=CONFIG)
    session_id = created["session"]["id"]
    post_sse(
        client,
        f"/api/interview-sessions/{session_id}/answer/stream",
        json_body={"answer": "I would design clear service boundaries."},
    )

    response = client.post(
        f"/api/interview-sessions/{session_id}/next-question/stream",
        content=b"",
        headers={"content-type": "text/plain;charset=UTF-8"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert '"round_index":2' in response.text


def test_chinese_session_uses_chinese_question_and_feedback(client: TestClient) -> None:
    config = {**CONFIG, "language": "zh-CN", "target_role": "后端工程师", "target_company": "字节"}
    body = post_sse(client, "/api/interview-sessions/stream", json_body=config)
    assert "请" in body["turn"]["question"] or "如何" in body["turn"]["question"]
    session_id = body["session"]["id"]

    feedback = post_sse(
        client,
        f"/api/interview-sessions/{session_id}/answer/stream",
        json_body={"answer": "我会先明确边界，再设计服务和故障处理。"},
    )

    assert "回答" in feedback["feedback"]
    assert "？" in feedback["follow_up_question"]


def test_completed_session_rejects_answer(client: TestClient) -> None:
    created = post_sse(client, "/api/interview-sessions/stream", json_body=CONFIG)
    session_id = created["session"]["id"]
    post_sse(client, f"/api/interview-sessions/{session_id}/finish/stream")
    response = client.post(
        f"/api/interview-sessions/{session_id}/answer/stream",
        json={"answer": "late"},
    )
    assert response.status_code == 409


def test_next_question_requires_answer_but_does_not_force_finish(client: TestClient) -> None:
    config = {**CONFIG, "target_rounds": 1}
    created = post_sse(client, "/api/interview-sessions/stream", json_body=config)
    session_id = created["session"]["id"]

    unanswered = client.post(f"/api/interview-sessions/{session_id}/next-question/stream")
    assert unanswered.status_code == 409
    assert unanswered.json()["detail"]["code"] == "current_turn_unanswered"

    post_sse(
        client,
        f"/api/interview-sessions/{session_id}/answer/stream",
        json_body={"answer": "A concrete answer."},
    )
    next_question = post_sse(
        client,
        f"/api/interview-sessions/{session_id}/next-question/stream",
    )
    assert next_question["turn"]["round_index"] == 2


def test_answers_cannot_be_submitted_twice(client: TestClient) -> None:
    created = post_sse(client, "/api/interview-sessions/stream", json_body=CONFIG)
    session_id = created["session"]["id"]

    follow_up_before_answer = client.post(
        f"/api/interview-sessions/{session_id}/follow-up-answer/stream",
        json={"answer": "Too early."},
    )
    assert follow_up_before_answer.status_code == 409
    assert follow_up_before_answer.json()["detail"]["code"] == "main_answer_required"

    post_sse(
        client,
        f"/api/interview-sessions/{session_id}/answer/stream",
        json_body={"answer": "First answer."},
    )
    duplicate_answer = client.post(
        f"/api/interview-sessions/{session_id}/answer/stream",
        json={"answer": "Replacement answer."},
    )
    assert duplicate_answer.status_code == 409
    assert duplicate_answer.json()["detail"]["code"] == "answer_already_submitted"

    post_sse(
        client,
        f"/api/interview-sessions/{session_id}/follow-up-answer/stream",
        json_body={"answer": "First follow-up."},
    )
    duplicate_follow_up = client.post(
        f"/api/interview-sessions/{session_id}/follow-up-answer/stream",
        json={"answer": "Replacement follow-up."},
    )
    assert duplicate_follow_up.status_code == 409
    assert duplicate_follow_up.json()["detail"]["code"] == "follow_up_already_submitted"


def test_user_cannot_read_other_users_interview(client: TestClient) -> None:
    alice = dict(client.headers)
    created = post_sse(client, "/api/interview-sessions/stream", json_body=CONFIG)
    session_id = created["session"]["id"]

    bob = _register(client, "bob")
    client.headers.update(bob)

    detail = client.get(f"/api/interview-sessions/{session_id}")
    answer = client.post(
        f"/api/interview-sessions/{session_id}/answer/stream",
        json={"answer": "I should not be able to answer this."},
    )
    finish = client.post(f"/api/interview-sessions/{session_id}/finish/stream")

    assert detail.status_code == 404
    assert answer.status_code == 404
    assert sse_error(finish)["status_code"] == 404

    client.headers.update(alice)
    own_detail = client.get(f"/api/interview-sessions/{session_id}")
    assert own_detail.status_code == 200
