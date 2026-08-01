from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings


@dataclass(frozen=True)
class ChatProvider:
    name: str
    api_key: str | None
    models: tuple[str, ...]
    base_url: str | None
    # ``None`` keeps the provider default and sends no request parameter. A
    # boolean pins the OpenAI-protocol ``parallel_tool_calls`` flag so a model
    # that cannot serve parallel tool calls degrades to sequential ReAct rounds
    # instead of producing an unparseable stream.
    parallel_tool_calls: bool | None = None


def split_models(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(model.strip() for model in value.split(",") if model.strip()))


def _parallel_tool_calls(value: str) -> bool | None:
    if value == "on":
        return True
    if value == "off":
        return False
    return None


def chat_providers(settings: Settings) -> tuple[ChatProvider, ...]:
    return (
        ChatProvider(
            "openai",
            settings.openai_api_key,
            split_models(settings.openai_chat_models),
            None,
            _parallel_tool_calls(settings.openai_parallel_tool_calls),
        ),
        ChatProvider(
            "deepseek",
            settings.deepseek_api_key,
            split_models(settings.deepseek_chat_models),
            settings.deepseek_base_url,
            _parallel_tool_calls(settings.deepseek_parallel_tool_calls),
        ),
        ChatProvider(
            "qwen",
            settings.qwen_api_key,
            split_models(settings.qwen_chat_models),
            settings.qwen_base_url,
            _parallel_tool_calls(settings.qwen_parallel_tool_calls),
        ),
    )


def configured_chat_providers(settings: Settings) -> tuple[ChatProvider, ...]:
    return tuple(
        provider for provider in chat_providers(settings) if provider.api_key and provider.models
    )


def default_chat_provider(settings: Settings) -> ChatProvider | None:
    configured = configured_chat_providers(settings)
    return next(
        (provider for provider in configured if provider.name == settings.default_chat_provider),
        None,
    )


def preferred_chat_provider(settings: Settings) -> ChatProvider:
    providers = chat_providers(settings)
    return next(
        (provider for provider in providers if provider.name == settings.default_chat_provider),
        providers[0],
    )


def find_chat_provider(settings: Settings, name: str | None) -> ChatProvider | None:
    if name is None:
        return default_chat_provider(settings)
    return next(
        (provider for provider in configured_chat_providers(settings) if provider.name == name),
        None,
    )
