from fastapi import APIRouter, Depends, status

from app.api.dependencies import SessionDep, get_current_admin, get_current_user
from app.db import models
from app.schemas.knowledge_collections import (
    KnowledgeCollectionCreateRequest,
    KnowledgeCollectionListResponse,
    KnowledgeCollectionPutRequest,
    KnowledgeCollectionResponse,
)
from app.schemas.resources import ResourceDeleteResponse, ResourceId, ResourceListScope
from app.services.knowledge_collection_service import KnowledgeCollectionService

router = APIRouter(prefix="/api/knowledge-collections")
admin_router = APIRouter(prefix="/api/admin/knowledge-collections")


@router.get("", response_model=KnowledgeCollectionListResponse)
def list_knowledge_collections(
    session: SessionDep,
    actor: models.User = Depends(get_current_user),
    scope: ResourceListScope = "visible",
    include_inactive: bool = False,
) -> KnowledgeCollectionListResponse:
    return KnowledgeCollectionService().list_resources(
        session,
        actor=actor,
        scope=scope,
        include_inactive=include_inactive,
    )


@router.post(
    "",
    response_model=KnowledgeCollectionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_private_knowledge_collection(
    payload: KnowledgeCollectionCreateRequest,
    session: SessionDep,
    actor: models.User = Depends(get_current_user),
) -> KnowledgeCollectionResponse:
    return KnowledgeCollectionService().create_private(
        session,
        actor=actor,
        payload=payload,
    )


@admin_router.post(
    "",
    response_model=KnowledgeCollectionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_global_knowledge_collection(
    payload: KnowledgeCollectionCreateRequest,
    session: SessionDep,
    actor: models.User = Depends(get_current_admin),
) -> KnowledgeCollectionResponse:
    return KnowledgeCollectionService().create_global(
        session,
        actor=actor,
        payload=payload,
    )


@router.get(
    "/{collection_id}",
    response_model=KnowledgeCollectionResponse,
)
def get_knowledge_collection(
    collection_id: ResourceId,
    session: SessionDep,
    actor: models.User = Depends(get_current_user),
) -> KnowledgeCollectionResponse:
    return KnowledgeCollectionService().get_resource(
        session,
        actor=actor,
        resource_id=collection_id,
    )


@router.put(
    "/{collection_id}",
    response_model=KnowledgeCollectionResponse,
)
def put_knowledge_collection(
    collection_id: ResourceId,
    payload: KnowledgeCollectionPutRequest,
    session: SessionDep,
    actor: models.User = Depends(get_current_user),
) -> KnowledgeCollectionResponse:
    return KnowledgeCollectionService().put_resource(
        session,
        actor=actor,
        resource_id=collection_id,
        payload=payload,
    )


@router.delete(
    "/{collection_id}",
    response_model=ResourceDeleteResponse,
)
def delete_knowledge_collection(
    collection_id: ResourceId,
    session: SessionDep,
    actor: models.User = Depends(get_current_user),
) -> ResourceDeleteResponse:
    return KnowledgeCollectionService().delete_resource(
        session,
        actor=actor,
        resource_id=collection_id,
    )


@admin_router.put(
    "/{collection_id}",
    response_model=KnowledgeCollectionResponse,
)
def put_global_knowledge_collection(
    collection_id: ResourceId,
    payload: KnowledgeCollectionPutRequest,
    session: SessionDep,
    actor: models.User = Depends(get_current_admin),
) -> KnowledgeCollectionResponse:
    return KnowledgeCollectionService().put_global_resource(
        session,
        actor=actor,
        resource_id=collection_id,
        payload=payload,
    )


@admin_router.delete(
    "/{collection_id}",
    response_model=ResourceDeleteResponse,
)
def delete_global_knowledge_collection(
    collection_id: ResourceId,
    session: SessionDep,
    actor: models.User = Depends(get_current_admin),
) -> ResourceDeleteResponse:
    return KnowledgeCollectionService().delete_global_resource(
        session,
        actor=actor,
        resource_id=collection_id,
    )
