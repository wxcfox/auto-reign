from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC

from app.schemas.modeling import InterviewReportResult, LearningNoteSummaryResult
from app.services.markdown_utils import (
    indented_bullet_list,
    indented_text,
    markdown_list_items,
    plain_bullet_list,
)


@dataclass(frozen=True)
class ContentLabels:
    colon: str
    clause_separator: str
    my_understanding: str
    corrections: str
    interview_expression: str
    confusing_points: str
    no_confusing_points: str
    follow_up_questions: str
    default_follow_up: str
    report_title: str
    summary: str
    strong_signals: str
    missing_points: str
    weaknesses: str
    review_focus: str
    source_context: str
    none: str
    learning_input: str
    real_interview_record: str
    original_record: str
    extracted_questions: str
    weak_signals: str
    high_frequency_title: str
    real_interview_questions: str
    exposed_issues: str
    review_status: str
    current_focus: str
    recent_learning: str
    recent_practice: str
    auto_focus: str
    organize_card: str
    review_question: str
    address_weakness: str
    prepare_answer: str
    correct_issue: str
    organize_record: str
    practice_title: str
    session: str
    started_at: str
    interview_requirement: str
    default_requirement: str
    round: str
    question: str
    answer: str
    feedback: str
    review_suggestions: str
    better_answer: str
    tested_points: str
    mastery_change: str
    follow_up: str
    follow_up_answer: str
    follow_up_feedback: str
    persistence_suggestion: str
    write_weakness: str
    write_high_frequency: str
    practice_prefix: str
    continue_practice: str
    question_points: str
    standard_answer: str
    project_context: str
    common_follow_up: str
    error_points: str
    project_fallback: str


_LABELS = {
    "en": ContentLabels(
        colon=": ",
        clause_separator="; ",
        my_understanding="My understanding",
        corrections="Corrections and additions",
        interview_expression="30-second interview expression",
        confusing_points="Potential confusion",
        no_confusing_points="No clear confusion yet; add evidence through practice.",
        follow_up_questions="Follow-up questions",
        default_follow_up="How would you apply this in a real project?",
        report_title="Interview Review",
        summary="Summary",
        strong_signals="Strong Signals",
        missing_points="Missing Points",
        weaknesses="Weaknesses",
        review_focus="Review Focus",
        source_context="Source Context",
        none="None yet.",
        learning_input="Learning input",
        real_interview_record="Real Interview Record",
        original_record="Original Record",
        extracted_questions="Extracted Questions",
        weak_signals="Weak Signals",
        high_frequency_title="High-frequency Questions and Weaknesses",
        real_interview_questions="High-frequency Real Interview Questions",
        exposed_issues="Exposed Issues",
        review_status="Review Status",
        current_focus="Current Focus",
        recent_learning="Recent Learning",
        recent_practice="Recent Practice",
        auto_focus="Updated after mock interviews expose weaknesses.",
        organize_card="Organize knowledge card",
        review_question="Review real interview question",
        address_weakness="Address weakness",
        prepare_answer="Prepare a standard answer",
        correct_issue="Correct an issue exposed in a real interview",
        organize_record="Organize the real interview record and complete the questions and answers.",
        practice_title="Mock Interview Record",
        session="Session",
        started_at="Started at",
        interview_requirement="Interview requirement",
        default_requirement="Default practice",
        round="Round",
        question="Question",
        answer="Answer",
        feedback="Feedback",
        review_suggestions="Review suggestions",
        better_answer="Better interview answer",
        tested_points="Tested points",
        mastery_change="Mastery change",
        follow_up="Follow-up",
        follow_up_answer="Follow-up answer",
        follow_up_feedback="Follow-up feedback",
        persistence_suggestion="Persistence suggestion",
        write_weakness="Write weakness",
        write_high_frequency="Write high-frequency question",
        practice_prefix="Practice",
        continue_practice="Keep using mock interviews to expose weaknesses",
        question_points="Assessment Points",
        standard_answer="Standard Answer",
        project_context="Project Context",
        common_follow_up="Common Follow-up",
        error_points="Common Errors",
        project_fallback=(
            "Add a business scenario, responsibilities, technical tradeoffs, and results "
            "from existing project materials."
        ),
    ),
    "zh-CN": ContentLabels(
        colon="：",
        clause_separator="；",
        my_understanding="我的理解",
        corrections="修正/补充",
        interview_expression="30 秒面试说法",
        confusing_points="易混点",
        no_confusing_points="暂无明确易混点，后续练习中补充。",
        follow_up_questions="追问",
        default_follow_up="这个知识点在真实项目中如何落地？",
        report_title="面试复盘报告",
        summary="总结",
        strong_signals="表现较好",
        missing_points="缺失要点",
        weaknesses="薄弱点",
        review_focus="复习重点",
        source_context="来源上下文",
        none="暂无。",
        learning_input="学习输入",
        real_interview_record="真实面试记录",
        original_record="原始记录",
        extracted_questions="抽取问题",
        weak_signals="薄弱线索",
        high_frequency_title="高频与薄弱点",
        real_interview_questions="真实面试高频问题",
        exposed_issues="暴露问题",
        review_status="复习状态",
        current_focus="当前重点",
        recent_learning="最近整理",
        recent_practice="最近练习",
        auto_focus="通过模拟面试暴露薄弱点后自动更新。",
        organize_card="整理知识卡",
        review_question="复盘真实面试题",
        address_weakness="补齐薄弱点",
        prepare_answer="准备标准说法",
        correct_issue="纠正真实面试暴露问题",
        organize_record="整理真实面试记录，补全问题和回答。",
        practice_title="模拟面试记录",
        session="会话",
        started_at="开始时间",
        interview_requirement="出题要求",
        default_requirement="默认抽检",
        round="第 {index} 轮",
        question="问题",
        answer="回答",
        feedback="点评",
        review_suggestions="复习建议",
        better_answer="更好的面试说法",
        tested_points="本题考察点",
        mastery_change="掌握状态变化",
        follow_up="追问",
        follow_up_answer="追问回答",
        follow_up_feedback="追问点评",
        persistence_suggestion="写入建议",
        write_weakness="写入薄弱点",
        write_high_frequency="写入高频题",
        practice_prefix="练习",
        continue_practice="继续通过模拟面试暴露薄弱点",
        question_points="考察点",
        standard_answer="标准回答",
        project_context="结合项目",
        common_follow_up="常见追问",
        error_points="易错点",
        project_fallback="结合已有项目材料补充业务场景、角色职责、技术取舍和结果指标。",
    ),
}


def labels_for(language: str) -> ContentLabels:
    return _LABELS["zh-CN" if language == "zh-CN" else "en"]


def render_learning_note_card(
    note: str,
    summary: LearningNoteSummaryResult,
    language: str,
) -> str:
    labels = labels_for(language)
    correction_items = _unique_items([summary.summary, *summary.key_points])
    interview_items = summary.interview_takeaways or [summary.summary]
    follow_up_items = summary.follow_up_questions[:3] or [labels.default_follow_up]
    return (
        f"- {labels.my_understanding}{labels.colon}\n"
        f"{indented_text(note)}\n"
        f"- {labels.corrections}{labels.colon}\n"
        f"{indented_bullet_list(correction_items)}\n"
        f"- {labels.interview_expression}{labels.colon}\n"
        f"{indented_bullet_list(interview_items)}\n"
        f"- {labels.confusing_points}{labels.colon}\n"
        f"  - {labels.no_confusing_points}\n"
        f"- {labels.follow_up_questions}{labels.colon}\n"
        f"{indented_bullet_list(follow_up_items)}\n"
    )


def render_interview_report(result: InterviewReportResult, language: str) -> str:
    labels = labels_for(language)
    return (
        f"# {labels.report_title}\n\n"
        f"## {labels.summary}\n\n{result.summary.strip()}\n\n"
        f"## {labels.strong_signals}\n\n{_list_or_none(result.strong_signals, labels.none)}\n\n"
        f"## {labels.missing_points}\n\n{_list_or_none(result.missing_points, labels.none)}\n\n"
        f"## {labels.weaknesses}\n\n{_list_or_none(result.weaknesses, labels.none)}\n\n"
        f"## {labels.review_focus}\n\n{_list_or_none(result.review_focus, labels.none)}\n\n"
        f"## {labels.source_context}\n\n{_list_or_none(result.source_context, labels.none)}\n"
    )


def render_learning_inbox_entry(note: str, timestamp: str, language: str) -> str:
    labels = labels_for(language)
    return f"## {timestamp} {labels.learning_input}\n\n{note.strip()}\n"


def render_real_interview_record(
    record: str,
    questions: list[str],
    weak_points: list[str],
    language: str,
) -> str:
    labels = labels_for(language)
    return (
        f"# {labels.real_interview_record}\n\n"
        f"## {labels.original_record}\n\n{record.strip()}\n\n"
        f"## {labels.extracted_questions}\n\n{plain_bullet_list(questions)}\n\n"
        f"## {labels.weak_signals}\n\n{plain_bullet_list(weak_points)}\n"
    )


def render_high_frequency(
    questions: list[str],
    weak_points: list[str],
    language: str,
) -> str:
    labels = labels_for(language)
    return (
        f"# {labels.high_frequency_title}\n\n"
        f"## {labels.real_interview_questions}\n\n{plain_bullet_list(questions)}\n\n"
        f"## {labels.exposed_issues}\n\n{plain_bullet_list(weak_points)}\n"
    )


def render_review_status(
    focus: list[str],
    recent_learning: list[str],
    recent_practice: list[str],
    language: str,
) -> str:
    labels = labels_for(language)
    return (
        f"# {labels.review_status}\n\n"
        f"## {labels.current_focus}\n\n{_list_or_none(focus, labels.none)}\n\n"
        f"## {labels.recent_learning}\n\n{_list_or_none(recent_learning, labels.none)}\n\n"
        f"## {labels.recent_practice}\n\n{_list_or_none(recent_practice, labels.none)}\n"
    )


def real_interview_focus_items(
    questions: list[str],
    weak_points: list[str],
    language: str,
) -> list[str]:
    labels = labels_for(language)
    tasks: list[str] = []
    if questions:
        tasks.append(f"{labels.review_question}{labels.colon}{questions[0]}")
    if weak_points:
        tasks.append(f"{labels.address_weakness}{labels.colon}{weak_points[0]}")
    tasks.extend(
        f"{labels.prepare_answer}{labels.colon}{question}" for question in questions[1:]
    )
    tasks.extend(
        f"{labels.correct_issue}{labels.colon}{weakness}" for weakness in weak_points[1:]
    )
    return (tasks or [labels.organize_record])[:3]


def section_items(sections: dict[str, str], language: str, field: str) -> list[str]:
    current = labels_for(language)
    alternate = labels_for("en" if language == "zh-CN" else "zh-CN")
    heading = getattr(current, field)
    alternate_heading = getattr(alternate, field)
    content = next(
        (
            sections[key]
            for key in (heading, heading.casefold(), alternate_heading, alternate_heading.casefold())
            if key in sections and sections[key].strip()
        ),
        "",
    )
    return markdown_list_items(content)


def render_practice_session(
    interview_session: object,
    config: object,
    turns: list[object],
    language: str,
) -> str:
    labels = labels_for(language)
    started_at = (
        getattr(interview_session, "started_at")
        .astimezone(UTC)
        .isoformat()
        .replace("+00:00", "Z")
    )
    requirement = getattr(config, "extra_prompt", "").strip() or labels.default_requirement
    body = [
        f"- {labels.started_at}{labels.colon}{started_at}",
        f"- {labels.interview_requirement}{labels.colon}{requirement}",
        "",
    ]
    for turn in turns:
        index = getattr(turn, "round_index")
        round_heading = labels.round.format(index=index) if "{index}" in labels.round else f"{labels.round} {index}"
        body.extend(
            [
                f"### {round_heading}",
                "",
                f"**{labels.question}**{labels.colon}{getattr(turn, 'question')}",
                "",
                f"**{labels.answer}**{labels.colon}{getattr(turn, 'answer') or ''}",
                "",
                f"**{labels.feedback}**{labels.colon}{getattr(turn, 'feedback') or ''}",
                "",
            ]
        )
        _append_feedback_sections(body, turn, labels)
        if getattr(turn, "follow_up_question"):
            body.extend(
                [
                    f"**{labels.follow_up}**{labels.colon}{getattr(turn, 'follow_up_question')}",
                    "",
                    f"**{labels.follow_up_answer}**{labels.colon}{getattr(turn, 'follow_up_answer') or ''}",
                    "",
                    f"**{labels.follow_up_feedback}**{labels.colon}{getattr(turn, 'follow_up_feedback') or ''}",
                    "",
                ]
            )
            _append_feedback_sections(body, turn, labels, prefix="follow_up_")
    return "\n".join(body).strip()


def render_question_bank(
    question: str,
    tested_points: list[str],
    answer: str,
    project_context: str,
    follow_up: str,
    error_points: list[str],
    review_status: str,
    language: str,
) -> str:
    labels = labels_for(language)
    return (
        f"## {labels.question}{labels.colon}{question.strip()}\n\n"
        f"### {labels.question_points}\n\n{plain_bullet_list(tested_points)}\n\n"
        f"### {labels.standard_answer}\n\n{answer.strip()}\n\n"
        f"### {labels.project_context}\n\n{project_context}\n\n"
        f"### {labels.common_follow_up}\n\n{follow_up.strip() or labels.none}\n\n"
        f"### {labels.error_points}\n\n{_list_or_none(error_points, labels.none)}\n\n"
        f"### {labels.review_status}\n\n{review_status}\n"
    )


def render_answer_preview(result: object, language: str) -> str:
    labels = labels_for(language)
    sections = [getattr(result, "feedback").strip()]
    for field, heading in (
        ("missing_points", labels.missing_points),
        ("weaknesses", labels.weaknesses),
        ("review_suggestions", labels.review_suggestions),
        ("tested_points", labels.tested_points),
    ):
        values = getattr(result, field)
        if values:
            sections.append(f"{heading}\n{plain_bullet_list(values)}")
    if getattr(result, "better_answer"):
        sections.append(f"{labels.better_answer}\n{getattr(result, 'better_answer').strip()}")
    sections.append(f"{labels.mastery_change}\n{getattr(result, 'mastery_change')}")
    write_flags = []
    if getattr(result, "should_write_weakness"):
        write_flags.append(labels.write_weakness)
    if getattr(result, "should_write_high_frequency"):
        write_flags.append(labels.write_high_frequency)
    if write_flags:
        sections.append(f"{labels.persistence_suggestion}\n{', '.join(write_flags)}")
    if getattr(result, "follow_up_question"):
        sections.append(f"{labels.follow_up}\n{getattr(result, 'follow_up_question').strip()}")
    return "\n\n".join(section for section in sections if section)


def _append_feedback_sections(
    body: list[str],
    turn: object,
    labels: ContentLabels,
    *,
    prefix: str = "",
) -> None:
    mappings = (
        ("missing_points", labels.missing_points),
        ("weaknesses", labels.weaknesses),
        ("review_suggestions", labels.review_suggestions),
        ("better_answer", labels.better_answer),
        ("tested_points", labels.tested_points),
        ("mastery_change", labels.mastery_change),
    )
    for field, heading in mappings:
        value = getattr(turn, f"{prefix}{field}")
        if not value or (field == "mastery_change" and value == "unchanged"):
            continue
        rendered = plain_bullet_list(value) if isinstance(value, list) else str(value)
        joiner = " " if labels.colon.startswith(":") else ""
        label = f"{labels.follow_up}{joiner}{heading}" if prefix else heading
        body.extend([f"**{label}**{labels.colon}", rendered, ""])
    flags = []
    if getattr(turn, f"{prefix}should_write_weakness"):
        flags.append(labels.write_weakness)
    if getattr(turn, f"{prefix}should_write_high_frequency"):
        flags.append(labels.write_high_frequency)
    if flags:
        joiner = " " if labels.colon.startswith(":") else ""
        label = (
            f"{labels.follow_up}{joiner}{labels.persistence_suggestion}"
            if prefix
            else labels.persistence_suggestion
        )
        body.extend([f"**{label}**{labels.colon}", plain_bullet_list(flags), ""])


def _list_or_none(items: list[str], fallback: str) -> str:
    return plain_bullet_list(items) if items else fallback


def _unique_items(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        cleaned = item.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            result.append(cleaned)
    return result
