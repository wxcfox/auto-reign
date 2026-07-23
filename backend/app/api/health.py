from fastapi import APIRouter, Request, Response, status

router = APIRouter(prefix="/api")


@router.get("/health")
def health(request: Request) -> dict[str, object]:
    settings = request.app.state.settings
    chat_realtime = getattr(request.app.state, "chat_realtime", None)
    realtime_backend = getattr(chat_realtime, "backend", "memory")
    realtime_degraded = getattr(chat_realtime, "degraded", True)
    return {
        "status": "ok",
        "version": settings.app_version,
        "storage": {
            "mysql": "configured",
            "elasticsearch": "configured",
            "qdrant": "configured",
            "object_store": settings.object_store_backend,
        },
        "providers": {
            "openai": bool(settings.openai_api_key),
            "deepseek": bool(settings.deepseek_api_key),
            "qwen": bool(settings.qwen_api_key),
        },
        "chat_realtime": {
            "backend": realtime_backend,
            "degraded": realtime_degraded,
        },
    }


@router.get("/health/retrievers")
def retriever_health(request: Request, response: Response) -> dict[str, object]:
    connections = request.app.state.knowledge_retriever_factory.test_connections()
    healthy = all(connections.values())
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ok" if healthy else "unavailable",
        "retrievers": connections,
    }
