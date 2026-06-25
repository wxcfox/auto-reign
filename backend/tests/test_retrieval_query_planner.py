from app.services.retrieval_query_planner import RetrievalQueryPlanner, RetrievalRequest


def test_question_generation_plan_prefers_interview_material() -> None:
    plan = RetrievalQueryPlanner().plan(
        RetrievalRequest(
            purpose="question_generation",
            query="字节后端岗位 JD 关注 Redis 高并发",
            mode="comprehensive",
            limit=4,
        )
    )

    assert plan.semantic_query == "字节后端岗位 JD 关注 Redis 高并发"
    assert plan.candidate_limit == 12
    assert plan.final_limit == 4
    assert plan.score_threshold == 0.25
    assert plan.max_per_artifact == 2
    assert plan.purpose == "question_generation"
    assert plan.artifact_kinds == (
        "question_bank",
        "knowledge",
        "project",
        "high_frequency",
        "source",
        "extracted",
    )


def test_project_deep_dive_plan_filters_projects_first() -> None:
    plan = RetrievalQueryPlanner().plan(
        RetrievalRequest(
            purpose="question_generation",
            query="订单缓存项目",
            mode="project_deep_dive",
            limit=4,
        )
    )

    assert plan.semantic_query == "projects 项目 项目经历 订单缓存项目"
    assert plan.artifact_kinds == ("project", "knowledge", "practice", "source", "extracted")
    assert plan.candidate_limit == 12
    assert plan.final_limit == 4
    assert plan.score_threshold == 0.25
    assert plan.max_per_artifact == 2


def test_answer_feedback_plan_uses_answer_context() -> None:
    plan = RetrievalQueryPlanner().plan(
        RetrievalRequest(
            purpose="answer_feedback",
            query="Redis 缓存击穿 我使用互斥锁",
            mode="comprehensive",
            limit=4,
        )
    )

    assert plan.semantic_query == "Redis 缓存击穿 我使用互斥锁"
    assert plan.artifact_kinds == (
        "knowledge",
        "question_bank",
        "project",
        "high_frequency",
        "practice",
        "source",
        "extracted",
    )
    assert plan.score_threshold == 0.25


def test_follow_up_feedback_plan_prefers_question_and_practice_context() -> None:
    plan = RetrievalQueryPlanner().plan(
        RetrievalRequest(
            purpose="follow_up_feedback",
            query="为什么不用逻辑过期",
            limit=3,
        )
    )

    assert plan.semantic_query == "为什么不用逻辑过期"
    assert plan.artifact_kinds == ("question_bank", "practice", "knowledge", "source", "extracted")
    assert plan.candidate_limit == 9
    assert plan.final_limit == 3
    assert plan.score_threshold == 0.25


def test_generic_plan_normalizes_blank_query_and_minimum_limit() -> None:
    plan = RetrievalQueryPlanner().plan(
        RetrievalRequest(
            purpose="generic",
            query="  Redis  ",
            limit=0,
        )
    )

    assert plan.semantic_query == "Redis"
    assert plan.artifact_kinds == (
        "question_bank",
        "knowledge",
        "project",
        "high_frequency",
        "source",
        "extracted",
    )
    assert plan.candidate_limit == 3
    assert plan.final_limit == 1
