from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings


@dataclass(frozen=True)
class ChatProvider:
    name: str
    api_key: str | None
    models: tuple[str, ...]
    base_url: str | None


def split_models(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(model.strip() for model in value.split(",") if model.strip()))


def chat_providers(settings: Settings) -> tuple[ChatProvider, ...]:
    return (
        ChatProvider(
            "openai",
            settings.openai_api_key,
            split_models(settings.openai_chat_models),
            None,
        ),
        ChatProvider(
            "deepseek",
            settings.deepseek_api_key,
            split_models(settings.deepseek_chat_models),
            settings.deepseek_base_url,
        ),
        ChatProvider(
            "qwen",
            settings.qwen_api_key,
            split_models(settings.qwen_chat_models),
            settings.qwen_base_url,
        ),
    )


def configured_chat_providers(settings: Settings) -> tuple[ChatProvider, ...]:
    return tuple(
        provider for provider in chat_providers(settings) if provider.api_key and provider.models
    )


def default_chat_provider(settings: Settings) -> ChatProvider | None:
    configured = configured_chat_providers(settings)
    preferred = next(
        (provider for provider in configured if provider.name == settings.default_chat_provider),
        None,
    )
    return preferred or next(iter(configured), None)


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
