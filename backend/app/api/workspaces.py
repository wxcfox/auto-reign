from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.api.dependencies import SessionDep, get_current_admin, get_current_user
from app.core.errors import bad_request, conflict, not_found, service_unavailable
from app.db import models
from app.schemas.resources import ResourceDeleteResponse, ResourceId, ResourceListScope
from app.schemas.workspaces import (
    CreateWorkspaceFileRequest,
    WorkspaceFileContent,
    WorkspaceFileItem,
    WorkspaceFileListResponse,
    WorkspaceCreateRequest,
    WorkspaceListResponse,
    WorkspacePutRequest,
    WorkspaceResponse,
    WriteWorkspaceFileRequest,
)
from app.services.agent_home_paths import normalize_home_path
from app.services.agent_home_service import (
    AgentHomeFile,
    AgentHomeService,
    WorkspaceConflict,
    WorkspaceFileNotUtf8,
    WorkspaceUnavailable,
)
from app.services.workspace_resource_service import WorkspaceResourceService
from app.storage.object_store import ObjectConflict, ObjectNotFound, ObjectStoreError

router = APIRouter(prefix="/api/workspaces")
admin_router = APIRouter(prefix="/api/admin/workspaces")


def get_agent_home_service(request: Request) -> AgentHomeService:
    return cast(AgentHomeService, request.app.state.agent_home_service)


AgentHomeDep = Annotated[AgentHomeService, Depends(get_agent_home_service)]


def get_workspace_resource_service() -> WorkspaceResourceService:
    return WorkspaceResourceService()


WorkspaceResourceServiceDep = Annotated[
    WorkspaceResourceService,
    Depends(get_workspace_resource_service),
]

_WORKSPACE_FILE_ERRORS = (
    ValueError,
    WorkspaceConflict,
    WorkspaceFileNotUtf8,
    WorkspaceUnavailable,
    ObjectStoreError,
)


def _workspace_file_error(error: Exception) -> HTTPException:
    if isinstance(error, (WorkspaceConflict, ObjectConflict)):
        return conflict(
            "workspace_conflict",
            "The workspace file changed. Read it again before writing.",
        )
    if isinstance(error, ObjectNotFound):
        return not_found(
            "workspace_file_not_found",
            "Workspace file not found.",
        )
    if isinstance(error, ValueError):
        return bad_request(
            "workspace_file_invalid",
            "Workspace file path or content is invalid.",
        )
    if isinstance(
        error,
        (WorkspaceUnavailable, WorkspaceFileNotUtf8, ObjectStoreError),
    ):
        return service_unavailable(
            "workspace_unavailable",
            "Workspace storage is unavailable.",
        )
    return service_unavailable(
        "workspace_unavailable",
        "Workspace storage is unavailable.",
    )


def _file_content(file: AgentHomeFile) -> WorkspaceFileContent:
    return WorkspaceFileContent(
        path=file.path,
        name=file.path.rsplit("/", maxsplit=1)[-1],
        is_directory=False,
        size_bytes=file.size_bytes,
        etag=file.etag,
        content=file.content,
    )


def _require_workspace(
    *,
    workspace_id: str,
    session: SessionDep,
    actor: models.User,
    authority_scope: Literal["private", "global"],
    workspace_service: WorkspaceResourceService,
) -> WorkspaceResponse:
    return workspace_service.require_file_access(
        session,
        actor=actor,
        workspace_id=workspace_id,
        authority_scope=authority_scope,
    )


def _initialize_workspace(
    *,
    workspace: WorkspaceResponse,
    actor: models.User,
    agent_home: AgentHomeService,
) -> None:
    try:
        agent_home.ensure_agents_md(
            user_id=actor.id,
            workspace_id=workspace.id,
            initial_content=workspace.config.initial_agents_md,
        )
    except _WORKSPACE_FILE_ERRORS as error:
        raise _workspace_file_error(error) from error


def _list_workspace_files(
    *,
    workspace_id: str,
    session: SessionDep,
    actor: models.User,
    authority_scope: Literal["private", "global"],
    directory: str,
    agent_home: AgentHomeService,
    workspace_service: WorkspaceResourceService,
) -> WorkspaceFileListResponse:
    workspace = _require_workspace(
        workspace_id=workspace_id,
        session=session,
        actor=actor,
        authority_scope=authority_scope,
        workspace_service=workspace_service,
    )
    _initialize_workspace(workspace=workspace, actor=actor, agent_home=agent_home)
    try:
        items = agent_home.list_files(
            user_id=actor.id,
            workspace_id=workspace.id,
            directory=directory,
        )
    except _WORKSPACE_FILE_ERRORS as error:
        raise _workspace_file_error(error) from error
    return WorkspaceFileListResponse(
        directory=directory,
        items=[
            WorkspaceFileItem(
                path=item.path,
                name=item.name,
                is_directory=item.is_directory,
                size_bytes=item.size_bytes,
                etag=item.etag,
            )
            for item in items
        ],
    )


def _read_workspace_file(
    *,
    workspace_id: str,
    session: SessionDep,
    actor: models.User,
    authority_scope: Literal["private", "global"],
    path: str,
    agent_home: AgentHomeService,
    workspace_service: WorkspaceResourceService,
) -> WorkspaceFileContent:
    workspace = _require_workspace(
        workspace_id=workspace_id,
        session=session,
        actor=actor,
        authority_scope=authority_scope,
        workspace_service=workspace_service,
    )
    _initialize_workspace(workspace=workspace, actor=actor, agent_home=agent_home)
    try:
        file = agent_home.read_file(
            user_id=actor.id,
            workspace_id=workspace.id,
            path=path,
        )
    except _WORKSPACE_FILE_ERRORS as error:
        raise _workspace_file_error(error) from error
    return _file_content(file)


def _create_workspace_file(
    *,
    workspace_id: str,
    session: SessionDep,
    actor: models.User,
    authority_scope: Literal["private", "global"],
    payload: CreateWorkspaceFileRequest,
    agent_home: AgentHomeService,
    workspace_service: WorkspaceResourceService,
) -> WorkspaceFileContent:
    workspace = _require_workspace(
        workspace_id=workspace_id,
        session=session,
        actor=actor,
        authority_scope=authority_scope,
        workspace_service=workspace_service,
    )
    try:
        normalized_path = normalize_home_path(payload.path)
        agent_home.validate_content(payload.content)
    except _WORKSPACE_FILE_ERRORS as error:
        raise _workspace_file_error(error) from error
    _initialize_workspace(workspace=workspace, actor=actor, agent_home=agent_home)
    try:
        file = agent_home.create_file(
            user_id=actor.id,
            workspace_id=workspace.id,
            path=normalized_path,
            content=payload.content,
        )
    except _WORKSPACE_FILE_ERRORS as error:
        raise _workspace_file_error(error) from error
    return _file_content(file)


def _write_workspace_file(
    *,
    workspace_id: str,
    session: SessionDep,
    actor: models.User,
    authority_scope: Literal["private", "global"],
    payload: WriteWorkspaceFileRequest,
    agent_home: AgentHomeService,
    workspace_service: WorkspaceResourceService,
) -> WorkspaceFileContent:
    workspace = _require_workspace(
        workspace_id=workspace_id,
        session=session,
        actor=actor,
        authority_scope=authority_scope,
        workspace_service=workspace_service,
    )
    try:
        normalized_path = normalize_home_path(payload.path)
        agent_home.validate_content(payload.content)
    except _WORKSPACE_FILE_ERRORS as error:
        raise _workspace_file_error(error) from error
    _initialize_workspace(workspace=workspace, actor=actor, agent_home=agent_home)
    try:
        file = agent_home.write_file(
            user_id=actor.id,
            workspace_id=workspace.id,
            path=normalized_path,
            content=payload.content,
            expected_etag=payload.expected_etag,
        )
    except _WORKSPACE_FILE_ERRORS as error:
        raise _workspace_file_error(error) from error
    return _file_content(file)


def _delete_workspace_file(
    *,
    workspace_id: str,
    session: SessionDep,
    actor: models.User,
    authority_scope: Literal["private", "global"],
    path: str,
    agent_home: AgentHomeService,
    workspace_service: WorkspaceResourceService,
) -> Response:
    workspace = _require_workspace(
        workspace_id=workspace_id,
        session=session,
        actor=actor,
        authority_scope=authority_scope,
        workspace_service=workspace_service,
    )
    try:
        normalized_path = normalize_home_path(path)
        if normalized_path == "AGENTS.md":
            raise ValueError("AGENTS.md cannot be deleted")
    except _WORKSPACE_FILE_ERRORS as error:
        raise _workspace_file_error(error) from error
    _initialize_workspace(workspace=workspace, actor=actor, agent_home=agent_home)
    try:
        agent_home.delete_file(
            user_id=actor.id,
            workspace_id=workspace.id,
            path=normalized_path,
        )
    except _WORKSPACE_FILE_ERRORS as error:
        raise _workspace_file_error(error) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("", response_model=WorkspaceListResponse)
def list_workspaces(
    session: SessionDep,
    actor: models.User = Depends(get_current_user),
    scope: ResourceListScope = "visible",
    include_inactive: bool = False,
) -> WorkspaceListResponse:
    return WorkspaceResourceService().list_resources(
        session,
        actor=actor,
        scope=scope,
        include_inactive=include_inactive,
    )


@router.post(
    "",
    response_model=WorkspaceResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_private_workspace(
    payload: WorkspaceCreateRequest,
    session: SessionDep,
    actor: models.User = Depends(get_current_user),
) -> WorkspaceResponse:
    return WorkspaceResourceService().create_private(
        session,
        actor=actor,
        payload=payload,
    )


@admin_router.post(
    "",
    response_model=WorkspaceResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_global_workspace(
    payload: WorkspaceCreateRequest,
    session: SessionDep,
    actor: models.User = Depends(get_current_admin),
) -> WorkspaceResponse:
    return WorkspaceResourceService().create_global(
        session,
        actor=actor,
        payload=payload,
    )


@router.get("/{workspace_id}", response_model=WorkspaceResponse)
def get_workspace(
    workspace_id: ResourceId,
    session: SessionDep,
    actor: models.User = Depends(get_current_user),
) -> WorkspaceResponse:
    return WorkspaceResourceService().get_resource(
        session,
        actor=actor,
        resource_id=workspace_id,
    )


@router.put("/{workspace_id}", response_model=WorkspaceResponse)
def put_workspace(
    workspace_id: ResourceId,
    payload: WorkspacePutRequest,
    session: SessionDep,
    actor: models.User = Depends(get_current_user),
) -> WorkspaceResponse:
    return WorkspaceResourceService().put_resource(
        session,
        actor=actor,
        resource_id=workspace_id,
        payload=payload,
    )


@router.delete("/{workspace_id}", response_model=ResourceDeleteResponse)
def delete_workspace(
    workspace_id: ResourceId,
    session: SessionDep,
    actor: models.User = Depends(get_current_user),
) -> ResourceDeleteResponse:
    return WorkspaceResourceService().delete_resource(
        session,
        actor=actor,
        resource_id=workspace_id,
    )


@admin_router.put("/{workspace_id}", response_model=WorkspaceResponse)
def put_global_workspace(
    workspace_id: ResourceId,
    payload: WorkspacePutRequest,
    session: SessionDep,
    actor: models.User = Depends(get_current_admin),
) -> WorkspaceResponse:
    return WorkspaceResourceService().put_global_resource(
        session,
        actor=actor,
        resource_id=workspace_id,
        payload=payload,
    )


@admin_router.delete("/{workspace_id}", response_model=ResourceDeleteResponse)
def delete_global_workspace(
    workspace_id: ResourceId,
    session: SessionDep,
    actor: models.User = Depends(get_current_admin),
) -> ResourceDeleteResponse:
    return WorkspaceResourceService().delete_global_resource(
        session,
        actor=actor,
        resource_id=workspace_id,
    )


@router.get("/{workspace_id}/files", response_model=WorkspaceFileListResponse)
def list_workspace_files(
    workspace_id: ResourceId,
    session: SessionDep,
    agent_home: AgentHomeDep,
    workspace_service: WorkspaceResourceServiceDep,
    actor: models.User = Depends(get_current_user),
    directory: str = "",
) -> WorkspaceFileListResponse:
    return _list_workspace_files(
        workspace_id=workspace_id,
        session=session,
        actor=actor,
        authority_scope="private",
        directory=directory,
        agent_home=agent_home,
        workspace_service=workspace_service,
    )


@admin_router.get("/{workspace_id}/files", response_model=WorkspaceFileListResponse)
def list_global_workspace_files(
    workspace_id: ResourceId,
    session: SessionDep,
    agent_home: AgentHomeDep,
    workspace_service: WorkspaceResourceServiceDep,
    actor: models.User = Depends(get_current_admin),
    directory: str = "",
) -> WorkspaceFileListResponse:
    return _list_workspace_files(
        workspace_id=workspace_id,
        session=session,
        actor=actor,
        authority_scope="global",
        directory=directory,
        agent_home=agent_home,
        workspace_service=workspace_service,
    )


@router.get("/{workspace_id}/files/content", response_model=WorkspaceFileContent)
def read_workspace_file(
    workspace_id: ResourceId,
    path: str,
    session: SessionDep,
    agent_home: AgentHomeDep,
    workspace_service: WorkspaceResourceServiceDep,
    actor: models.User = Depends(get_current_user),
) -> WorkspaceFileContent:
    return _read_workspace_file(
        workspace_id=workspace_id,
        session=session,
        actor=actor,
        authority_scope="private",
        path=path,
        agent_home=agent_home,
        workspace_service=workspace_service,
    )


@admin_router.get(
    "/{workspace_id}/files/content",
    response_model=WorkspaceFileContent,
)
def read_global_workspace_file(
    workspace_id: ResourceId,
    path: str,
    session: SessionDep,
    agent_home: AgentHomeDep,
    workspace_service: WorkspaceResourceServiceDep,
    actor: models.User = Depends(get_current_admin),
) -> WorkspaceFileContent:
    return _read_workspace_file(
        workspace_id=workspace_id,
        session=session,
        actor=actor,
        authority_scope="global",
        path=path,
        agent_home=agent_home,
        workspace_service=workspace_service,
    )


@router.post(
    "/{workspace_id}/files/content",
    response_model=WorkspaceFileContent,
    status_code=status.HTTP_201_CREATED,
)
def create_workspace_file(
    workspace_id: ResourceId,
    payload: CreateWorkspaceFileRequest,
    session: SessionDep,
    agent_home: AgentHomeDep,
    workspace_service: WorkspaceResourceServiceDep,
    actor: models.User = Depends(get_current_user),
) -> WorkspaceFileContent:
    return _create_workspace_file(
        workspace_id=workspace_id,
        session=session,
        actor=actor,
        authority_scope="private",
        payload=payload,
        agent_home=agent_home,
        workspace_service=workspace_service,
    )


@admin_router.post(
    "/{workspace_id}/files/content",
    response_model=WorkspaceFileContent,
    status_code=status.HTTP_201_CREATED,
)
def create_global_workspace_file(
    workspace_id: ResourceId,
    payload: CreateWorkspaceFileRequest,
    session: SessionDep,
    agent_home: AgentHomeDep,
    workspace_service: WorkspaceResourceServiceDep,
    actor: models.User = Depends(get_current_admin),
) -> WorkspaceFileContent:
    return _create_workspace_file(
        workspace_id=workspace_id,
        session=session,
        actor=actor,
        authority_scope="global",
        payload=payload,
        agent_home=agent_home,
        workspace_service=workspace_service,
    )


@router.put("/{workspace_id}/files/content", response_model=WorkspaceFileContent)
def write_workspace_file(
    workspace_id: ResourceId,
    payload: WriteWorkspaceFileRequest,
    session: SessionDep,
    agent_home: AgentHomeDep,
    workspace_service: WorkspaceResourceServiceDep,
    actor: models.User = Depends(get_current_user),
) -> WorkspaceFileContent:
    return _write_workspace_file(
        workspace_id=workspace_id,
        session=session,
        actor=actor,
        authority_scope="private",
        payload=payload,
        agent_home=agent_home,
        workspace_service=workspace_service,
    )


@admin_router.put(
    "/{workspace_id}/files/content",
    response_model=WorkspaceFileContent,
)
def write_global_workspace_file(
    workspace_id: ResourceId,
    payload: WriteWorkspaceFileRequest,
    session: SessionDep,
    agent_home: AgentHomeDep,
    workspace_service: WorkspaceResourceServiceDep,
    actor: models.User = Depends(get_current_admin),
) -> WorkspaceFileContent:
    return _write_workspace_file(
        workspace_id=workspace_id,
        session=session,
        actor=actor,
        authority_scope="global",
        payload=payload,
        agent_home=agent_home,
        workspace_service=workspace_service,
    )


@router.delete("/{workspace_id}/files", status_code=status.HTTP_204_NO_CONTENT)
def delete_workspace_file(
    workspace_id: ResourceId,
    path: str,
    session: SessionDep,
    agent_home: AgentHomeDep,
    workspace_service: WorkspaceResourceServiceDep,
    actor: models.User = Depends(get_current_user),
) -> Response:
    return _delete_workspace_file(
        workspace_id=workspace_id,
        session=session,
        actor=actor,
        authority_scope="private",
        path=path,
        agent_home=agent_home,
        workspace_service=workspace_service,
    )


@admin_router.delete(
    "/{workspace_id}/files",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_global_workspace_file(
    workspace_id: ResourceId,
    path: str,
    session: SessionDep,
    agent_home: AgentHomeDep,
    workspace_service: WorkspaceResourceServiceDep,
    actor: models.User = Depends(get_current_admin),
) -> Response:
    return _delete_workspace_file(
        workspace_id=workspace_id,
        session=session,
        actor=actor,
        authority_scope="global",
        path=path,
        agent_home=agent_home,
        workspace_service=workspace_service,
    )
