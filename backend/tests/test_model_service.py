from __future__ import annotations

import logging
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from openai import APIStatusError

from app.core.config import Settings
from app.services.model_service import ModelService
from app.services.runtime_types import (
    ProviderCallMetrics,
    ToolCall,
    ToolDefinition,
)


def _settings(tmp_path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "data_dir": tmp_path,
        "database_url": f"sqlite:///{tmp_path / 'app.db'}",
        "qdrant_url": ":memory:",
        "qdrant_collection": "auto_reign_test",
        "openai_api_key": None,
        "deepseek_api_key": None,
        "qwen_api_key": None,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


class RecordingCompletions:
    def __init__(self, stream: object) -> None:
        self.stream = stream
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return self.stream


def _client(completions: object) -> object:
    return SimpleNamespace(chat=SimpleNamespace(completions=completions))


class RecordingStream:
    def __init__(
        self,
        chunks: list[object],
        *,
        request_id: str | None = None,
        error: Exception | None = None,
    ) -> None:
        self.chunks = chunks
        self.error = error
        self.close_calls = 0
        self.response = SimpleNamespace(
            headers={"x-request-id": request_id} if request_id is not None else {}
        )

    def __iter__(self):
        yield from self.chunks
        if self.error is not None:
            raise self.error

    def close(self) -> None:
        self.close_calls += 1


def _chunk(content: str | None, *, dict_delta: bool = False) -> object:
    delta: object = {"content": content} if dict_delta else SimpleNamespace(content=content)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


def _tool_chunk(
    *,
    index: int,
    call_id: str | None = None,
    name: str | None = None,
    arguments: str | None = None,
    content: str | None = None,
) -> object:
    function = SimpleNamespace(name=name, arguments=arguments)
    tool_call = SimpleNamespace(
        index=index,
        id=call_id,
        type="function" if call_id is not None else None,
        function=function,
    )
    delta = SimpleNamespace(content=content, tool_calls=[tool_call])
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


def _usage_chunk(*, input_tokens: object, output_tokens: object) -> object:
    return SimpleNamespace(
        choices=[],
        usage=SimpleNamespace(
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
        ),
    )


def _ignore_provider_metrics(_metrics: ProviderCallMetrics) -> None:
    return None


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
            "qwen3.7-plus",
            {"qwen_api_key": "provider-secret"},
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ),
    ],
)
def test_stream_turn_uses_only_the_selected_provider_and_model(
    tmp_path,
    provider: str,
    model: str,
    settings_overrides: dict[str, str],
    expected_base_url: str | None,
) -> None:
    settings = _settings(tmp_path, **settings_overrides)
    completions = RecordingCompletions([_chunk("response")])
    factory_calls: list[dict[str, str | float | None]] = []

    def client_factory(
        *,
        api_key: str,
        base_url: str | None = None,
        timeout: float,
        max_retries: int,
    ) -> object:
        factory_calls.append(
            {
                "api_key": api_key,
                "base_url": base_url,
                "timeout": timeout,
                "max_retries": max_retries,
            }
        )
        return _client(completions)

    messages = [
        {"role": "system", "content": "platform first", "ignored": "drop me"},
        {"role": "user", "content": "hello"},
    ]
    service = ModelService(settings=settings, client_factory=client_factory)

    result = list(
        service.stream_turn(
            messages,
            provider=provider,
            model=model,
            call_index=1,
            observer=_ignore_provider_metrics,
        )
    )

    assert result == ["response"]
    assert factory_calls == [
        {
            "api_key": "provider-secret",
            "base_url": expected_base_url,
            "timeout": 30.0,
            "max_retries": 0,
        }
    ]
    assert completions.calls == [
        {
            "model": model,
            "messages": [
                {"role": "system", "content": "platform first"},
                {"role": "user", "content": "hello"},
            ],
            "stream": True,
            "stream_options": {"include_usage": True},
        }
    ]


def test_stream_turn_passes_configured_timeout_to_sdk_client(tmp_path) -> None:
    settings = _settings(
        tmp_path,
        openai_api_key="provider-secret",
        model_request_timeout_seconds=0.25,
    )
    captured: dict[str, object] = {}

    def client_factory(**kwargs: object) -> object:
        captured.update(kwargs)
        return _client(RecordingCompletions([_chunk("response")]))

    result = list(
        ModelService(settings=settings, client_factory=client_factory).stream_turn(
            [{"role": "user", "content": "hello"}],
            provider="openai",
            model="gpt-4.1-mini",
            call_index=1,
            observer=_ignore_provider_metrics,
        )
    )

    assert result == ["response"]
    assert captured["timeout"] == 0.25
    assert captured["max_retries"] == 0


def test_stream_turn_passes_strict_openai_compatible_multimodal_user_blocks(
    tmp_path,
) -> None:
    settings = _settings(tmp_path, openai_api_key="provider-secret")
    completions = RecordingCompletions([_chunk("response")])
    messages: list[dict[str, object]] = [
        {"role": "system", "content": "platform"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "inspect"},
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,cG5n"},
                },
            ],
        },
    ]
    service = ModelService(
        settings=settings,
        client_factory=lambda **_kwargs: _client(completions),
    )

    assert list(
        service.stream_turn(
            messages,
            provider="openai",
            model="gpt-4.1-mini",
            call_index=1,
            observer=_ignore_provider_metrics,
        )
    ) == ["response"]

    assert completions.calls[0]["messages"] == messages


@pytest.mark.parametrize(
    "message",
    [
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "not allowed"}],
        },
        {
            "role": "user",
            "content": [{"type": "text", "text": "x", "extra": True}],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/png;base64,cG5n",
                        "detail": "high",
                    },
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": "https://example.test/private.png"},
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,not-valid="},
                }
            ],
        },
        {"role": "user", "content": [b"raw bytes"]},
    ],
)
def test_stream_turn_rejects_malformed_multimodal_blocks_before_provider(
    tmp_path,
    message: dict[str, object],
) -> None:
    settings = _settings(tmp_path, openai_api_key="provider-secret")

    def unexpected_factory(**_kwargs: object) -> object:
        raise AssertionError("provider must not be called")

    with pytest.raises(ValueError, match="validated chat messages are required"):
        list(
            ModelService(
                settings=settings,
                client_factory=unexpected_factory,
            ).stream_turn(
                [message],
                provider="openai",
                model="gpt-4.1-mini",
                call_index=1,
                observer=_ignore_provider_metrics,
            )
        )


def test_stream_turn_yields_chunks_and_ignores_empty_choices(tmp_path) -> None:
    settings = _settings(tmp_path, openai_api_key="provider-secret")
    completions = RecordingCompletions(
        [
            SimpleNamespace(choices=[]),
            SimpleNamespace(choices=None),
            SimpleNamespace(),
            _chunk("Hel"),
            _chunk("lo", dict_delta=True),
            _chunk(None),
            _chunk(""),
        ]
    )
    service = ModelService(
        settings=settings,
        client_factory=lambda **_kwargs: _client(completions),
    )

    chunks = list(
        service.stream_turn(
            [{"role": "user", "content": "Say hello"}],
            provider="openai",
            model="gpt-4.1-mini",
            call_index=1,
            observer=_ignore_provider_metrics,
        )
    )

    assert chunks == ["Hel", "lo"]


def test_stream_turn_sends_tool_schemas_and_assembles_one_fragmented_tool_call(
    tmp_path,
) -> None:
    settings = _settings(tmp_path, openai_api_key="provider-secret")
    completions = RecordingCompletions(
        [
            _tool_chunk(
                index=0,
                call_id="call-1",
                name="read_file",
                arguments='{"path":"notes',
            ),
            _tool_chunk(index=0, arguments='/today.md"}'),
        ]
    )
    definition = ToolDefinition(
        name="read_file",
        description="Read one workspace file.",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    )
    service = ModelService(
        settings=settings,
        client_factory=lambda **_kwargs: _client(completions),
    )

    events = list(
        service.stream_turn(
            [{"role": "user", "content": "read today's notes"}],
            provider="openai",
            model="gpt-4.1-mini",
            call_index=1,
            observer=_ignore_provider_metrics,
            tools=(definition,),
        )
    )

    assert events == [
        ToolCall(
            id="call-1",
            name="read_file",
            arguments={"path": "notes/today.md"},
        )
    ]
    assert completions.calls == [
        {
            "model": "gpt-4.1-mini",
            "messages": [
                {"role": "user", "content": "read today's notes"}
            ],
            "stream": True,
            "stream_options": {"include_usage": True},
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": definition.name,
                        "description": definition.description,
                        "parameters": definition.input_schema,
                    },
                }
            ],
        }
    ]


@pytest.mark.parametrize(
    "stream",
    [
        [
            _chunk("partial text"),
            _tool_chunk(
                index=0,
                call_id="call-1",
                name="read_file",
                arguments='{"path":"notes.md"}',
            ),
        ],
        [
            _tool_chunk(
                index=0,
                call_id="call-1",
                name="read_file",
                arguments='{"path":"notes.md"}',
            ),
            _chunk("partial text"),
        ],
    ],
)
def test_stream_turn_rejects_mixed_text_and_tool_call_output(
    tmp_path,
    stream: list[object],
) -> None:
    settings = _settings(tmp_path, openai_api_key="provider-secret")
    service = ModelService(
        settings=settings,
        client_factory=lambda **_kwargs: _client(RecordingCompletions(stream)),
    )

    with pytest.raises(HTTPException) as captured:
        list(
            service.stream_turn(
                [{"role": "user", "content": "hello"}],
                provider="openai",
                model="gpt-4.1-mini",
                call_index=1,
                observer=_ignore_provider_metrics,
            )
        )

    assert captured.value.status_code == 502
    assert captured.value.detail["code"] == "provider_call_failed"


def test_stream_turn_rejects_multiple_or_malformed_tool_calls(tmp_path) -> None:
    settings = _settings(tmp_path, openai_api_key="provider-secret")
    multiple = SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            index=0,
                            id="call-1",
                            type="function",
                            function=SimpleNamespace(name="one", arguments="{}"),
                        ),
                        SimpleNamespace(
                            index=1,
                            id="call-2",
                            type="function",
                            function=SimpleNamespace(name="two", arguments="{}"),
                        ),
                    ],
                )
            )
        ]
    )

    for stream in (
        [multiple],
        [
            _tool_chunk(
                index=0,
                call_id="call-1",
                name="read_file",
                arguments="not-json",
            )
        ],
    ):
        service = ModelService(
            settings=settings,
            client_factory=lambda **_kwargs: _client(
                RecordingCompletions(stream)
            ),
        )
        with pytest.raises(HTTPException) as captured:
            list(
                service.stream_turn(
                    [{"role": "user", "content": "hello"}],
                    provider="openai",
                    model="gpt-4.1-mini",
                    call_index=1,
                    observer=_ignore_provider_metrics,
                )
            )
        assert captured.value.status_code == 502
        assert captured.value.detail["code"] == "provider_call_failed"


@pytest.mark.parametrize(
    "messages",
    [
        [],
        [{}],
        [{"role": "tool", "content": "not allowed"}],
        [{"role": [], "content": "not allowed"}],
        [{"role": "user", "content": ""}],
        [{"role": "user", "content": None}],
        [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [],
            }
        ],
        [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": "[]",
                        },
                    }
                ],
            }
        ],
        [{"role": "tool", "tool_call_id": "", "content": "{}"}],
        ["not a message"],
    ],
)
def test_stream_turn_rejects_invalid_messages_before_calling_provider(
    tmp_path,
    messages: list[object],
) -> None:
    settings = _settings(tmp_path, openai_api_key="provider-secret")

    def unexpected_factory(**_kwargs: object) -> object:
        raise AssertionError("provider must not be called")

    service = ModelService(settings=settings, client_factory=unexpected_factory)

    with pytest.raises(ValueError, match="validated chat messages are required"):
        list(
            service.stream_turn(  # type: ignore[arg-type]
                messages,
                provider="openai",
                model="gpt-4.1-mini",
                call_index=1,
                observer=_ignore_provider_metrics,
            )
        )


@pytest.mark.parametrize(
    ("provider", "model", "settings_overrides"),
    [
        ("openai", "gpt-4.1-mini", {}),
        ("missing", "anything", {"openai_api_key": "provider-secret"}),
        ("openai", "missing", {"openai_api_key": "provider-secret"}),
        ("", "gpt-4.1-mini", {"openai_api_key": "provider-secret"}),
        ("openai", "", {"openai_api_key": "provider-secret"}),
        (None, "gpt-4.1-mini", {"openai_api_key": "provider-secret"}),
        ("openai", None, {"openai_api_key": "provider-secret"}),
    ],
)
def test_stream_turn_never_falls_back_from_an_unavailable_explicit_model(
    tmp_path,
    provider: str | None,
    model: str | None,
    settings_overrides: dict[str, str],
) -> None:
    settings = _settings(tmp_path, **settings_overrides)

    def unexpected_factory(**_kwargs: object) -> object:
        raise AssertionError("provider must not be called")

    service = ModelService(settings=settings, client_factory=unexpected_factory)

    with pytest.raises(HTTPException) as captured:
        list(
            service.stream_turn(
                [{"role": "user", "content": "hello"}],
                provider=provider,  # type: ignore[arg-type]
                model=model,  # type: ignore[arg-type]
                call_index=1,
                observer=_ignore_provider_metrics,
            )
        )

    assert captured.value.status_code == 503
    assert captured.value.detail == {
        "code": "model_unavailable",
        "message": "The selected model is unavailable.",
    }


def test_stream_turn_maps_an_empty_stream_to_provider_failure(tmp_path) -> None:
    settings = _settings(tmp_path, openai_api_key="provider-secret")
    completions = RecordingCompletions(
        [SimpleNamespace(choices=None), SimpleNamespace(choices=[])]
    )
    service = ModelService(
        settings=settings,
        client_factory=lambda **_kwargs: _client(completions),
    )

    with pytest.raises(HTTPException) as captured:
        list(
            service.stream_turn(
                [{"role": "user", "content": "hello"}],
                provider="openai",
                model="gpt-4.1-mini",
                call_index=1,
                observer=_ignore_provider_metrics,
            )
        )

    assert captured.value.status_code == 502
    assert captured.value.detail["code"] == "provider_call_failed"


def test_stream_turn_failure_log_never_contains_input_exception_or_secret(
    tmp_path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = _settings(tmp_path, openai_api_key="api-secret-value")
    private_content = "user-private-message"

    class FailingCompletions:
        def create(self, **_kwargs: object) -> object:
            raise RuntimeError(
                f"upstream-super-secret-error {private_content} api-secret-value"
            )

    service = ModelService(
        settings=settings,
        client_factory=lambda **_kwargs: _client(FailingCompletions()),
    )

    with caplog.at_level(logging.WARNING, logger="app.services.model_service"):
        with pytest.raises(HTTPException) as captured:
            list(
                service.stream_turn(
                    [{"role": "user", "content": private_content}],
                    provider="openai",
                    model="gpt-4.1-mini",
                    call_index=1,
                    observer=_ignore_provider_metrics,
                )
            )

    assert captured.value.status_code == 502
    assert captured.value.detail["code"] == "provider_call_failed"
    records = [
        record for record in caplog.records if record.getMessage() == "provider_stream_failed"
    ]
    assert len(records) == 1
    record = records[0]
    assert record.provider == "openai"
    assert record.model == "gpt-4.1-mini"
    assert record.exception_type == "RuntimeError"
    assert record.error_code == "provider_call_failed"
    assert not record.exc_info
    assert not hasattr(record, "error_message")
    for forbidden in (
        private_content,
        "upstream-super-secret-error",
        "api-secret-value",
        "Traceback",
    ):
        assert forbidden not in caplog.text
        assert forbidden not in str(captured.value.detail)


def test_stream_turn_wraps_provider_http_exception_without_leaking_detail(
    tmp_path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = _settings(tmp_path, openai_api_key="provider-secret")
    upstream_sentinel = "upstream-http-secret"

    class FailingCompletions:
        def create(self, **_kwargs: object) -> object:
            raise HTTPException(
                status_code=429,
                detail={"message": upstream_sentinel},
            )

    service = ModelService(
        settings=settings,
        client_factory=lambda **_kwargs: _client(FailingCompletions()),
    )

    with caplog.at_level(logging.WARNING, logger="app.services.model_service"):
        with pytest.raises(HTTPException) as captured:
            list(
                service.stream_turn(
                    [{"role": "user", "content": "private request"}],
                    provider="openai",
                    model="gpt-4.1-mini",
                    call_index=1,
                    observer=_ignore_provider_metrics,
                )
            )

    assert captured.value.status_code == 502
    assert captured.value.detail["code"] == "provider_call_failed"
    assert upstream_sentinel not in str(captured.value.detail)
    assert upstream_sentinel not in caplog.text
    record = next(
        record for record in caplog.records if record.getMessage() == "provider_stream_failed"
    )
    assert record.exception_type == "HTTPException"
    assert record.error_code == "provider_call_failed"
    assert not record.exc_info


@pytest.mark.parametrize(
    ("provider", "model", "settings_overrides"),
    [
        ("openai", "gpt-4.1-mini", {"openai_api_key": "provider-secret"}),
        ("deepseek", "deepseek-chat", {"deepseek_api_key": "provider-secret"}),
        ("qwen", "qwen3.7-plus", {"qwen_api_key": "provider-secret"}),
    ],
)
def test_stream_turn_reports_structured_provider_metrics_once(
    tmp_path,
    provider: str,
    model: str,
    settings_overrides: dict[str, str],
) -> None:
    stream = RecordingStream(
        [
            _chunk("answer"),
            _usage_chunk(input_tokens=12, output_tokens=4),
        ],
        request_id="provider-request-1",
    )
    completions = RecordingCompletions(stream)
    observed: list[ProviderCallMetrics] = []
    clock = iter([10.0, 10.02, 10.03]).__next__
    service = ModelService(
        settings=_settings(tmp_path, **settings_overrides),
        client_factory=lambda **_kwargs: _client(completions),
        clock=clock,
    )

    assert list(
        service.stream_turn(
            [{"role": "user", "content": "hello"}],
            provider=provider,
            model=model,
            call_index=3,
            observer=observed.append,
        )
    ) == ["answer"]

    assert completions.calls[0]["stream_options"] == {"include_usage": True}
    assert stream.close_calls == 1
    assert observed == [
        ProviderCallMetrics(
            call_index=3,
            provider=provider,
            model=model,
            provider_request_id="provider-request-1",
            input_tokens=12,
            output_tokens=4,
            first_token_latency_ms=20.0,
            duration_ms=30.0,
            status="completed",
            unavailable_fields=(),
        )
    ]


def test_stream_turn_marks_missing_or_unsafe_metrics_unavailable(tmp_path) -> None:
    stream = RecordingStream(
        [
            _tool_chunk(
                index=0,
                call_id="call-1",
                name="read_file",
                arguments='{"path":"notes.md"}',
            ),
            _usage_chunk(input_tokens=True, output_tokens=10**5_000),
        ],
        request_id="unsafe request id",
    )
    observed: list[ProviderCallMetrics] = []
    service = ModelService(
        settings=_settings(tmp_path, openai_api_key="provider-secret"),
        client_factory=lambda **_kwargs: _client(RecordingCompletions(stream)),
        clock=iter([1.0, 1.015]).__next__,
    )

    events = list(
        service.stream_turn(
            [{"role": "user", "content": "read"}],
            provider="openai",
            model="gpt-4.1-mini",
            call_index=1,
            observer=observed.append,
        )
    )

    assert events == [
        ToolCall(
            id="call-1",
            name="read_file",
            arguments={"path": "notes.md"},
        )
    ]
    assert observed[0].status == "completed"
    assert observed[0].provider_request_id is None
    assert observed[0].input_tokens is None
    assert observed[0].output_tokens is None
    assert observed[0].first_token_latency_ms is None
    assert observed[0].unavailable_fields == (
        "provider_request_id",
        "input_tokens",
        "output_tokens",
        "first_token_latency_ms",
    )


def test_stream_turn_failure_and_close_each_emit_one_failed_metric(tmp_path) -> None:
    provider_error = RuntimeError("private provider failure")
    failing_stream = RecordingStream(
        [_chunk("partial")],
        request_id="provider-failure-1",
        error=provider_error,
    )
    failed: list[ProviderCallMetrics] = []
    failing = ModelService(
        settings=_settings(tmp_path, openai_api_key="provider-secret"),
        client_factory=lambda **_kwargs: _client(
            RecordingCompletions(failing_stream)
        ),
        clock=iter([2.0, 2.01, 2.03]).__next__,
    )

    with pytest.raises(HTTPException) as captured:
        list(
            failing.stream_turn(
                [{"role": "user", "content": "hello"}],
                provider="openai",
                model="gpt-4.1-mini",
                call_index=1,
                observer=failed.append,
            )
        )

    assert captured.value.detail["code"] == "provider_call_failed"
    assert len(failed) == 1
    assert failed[0].status == "failed"
    assert failed[0].provider_request_id == "provider-failure-1"
    assert failed[0].first_token_latency_ms == 10.0
    assert failing_stream.close_calls == 1

    cancelled_stream = RecordingStream(
        [_chunk("first"), _chunk("never consumed")],
        request_id="provider-cancel-1",
    )
    cancelled: list[ProviderCallMetrics] = []
    service = ModelService(
        settings=_settings(tmp_path, openai_api_key="provider-secret"),
        client_factory=lambda **_kwargs: _client(
            RecordingCompletions(cancelled_stream)
        ),
        clock=iter([3.0, 3.01, 3.02]).__next__,
    )
    generator = service.stream_turn(
        [{"role": "user", "content": "hello"}],
        provider="openai",
        model="gpt-4.1-mini",
        call_index=1,
        observer=cancelled.append,
    )
    assert next(generator) == "first"
    generator.close()

    assert len(cancelled) == 1
    assert cancelled[0].status == "failed"
    assert cancelled_stream.close_calls == 1


def test_structured_sdk_error_request_id_survives_observer_failure(
    tmp_path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    response = httpx.Response(
        429,
        request=httpx.Request("POST", "https://private.example/chat"),
        headers={"x-request-id": "provider-error-1"},
    )
    provider_error = APIStatusError(
        "private provider body",
        response=response,
        body={"secret": "private provider body"},
    )

    class FailingCompletions:
        def create(self, **_kwargs: object) -> object:
            raise provider_error

    observed: list[ProviderCallMetrics] = []

    def failing_observer(metrics: ProviderCallMetrics) -> None:
        observed.append(metrics)
        raise RuntimeError("private observer detail")

    service = ModelService(
        settings=_settings(tmp_path, openai_api_key="provider-secret"),
        client_factory=lambda **_kwargs: _client(FailingCompletions()),
        clock=iter([4.0, 4.01]).__next__,
    )

    with caplog.at_level(logging.ERROR, logger="app.services.model_service"):
        with pytest.raises(HTTPException) as captured:
            list(
                service.stream_turn(
                    [{"role": "user", "content": "private input"}],
                    provider="openai",
                    model="gpt-4.1-mini",
                    call_index=1,
                    observer=failing_observer,
                )
            )

    assert captured.value.detail["code"] == "provider_call_failed"
    assert len(observed) == 1
    assert observed[0].provider_request_id == "provider-error-1"
    assert observed[0].status == "failed"
    assert "provider_observer_failed" in caplog.messages
    for forbidden in (
        "private.example",
        "private provider body",
        "private observer detail",
        "private input",
    ):
        assert forbidden not in caplog.text


def test_extreme_clock_values_cannot_break_a_successful_provider_stream(
    tmp_path,
) -> None:
    observed: list[ProviderCallMetrics] = []
    stream = RecordingStream([_chunk("answer")])
    service = ModelService(
        settings=_settings(tmp_path, openai_api_key="provider-secret"),
        client_factory=lambda **_kwargs: _client(RecordingCompletions(stream)),
        clock=iter([0, 10**5_000, 10**5_000]).__next__,
    )

    assert list(
        service.stream_turn(
            [{"role": "user", "content": "hello"}],
            provider="openai",
            model="gpt-4.1-mini",
            call_index=1,
            observer=observed.append,
        )
    ) == ["answer"]
    assert observed[0].first_token_latency_ms == 0.0
    assert observed[0].duration_ms == 0.0


def test_model_service_exposes_only_runtime_chat_methods() -> None:
    public_methods = {
        name
        for name in dir(ModelService)
        if not name.startswith("_") and callable(getattr(ModelService, name))
    }

    assert public_methods == {"stream_turn"}
