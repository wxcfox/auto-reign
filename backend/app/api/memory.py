from fastapi import APIRouter

from app.schemas.memory import MemoryFileContent, MemoryResponse
from app.services.memory_service import MemoryService

router = APIRouter(prefix="/api")


@router.get("/memory", response_model=MemoryResponse)
def get_memory() -> MemoryResponse:
    files = MemoryService().read_memory()
    return MemoryResponse(
        files={
            kind: MemoryFileContent(kind=kind, content=content, updated_at=updated_at)
            for kind, (content, updated_at) in files.items()
        }
    )
