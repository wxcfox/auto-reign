from fastapi import APIRouter, Depends, status

from app.api.dependencies import SessionDep, get_current_admin, get_current_user
from app.db import models
from app.schemas.agents import (
    AgentCreateRequest,
    AgentListResponse,
    AgentPutRequest,
    AgentResponse,
)
from app.schemas.resources import ResourceDeleteResponse, ResourceId, ResourceListScope
from app.services.agent_service import AgentService


router = APIRouter(prefix="/api/agents")
admin_router = APIRouter(prefix="/api/admin/agents")


@router.get("", response_model=AgentListResponse)
def list_agents(
    session: SessionDep,
    actor: models.User = Depends(get_current_user),
    scope: ResourceListScope = "visible",
    include_inactive: bool = False,
) -> AgentListResponse:
    return AgentService().list_agents(
        session,
        actor=actor,
        scope=scope,
        include_inactive=include_inactive,
    )


@router.post("", response_model=AgentResponse, status_code=status.HTTP_201_CREATED)
def create_private_agent(
    payload: AgentCreateRequest,
    session: SessionDep,
    actor: models.User = Depends(get_current_user),
) -> AgentResponse:
    return AgentService().create_private(session, actor=actor, payload=payload)


@admin_router.post(
    "",
    response_model=AgentResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_global_agent(
    payload: AgentCreateRequest,
    session: SessionDep,
    actor: models.User = Depends(get_current_admin),
) -> AgentResponse:
    return AgentService().create_global(session, actor=actor, payload=payload)


@router.get("/{agent_id}", response_model=AgentResponse)
def get_agent(
    agent_id: ResourceId,
    session: SessionDep,
    actor: models.User = Depends(get_current_user),
) -> AgentResponse:
    return AgentService().get_agent(session, actor=actor, agent_id=agent_id)


@router.put("/{agent_id}", response_model=AgentResponse)
def put_agent(
    agent_id: ResourceId,
    payload: AgentPutRequest,
    session: SessionDep,
    actor: models.User = Depends(get_current_user),
) -> AgentResponse:
    return AgentService().put_agent(
        session,
        actor=actor,
        agent_id=agent_id,
        payload=payload,
    )


@router.delete("/{agent_id}", response_model=ResourceDeleteResponse)
def delete_agent(
    agent_id: ResourceId,
    session: SessionDep,
    actor: models.User = Depends(get_current_user),
) -> ResourceDeleteResponse:
    return AgentService().delete_agent(session, actor=actor, agent_id=agent_id)
