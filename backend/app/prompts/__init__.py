from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from importlib.resources import files

from pydantic import BaseModel

from app.schemas.modeling import (
    AnswerEvaluationResult,
    InterviewReportResult,
    LearningNoteSummaryResult,
)


class PromptId(StrEnum):
    ANSWER_FEEDBACK = "answer_feedback"
    LEARNING_NOTE_SUMMARY = "learning_note_summary"
    QUESTION_GENERATION = "question_generation"
    REPORT_GENERATION = "report_generation"


@dataclass(frozen=True)
class PromptSpec:
    filename: str
    result_type: type[BaseModel] | None = None


PROMPTS: dict[PromptId, PromptSpec] = {
    PromptId.ANSWER_FEEDBACK: PromptSpec(
        "answer_feedback.md",
        AnswerEvaluationResult,
    ),
    PromptId.LEARNING_NOTE_SUMMARY: PromptSpec(
        "learning_note_summary.md",
        LearningNoteSummaryResult,
    ),
    PromptId.QUESTION_GENERATION: PromptSpec("question_generation.md"),
    PromptId.REPORT_GENERATION: PromptSpec(
        "report_generation.md",
        InterviewReportResult,
    ),
}


@lru_cache
def load_prompt(prompt_id: PromptId) -> str:
    spec = PROMPTS[prompt_id]
    prompt = files("app.prompts").joinpath(spec.filename).read_text(encoding="utf-8").strip()
    if spec.result_type is None:
        return prompt
    schema = json.dumps(spec.result_type.model_json_schema(), ensure_ascii=False)
    return (
        f"{prompt}\n\n"
        "Return one JSON object matching this JSON Schema exactly. Do not wrap it in "
        f"Markdown code fences.\n{schema}"
    )
