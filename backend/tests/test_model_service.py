from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.core.config import Settings
from app.services.model_service import (
    AnswerEvaluationRequest,
    ModelService,
    QuestionGenerationRequest,
)


class FakeChatCompletions:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))]
        )


class FakeOpenAIClient:
    def __init__(self, content: str) -> None:
        self.completions = FakeChatCompletions(content)
        self.chat = SimpleNamespace(completions=self.completions)


@pytest.mark.parametrize(
    ("provider", "model", "settings_overrides", "expected_base_url"),
    [
        (
            "openai",
            "gpt-4.1-mini",
            {"openai_api_key": "provider-secret"},
            None,
        ),
        (
            "deepseek",
            "deepseek-chat",
            {"deepseek_api_key": "provider-secret"},
            "https://api.deepseek.com",
        ),
        (
            "qwen",
            "qwen-plus",
            {"qwen_api_key": "provider-secret"},
            "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        ),
    ],
)
def test_model_service_uses_selected_provider(
    tmp_path,
    provider: str,
    model: str,
    settings_overrides: dict[str, str],
    expected_base_url: str | None,
) -> None:
    settings = Settings(
        data_dir=tmp_path,
        sqlite_path=tmp_path / "app.db",
        chroma_dir=tmp_path / "chroma",
        deterministic_model_fallback=False,
        **settings_overrides,
    )
    client = FakeOpenAIClient(
        '{"feedback":"Good structure.","missing_points":["Metrics"],'
        '"follow_up_question":"Which metrics?","weaknesses":["Observability"],'
        '"review_suggestions":["Add an SLO example."]}'
    )
    factory_calls: list[dict[str, str | None]] = []

    def client_factory(*, api_key: str, base_url: str | None = None):
        factory_calls.append({"api_key": api_key, "base_url": base_url})
        return client

    service = ModelService(settings=settings, client_factory=client_factory)
    result = service.evaluate_answer(
        AnswerEvaluationRequest(
            question="How do you operate the service?",
            answer="With dashboards and alerts.",
            provider=provider,
            model=model,
        )
    )

    assert result.feedback == "Good structure."
    assert factory_calls == [
        {"api_key": "provider-secret", "base_url": expected_base_url}
    ]
    assert client.completions.calls[0]["model"] == model


def test_model_service_rejects_unconfigured_provider(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        sqlite_path=tmp_path / "app.db",
        chroma_dir=tmp_path / "chroma",
        deterministic_model_fallback=False,
    )
    service = ModelService(settings=settings)

    with pytest.raises(HTTPException) as error:
        service.generate_question(
            QuestionGenerationRequest(
                target_company="OpenAI",
                target_role="Backend Engineer",
                provider="openai",
                model="gpt-4.1-mini",
            )
        )

    assert error.value.status_code == 503
    assert error.value.detail["code"] == "provider_not_configured"
    assert "key" not in error.value.detail["message"].lower()


def test_model_service_uses_deterministic_fallback_when_enabled(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        sqlite_path=tmp_path / "app.db",
        chroma_dir=tmp_path / "chroma",
        deterministic_model_fallback=True,
    )
    service = ModelService(settings=settings)

    question = service.generate_question(
        QuestionGenerationRequest(
            target_company="OpenAI",
            target_role="Backend Engineer",
            provider="openai",
            model="gpt-4.1-mini",
        )
    )

    assert question == "How would you explain your Backend Engineer experience for OpenAI?"


def test_model_service_hides_provider_failure_details(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        sqlite_path=tmp_path / "app.db",
        chroma_dir=tmp_path / "chroma",
        openai_api_key="openai-secret",
        deterministic_model_fallback=False,
    )

    class FailingCompletions:
        def create(self, **_kwargs):
            raise RuntimeError("upstream rejected openai-secret")

    client = SimpleNamespace(chat=SimpleNamespace(completions=FailingCompletions()))
    service = ModelService(settings=settings, client_factory=lambda **_kwargs: client)

    with pytest.raises(HTTPException) as error:
        service.generate_question(
            QuestionGenerationRequest(
                target_company="OpenAI",
                target_role="Backend Engineer",
                provider="openai",
                model="gpt-4.1-mini",
            )
        )

    assert error.value.status_code == 502
    assert error.value.detail["code"] == "provider_call_failed"
    assert "secret" not in str(error.value.detail).lower()
