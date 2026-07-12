import json
import logging
import re
from collections.abc import Callable, Iterator
from typing import Any

from fastapi import HTTPException
from openai import OpenAI
from pydantic import BaseModel, ValidationError

from app.core.config import Settings, get_settings
from app.core.errors import bad_gateway, service_unavailable
from app.core.model_providers import find_chat_provider
from app.prompts import PromptId, load_prompt
from app.schemas.modeling import (
    AnswerEvaluationRequest,
    AnswerEvaluationResult,
    InterviewReportResult,
    LearningNoteSummaryResult,
    QuestionGenerationRequest,
    ReportGenerationRequest,
)

logger = logging.getLogger(__name__)


class ModelService:
    def __init__(
        self,
        settings: Settings | None = None,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.client_factory = client_factory or OpenAI

    def generate_learning_note_summary(
        self,
        text: str,
        *,
        language: str = "zh-CN",
        provider: str | None = None,
        model: str | None = None,
    ) -> LearningNoteSummaryResult:
        return self.parse_structured_response(
            self._chat(
                PromptId.LEARNING_NOTE_SUMMARY,
                {"text": text, "language": language},
                provider,
                model,
            ),
            LearningNoteSummaryResult,
        )

    def stream_question(self, request: QuestionGenerationRequest) -> Iterator[str]:
        yield from self._stream_chat(
            PromptId.QUESTION_GENERATION,
            request.model_dump(exclude={"provider", "model"}),
            request.provider,
            request.model,
        )

    def stream_answer_evaluation(self, request: AnswerEvaluationRequest) -> Iterator[str]:
        yield from self._stream_chat(
            PromptId.ANSWER_FEEDBACK,
            request.model_dump(exclude={"provider", "model"}),
            request.provider,
            request.model,
        )

    def stream_messages(
        self,
        messages: list[dict[str, str]],
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> Iterator[str]:
        normalized = [
            {"role": message["role"], "content": message["content"]}
            for message in messages
            if message.get("role") in {"user", "assistant"}
            and isinstance(message.get("content"), str)
            and message["content"]
        ]
        if not normalized:
            raise ValueError("chat messages are required")
        yield from self._stream_messages(normalized, provider, model)

    def parse_answer_evaluation(self, content: str) -> AnswerEvaluationResult:
        return self.parse_structured_response(content, AnswerEvaluationResult)

    def generate_report(self, request: ReportGenerationRequest) -> InterviewReportResult:
        return self.parse_structured_response(
            self._chat(
                PromptId.REPORT_GENERATION,
                request.model_dump(exclude={"provider", "model"}),
                request.provider,
                request.model,
            ),
            InterviewReportResult,
        )

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

    def _chat(
        self,
        prompt_id: PromptId,
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
                        "content": load_prompt(prompt_id),
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
        prompt_id: PromptId,
        payload: dict[str, object],
        provider: str | None,
        model: str | None,
    ) -> Iterator[str]:
        yield from self._stream_messages(
            [
                {"role": "system", "content": load_prompt(prompt_id)},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False),
                },
            ],
            provider,
            model,
        )

    def _stream_messages(
        self,
        messages: list[dict[str, str]],
        provider: str | None,
        model: str | None,
    ) -> Iterator[str]:
        resolved_provider, resolved_model, api_key, base_url = self._resolve_provider(provider, model)
        try:
            client = self.client_factory(api_key=api_key, base_url=base_url)
            stream = client.chat.completions.create(
                model=resolved_model,
                messages=messages,
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

    def _resolve_provider(
        self, provider: str | None, model: str | None
    ) -> tuple[str, str, str, str | None]:
        provider_config = find_chat_provider(self.settings, provider)
        if provider_config is None:
            raise service_unavailable(
                "provider_not_configured",
                "The selected model provider is not configured.",
            )
        api_key = provider_config.api_key
        if api_key is None:
            raise service_unavailable(
                "provider_not_configured",
                "The selected model provider is not configured.",
            )
        allowed_models = provider_config.models
        resolved_model = model or (allowed_models[0] if allowed_models else "")
        if resolved_model not in allowed_models:
            raise service_unavailable(
                "model_not_configured",
                "The selected model is not configured for this provider.",
            )
        return provider_config.name, resolved_model, api_key, provider_config.base_url
