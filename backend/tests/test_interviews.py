from fastapi.testclient import TestClient


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

    created = client.post("/api/interview-sessions", json=CONFIG)
    assert created.status_code == 200
    body = created.json()
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


def test_list_interview_sessions_includes_history_context(client: TestClient) -> None:
    active = client.post(
        "/api/interview-sessions",
        json={**CONFIG, "extra_prompt": "Active backend interview", "target_rounds": 2},
    ).json()
    active_session_id = active["session"]["id"]
    completed = client.post(
        "/api/interview-sessions",
        json={**CONFIG, "extra_prompt": "Completed backend interview", "target_rounds": 1},
    ).json()
    completed_session_id = completed["session"]["id"]
    client.post(
        f"/api/interview-sessions/{completed_session_id}/answer",
        json={"answer": "I would give a concise architecture answer."},
    )
    finished = client.post(f"/api/interview-sessions/{completed_session_id}/finish")
    assert finished.status_code == 200

    listed = client.get("/api/interview-sessions")

    assert listed.status_code == 200
    sessions = listed.json()["sessions"]
    assert [item["session"]["id"] for item in sessions] == [completed_session_id, active_session_id]
    assert sessions[0]["resumable"] is False
    assert sessions[0]["config"]["extra_prompt"] == "Completed backend interview"
    assert sessions[0]["turns"][0]["answer"] == "I would give a concise architecture answer."
    assert sessions[1]["resumable"] is True
    assert sessions[1]["config"]["extra_prompt"] == "Active backend interview"
    assert sessions[1]["turns"][0]["question"]


def test_stream_finish_returns_summary_delta_and_result(client: TestClient) -> None:
    created = client.post(
        "/api/interview-sessions",
        json={**CONFIG, "target_rounds": 1},
    ).json()
    session_id = created["session"]["id"]
    client.post(
        f"/api/interview-sessions/{session_id}/answer",
        json={"answer": "I would explain clear service boundaries."},
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
    def fail_embed_texts(_self, _texts):
        raise AssertionError("embedding should not run for an empty library")

    monkeypatch.setattr("app.services.rag_service.RagService.embed_texts", fail_embed_texts)

    created = client.post("/api/interview-sessions", json=CONFIG)

    assert created.status_code == 200
    body = created.json()
    assert body["turn"]["question"]
    assert body["turn"]["retrieved_context_refs"] == []


def test_create_session_uses_workspace_indexed_artifacts(client: TestClient) -> None:
    client.post(
        "/api/workspace/materials/upload",
        files={"files": ("redis.md", b"# Redis\n\nRedis cache stampede", "text/markdown")},
    )
    rebuild = client.post("/api/workspace/rebuild-index")
    assert rebuild.status_code == 200

    created = client.post(
        "/api/interview-sessions",
        json={**CONFIG, "target_role": "Redis Backend Engineer", "job_description": "Redis cache"},
    )

    assert created.status_code == 200
    refs = created.json()["turn"]["retrieved_context_refs"]
    assert refs
    assert refs[0]["source_type"] == "artifact"


def test_create_session_reads_workspace_state_before_question_generation(
    client: TestClient,
    monkeypatch,
) -> None:
    captured_contexts: list[list[str]] = []

    artifacts = client.app.state.artifact_service
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
    client.post("/api/workspace/rebuild-projection")

    def capture_search(_self, _session, query: str, limit: int):
        assert limit == 4
        return [
            {
                "content": "题卡：Redis 缓存击穿需要互斥锁、逻辑过期和降级预案。",
                "score": 0.9,
                "source_type": "artifact",
                "source_id": "knowledge-redis",
            }
        ]

    def capture_generate_question(_self, request):
        captured_contexts.append(request.context)
        return "请讲讲 Redis 缓存击穿治理。"

    monkeypatch.setattr(
        "app.services.workspace_retrieval_service.WorkspaceRetrievalService.search",
        capture_search,
    )
    monkeypatch.setattr(
        "app.services.model_service.ModelService.generate_question",
        capture_generate_question,
    )

    created = client.post(
        "/api/interview-sessions",
        json={**CONFIG, "language": "zh-CN", "target_role": "后端工程师"},
    )

    assert created.status_code == 200
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
        def rebuild_index(self, _session_factory, _workspace, _repository) -> str:
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

    created = client.post(
        "/api/interview-sessions",
        json={**CONFIG, "target_role": "Redis Backend Engineer", "job_description": "Redis hot key"},
    )

    assert created.status_code == 200
    refs = created.json()["turn"]["retrieved_context_refs"]
    assert refs
    assert refs[0]["source_type"] == "artifact"
    indexed = client.get("/api/workspace/artifacts").json()["artifacts"]
    assert any(artifact["index_status"] == "completed" for artifact in indexed)


def test_natural_language_target_context_drives_workspace_retrieval(
    client: TestClient,
    monkeypatch,
) -> None:
    queries: list[str] = []

    def capture_search(_self, _session, query: str, limit: int):
        assert limit == 4
        queries.append(query)
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

    created = client.post("/api/interview-sessions", json=config)
    assert created.status_code == 200
    session_id = created.json()["session"]["id"]
    answer = client.post(
        f"/api/interview-sessions/{session_id}/answer",
        json={"answer": "我会结合 Redis、限流和服务拆分说明。"},
    )
    assert answer.status_code == 200
    next_question = client.post(f"/api/interview-sessions/{session_id}/next-question")
    assert next_question.status_code == 200

    assert queries[0] == "面试字节后端岗位，JD 关注缓存和高并发。"
    assert "面试字节后端岗位，JD 关注缓存和高并发。" in queries[1]
    assert "我会结合 Redis、限流和服务拆分说明。" in queries[1]
    assert queries[2] == "面试字节后端岗位，JD 关注缓存和高并发。 round 2"


def test_answer_feedback_follow_up_and_next_question(client: TestClient) -> None:
    created = client.post("/api/interview-sessions", json=CONFIG).json()
    session_id = created["session"]["id"]

    answer = client.post(
        f"/api/interview-sessions/{session_id}/answer",
        json={"answer": "I would design services around clear repository and service boundaries."},
    )
    assert answer.status_code == 200
    body = answer.json()
    assert body["feedback"]
    assert isinstance(body["missing_points"], list)
    assert body["follow_up_question"]
    assert isinstance(body["weaknesses"], list)
    assert isinstance(body["review_suggestions"], list)

    follow_up = client.post(
        f"/api/interview-sessions/{session_id}/follow-up-answer",
        json={"answer": "I would add retries, timeouts, and structured errors."},
    )
    assert follow_up.status_code == 200
    follow_up_body = follow_up.json()
    assert follow_up_body["feedback"]
    assert isinstance(follow_up_body["missing_points"], list)
    assert isinstance(follow_up_body["weaknesses"], list)
    assert isinstance(follow_up_body["review_suggestions"], list)

    next_question = client.post(f"/api/interview-sessions/{session_id}/next-question")
    assert next_question.status_code == 200
    assert next_question.json()["turn"]["round_index"] == 2


def test_answer_feedback_uses_workspace_retrieval_context(
    client: TestClient,
    monkeypatch,
) -> None:
    from app.services.model_service import AnswerEvaluationResult

    captured_contexts: list[list[str]] = []
    captured_queries: list[str] = []
    artifacts = client.app.state.artifact_service
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
    client.post("/api/workspace/rebuild-projection")

    def capture_search(_self, _session, query: str, limit: int):
        captured_queries.append(query)
        assert limit == 4
        if "Redis cache stampede" in query and "I use mutex locks" in query:
            return [
                {
                    "content": "Use mutex locks and logical expiration for cache breakdown.",
                    "score": 0.91,
                    "source_type": "artifact",
                    "source_id": "knowledge-redis",
                }
            ]
        return []

    def capture_evaluate_answer(_self, request):
        captured_contexts.append(request.context)
        return AnswerEvaluationResult(
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

    monkeypatch.setattr(
        "app.services.workspace_retrieval_service.WorkspaceRetrievalService.search",
        capture_search,
    )
    monkeypatch.setattr(
        "app.services.model_service.ModelService.evaluate_answer",
        capture_evaluate_answer,
    )

    created = client.post(
        "/api/interview-sessions",
        json={
            **CONFIG,
            "target_role": "Redis Backend Engineer",
            "job_description": "Redis cache stampede",
        },
    ).json()
    session_id = created["session"]["id"]

    response = client.post(
        f"/api/interview-sessions/{session_id}/answer",
        json={"answer": "I use mutex locks and logical expiration."},
    )

    assert response.status_code == 200
    body = response.json()
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
    high_frequency = next(artifact for artifact in artifacts if artifact["kind"] == "high_frequency")
    high_frequency_detail = client.get(f"/api/workspace/artifacts/{high_frequency['id']}").json()
    assert "Redis Backend Engineer" in high_frequency_detail["body"]


def test_weak_answer_feedback_creates_question_bank_entry(
    client: TestClient,
    monkeypatch,
) -> None:
    from app.services.model_service import AnswerEvaluationResult

    def evaluate_weak_answer(_self, _request):
        return AnswerEvaluationResult(
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

    monkeypatch.setattr(
        "app.services.model_service.ModelService.evaluate_answer",
        evaluate_weak_answer,
    )

    created = client.post(
        "/api/interview-sessions",
        json={
            **CONFIG,
            "language": "zh-CN",
            "target_role": "后端工程师",
            "job_description": "Redis 缓存击穿",
        },
    ).json()
    session_id = created["session"]["id"]

    response = client.post(
        f"/api/interview-sessions/{session_id}/answer",
        json={"answer": "我会加锁。"},
    )

    assert response.status_code == 200
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
    artifacts = client.app.state.artifact_service
    artifacts.create_markdown(
        "projects/order-cache.md",
        kind="project",
        body="# 订单缓存项目\n\n我负责订单缓存一致性、热点 key 保护和降级预案。",
        origin="human",
        edited_by="user",
    )
    client.post("/api/workspace/rebuild-projection")

    def capture_generate_question(_self, request):
        captured_contexts.append(request.context)
        return "请结合订单缓存项目讲一次热点 key 保护。"

    monkeypatch.setattr(
        "app.services.model_service.ModelService.generate_question",
        capture_generate_question,
    )

    created = client.post(
        "/api/interview-sessions",
        json={
            **CONFIG,
            "language": "zh-CN",
            "mode": "project_deep_dive",
            "target_role": "后端工程师",
        },
    )

    assert created.status_code == 200
    context_text = "\n".join(captured_contexts[0])
    assert "项目材料" in context_text
    assert "订单缓存项目" in context_text
    assert "热点 key 保护" in context_text


def test_stream_answer_feedback_returns_delta_and_result(client: TestClient) -> None:
    created = client.post("/api/interview-sessions", json=CONFIG).json()
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
        f"/api/interview-sessions/{session_id}/answer",
        json={"answer": "duplicate"},
    )
    assert duplicate.status_code == 409


def test_stream_follow_up_answer_returns_delta_and_result(client: TestClient) -> None:
    created = client.post("/api/interview-sessions", json=CONFIG).json()
    session_id = created["session"]["id"]
    client.post(
        f"/api/interview-sessions/{session_id}/answer",
        json={"answer": "I would design clear service boundaries."},
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
    created = client.post("/api/interview-sessions", json=CONFIG).json()
    session_id = created["session"]["id"]
    client.post(
        f"/api/interview-sessions/{session_id}/answer",
        json={"answer": "I would design clear service boundaries."},
    )

    response = client.post(f"/api/interview-sessions/{session_id}/next-question/stream")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    body = response.text
    assert "event: delta" in body
    assert "event: result" in body
    assert '"turn"' in body
    assert '"round_index":2' in body


def test_next_question_accepts_empty_body_with_non_json_content_type(client: TestClient) -> None:
    created = client.post("/api/interview-sessions", json=CONFIG).json()
    session_id = created["session"]["id"]
    client.post(
        f"/api/interview-sessions/{session_id}/answer",
        json={"answer": "I would design clear service boundaries."},
    )

    response = client.post(
        f"/api/interview-sessions/{session_id}/next-question",
        content=b"",
        headers={"content-type": "text/plain;charset=UTF-8"},
    )

    assert response.status_code == 200
    assert response.json()["turn"]["round_index"] == 2


def test_stream_next_question_accepts_empty_body_with_non_json_content_type(
    client: TestClient,
) -> None:
    created = client.post("/api/interview-sessions", json=CONFIG).json()
    session_id = created["session"]["id"]
    client.post(
        f"/api/interview-sessions/{session_id}/answer",
        json={"answer": "I would design clear service boundaries."},
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
    created = client.post("/api/interview-sessions", json=config)
    assert created.status_code == 200
    body = created.json()
    assert "请" in body["turn"]["question"] or "如何" in body["turn"]["question"]
    session_id = body["session"]["id"]

    answer = client.post(
        f"/api/interview-sessions/{session_id}/answer",
        json={"answer": "我会先明确边界，再设计服务和故障处理。"},
    )

    assert answer.status_code == 200
    feedback = answer.json()
    assert "回答" in feedback["feedback"]
    assert "？" in feedback["follow_up_question"]


def test_completed_session_rejects_answer(client: TestClient) -> None:
    created = client.post("/api/interview-sessions", json=CONFIG).json()
    session_id = created["session"]["id"]
    client.post(f"/api/interview-sessions/{session_id}/finish")
    response = client.post(f"/api/interview-sessions/{session_id}/answer", json={"answer": "late"})
    assert response.status_code == 409


def test_next_question_requires_answer_but_does_not_force_finish(client: TestClient) -> None:
    config = {**CONFIG, "target_rounds": 1}
    created = client.post("/api/interview-sessions", json=config).json()
    session_id = created["session"]["id"]

    unanswered = client.post(f"/api/interview-sessions/{session_id}/next-question")
    assert unanswered.status_code == 409
    assert unanswered.json()["detail"]["code"] == "current_turn_unanswered"

    client.post(
        f"/api/interview-sessions/{session_id}/answer",
        json={"answer": "A concrete answer."},
    )
    next_question = client.post(f"/api/interview-sessions/{session_id}/next-question")
    assert next_question.status_code == 200
    assert next_question.json()["turn"]["round_index"] == 2


def test_answers_cannot_be_submitted_twice(client: TestClient) -> None:
    created = client.post("/api/interview-sessions", json=CONFIG).json()
    session_id = created["session"]["id"]

    follow_up_before_answer = client.post(
        f"/api/interview-sessions/{session_id}/follow-up-answer",
        json={"answer": "Too early."},
    )
    assert follow_up_before_answer.status_code == 409
    assert follow_up_before_answer.json()["detail"]["code"] == "main_answer_required"

    first_answer = client.post(
        f"/api/interview-sessions/{session_id}/answer",
        json={"answer": "First answer."},
    )
    assert first_answer.status_code == 200
    duplicate_answer = client.post(
        f"/api/interview-sessions/{session_id}/answer",
        json={"answer": "Replacement answer."},
    )
    assert duplicate_answer.status_code == 409
    assert duplicate_answer.json()["detail"]["code"] == "answer_already_submitted"

    first_follow_up = client.post(
        f"/api/interview-sessions/{session_id}/follow-up-answer",
        json={"answer": "First follow-up."},
    )
    assert first_follow_up.status_code == 200
    duplicate_follow_up = client.post(
        f"/api/interview-sessions/{session_id}/follow-up-answer",
        json={"answer": "Replacement follow-up."},
    )
    assert duplicate_follow_up.status_code == 409
    assert duplicate_follow_up.json()["detail"]["code"] == "follow_up_already_submitted"
