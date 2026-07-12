from typing import Literal

from pydantic import BaseModel, Field


SupportedLanguage = Literal["en", "zh-CN"]


class LearningNoteSummaryResult(BaseModel):
    title: str
    summary: str
    key_points: list[str] = Field(default_factory=list)
    interview_takeaways: list[str] = Field(default_factory=list)
    follow_up_questions: list[str] = Field(default_factory=list)


class ProviderRequest(BaseModel):
    provider: str | None = None
    model: str | None = None


class QuestionGenerationRequest(ProviderRequest):
    target_company: str
    target_role: str
    job_description: str = ""
    extra_prompt: str = ""
    language: SupportedLanguage = "en"
    mode: str = "comprehensive"
    context: list[str] = Field(default_factory=list)


class AnswerEvaluationRequest(ProviderRequest):
    question: str
    answer: str
    language: SupportedLanguage = "en"
    context: list[str] = Field(default_factory=list)


class AnswerEvaluationResult(BaseModel):
    feedback: str
    missing_points: list[str] = Field(default_factory=list)
    follow_up_question: str
    weaknesses: list[str] = Field(default_factory=list)
    review_suggestions: list[str] = Field(default_factory=list)
    better_answer: str = ""
    mastery_change: str = "unchanged"
    should_write_weakness: bool = False
    should_write_high_frequency: bool = False
    tested_points: list[str] = Field(default_factory=list)


class ReportGenerationRequest(ProviderRequest):
    session_id: str
    language: SupportedLanguage = "en"
    turns: list[dict[str, object]] = Field(default_factory=list)


class InterviewReportResult(BaseModel):
    summary: str
    strong_signals: list[str] = Field(default_factory=list)
    missing_points: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    review_focus: list[str] = Field(default_factory=list)
    source_context: list[str] = Field(default_factory=list)
