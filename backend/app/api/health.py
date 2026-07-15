from fastapi import APIRouter, Request

router = APIRouter(prefix="/api")


@router.get("/health")
def health(request: Request) -> dict[str, object]:
    settings = request.app.state.settings
    return {
        "status": "ok",
        "version": settings.app_version,
        "storage": {
            "mysql": "configured",
            "qdrant": "configured",
            "object_store": settings.object_store_backend,
        },
        "providers": {
            "openai": bool(settings.openai_api_key),
            "deepseek": bool(settings.deepseek_api_key),
            "qwen": bool(settings.qwen_api_key),
        },
    }
