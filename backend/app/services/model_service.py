import json
import logging
import re
from collections import Counter
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError

from app.core.config import Settings, get_settings
from app.core.errors import bad_gateway, service_unavailable

logger = logging.getLogger(__name__)


class DocumentAnalysisResult(BaseModel):
    title: str
    summary: str
    tags: list[str] = Field(default_factory=list)
    knowledge_points: list[str] = Field(default_factory=list)
    weakness_candidates: list[str] = Field(default_factory=list)


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
    language: str = "en"
    mode: str = "comprehensive"
    context: list[str] = Field(default_factory=list)


class AnswerEvaluationRequest(ProviderRequest):
    question: str
    answer: str
    language: str = "en"
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
    language: str = "en"
    turns: list[dict[str, object]] = Field(default_factory=list)


class MemoryUpdateRequest(ProviderRequest):
    report_markdown: str
    language: str = "en"
    existing_memory: dict[str, str] = Field(default_factory=dict)


class MemoryUpdateResult(BaseModel):
    weakness_summary: str
    interview_summary: str
    learning_profile: str


class ModelService:
    def __init__(
        self,
        settings: Settings | None = None,
        client_factory: Callable[..., Any] = OpenAI,
    ) -> None:
        self.settings = settings or get_settings()
        self.client_factory = client_factory
        self.prompt_dir = Path(__file__).resolve().parent.parent / "prompts"

    def analyze_document(self, text: str) -> DocumentAnalysisResult:
        if self.settings.deterministic_model_fallback:
            return self._fallback_document_analysis(text)
        return self._structured_chat(
            "document_analysis.md",
            {"document": text},
            DocumentAnalysisResult,
        )

    def summarize_learning_note(
        self,
        text: str,
        *,
        language: str = "zh-CN",
        provider: str | None = None,
        model: str | None = None,
    ) -> LearningNoteSummaryResult:
        if self.settings.deterministic_model_fallback:
            stripped = re.sub(r"\s+", " ", text).strip()
            title = self._fallback_title(stripped, "学习记录")
            summary = stripped[:240] if stripped else "（空内容）"
            if language == "zh-CN":
                return LearningNoteSummaryResult(
                    title=title,
                    summary=f"已记录：{summary}",
                    key_points=[summary] if summary else [],
                    interview_takeaways=["把学习内容整理成 30 秒可口述表达。"],
                    follow_up_questions=["这个知识点在真实项目中如何落地？"],
                )
            return LearningNoteSummaryResult(
                title=title,
                summary=f"Recorded: {summary}",
                key_points=[summary] if summary else [],
                interview_takeaways=["Prepare a 30-second explanation for this learning note."],
                follow_up_questions=["How would this topic apply in a real project?"],
            )
        return self._structured_chat(
            "learning_note_summary.md",
            {"text": text, "language": language},
            LearningNoteSummaryResult,
            provider,
            model,
        )

    def stream_learning_note_summary(
        self,
        text: str,
        *,
        language: str = "zh-CN",
        provider: str | None = None,
        model: str | None = None,
    ) -> Iterator[str]:
        if self.settings.deterministic_model_fallback:
            result = self.summarize_learning_note(
                text,
                language=language,
                provider=provider,
                model=model,
            )
            yield from self._chunk_text(self._learning_note_summary_markdown(result, language))
            return
        yield from self.stream_chat(
            "learning_note_summary_stream.md",
            {"text": text, "language": language},
            provider,
            model,
        )

    def generate_question(self, request: QuestionGenerationRequest) -> str:
        if self.settings.deterministic_model_fallback:
            if request.language == "zh-CN":
                if request.target_company or request.target_role:
                    role = request.target_role or "这个岗位"
                    company = request.target_company or "目标公司"
                    return f"请结合你的经历，说明你会如何胜任{company}的{role}？"
                return "请先介绍一个你最近做过、最能体现能力的项目，并说明你的关键贡献。"
            if request.target_company or request.target_role:
                role = request.target_role or "the target role"
                company = request.target_company or "the target company"
                return f"How would you explain your {role} experience for {company}?"
            return (
                "Walk me through a recent project that best demonstrates your strengths, "
                "and explain your specific contribution."
            )
        return self._chat(
            "question_generation.md",
            request.model_dump(exclude={"provider", "model"}),
            request.provider,
            request.model,
        ).strip()

    def stream_question(self, request: QuestionGenerationRequest) -> Iterator[str]:
        if self.settings.deterministic_model_fallback:
            yield from self._chunk_text(self.generate_question(request))
            return
        yield from self.stream_chat(
            "question_generation.md",
            request.model_dump(exclude={"provider", "model"}),
            request.provider,
            request.model,
        )

    def evaluate_answer(self, request: AnswerEvaluationRequest) -> AnswerEvaluationResult:
        if self.settings.deterministic_model_fallback:
            if request.language == "zh-CN":
                return AnswerEvaluationResult(
                    feedback="回答有基本结构，可以继续补充具体取舍、失败场景和量化结果。",
                    missing_points=["具体失败处理", "可观测性指标"],
                    follow_up_question="在真实生产流量下，你会优先做哪些取舍？",
                    weaknesses=["需要更深入的工程细节"],
                    review_suggestions=["准备一个包含故障处理和指标的项目案例"],
                    better_answer=(
                        "我会先说明方案边界，再结合真实流量下的失败处理、监控指标和降级预案，"
                        "把技术取舍讲清楚。"
                    ),
                    mastery_change="basic",
                    should_write_weakness=True,
                    should_write_high_frequency=False,
                    tested_points=["工程取舍", "失败处理", "可观测性"],
                )
            return AnswerEvaluationResult(
                feedback=(
                    "The answer shows relevant structure and can be strengthened with "
                    "concrete tradeoffs."
                ),
                missing_points=["Concrete failure handling", "Operational metrics"],
                follow_up_question="What tradeoffs would you make under production traffic?",
                weaknesses=["Needs deeper operational detail"],
                review_suggestions=["Prepare one concrete architecture incident example"],
                better_answer=(
                    "I would start with the system boundary, then explain concrete failure "
                    "handling, observability, and degradation tradeoffs."
                ),
                mastery_change="basic",
                should_write_weakness=True,
                should_write_high_frequency=False,
                tested_points=["Tradeoffs", "Failure handling", "Observability"],
            )
        return self._structured_chat(
            "answer_feedback.md",
            request.model_dump(exclude={"provider", "model"}),
            AnswerEvaluationResult,
            request.provider,
            request.model,
        )

    def stream_answer_evaluation(self, request: AnswerEvaluationRequest) -> Iterator[str]:
        if self.settings.deterministic_model_fallback:
            result = self.evaluate_answer(request)
            yield from self._chunk_text(json.dumps(result.model_dump(), ensure_ascii=False))
            return
        yield from self.stream_chat(
            "answer_feedback.md",
            request.model_dump(exclude={"provider", "model"}),
            request.provider,
            request.model,
        )

    def parse_answer_evaluation(self, content: str) -> AnswerEvaluationResult:
        return self.parse_structured_response(content, AnswerEvaluationResult)

    def generate_report(self, request: ReportGenerationRequest) -> str:
        if self.settings.deterministic_model_fallback:
            return self._fallback_report(request)
        return self._chat(
            "report_generation.md",
            request.model_dump(exclude={"provider", "model"}),
            request.provider,
            request.model,
        ).strip()

    def update_memory(self, request: MemoryUpdateRequest) -> MemoryUpdateResult:
        if self.settings.deterministic_model_fallback:
            return self._fallback_memory_update(request)
        return self._structured_chat(
            "memory_update.md",
            request.model_dump(exclude={"provider", "model"}),
            MemoryUpdateResult,
            request.provider,
            request.model,
        )

    def stream_chat(
        self,
        prompt_filename: str,
        payload: dict[str, object],
        provider: str | None,
        model: str | None,
    ) -> Iterator[str]:
        if self.settings.deterministic_model_fallback:
            yield from self._chunk_text(json.dumps(payload, ensure_ascii=False))
            return
        yield from self._stream_chat(prompt_filename, payload, provider, model)

    def parse_structured_response(
        self,
        content: str,
        result_type: type[BaseModel],
    ):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
        try:
            return result_type.model_validate_json(cleaned)
        except ValidationError as error:
            raise bad_gateway(
                "provider_invalid_response",
                "The selected model returned an invalid structured response.",
            ) from error

    def _structured_chat(
        self,
        prompt_filename: str,
        payload: dict[str, object],
        result_type: type[BaseModel],
        provider: str | None = None,
        model: str | None = None,
    ):
        content = self._chat(prompt_filename, payload, provider, model)
        return self.parse_structured_response(content, result_type)

    def _chat(
        self,
        prompt_filename: str,
        payload: dict[str, object],
        provider: str | None,
        model: str | None,
    ) -> str:
        resolved_provider, resolved_model, api_key, base_url = self._resolve_provider(
            provider, model
        )
        try:
            client = self.client_factory(api_key=api_key, base_url=base_url)
            response = client.chat.completions.create(
                model=resolved_model,
                messages=[
                    {
                        "role": "system",
                        "content": (self.prompt_dir / prompt_filename).read_text(encoding="utf-8"),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(payload, ensure_ascii=False),
                    },
                ],
            )
            content = response.choices[0].message.content
            if not isinstance(content, str) or not content.strip():
                raise ValueError("empty model response")
            return content
        except HTTPException:
            raise
        except Exception as error:
            logger.exception(
                "Provider chat request failed: provider=%s model=%s error_type=%s error_message=%s",
                resolved_provider,
                resolved_model,
                type(error).__name__,
                str(error),
                extra={
                    "provider": resolved_provider,
                    "model": resolved_model,
                    "error_type": type(error).__name__,
                    "error_message": str(error),
                },
            )
            raise bad_gateway(
                "provider_call_failed",
                f"The {resolved_provider} model request failed.",
            ) from error

    def _stream_chat(
        self,
        prompt_filename: str,
        payload: dict[str, object],
        provider: str | None,
        model: str | None,
    ) -> Iterator[str]:
        resolved_provider, resolved_model, api_key, base_url = self._resolve_provider(
            provider, model
        )
        try:
            client = self.client_factory(api_key=api_key, base_url=base_url)
            stream = client.chat.completions.create(
                model=resolved_model,
                messages=[
                    {
                        "role": "system",
                        "content": (self.prompt_dir / prompt_filename).read_text(encoding="utf-8"),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(payload, ensure_ascii=False),
                    },
                ],
                stream=True,
            )
            yielded = False
            for chunk in stream:
                for choice in getattr(chunk, "choices", []) or []:
                    delta = getattr(choice, "delta", None)
                    content = (
                        delta.get("content")
                        if isinstance(delta, dict)
                        else getattr(delta, "content", None)
                    )
                    if isinstance(content, str) and content:
                        yielded = True
                        yield content
            if not yielded:
                raise ValueError("empty model stream")
        except HTTPException:
            raise
        except Exception as error:
            logger.exception(
                "Provider streaming request failed: provider=%s model=%s error_type=%s error_message=%s",
                resolved_provider,
                resolved_model,
                type(error).__name__,
                str(error),
                extra={
                    "provider": resolved_provider,
                    "model": resolved_model,
                    "error_type": type(error).__name__,
                    "error_message": str(error),
                },
            )
            raise bad_gateway(
                "provider_call_failed",
                f"The {resolved_provider} model request failed.",
            ) from error

    def _chunk_text(self, text: str, chunk_size: int = 24) -> Iterator[str]:
        for index in range(0, len(text), chunk_size):
            yield text[index : index + chunk_size]

    def _resolve_provider(
        self, provider: str | None, model: str | None
    ) -> tuple[str, str, str, str | None]:
        providers = {
            "openai": (
                self.settings.openai_api_key,
                self.settings.openai_chat_models,
                None,
            ),
            "deepseek": (
                self.settings.deepseek_api_key,
                self.settings.deepseek_chat_models,
                self.settings.deepseek_base_url,
            ),
            "qwen": (
                self.settings.qwen_api_key,
                self.settings.qwen_chat_models,
                self.settings.qwen_base_url,
            ),
        }
        if provider is None:
            provider = next(
                (name for name, (key, _models, _url) in providers.items() if key),
                None,
            )
        if provider not in providers:
            raise service_unavailable(
                "provider_not_configured",
                "The selected model provider is not configured.",
            )
        api_key, configured_models, base_url = providers[provider]
        if not api_key:
            raise service_unavailable(
                "provider_not_configured",
                "The selected model provider is not configured.",
            )
        allowed_models = [item.strip() for item in configured_models.split(",") if item.strip()]
        resolved_model = model or (allowed_models[0] if allowed_models else "")
        if resolved_model not in allowed_models:
            raise service_unavailable(
                "model_not_configured",
                "The selected model is not configured for this provider.",
            )
        return provider, resolved_model, api_key, base_url

    def _fallback_document_analysis(self, text: str) -> DocumentAnalysisResult:
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

    def _fallback_report(self, request: ReportGenerationRequest) -> str:
        weaknesses = sorted(
            {
                weakness
                for turn in request.turns
                for weakness in (turn.get("weaknesses") or [])
                if isinstance(weakness, str)
            }
        )
        missing_points = sorted(
            {
                point
                for turn in request.turns
                for point in (turn.get("missing_points") or [])
                if isinstance(point, str)
            }
        )
        if request.language == "zh-CN":
            return "\n\n".join(
                [
                    "# 面试复盘报告",
                    "## 总结\n"
                    f"本场面试会话 {request.session_id} 共完成 {len(request.turns)} 轮。",
                    "## 亮点\n- 回答结构清晰，具备后端问题拆解能力",
                    "## 缺失点\n"
                    + "\n".join(
                        f"- {point}" for point in missing_points or ["当前未记录明显缺失点。"]
                    ),
                    "## 薄弱项\n"
                    + "\n".join(
                        f"- {weakness}" for weakness in weaknesses or ["当前未记录明显薄弱项。"]
                    ),
                    "## 复习计划\n- 针对缺失点各准备一个具体案例，并补齐关键取舍说明。",
                    "## 参考上下文\n- 内容基于本次面试轮次记录与长期记忆生成。",
                ]
            )
        return "\n\n".join(
            [
                "# Interview Report",
                "## Summary\n"
                f"Session {request.session_id} completed with {len(request.turns)} turns.",
                "## Strong Signals\n- Structured backend reasoning",
                "## Missing Points\n"
                + "\n".join(
                    f"- {point}" for point in missing_points or ["No major gaps recorded."]
                ),
                "## Weaknesses\n"
                + "\n".join(
                    f"- {weakness}"
                    for weakness in weaknesses or ["No major weaknesses recorded."]
                ),
                "## Review Plan\n- Revisit feedback and prepare one concrete example per gap.",
                "## Source Context\n- Generated from local interview turns and memory.",
            ]
        )

    def _fallback_memory_update(self, request: MemoryUpdateRequest) -> MemoryUpdateResult:
        if request.language == "zh-CN":
            return MemoryUpdateResult(
                weakness_summary="重点补强系统设计取舍、失败场景处理和可观测性表达。",
                interview_summary="已完成一场本地中文模拟面试，建议围绕反馈继续复盘。",
                learning_profile="当前更适合通过结构化问答与RAG相关案例进行强化练习。",
            )
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

    def _fallback_title(self, text: str, default: str) -> str:
        markdown_title = self._title_from_markdown(text)
        if markdown_title != "Untitled Document":
            return markdown_title[:80]
        chinese_terms = re.findall(r"[\u4e00-\u9fffA-Za-z0-9]+", text)
        if chinese_terms:
            return "".join(chinese_terms[:8])[:80]
        return default

    def _learning_note_summary_markdown(
        self,
        result: LearningNoteSummaryResult,
        language: str,
    ) -> str:
        if language == "zh-CN":
            sections = [
                f"# {result.title}",
                f"## 摘要\n\n{result.summary}",
                "## 关键点\n\n"
                + "\n".join(f"- {point}" for point in result.key_points or ["继续补充细节。"]),
                "## 面试表达\n\n"
                + "\n".join(
                    f"- {takeaway}"
                    for takeaway in result.interview_takeaways
                    or ["整理成项目案例里的可口述表达。"]
                ),
                "## 可追问问题\n\n"
                + "\n".join(
                    f"- {question}"
                    for question in result.follow_up_questions
                    or ["这个知识点如何在真实项目中落地？"]
                ),
            ]
            return "\n\n".join(sections)
        sections = [
            f"# {result.title}",
            f"## Summary\n\n{result.summary}",
            "## Key points\n\n"
            + "\n".join(f"- {point}" for point in result.key_points or ["Add more detail."]),
            "## Interview expression\n\n"
            + "\n".join(
                f"- {takeaway}"
                for takeaway in result.interview_takeaways
                or ["Turn this into a concise interview-ready story."]
            ),
            "## Follow-up questions\n\n"
            + "\n".join(
                f"- {question}"
                for question in result.follow_up_questions
                or ["How would this apply in a real project?"]
            ),
        ]
        return "\n\n".join(sections)

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
