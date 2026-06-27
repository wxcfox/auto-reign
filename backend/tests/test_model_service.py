from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.core.config import Settings
from app.schemas.modeling import (
    AnswerEvaluationRequest,
    QuestionGenerationRequest,
    ReportGenerationRequest,
)
from app.services.model_service import ModelService


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
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
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
        database_url=f"sqlite:///{tmp_path / 'app.db'}",
        qdrant_url=":memory:",
        qdrant_collection="auto_reign_test",
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


def test_model_service_streams_provider_chunks(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'app.db'}",
        qdrant_url=":memory:",
        qdrant_collection="auto_reign_test",
        openai_api_key="provider-secret",
        deterministic_model_fallback=False,
    )

    class FakeStreamCompletions:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            return [
                SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="Hel"))]),
                SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="lo"))]),
                SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None))]),
            ]

    completions = FakeStreamCompletions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    service = ModelService(settings=settings, client_factory=lambda **_kwargs: client)

    chunks = list(
        service.stream_chat(
            "question_generation.md",
            {"target_role": "Backend Engineer"},
            "openai",
            "gpt-4.1-mini",
        )
    )

    assert chunks == ["Hel", "lo"]
    assert completions.calls[0]["stream"] is True
    assert completions.calls[0]["model"] == "gpt-4.1-mini"


def test_model_service_streams_deterministic_answer_in_chunks(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'app.db'}",
        qdrant_url=":memory:",
        qdrant_collection="auto_reign_test",
        deterministic_model_fallback=True,
    )
    service = ModelService(settings=settings)

    chunks = list(
        service.stream_answer_evaluation(
            AnswerEvaluationRequest(
                question="How do you operate the service?",
                answer="With dashboards and alerts.",
                provider="openai",
                model="gpt-4.1-mini",
            )
        )
    )

    assert len(chunks) > 1
    parsed = service.parse_answer_evaluation("".join(chunks))
    assert parsed.feedback.startswith("The answer shows")


def test_model_service_rejects_unconfigured_provider(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'app.db'}",
        qdrant_url=":memory:",
        qdrant_collection="auto_reign_test",
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
        database_url=f"sqlite:///{tmp_path / 'app.db'}",
        qdrant_url=":memory:",
        qdrant_collection="auto_reign_test",
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


def test_model_service_generates_generic_question_without_target_fields(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'app.db'}",
        qdrant_url=":memory:",
        qdrant_collection="auto_reign_test",
        deterministic_model_fallback=True,
    )
    service = ModelService(settings=settings)

    question = service.generate_question(
        QuestionGenerationRequest(
            target_company="",
            target_role="",
            extra_prompt="面试字节后端岗位，JD 关注缓存和高并发。",
            language="zh-CN",
            provider="openai",
            model="gpt-4.1-mini",
        )
    )

    assert "目标公司" not in question
    assert "目标岗位" not in question
    assert "最近做过" in question


def test_model_service_uses_chinese_fallback_outputs_when_requested(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'app.db'}",
        qdrant_url=":memory:",
        qdrant_collection="auto_reign_test",
        deterministic_model_fallback=True,
    )
    service = ModelService(settings=settings)

    report = service.generate_report(
        ReportGenerationRequest(
            session_id="session-1",
            language="zh-CN",
            turns=[],
            provider="openai",
            model="gpt-4.1-mini",
        )
    )
    assert "# 面试复盘报告" in report


def test_model_service_hides_provider_failure_details(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'app.db'}",
        qdrant_url=":memory:",
        qdrant_collection="auto_reign_test",
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


def test_model_service_logs_provider_error_details(
    tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'app.db'}",
        qdrant_url=":memory:",
        qdrant_collection="auto_reign_test",
        openai_api_key="openai-secret",
        deterministic_model_fallback=False,
    )

    class FailingCompletions:
        def create(self, **_kwargs):
            raise RuntimeError("insufficient quota from upstream")

    client = SimpleNamespace(chat=SimpleNamespace(completions=FailingCompletions()))
    service = ModelService(settings=settings, client_factory=lambda **_kwargs: client)

    with caplog.at_level("ERROR"):
        with pytest.raises(HTTPException) as error:
            service.generate_question(
                QuestionGenerationRequest(
                    target_company="OpenAI",
                    target_role="Backend Engineer",
                    provider="openai",
                    model="gpt-4.1-mini",
                )
            )

    record = caplog.records[-1]
    assert error.value.status_code == 502
    assert error.value.detail["code"] == "provider_call_failed"
    assert "provider=openai" in caplog.text
    assert "model=gpt-4.1-mini" in caplog.text
    assert "insufficient quota from upstream" in caplog.text
    assert "RuntimeError" in caplog.text
    assert record.provider == "openai"
    assert record.model == "gpt-4.1-mini"
    assert record.error_type == "RuntimeError"
    assert record.error_message == "insufficient quota from upstream"
