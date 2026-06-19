from app.core.config import Settings


def _split_models(value: str) -> list[str]:
    return [model.strip() for model in value.split(",") if model.strip()]


def available_chat_models(settings: Settings) -> list[dict[str, object]]:
    providers: list[dict[str, object]] = []
    if settings.openai_api_key:
        providers.append({"provider": "openai", "models": _split_models(settings.openai_chat_models)})
    if settings.deepseek_api_key:
        providers.append(
            {"provider": "deepseek", "models": _split_models(settings.deepseek_chat_models)}
        )
    if settings.qwen_api_key:
        providers.append({"provider": "qwen", "models": _split_models(settings.qwen_chat_models)})
    return providers
