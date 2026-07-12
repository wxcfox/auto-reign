from app.schemas.modeling import InterviewReportResult, LearningNoteSummaryResult
from app.services.content_renderer import (
    render_high_frequency,
    render_interview_report,
    render_learning_note_card,
    render_real_interview_record,
    render_review_status,
    section_items,
)
from app.services.markdown_utils import markdown_sections


def test_english_workspace_renderers_do_not_use_chinese_scaffolding() -> None:
    summary = LearningNoteSummaryResult(
        title="Redis cache stampede",
        summary="A hot key can overload the database.",
        key_points=["Use single flight or logical expiration."],
        interview_takeaways=["Explain the failure mode before the mitigation."],
        follow_up_questions=["What happens when Redis is unavailable?"],
    )

    card = render_learning_note_card("I learned about hot keys.", summary, "en")
    record = render_real_interview_record(
        "Interviewer: How do you handle hot keys?",
        ["How do you handle hot keys?"],
        ["I omitted degradation."],
        "en",
    )
    high_frequency = render_high_frequency(
        ["How do you handle hot keys?"],
        ["Missing degradation plan"],
        "en",
    )
    status = render_review_status(
        ["Prepare a degradation plan"],
        ["Organized Redis notes"],
        ["Practiced hot keys"],
        "en",
    )

    combined = "\n".join([card, record, high_frequency, status])
    assert "My understanding" in combined
    assert "Real Interview Record" in combined
    assert "Current Focus" in combined
    assert "我的理解" not in combined
    assert "真实面试记录" not in combined
    assert "当前重点" not in combined


def test_interview_report_is_rendered_from_validated_fields() -> None:
    result = InterviewReportResult(
        summary="回答结构清晰。",
        strong_signals=["能说明技术取舍"],
        missing_points=["缺少降级方案"],
        weaknesses=["故障处理"],
        review_focus=["准备一个线上故障案例"],
        source_context=["practice:session-1"],
    )

    report = render_interview_report(result, "zh-CN")

    assert report.startswith("# 面试复盘报告")
    assert "## 表现较好\n\n- 能说明技术取舍" in report
    assert "## 来源上下文\n\n- practice:session-1" in report


def test_section_items_accepts_previous_language_heading() -> None:
    chinese_sections = markdown_sections("# 复习状态\n\n## 最近整理\n\n- Redis 缓存\n")
    english_sections = markdown_sections("# Review Status\n\n## Recent Learning\n\n- Redis cache\n")

    assert section_items(chinese_sections, "en", "recent_learning") == ["Redis 缓存"]
    assert section_items(english_sections, "zh-CN", "recent_learning") == ["Redis cache"]
