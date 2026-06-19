import re
from collections import Counter

from pydantic import BaseModel, Field


class DocumentAnalysisResult(BaseModel):
    title: str
    summary: str
    tags: list[str] = Field(default_factory=list)
    knowledge_points: list[str] = Field(default_factory=list)
    weakness_candidates: list[str] = Field(default_factory=list)


class QuestionGenerationRequest(BaseModel):
    target_company: str
    target_role: str
    job_description: str = ""
    extra_prompt: str = ""
    mode: str = "comprehensive"
    context: list[str] = Field(default_factory=list)


class AnswerEvaluationRequest(BaseModel):
    question: str
    answer: str
    context: list[str] = Field(default_factory=list)


class AnswerEvaluationResult(BaseModel):
    feedback: str
    missing_points: list[str] = Field(default_factory=list)
    follow_up_question: str
    weaknesses: list[str] = Field(default_factory=list)
    review_suggestions: list[str] = Field(default_factory=list)


class ReportGenerationRequest(BaseModel):
    session_id: str
    turns: list[dict[str, object]] = Field(default_factory=list)


class MemoryUpdateRequest(BaseModel):
    report_markdown: str
    existing_memory: dict[str, str] = Field(default_factory=dict)


class MemoryUpdateResult(BaseModel):
    weakness_summary: str
    interview_summary: str
    learning_profile: str


class ModelService:
    def analyze_document(self, text: str) -> DocumentAnalysisResult:
        title = self._title_from_markdown(text)
        summary = self._summary_from_text(text)
        tags = self._tags_from_text(text)
        knowledge_points = self._knowledge_points_from_text(text)
        weakness_candidates = [
            f"Review {tags[0]} tradeoffs" if tags else "Review core project tradeoffs"
        ]
        return DocumentAnalysisResult(
            title=title,
            summary=summary,
            tags=tags,
            knowledge_points=knowledge_points,
            weakness_candidates=weakness_candidates,
        )

    def generate_question(self, request: QuestionGenerationRequest) -> str:
        return f"How would you explain your {request.target_role} experience for {request.target_company}?"

    def evaluate_answer(self, request: AnswerEvaluationRequest) -> AnswerEvaluationResult:
        return AnswerEvaluationResult(
            feedback="The answer shows relevant structure and can be strengthened with concrete tradeoffs.",
            missing_points=["Concrete failure handling", "Operational metrics"],
            follow_up_question="What tradeoffs would you make under production traffic?",
            weaknesses=["Needs deeper operational detail"],
            review_suggestions=["Prepare one concrete architecture incident example"],
        )

    def generate_report(self, request: ReportGenerationRequest) -> str:
        return (
            "# Interview Report\n\n"
            "## Summary\n"
            f"Session {request.session_id} completed with {len(request.turns)} turns.\n"
        )

    def update_memory(self, request: MemoryUpdateRequest) -> MemoryUpdateResult:
        return MemoryUpdateResult(
            weakness_summary="Focus on concrete system design tradeoffs.",
            interview_summary="Completed a local mock interview session.",
            learning_profile="Prefers structured backend and RAG preparation.",
        )

    def _title_from_markdown(self, text: str) -> str:
        for line in text.splitlines():
            match = re.match(r"^#\s+(.+)$", line.strip())
            if match:
                return match.group(1).strip()
        words = re.findall(r"[A-Za-z][A-Za-z0-9_-]*", text)
        return " ".join(words[:5]) or "Untitled Document"

    def _summary_from_text(self, text: str) -> str:
        compact = " ".join(text.split())
        if not compact:
            return "Empty document."
        return compact[:180]

    def _tags_from_text(self, text: str) -> list[str]:
        words = [word.lower() for word in re.findall(r"[A-Za-z][A-Za-z0-9_-]*", text)]
        stop_words = {"with", "that", "this", "from", "have", "built", "systems"}
        counts = Counter(word for word in words if len(word) > 3 and word not in stop_words)
        return [word for word, _ in counts.most_common(5)] or ["document"]

    def _knowledge_points_from_text(self, text: str) -> list[str]:
        compact = " ".join(text.split())
        if not compact:
            return ["Document is empty and needs more source material."]
        return [compact[:120]]
