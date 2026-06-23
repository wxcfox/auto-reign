from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

InterviewMode = Literal[
    "comprehensive",
    "project_deep_dive",
    "knowledge_drill",
    "weakness_reinforcement",
]
SessionStatus = Literal["active", "completed", "cancelled"]
InterviewLanguage = Literal["en", "zh-CN"]


class InterviewConfigIn(BaseModel):
    target_company: str
    target_role: str
    job_description: str = ""
    extra_prompt: str = ""
    language: InterviewLanguage = "en"
    mode: InterviewMode = "comprehensive"
    chat_model_provider: str
    chat_model: str
    target_rounds: int = Field(default=3, ge=1)


class InterviewConfigResponse(InterviewConfigIn):
    model_config = ConfigDict(from_attributes=True)

    id: str
    is_last_used: bool
    updated_at: datetime


class InterviewSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    config_id: str
    status: SessionStatus
    current_round: int
    started_at: datetime
    ended_at: datetime | None = None
    report_path: str | None = None


class InterviewTurnResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    session_id: str
    round_index: int
    question: str
    answer: str | None = None
    feedback: str | None = None
    missing_points: list[str] = Field(default_factory=list)
    follow_up_question: str | None = None
    follow_up_answer: str | None = None
    follow_up_feedback: str | None = None
    follow_up_missing_points: list[str] = Field(default_factory=list)
    follow_up_weaknesses: list[str] = Field(default_factory=list)
    follow_up_review_suggestions: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    review_suggestions: list[str] = Field(default_factory=list)
    retrieved_context_refs: list[dict[str, str]] = Field(default_factory=list)
    created_at: datetime


class InterviewSessionDetailResponse(BaseModel):
    session: InterviewSessionResponse
    config: InterviewConfigResponse
    turns: list[InterviewTurnResponse]


class InterviewSessionHistoryItemResponse(InterviewSessionDetailResponse):
    resumable: bool


class InterviewSessionListResponse(BaseModel):
    sessions: list[InterviewSessionHistoryItemResponse]


class InterviewSessionCreatedResponse(BaseModel):
    session: InterviewSessionResponse
    turn: InterviewTurnResponse


class AnswerRequest(BaseModel):
    answer: str


class NextQuestionRequest(BaseModel):
    intent: str = ""


class FeedbackResponse(BaseModel):
    feedback: str
    missing_points: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    review_suggestions: list[str] = Field(default_factory=list)
    better_answer: str = ""
    mastery_change: str = "unchanged"
    should_write_weakness: bool = False
    should_write_high_frequency: bool = False
    tested_points: list[str] = Field(default_factory=list)


class AnswerFeedbackResponse(FeedbackResponse):
    follow_up_question: str


class FollowUpFeedbackResponse(FeedbackResponse):
    pass
