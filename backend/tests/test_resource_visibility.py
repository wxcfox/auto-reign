from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import models
from app.repositories.resource_repository import ResourceRepository
from app.schemas.agents import (
    AgentConfig,
    AgentCreateRequest,
    AgentPutRequest,
    KnowledgeScope,
)
from app.schemas.knowledge_collections import (
    KnowledgeCollectionConfig,
    KnowledgeCollectionCreateRequest,
    KnowledgeCollectionPutRequest,
)
from app.schemas.modeling import ModelRef
from app.schemas.workspaces import (
    WorkspaceConfig,
    WorkspaceCreateRequest,
    WorkspacePutRequest,
)


@pytest.fixture
def db_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def add_resource(
    session: Session,
    *,
    owner: int,
    kind: str,
    name: str,
    active: bool = True,
    deleted: bool = False,
) -> models.Resource:
    resource = models.Resource(
        user_id=owner,
        resource_type=kind,
        name=name,
        config_json={},
        is_active=active,
        deleted_at=datetime.now(UTC) if deleted else None,
    )
    session.add(resource)
    session.flush()
    return resource


def test_resource_repository_lists_only_visible_active_resources(
    db_session: Session,
) -> None:
    global_agent = add_resource(
        db_session, owner=0, kind="agent", name="global"
    )
    alice_agent = add_resource(
        db_session, owner=1, kind="agent", name="alice"
    )
    add_resource(db_session, owner=2, kind="agent", name="bob")
    add_resource(
        db_session, owner=1, kind="agent", name="inactive", active=False
    )
    add_resource(
        db_session, owner=1, kind="agent", name="deleted", deleted=True
    )
    add_resource(db_session, owner=1, kind="workspace", name="wrong-type")

    result = ResourceRepository().list_visible(
        db_session,
        user_id=1,
        resource_type="agent",
        scope="visible",
    )

    assert [item.id for item in result] == [global_agent.id, alice_agent.id]


def test_global_scope_and_owned_scope_are_distinct(db_session: Session) -> None:
    add_resource(db_session, owner=0, kind="workspace", name="global")
    add_resource(db_session, owner=1, kind="workspace", name="mine")
    add_resource(db_session, owner=2, kind="workspace", name="other")
    repository = ResourceRepository()

    assert [
        item.name
        for item in repository.list_visible(
            db_session,
            user_id=1,
            resource_type="workspace",
            scope="global",
        )
    ] == ["global"]
    assert [
        item.name
        for item in repository.list_visible(
            db_session,
            user_id=1,
            resource_type="workspace",
            scope="owned",
        )
    ] == ["mine"]


def test_include_inactive_lists_non_deleted_owned_resources_only(
    db_session: Session,
) -> None:
    active = add_resource(
        db_session, owner=1, kind="workspace", name="active"
    )
    inactive = add_resource(
        db_session,
        owner=1,
        kind="workspace",
        name="inactive",
        active=False,
    )
    add_resource(
        db_session,
        owner=1,
        kind="workspace",
        name="deleted",
        active=False,
        deleted=True,
    )
    add_resource(
        db_session,
        owner=2,
        kind="workspace",
        name="other",
        active=False,
    )
    add_resource(
        db_session,
        owner=1,
        kind="agent",
        name="wrong-type",
        active=False,
    )

    resources = ResourceRepository().list_visible(
        db_session,
        user_id=1,
        resource_type="workspace",
        scope="owned",
        include_inactive=True,
    )

    assert {resource.id for resource in resources} == {active.id, inactive.id}


def test_visible_resources_have_stable_owner_name_id_order(
    db_session: Session,
) -> None:
    global_z = add_resource(db_session, owner=0, kind="agent", name="zeta")
    global_a = add_resource(db_session, owner=0, kind="agent", name="alpha")
    owned_z = add_resource(db_session, owner=1, kind="agent", name="zeta")
    owned_a = add_resource(db_session, owner=1, kind="agent", name="alpha")

    resources = ResourceRepository().list_visible(
        db_session,
        user_id=1,
        resource_type="agent",
    )

    assert [item.id for item in resources] == [
        global_a.id,
        global_z.id,
        owned_a.id,
        owned_z.id,
    ]


def test_get_visible_enforces_owner_type_and_availability(
    db_session: Session,
) -> None:
    global_agent = add_resource(db_session, owner=0, kind="agent", name="global")
    owned_agent = add_resource(db_session, owner=1, kind="agent", name="mine")
    other_agent = add_resource(db_session, owner=2, kind="agent", name="other")
    inactive_agent = add_resource(
        db_session, owner=1, kind="agent", name="inactive", active=False
    )
    deleted_agent = add_resource(
        db_session, owner=1, kind="agent", name="deleted", deleted=True
    )
    repository = ResourceRepository()

    assert (
        repository.get_visible(
            db_session,
            user_id=1,
            resource_id=global_agent.id,
            resource_type="agent",
        )
        is global_agent
    )
    assert (
        repository.get_visible(
            db_session,
            user_id=1,
            resource_id=owned_agent.id,
            resource_type="agent",
        )
        is owned_agent
    )
    assert (
        repository.get_visible(
            db_session,
            user_id=1,
            resource_id=other_agent.id,
            resource_type="agent",
        )
        is None
    )
    assert (
        repository.get_visible(
            db_session,
            user_id=1,
            resource_id=owned_agent.id,
            resource_type="workspace",
        )
        is None
    )
    assert (
        repository.get_visible(
            db_session,
            user_id=1,
            resource_id=inactive_agent.id,
            resource_type="agent",
        )
        is None
    )
    assert (
        repository.get_visible(
            db_session,
            user_id=1,
            resource_id=deleted_agent.id,
            resource_type="agent",
        )
        is None
    )


def test_get_visible_can_include_owned_or_global_unavailable_resources(
    db_session: Session,
) -> None:
    inactive = add_resource(
        db_session, owner=1, kind="agent", name="inactive", active=False
    )
    deleted = add_resource(
        db_session, owner=0, kind="agent", name="deleted", deleted=True
    )
    other = add_resource(
        db_session, owner=2, kind="agent", name="other", active=False
    )
    repository = ResourceRepository()

    assert (
        repository.get_visible(
            db_session,
            user_id=1,
            resource_id=inactive.id,
            resource_type="agent",
            include_unavailable=True,
        )
        is inactive
    )
    assert (
        repository.get_visible(
            db_session,
            user_id=1,
            resource_id=deleted.id,
            resource_type="agent",
            include_unavailable=True,
        )
        is deleted
    )
    assert (
        repository.get_visible(
            db_session,
            user_id=1,
            resource_id=other.id,
            resource_type="agent",
            include_unavailable=True,
        )
        is None
    )


def test_list_visible_can_batch_selected_tombstones_without_cross_user_leaks(
    db_session: Session,
) -> None:
    global_inactive = add_resource(
        db_session,
        owner=0,
        kind="agent",
        name="global inactive",
        active=False,
    )
    owned_deleted = add_resource(
        db_session,
        owner=1,
        kind="agent",
        name="owned deleted",
        deleted=True,
    )
    other_deleted = add_resource(
        db_session,
        owner=2,
        kind="agent",
        name="other deleted",
        deleted=True,
    )
    unselected = add_resource(
        db_session,
        owner=1,
        kind="agent",
        name="unselected",
    )

    resources = ResourceRepository().list_visible(
        db_session,
        user_id=1,
        resource_type="agent",
        include_unavailable=True,
        resource_ids={
            global_inactive.id,
            owned_deleted.id,
            other_deleted.id,
        },
    )

    assert {item.id for item in resources} == {
        global_inactive.id,
        owned_deleted.id,
    }
    assert unselected.id not in {item.id for item in resources}
    assert (
        ResourceRepository().list_visible(
            db_session,
            user_id=1,
            resource_type="agent",
            include_unavailable=True,
            resource_ids=set(),
        )
        == []
    )


def test_locking_queries_preserve_visibility_and_type_boundaries(
    db_session: Session,
) -> None:
    owned = add_resource(db_session, owner=1, kind="workspace", name="mine")
    other = add_resource(db_session, owner=2, kind="workspace", name="other")
    tombstone = add_resource(
        db_session, owner=1, kind="workspace", name="deleted", deleted=True
    )
    repository = ResourceRepository()

    assert (
        repository.get_visible_for_update(
            db_session,
            user_id=1,
            resource_id=owned.id,
            resource_type="workspace",
        )
        is owned
    )
    assert (
        repository.get_visible_for_update(
            db_session,
            user_id=1,
            resource_id=other.id,
            resource_type="workspace",
        )
        is None
    )
    assert (
        repository.get_visible_for_update(
            db_session,
            user_id=1,
            resource_id=tombstone.id,
            resource_type="workspace",
        )
        is None
    )
    assert (
        repository.get_for_update(
            db_session,
            resource_id=tombstone.id,
            resource_type="workspace",
        )
        is tombstone
    )
    assert (
        repository.get_for_update(
            db_session,
            resource_id=owned.id,
            resource_type="agent",
        )
        is None
    )


def test_create_list_active_agents_and_soft_delete(db_session: Session) -> None:
    repository = ResourceRepository()
    created = repository.create(
        db_session,
        owner_id=1,
        resource_type="agent",
        name="assistant",
        config_json={"system_prompt": "help"},
    )
    add_resource(db_session, owner=1, kind="workspace", name="not-agent")
    add_resource(db_session, owner=1, kind="agent", name="inactive", active=False)
    add_resource(db_session, owner=1, kind="agent", name="deleted", deleted=True)

    assert created.id
    assert repository.list_active_agents(db_session) == [created]

    repository.soft_delete(db_session, created)

    assert created.is_active is False
    assert created.deleted_at is not None
    assert created.updated_at == created.deleted_at
    assert repository.list_active_agents(db_session) == []


def test_model_ref_is_stripped_strict_and_frozen() -> None:
    model_ref = ModelRef(provider="  qwen  ", model="  qwen-plus  ")
    assert model_ref == ModelRef(provider="qwen", model="qwen-plus")

    with pytest.raises(ValidationError):
        ModelRef.model_validate(
            {"provider": "qwen", "model": "qwen-plus", "temperature": 0.2}
        )
    with pytest.raises(ValidationError):
        model_ref.model = "another-model"


@pytest.mark.parametrize(
    "payload",
    [
        {"collection_id": "collection", "document_ids": []},
        {"collection_id": "collection", "document_ids": ["doc", "doc"]},
        {"collection_id": "collection", "unexpected": True},
    ],
)
def test_knowledge_scope_rejects_ambiguous_or_extra_values(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        KnowledgeScope.model_validate(payload)


@pytest.mark.parametrize("document_id", ["", "   ", "d" * 37])
def test_knowledge_scope_rejects_invalid_document_ids(document_id: str) -> None:
    with pytest.raises(ValidationError):
        KnowledgeScope(
            collection_id="collection",
            document_ids=[document_id],
        )


@pytest.mark.parametrize("resource_id", ["", "   ", "r" * 37])
def test_agent_config_rejects_invalid_collection_and_workspace_ids(
    resource_id: str,
) -> None:
    with pytest.raises(ValidationError):
        KnowledgeScope(collection_id=resource_id)
    with pytest.raises(ValidationError):
        AgentConfig(system_prompt="help", home_workspace_id=resource_id)


def test_knowledge_scope_accepts_up_to_one_hundred_stripped_document_ids() -> None:
    document_ids = [f"  doc-{index}  " for index in range(100)]

    scope = KnowledgeScope(
        collection_id="  collection  ",
        document_ids=document_ids,
    )

    assert scope.collection_id == "collection"
    assert scope.document_ids == [f"doc-{index}" for index in range(100)]


def test_knowledge_scope_rejects_more_than_one_hundred_document_ids() -> None:
    with pytest.raises(ValidationError):
        KnowledgeScope(
            collection_id="collection",
            document_ids=[f"doc-{index}" for index in range(101)],
        )


def test_agent_config_accepts_default_empty_scopes_and_null_document_ids() -> None:
    assert AgentConfig(system_prompt="help").knowledge_scopes == []
    assert KnowledgeScope(
        collection_id="collection",
        document_ids=None,
    ).document_ids is None


def test_agent_config_accepts_twenty_knowledge_scopes() -> None:
    config = AgentConfig(
        system_prompt="help",
        home_workspace_id="  workspace  ",
        knowledge_scopes=[
            KnowledgeScope(collection_id=f"collection-{index}")
            for index in range(20)
        ],
    )

    assert config.home_workspace_id == "workspace"
    assert len(config.knowledge_scopes) == 20


def test_agent_config_rejects_more_than_twenty_knowledge_scopes() -> None:
    with pytest.raises(ValidationError):
        AgentConfig(
            system_prompt="help",
            knowledge_scopes=[
                KnowledgeScope(collection_id=f"collection-{index}")
                for index in range(21)
            ],
        )


def test_agent_config_rejects_duplicate_collections_and_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        AgentConfig.model_validate(
            {
                "system_prompt": "help",
                "knowledge_scopes": [
                    {"collection_id": "same", "document_ids": None},
                    {"collection_id": "same", "document_ids": ["doc"]},
                ],
            }
        )
    with pytest.raises(ValidationError):
        AgentConfig.model_validate(
            {"system_prompt": "help", "tool_permissions": ["filesystem"]}
        )


def test_workspace_and_collection_config_have_closed_shapes() -> None:
    assert WorkspaceConfig.model_validate(
        {
            "workspace_type": "agent_home",
            "initial_agents_md": "# Instructions",
        }
    ).workspace_type == "agent_home"

    for payload in (
        {"workspace_type": "code", "initial_agents_md": "# Instructions"},
        {
            "workspace_type": "agent_home",
            "initial_agents_md": "# Instructions",
            "unexpected": True,
        },
    ):
        with pytest.raises(ValidationError):
            WorkspaceConfig.model_validate(payload)

    assert KnowledgeCollectionConfig.model_validate({}).model_dump() == {
        "chunk_size": 900,
        "chunk_overlap": 120,
        "top_k": 8,
        "score_threshold": None,
    }
    assert KnowledgeCollectionConfig.model_validate({"top_k": 10}).top_k == 10
    with pytest.raises(ValidationError):
        KnowledgeCollectionConfig.model_validate({"unknown": True})


@pytest.mark.parametrize(
    ("schema", "payload"),
    [
        (
            AgentCreateRequest,
            {
                "name": "Agent",
                "config": {"system_prompt": "help"},
                "user_id": 99,
            },
        ),
        (
            AgentPutRequest,
            {
                "name": "Agent",
                "config": {"system_prompt": "help"},
                "scope": "global",
            },
        ),
        (
            WorkspaceCreateRequest,
            {
                "name": "Workspace",
                "config": {
                    "workspace_type": "agent_home",
                    "initial_agents_md": "# Instructions",
                },
                "user_id": 99,
            },
        ),
        (
            WorkspacePutRequest,
            {
                "name": "Workspace",
                "config": {
                    "workspace_type": "agent_home",
                    "initial_agents_md": "# Instructions",
                },
                "scope": "global",
            },
        ),
        (
            KnowledgeCollectionCreateRequest,
            {"name": "Collection", "user_id": 99},
        ),
        (
            KnowledgeCollectionPutRequest,
            {"name": "Collection", "scope": "global"},
        ),
    ],
)
def test_resource_mutation_requests_reject_ownership_and_scope_fields(
    schema: type,
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        schema.model_validate(payload)
