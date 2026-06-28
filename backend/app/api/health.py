from fastapi import APIRouter

from app.core.config import get_settings

router = APIRouter(prefix="/api")


@router.get("/health")
def health() -> dict[str, object]:
    settings = get_settings()
    return {
        "status": "ok",
        "storage": {
            "mysql": "configured",
            "qdrant": "configured",
        },
        "providers": {
            "openai": bool(settings.openai_api_key),
            "deepseek": bool(settings.deepseek_api_key),
            "qwen": bool(settings.qwen_api_key),
        },
        "workspace": {
            "initialized": (settings.workspace_dir / "workspace.md").exists(),
        },
    }
