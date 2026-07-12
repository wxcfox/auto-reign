from app.core.config import Settings
from app.core.model_providers import configured_chat_providers, default_chat_provider


def available_chat_models(settings: Settings) -> list[dict[str, object]]:
    return [
        {"provider": provider.name, "models": list(provider.models)}
        for provider in configured_chat_providers(settings)
    ]


def default_chat_model(settings: Settings) -> dict[str, str] | None:
    provider = default_chat_provider(settings)
    if provider is None or not provider.models:
        return None
    return {"provider": provider.name, "model": provider.models[0]}
