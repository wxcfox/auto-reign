from collections.abc import Iterator

from fastapi import APIRouter, Depends, Request, UploadFile
from sqlalchemy.orm import Session

from app.core.errors import not_found
from app.db.session import session_scope
from app.repositories.sqlite import DocumentRepository
from app.schemas.documents import DocumentListResponse, DocumentResponse, DocumentUpdate
from app.services.document_service import DocumentService
from app.services.rag_service import RagService

router = APIRouter(prefix="/api/documents")


def get_session(request: Request) -> Iterator[Session]:
    with session_scope(request.app.state.session_factory) as session:
        yield session


@router.post("/upload", response_model=DocumentResponse)
async def upload_document(file: UploadFile, session: Session = Depends(get_session)) -> DocumentResponse:
    document = await DocumentService().upload_document(session, file)
    return DocumentResponse.model_validate(document)


@router.get("", response_model=DocumentListResponse)
def list_documents(session: Session = Depends(get_session)) -> DocumentListResponse:
    documents = DocumentRepository().list(session)
    return DocumentListResponse(
        documents=[DocumentResponse.model_validate(document) for document in documents]
    )


@router.get("/{document_id}", response_model=DocumentResponse)
def get_document(document_id: str, session: Session = Depends(get_session)) -> DocumentResponse:
    document = DocumentRepository().get(session, document_id)
    if document is None:
        raise not_found("document_not_found", "Document not found.")
    return DocumentResponse.model_validate(document)


@router.patch("/{document_id}", response_model=DocumentResponse)
def update_document(
    document_id: str, update: DocumentUpdate, session: Session = Depends(get_session)
) -> DocumentResponse:
    document = DocumentRepository().get(session, document_id)
    if document is None:
        raise not_found("document_not_found", "Document not found.")
    for field, value in update.model_dump(exclude_unset=True).items():
        setattr(document, field, value)
    session.flush()
    return DocumentResponse.model_validate(document)


@router.post("/{document_id}/reindex", response_model=DocumentResponse)
def reindex_document(document_id: str, session: Session = Depends(get_session)) -> DocumentResponse:
    document = RagService().reindex_document(session, document_id)
    return DocumentResponse.model_validate(document)
