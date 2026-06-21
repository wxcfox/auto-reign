from collections.abc import Iterator

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import session_scope
from app.services.rag_service import RagService

router = APIRouter(prefix="/api/rag")


class RagSearchRequest(BaseModel):
    query: str
    limit: int = Field(default=5, ge=1, le=20)


class RagSearchHit(BaseModel):
    content: str
    score: float
    source_type: str
    source_id: str


class RagSearchResponse(BaseModel):
    hits: list[RagSearchHit]


def get_session(request: Request) -> Iterator[Session]:
    with session_scope(request.app.state.session_factory) as session:
        yield session


@router.post("/search", response_model=RagSearchResponse)
def search_rag(request: RagSearchRequest, session: Session = Depends(get_session)) -> RagSearchResponse:
    hits = RagService().search(session, request.query, request.limit)
    return RagSearchResponse(hits=[RagSearchHit(**hit) for hit in hits])
