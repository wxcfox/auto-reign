from fastapi import APIRouter

from app.core.config import get_settings
from app.services.config_service import available_chat_models

router = APIRouter(prefix="/api")


@router.get("/models")
def models() -> dict[str, list[dict[str, object]]]:
    settings = get_settings()
    return {"providers": available_chat_models(settings)}
