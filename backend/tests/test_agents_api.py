from collections.abc import Mapping
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import event

from app.core.config import Settings
from app.db import models
from app.schemas.agents import AgentConfig
from app.schemas.modeling import ModelRef
from app.services.agent_service import (
    AgentService,
    ResolvedAgent,
    ResolvedAgentConfig,
    freeze_json,
)


def _agent_payload(
    name: str,
    *,
    prompt: str = "帮助用户。",
    default_model: dict[str, str] | None = None,
    workspace_id: str | None = None,
    knowledge_scopes: list[dict[str, object]] | None = None,
    is_active: bool | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": name,
        "config": {
            "system_prompt": prompt,
            "default_model": default_model,
            "home_workspace_id": workspace_id,
            "knowledge_scopes": knowledge_scopes or [],
        },
    }
    if is_active is not None:
        payload["is_active"] = is_active
    return payload


def _workspace_payload(name: str) -> dict[str, object]:
    return {
        "name": name,
        "config": {
            "workspace_type": "agent_home",
            "initial_agents_md": "# 规则",
        },
    }


def _collection_payload(name: str) -> dict[str, object]:
    return {"name": name, "config": {}}


def _add_document(
    client,
    *,
    owner_id: int,
    collection_id: str,
    name: str,
    is_active: bool = True,
) -> str:
    with client.app.state.session_factory() as session:
        document = models.KnowledgeDocument(
            user_id=owner_id,
            collection_id=collection_id,
            name=name,
            source_object_key=f"source/{name}",
            parsed_object_key=None,
            mime_type="text/plain",
            size_bytes=4,
            content_hash=f"hash-{name}",
            status="indexed",
            is_active=is_active,
        )
        session.add(document)
        session.commit()
        return document.id


def _set_resource_state(
    client,
    resource_id: str,
    *,
    is_active: bool,
    deleted: bool = False,
) -> None:
    with client.app.state.session_factory() as session:
        resource = session.get(models.Resource, resource_id)
        assert resource is not None
        resource.is_active = is_active
        resource.deleted_at = models._now() if deleted else None
        session.commit()


def _resolved_agent(*, default_model: ModelRef | None = None) -> ResolvedAgent:
    updated_at = datetime.now(UTC)
    input_config = AgentConfig(
        system_prompt="Help.",
        default_model=default_model,
    )
    frozen = freeze_json(
        input_config.model_dump(mode="json", exclude_none=False)
    )
    assert isinstance(frozen, Mapping)
    config = ResolvedAgentConfig(
        agent_id="agent",
        owner_user_id=7,
        system_prompt=input_config.system_prompt,
        default_model=input_config.default_model,
        home_workspace=None,
        knowledge_scopes=(),
        config_json=frozen,
        updated_at=updated_at,
        config_hash="hash",
    )
    return ResolvedAgent(
        id="agent",
        name="Agent",
        config=config,
        updated_at=updated_at,
        config_hash=config.config_hash,
    )


def test_private_agent_route_exists(client, ordinary_user_headers) -> None:
    response = client.post(
        "/api/agents",
        headers=ordinary_user_headers,
        json=_agent_payload("我的助手"),
    )

    assert response.status_code == 201
    assert response.json()["scope"] == "private"
    assert response.json()["can_manage"] is True


def test_private_agent_can_reference_own_and_global_resources(
    client,
    admin_headers,
    create_user,
) -> None:
    alice, alice_headers = create_user("alice")
    global_workspace = client.post(
        "/api/admin/workspaces",
        headers=admin_headers,
        json=_workspace_payload("全局成长空间"),
    ).json()
    own_collection = client.post(
        "/api/knowledge-collections",
        headers=alice_headers,
        json=_collection_payload("个人资料"),
    ).json()
    document_id = _add_document(
        client,
        owner_id=alice["id"],
        collection_id=own_collection["id"],
        name="个人文档",
    )

    response = client.post(
        "/api/agents",
        headers=alice_headers,
        json=_agent_payload(
            "成长助手",
            workspace_id=global_workspace["id"],
            knowledge_scopes=[
                {
                    "collection_id": own_collection["id"],
                    "document_ids": [document_id],
                }
            ],
        ),
    )

    assert response.status_code == 201
    assert response.json()["config"]["home_workspace_id"] == global_workspace["id"]
    assert response.json()["config"]["knowledge_scopes"] == [
        {
            "collection_id": own_collection["id"],
            "document_ids": [document_id],
        }
    ]


def test_private_agent_can_bind_exact_document_from_visible_global_collection(
    client,
    admin_headers,
    create_user,
) -> None:
    _alice, alice_headers = create_user("alice")
    global_collection = client.post(
        "/api/admin/knowledge-collections",
        headers=admin_headers,
        json=_collection_payload("全局精确资料"),
    ).json()
    global_document_id = _add_document(
        client,
        owner_id=0,
        collection_id=global_collection["id"],
        name="全局文档",
    )

    response = client.post(
        "/api/agents",
        headers=alice_headers,
        json=_agent_payload(
            "全局资料助手",
            knowledge_scopes=[
                {
                    "collection_id": global_collection["id"],
                    "document_ids": [global_document_id],
                }
            ],
        ),
    )

    assert response.status_code == 201
    assert response.json()["config"]["knowledge_scopes"] == [
        {
            "collection_id": global_collection["id"],
            "document_ids": [global_document_id],
        }
    ]


def test_global_agent_accepts_only_global_references(
    client,
    admin_headers,
    ordinary_user_headers,
) -> None:
    global_workspace = client.post(
        "/api/admin/workspaces",
        headers=admin_headers,
        json=_workspace_payload("全局空间"),
    ).json()
    global_collection = client.post(
        "/api/admin/knowledge-collections",
        headers=admin_headers,
        json=_collection_payload("全局资料库"),
    ).json()
    valid = client.post(
        "/api/admin/agents",
        headers=admin_headers,
        json=_agent_payload(
            "全局助手",
            workspace_id=global_workspace["id"],
            knowledge_scopes=[
                {"collection_id": global_collection["id"], "document_ids": None}
            ],
        ),
    )
    assert valid.status_code == 201

    private_workspace = client.post(
        "/api/workspaces",
        headers=ordinary_user_headers,
        json=_workspace_payload("私有空间"),
    ).json()
    rejected = client.post(
        "/api/admin/agents",
        headers=admin_headers,
        json=_agent_payload("错误全局助手", workspace_id=private_workspace["id"]),
    )
    assert rejected.status_code == 400
    assert rejected.json()["detail"]["code"] == "resource_reference_invalid"


@pytest.mark.parametrize("reference_kind", ["workspace", "collection"])
def test_private_agent_rejects_another_users_private_reference(
    client,
    create_user,
    reference_kind: str,
) -> None:
    _alice, alice_headers = create_user("alice")
    _bob, bob_headers = create_user("bob")
    if reference_kind == "workspace":
        resource = client.post(
            "/api/workspaces",
            headers=bob_headers,
            json=_workspace_payload("Bob 空间"),
        ).json()
        payload = _agent_payload("越界助手", workspace_id=resource["id"])
    else:
        resource = client.post(
            "/api/knowledge-collections",
            headers=bob_headers,
            json=_collection_payload("Bob 资料"),
        ).json()
        payload = _agent_payload(
            "越界助手",
            knowledge_scopes=[
                {"collection_id": resource["id"], "document_ids": None}
            ],
        )

    response = client.post("/api/agents", headers=alice_headers, json=payload)

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "resource_reference_invalid"


@pytest.mark.parametrize("reference_kind", ["workspace", "collection"])
def test_agent_rejects_wrong_resource_type(
    client,
    ordinary_user_headers,
    reference_kind: str,
) -> None:
    workspace = client.post(
        "/api/workspaces",
        headers=ordinary_user_headers,
        json=_workspace_payload("空间"),
    ).json()
    collection = client.post(
        "/api/knowledge-collections",
        headers=ordinary_user_headers,
        json=_collection_payload("资料"),
    ).json()
    payload = (
        _agent_payload("错类型", workspace_id=collection["id"])
        if reference_kind == "workspace"
        else _agent_payload(
            "错类型",
            knowledge_scopes=[
                {"collection_id": workspace["id"], "document_ids": None}
            ],
        )
    )

    response = client.post(
        "/api/agents",
        headers=ordinary_user_headers,
        json=payload,
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "resource_reference_invalid"


@pytest.mark.parametrize("state", ["inactive", "deleted"])
@pytest.mark.parametrize("reference_kind", ["workspace", "collection"])
def test_agent_rejects_unavailable_resource_reference(
    client,
    ordinary_user_headers,
    state: str,
    reference_kind: str,
) -> None:
    if reference_kind == "workspace":
        resource = client.post(
            "/api/workspaces",
            headers=ordinary_user_headers,
            json=_workspace_payload(f"{state} 空间"),
        ).json()
        payload = _agent_payload("不可用引用", workspace_id=resource["id"])
    else:
        resource = client.post(
            "/api/knowledge-collections",
            headers=ordinary_user_headers,
            json=_collection_payload(f"{state} 资料"),
        ).json()
        payload = _agent_payload(
            "不可用引用",
            knowledge_scopes=[
                {"collection_id": resource["id"], "document_ids": None}
            ],
        )
    _set_resource_state(
        client,
        resource["id"],
        is_active=False,
        deleted=state == "deleted",
    )

    response = client.post(
        "/api/agents",
        headers=ordinary_user_headers,
        json=payload,
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "resource_reference_invalid"


@pytest.mark.parametrize(
    "scopes",
    [
        [{"collection_id": "collection", "document_ids": []}],
        [
            {"collection_id": "collection", "document_ids": None},
            {"collection_id": "collection", "document_ids": None},
        ],
        [
            {
                "collection_id": "collection",
                "document_ids": ["document", "document"],
            }
        ],
    ],
)
def test_agent_schema_rejects_ambiguous_knowledge_scopes(
    client,
    ordinary_user_headers,
    scopes: list[dict[str, object]],
) -> None:
    response = client.post(
        "/api/agents",
        headers=ordinary_user_headers,
        json=_agent_payload("非法范围", knowledge_scopes=scopes),
    )

    assert response.status_code == 422


def test_agent_rejects_documents_outside_the_declared_collection(
    client,
    create_user,
) -> None:
    alice, alice_headers = create_user("alice")
    first = client.post(
        "/api/knowledge-collections",
        headers=alice_headers,
        json=_collection_payload("第一资料库"),
    ).json()
    second = client.post(
        "/api/knowledge-collections",
        headers=alice_headers,
        json=_collection_payload("第二资料库"),
    ).json()
    second_document = _add_document(
        client,
        owner_id=alice["id"],
        collection_id=second["id"],
        name="第二文档",
    )

    response = client.post(
        "/api/agents",
        headers=alice_headers,
        json=_agent_payload(
            "错库助手",
            knowledge_scopes=[
                {
                    "collection_id": first["id"],
                    "document_ids": [second_document],
                }
            ],
        ),
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "resource_reference_invalid"


def test_agent_rejects_inactive_missing_and_wrong_owner_documents(
    client,
    create_user,
) -> None:
    alice, alice_headers = create_user("alice")
    bob, _bob_headers = create_user("bob")
    collection = client.post(
        "/api/knowledge-collections",
        headers=alice_headers,
        json=_collection_payload("资料库"),
    ).json()
    inactive_id = _add_document(
        client,
        owner_id=alice["id"],
        collection_id=collection["id"],
        name="停用文档",
        is_active=False,
    )
    wrong_owner_id = _add_document(
        client,
        owner_id=bob["id"],
        collection_id=collection["id"],
        name="错属主文档",
    )

    for index, document_id in enumerate(
        [inactive_id, wrong_owner_id, "missing-document"]
    ):
        response = client.post(
            "/api/agents",
            headers=alice_headers,
            json=_agent_payload(
                f"非法文档 {index}",
                knowledge_scopes=[
                    {
                        "collection_id": collection["id"],
                        "document_ids": [document_id],
                    }
                ],
            ),
        )
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "resource_reference_invalid"


def test_agent_rejects_document_repeated_across_scopes(
    client,
    create_user,
) -> None:
    alice, alice_headers = create_user("alice")
    first = client.post(
        "/api/knowledge-collections",
        headers=alice_headers,
        json=_collection_payload("第一资料库"),
    ).json()
    second = client.post(
        "/api/knowledge-collections",
        headers=alice_headers,
        json=_collection_payload("第二资料库"),
    ).json()
    document_id = _add_document(
        client,
        owner_id=alice["id"],
        collection_id=first["id"],
        name="重复文档",
    )

    response = client.post(
        "/api/agents",
        headers=alice_headers,
        json=_agent_payload(
            "重复文档助手",
            knowledge_scopes=[
                {"collection_id": first["id"], "document_ids": [document_id]},
                {"collection_id": second["id"], "document_ids": [document_id]},
            ],
        ),
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "resource_reference_invalid"


def test_agent_crud_visibility_permissions_and_list_scopes(
    client,
    admin_headers,
    create_user,
) -> None:
    _alice, alice_headers = create_user("alice")
    _bob, bob_headers = create_user("bob")
    alice_agent = client.post(
        "/api/agents",
        headers=alice_headers,
        json=_agent_payload("Alice 助手"),
    ).json()
    bob_agent = client.post(
        "/api/agents",
        headers=bob_headers,
        json=_agent_payload("Bob 助手"),
    ).json()
    global_agent = client.post(
        "/api/admin/agents",
        headers=admin_headers,
        json=_agent_payload("公共助手"),
    ).json()

    visible = client.get("/api/agents?scope=visible", headers=alice_headers).json()[
        "agents"
    ]
    owned = client.get("/api/agents?scope=owned", headers=alice_headers).json()[
        "agents"
    ]
    global_items = client.get(
        "/api/agents?scope=global", headers=alice_headers
    ).json()["agents"]
    assert alice_agent["id"] in {item["id"] for item in visible}
    assert global_agent["id"] in {item["id"] for item in visible}
    assert bob_agent["id"] not in {item["id"] for item in visible}
    assert {item["id"] for item in owned} == {alice_agent["id"]}
    assert global_agent["id"] in {item["id"] for item in global_items}
    assert all(item["scope"] == "global" for item in global_items)
    assert all(item["can_manage"] is False for item in global_items)

    assert client.get(
        f"/api/agents/{alice_agent['id']}", headers=bob_headers
    ).status_code == 404
    assert client.get(
        f"/api/agents/{alice_agent['id']}", headers=admin_headers
    ).status_code == 404
    assert client.put(
        f"/api/agents/{global_agent['id']}",
        headers=alice_headers,
        json=_agent_payload("越权", is_active=True),
    ).status_code == 404
    assert client.delete(
        f"/api/agents/{global_agent['id']}", headers=alice_headers
    ).status_code == 404

    updated = client.put(
        f"/api/agents/{global_agent['id']}",
        headers=admin_headers,
        json=_agent_payload("公共助手新版", prompt="新提示词", is_active=True),
    )
    assert updated.status_code == 200
    assert updated.json()["can_manage"] is True
    assert updated.json()["config"]["system_prompt"] == "新提示词"


def test_agent_put_is_full_replacement_and_supports_reactivation(
    client,
    ordinary_user_headers,
) -> None:
    created = client.post(
        "/api/agents",
        headers=ordinary_user_headers,
        json=_agent_payload("原助手", prompt="原提示词"),
    ).json()
    missing_config = client.put(
        f"/api/agents/{created['id']}",
        headers=ordinary_user_headers,
        json={"name": "缺配置", "is_active": True},
    )
    assert missing_config.status_code == 422

    deactivated = client.put(
        f"/api/agents/{created['id']}",
        headers=ordinary_user_headers,
        json=_agent_payload("新助手", prompt="新提示词", is_active=False),
    )
    assert deactivated.status_code == 200
    assert deactivated.json()["is_active"] is False
    assert deactivated.json()["config"]["system_prompt"] == "新提示词"
    assert client.get(
        f"/api/agents/{created['id']}", headers=ordinary_user_headers
    ).status_code == 404

    restored = client.put(
        f"/api/agents/{created['id']}",
        headers=ordinary_user_headers,
        json=_agent_payload("恢复助手", prompt="恢复提示词", is_active=True),
    )
    assert restored.status_code == 200
    assert restored.json()["is_active"] is True
    assert restored.json()["name"] == "恢复助手"


@pytest.mark.parametrize("method", ["POST", "PUT"])
@pytest.mark.parametrize(("name", "prompt"), [("   ", "有效"), ("有效", "   ")])
def test_agent_mutations_reject_whitespace_only_strings(
    client,
    ordinary_user_headers,
    method: str,
    name: str,
    prompt: str,
) -> None:
    if method == "POST":
        url = "/api/agents"
    else:
        created = client.post(
            "/api/agents",
            headers=ordinary_user_headers,
            json=_agent_payload("待更新助手"),
        ).json()
        url = f"/api/agents/{created['id']}"
    response = client.request(
        method,
        url,
        headers=ordinary_user_headers,
        json=_agent_payload(name, prompt=prompt, is_active=True),
    )
    assert response.status_code == 422


def test_agent_mutations_trim_persist_and_reserve_names(
    client,
    create_user,
) -> None:
    _alice, alice_headers = create_user("alice")
    _bob, bob_headers = create_user("bob")
    first = client.post(
        "/api/agents",
        headers=alice_headers,
        json=_agent_payload("  保留名  ", prompt="  初始提示词  "),
    )
    assert first.status_code == 201
    assert first.json()["name"] == "保留名"
    assert first.json()["config"]["system_prompt"] == "初始提示词"

    duplicate = client.post(
        "/api/agents",
        headers=alice_headers,
        json=_agent_payload("保留名"),
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "resource_name_taken"
    assert client.post(
        "/api/agents", headers=bob_headers, json=_agent_payload("保留名")
    ).status_code == 201

    second = client.post(
        "/api/agents",
        headers=alice_headers,
        json=_agent_payload("第二个", prompt="未改变"),
    ).json()
    collision = client.put(
        f"/api/agents/{second['id']}",
        headers=alice_headers,
        json=_agent_payload(
            "  保留名  ", prompt="不应保存", is_active=False
        ),
    )
    assert collision.status_code == 409
    unchanged = client.get(
        f"/api/agents/{second['id']}", headers=alice_headers
    ).json()
    assert unchanged["name"] == "第二个"
    assert unchanged["config"]["system_prompt"] == "未改变"
    assert unchanged["is_active"] is True

    deleted = client.delete(
        f"/api/agents/{first.json()['id']}", headers=alice_headers
    )
    assert deleted.status_code == 200
    assert client.get(
        f"/api/agents/{first.json()['id']}", headers=alice_headers
    ).status_code == 404
    assert client.put(
        f"/api/agents/{first.json()['id']}",
        headers=alice_headers,
        json=_agent_payload("保留名", is_active=True),
    ).status_code == 404
    recreated = client.post(
        "/api/agents", headers=alice_headers, json=_agent_payload("保留名")
    )
    assert recreated.status_code == 409


def test_agent_default_model_is_validated_on_create_and_put(
    client,
    ordinary_user_headers,
) -> None:
    valid_model = {"provider": "qwen", "model": "qwen3.7-plus"}
    created = client.post(
        "/api/agents",
        headers=ordinary_user_headers,
        json=_agent_payload("模型助手", default_model=valid_model),
    )
    assert created.status_code == 201
    assert created.json()["config"]["default_model"] == valid_model

    invalid_create = client.post(
        "/api/agents",
        headers=ordinary_user_headers,
        json=_agent_payload(
            "无效模型助手",
            default_model={"provider": "qwen", "model": "not-configured"},
        ),
    )
    assert invalid_create.status_code == 400
    assert invalid_create.json()["detail"]["code"] == "model_unavailable"

    invalid_put = client.put(
        f"/api/agents/{created.json()['id']}",
        headers=ordinary_user_headers,
        json=_agent_payload(
            "不应保存",
            default_model={"provider": "missing", "model": "anything"},
            is_active=False,
        ),
    )
    assert invalid_put.status_code == 400
    assert invalid_put.json()["detail"]["code"] == "model_unavailable"
    unchanged = client.get(
        f"/api/agents/{created.json()['id']}", headers=ordinary_user_headers
    ).json()
    assert unchanged["name"] == "模型助手"
    assert unchanged["config"]["default_model"] == valid_model
    assert unchanged["is_active"] is True


def test_resolve_for_turn_locks_agent_references_and_documents_in_order(
    client,
    create_user,
) -> None:
    alice, alice_headers = create_user("alice")
    workspace = client.post(
        "/api/workspaces",
        headers=alice_headers,
        json=_workspace_payload("运行空间"),
    ).json()
    collection = client.post(
        "/api/knowledge-collections",
        headers=alice_headers,
        json=_collection_payload("运行资料"),
    ).json()
    document_id = _add_document(
        client,
        owner_id=alice["id"],
        collection_id=collection["id"],
        name="运行文档",
    )
    agent = client.post(
        "/api/agents",
        headers=alice_headers,
        json=_agent_payload(
            "运行助手",
            workspace_id=workspace["id"],
            knowledge_scopes=[
                {
                    "collection_id": collection["id"],
                    "document_ids": [document_id],
                }
            ],
        ),
    ).json()

    with client.app.state.session_factory() as session:
        agent_resource = session.get(models.Resource, agent["id"])
        workspace_resource = session.get(models.Resource, workspace["id"])
        selected_entities: list[type[object]] = []
        locking_selects: list[object] = []

        @event.listens_for(session, "do_orm_execute")
        def capture(statement_state) -> None:
            statement = statement_state.statement
            if not statement_state.is_select:
                return
            descriptions = statement.column_descriptions
            selected_entities.append(descriptions[0].get("entity"))
            if statement._for_update_arg is not None:
                locking_selects.append(statement)

        resolved = AgentService(settings=client.app.state.settings).resolve_for_turn(
            session,
            user_id=alice["id"],
            agent_id=agent["id"],
        )

    assert selected_entities == [
        models.Resource,
        models.Resource,
        models.KnowledgeDocument,
    ]
    assert len(locking_selects) == 3
    assert "ORDER BY resources.id" in str(locking_selects[1])
    assert "ORDER BY knowledge_documents.id" in str(locking_selects[2])
    assert isinstance(resolved.config, ResolvedAgentConfig)
    assert resolved.config.agent_id == agent["id"]
    assert resolved.config.owner_user_id == alice["id"]
    assert resolved.config.home_workspace is not None
    assert resolved.config.home_workspace.workspace_id == workspace["id"]
    assert resolved.config.home_workspace.owner_user_id == alice["id"]
    assert resolved.config.home_workspace.initial_agents_md == "# 规则"
    assert resolved.config.knowledge_scopes[0].collection_id == collection["id"]
    assert resolved.config.knowledge_scopes[0].owner_user_id == alice["id"]
    assert resolved.config.knowledge_scopes[0].document_ids == (document_id,)
    assert dict(resolved.config.knowledge_scopes[0].config_json) == {
        "chunk_size": 900,
        "chunk_overlap": 120,
        "top_k": 8,
        "score_threshold": None,
    }
    assert resolved.config_hash == resolved.config.config_hash
    assert len(resolved.config_hash) == 64

    assert agent_resource is not None and workspace_resource is not None
    agent_resource.config_json["system_prompt"] = "mutated after transaction"
    workspace_resource.config_json["initial_agents_md"] = "# Mutated"
    assert resolved.config.system_prompt == "帮助用户。"
    assert resolved.config.home_workspace.initial_agents_md == "# 规则"
    with pytest.raises(TypeError):
        resolved.config.config_json["system_prompt"] = "blocked"  # type: ignore[index]
    scopes = resolved.config.config_json["knowledge_scopes"]
    assert isinstance(scopes, tuple)
    with pytest.raises(TypeError):
        scopes[0]["collection_id"] = "blocked"  # type: ignore[index]


def test_config_hash_covers_workspace_snapshot_without_agent_update(
    client,
    create_user,
) -> None:
    alice, alice_headers = create_user("alice")
    workspace = client.post(
        "/api/workspaces",
        headers=alice_headers,
        json=_workspace_payload("运行空间"),
    ).json()
    agent = client.post(
        "/api/agents",
        headers=alice_headers,
        json=_agent_payload("运行助手", workspace_id=workspace["id"]),
    ).json()

    with client.app.state.session_factory() as session:
        service = AgentService(settings=client.app.state.settings)
        first = service.resolve_for_turn(
            session,
            user_id=alice["id"],
            agent_id=agent["id"],
        )
        agent_updated_at = first.updated_at
        workspace_resource = session.get(models.Resource, workspace["id"])
        assert workspace_resource is not None
        workspace_resource.config_json = {
            "workspace_type": "agent_home",
            "initial_agents_md": "# Changed home config",
        }
        workspace_resource.updated_at = models._now()
        session.flush()
        second = service.resolve_for_turn(
            session,
            user_id=alice["id"],
            agent_id=agent["id"],
        )

    assert second.updated_at == agent_updated_at
    assert first.config.config_hash != second.config.config_hash
    assert second.config.home_workspace is not None
    assert (
        second.config.home_workspace.config_json["initial_agents_md"]
        == "# Changed home config"
    )


def test_global_agent_snapshot_keeps_global_definition_owners(
    client,
    admin_headers,
    create_user,
) -> None:
    alice, _alice_headers = create_user("alice")
    workspace = client.post(
        "/api/admin/workspaces",
        headers=admin_headers,
        json=_workspace_payload("全局运行空间"),
    ).json()
    agent = client.post(
        "/api/admin/agents",
        headers=admin_headers,
        json=_agent_payload("全局运行助手", workspace_id=workspace["id"]),
    ).json()

    with client.app.state.session_factory() as session:
        resolved = AgentService(settings=client.app.state.settings).resolve_for_turn(
            session,
            user_id=alice["id"],
            agent_id=agent["id"],
        )

    assert resolved.config.owner_user_id == 0
    assert resolved.config.home_workspace is not None
    assert resolved.config.home_workspace.owner_user_id == 0
    assert resolved.config.home_workspace.workspace_id == workspace["id"]


def test_resolve_for_turn_rejects_invisible_inactive_and_deleted_agents(
    client,
    create_user,
) -> None:
    alice, alice_headers = create_user("alice")
    bob, _bob_headers = create_user("bob")
    agent = client.post(
        "/api/agents", headers=alice_headers, json=_agent_payload("运行助手")
    ).json()
    service = AgentService(settings=client.app.state.settings)

    with client.app.state.session_factory() as session:
        with pytest.raises(HTTPException) as invisible:
            service.resolve_for_turn(
                session, user_id=bob["id"], agent_id=agent["id"]
            )
    assert invisible.value.status_code == 409
    assert invisible.value.detail["code"] == "agent_unavailable"

    for deleted in [False, True]:
        _set_resource_state(
            client, agent["id"], is_active=False, deleted=deleted
        )
        with client.app.state.session_factory() as session:
            with pytest.raises(HTTPException) as unavailable:
                service.resolve_for_turn(
                    session, user_id=alice["id"], agent_id=agent["id"]
                )
        assert unavailable.value.status_code == 409
        assert unavailable.value.detail["code"] == "agent_unavailable"


def test_resolve_model_obeys_override_agent_default_and_system_default(
    tmp_path,
) -> None:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        qwen_api_key="key",
        qwen_chat_models="system-default,agent-default,override",
        openai_api_key=None,
        deepseek_api_key=None,
        default_chat_provider="qwen",
    )
    service = AgentService(settings=settings)
    resolved = _resolved_agent(
        default_model=ModelRef(provider="qwen", model="agent-default")
    )

    assert service.resolve_model(
        agent=resolved,
        conversation_override=ModelRef(provider="qwen", model="override"),
    ).model == "override"
    assert service.resolve_model(agent=resolved, conversation_override=None).model == (
        "agent-default"
    )
    no_agent_default = _resolved_agent()
    assert service.resolve_model(
        agent=no_agent_default,
        conversation_override=None,
    ).model == "system-default"


def test_resolve_model_returns_stable_unavailable_error(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        qwen_api_key=None,
        openai_api_key=None,
        deepseek_api_key=None,
    )
    service = AgentService(settings=settings)
    resolved = _resolved_agent()
    with pytest.raises(HTTPException) as no_default:
        service.resolve_model(agent=resolved, conversation_override=None)
    assert no_default.value.status_code == 503
    assert no_default.value.detail["code"] == "model_unavailable"

    with pytest.raises(HTTPException) as invalid_override:
        service.resolve_model(
            agent=resolved,
            conversation_override=ModelRef(provider="qwen", model="missing"),
        )
    assert invalid_override.value.status_code == 503
    assert invalid_override.value.detail["code"] == "model_unavailable"


def test_resolve_model_does_not_fallback_from_unconfigured_system_provider(
    tmp_path,
) -> None:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        default_chat_provider="qwen",
        qwen_api_key=None,
        openai_api_key="key",
        openai_chat_models="available-model",
        deepseek_api_key=None,
    )
    service = AgentService(settings=settings)
    no_agent_default = _resolved_agent()

    with pytest.raises(HTTPException) as unavailable:
        service.resolve_model(
            agent=no_agent_default,
            conversation_override=None,
        )

    assert unavailable.value.status_code == 503
    assert unavailable.value.detail["code"] == "model_unavailable"

    explicit_openai_default = _resolved_agent(
        default_model=ModelRef(provider="openai", model="available-model")
    )
    assert service.resolve_model(
        agent=explicit_openai_default,
        conversation_override=None,
    ) == ModelRef(provider="openai", model="available-model")


def test_agent_routes_require_auth_and_validate_detail_ids(client) -> None:
    assert client.get("/api/agents").status_code == 401
    assert client.post("/api/agents", json=_agent_payload("未登录")).status_code == 401


@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE"])
@pytest.mark.parametrize("resource_id", ["%20%20%20", "x" * 37])
def test_agent_detail_routes_use_resource_id_schema(
    client,
    ordinary_user_headers,
    method: str,
    resource_id: str,
) -> None:
    response = client.request(
        method,
        f"/api/agents/{resource_id}",
        headers=ordinary_user_headers,
        json=_agent_payload("有效名称", is_active=True) if method == "PUT" else None,
    )
    assert response.status_code == 422


@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE"])
def test_admin_agent_detail_routes_do_not_exist(
    client,
    admin_headers,
    method: str,
) -> None:
    response = client.request(
        method,
        "/api/admin/agents/not-an-agent",
        headers=admin_headers,
        json=_agent_payload("无路由", is_active=True) if method == "PUT" else None,
    )
    assert response.status_code == 404
