import pytest

from app.db import models


AGENT_PAYLOAD = {
    "name": "Global helper",
    "config": {
        "system_prompt": "Answer clearly.",
        "default_model": None,
        "home_workspace_id": None,
        "knowledge_scopes": [],
    },
}

WORKSPACE_PAYLOAD = {
    "name": "Global home",
    "config": {
        "workspace_type": "agent_home",
        "initial_agents_md": "# Shared template",
    },
}

COLLECTION_PAYLOAD = {
    "name": "Global handbook",
    "config": {},
}

RESOURCE_LIST_CASES = [
    pytest.param(
        "/api/agents",
        "/api/admin/agents",
        "agents",
        AGENT_PAYLOAD,
        id="agents",
    ),
    pytest.param(
        "/api/workspaces",
        "/api/admin/workspaces",
        "workspaces",
        WORKSPACE_PAYLOAD,
        id="workspaces",
    ),
    pytest.param(
        "/api/knowledge-collections",
        "/api/admin/knowledge-collections",
        "collections",
        COLLECTION_PAYLOAD,
        id="knowledge-collections",
    ),
]


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


@pytest.mark.parametrize(
    ("private_endpoint", "global_endpoint", "response_key", "base_payload"),
    RESOURCE_LIST_CASES,
)
def test_management_lists_include_inactive_but_never_tombstones_or_other_owners(
    client,
    admin_headers,
    create_user,
    private_endpoint: str,
    global_endpoint: str,
    response_key: str,
    base_payload: dict[str, object],
) -> None:
    _, alice_headers = create_user("management-alice")
    _, bob_headers = create_user("management-bob")

    def create_private(name: str, headers: dict[str, str]) -> dict[str, object]:
        response = client.post(
            private_endpoint,
            headers=headers,
            json={**base_payload, "name": name},
        )
        assert response.status_code == 201
        return response.json()

    def create_global(name: str) -> dict[str, object]:
        response = client.post(
            global_endpoint,
            headers=admin_headers,
            json={**base_payload, "name": name},
        )
        assert response.status_code == 201
        return response.json()

    owned_active = create_private("Owned active", alice_headers)
    owned_inactive = create_private("Owned inactive", alice_headers)
    owned_deleted = create_private("Owned deleted", alice_headers)
    other_inactive = create_private("Other inactive", bob_headers)
    global_active = create_global("Global active")
    global_inactive = create_global("Global inactive")
    global_deleted = create_global("Global deleted")

    _set_resource_state(client, str(owned_inactive["id"]), is_active=False)
    _set_resource_state(
        client,
        str(owned_deleted["id"]),
        is_active=False,
        deleted=True,
    )
    _set_resource_state(client, str(other_inactive["id"]), is_active=False)
    _set_resource_state(client, str(global_inactive["id"]), is_active=False)
    _set_resource_state(
        client,
        str(global_deleted["id"]),
        is_active=False,
        deleted=True,
    )

    owned_response = client.get(
        f"{private_endpoint}?scope=owned&include_inactive=true",
        headers=alice_headers,
    )
    assert owned_response.status_code == 200
    owned_items = owned_response.json()[response_key]
    owned_ids = {item["id"] for item in owned_items}
    assert owned_active["id"] in owned_ids
    assert owned_inactive["id"] in owned_ids
    assert owned_deleted["id"] not in owned_ids
    assert other_inactive["id"] not in owned_ids
    assert all(item["can_manage"] is True for item in owned_items)

    global_response = client.get(
        f"{private_endpoint}?scope=global&include_inactive=true",
        headers=admin_headers,
    )
    assert global_response.status_code == 200
    global_items = global_response.json()[response_key]
    global_ids = {item["id"] for item in global_items}
    assert global_active["id"] in global_ids
    assert global_inactive["id"] in global_ids
    assert global_deleted["id"] not in global_ids
    assert all(item["can_manage"] is True for item in global_items)

    ordinary_global = client.get(
        f"{private_endpoint}?scope=global&include_inactive=true",
        headers=alice_headers,
    )
    assert ordinary_global.status_code == 403
    assert ordinary_global.json()["detail"]["code"] == "admin_required"

    invalid_visible = client.get(
        f"{private_endpoint}?scope=visible&include_inactive=true",
        headers=alice_headers,
    )
    assert invalid_visible.status_code == 400
    assert invalid_visible.json()["detail"]["code"] == "resource_scope_invalid"

    ordinary_active_global = client.get(
        f"{private_endpoint}?scope=global",
        headers=alice_headers,
    )
    assert ordinary_active_global.status_code == 200
    ordinary_global_ids = {
        item["id"] for item in ordinary_active_global.json()[response_key]
    }
    assert global_active["id"] in ordinary_global_ids
    assert global_inactive["id"] not in ordinary_global_ids
    assert global_deleted["id"] not in ordinary_global_ids


def test_ordinary_user_cannot_mutate_global_or_enter_admin_routes(
    client, admin_headers, ordinary_user_headers
) -> None:
    created = client.post(
        "/api/admin/agents",
        headers=admin_headers,
        json=AGENT_PAYLOAD,
    )
    assert created.status_code == 201
    agent = created.json()

    update = client.put(
        f"/api/agents/{agent['id']}",
        headers=ordinary_user_headers,
        json={**AGENT_PAYLOAD, "name": "Tampered", "is_active": True},
    )
    create_global = client.post(
        "/api/admin/agents",
        headers=ordinary_user_headers,
        json=AGENT_PAYLOAD,
    )
    list_users = client.get(
        "/api/admin/users",
        headers=ordinary_user_headers,
    )

    assert update.status_code == 404
    assert update.json()["detail"]["code"] == "resource_not_found"
    assert create_global.status_code == 403
    assert create_global.json()["detail"]["code"] == "admin_required"
    assert list_users.status_code == 403
    assert list_users.json()["detail"]["code"] == "admin_required"


def test_agent_reference_validation_is_atomic_across_private_owners(
    client, create_user
) -> None:
    _, alice_headers = create_user("alice")
    _, bob_headers = create_user("bob")
    workspace = client.post(
        "/api/workspaces",
        headers=bob_headers,
        json={**WORKSPACE_PAYLOAD, "name": "Bob home"},
    ).json()

    response = client.post(
        "/api/agents",
        headers=alice_headers,
        json={
            **AGENT_PAYLOAD,
            "name": "Invalid agent",
            "config": {
                **AGENT_PAYLOAD["config"],
                "home_workspace_id": workspace["id"],
            },
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "resource_reference_invalid"
    owned = client.get("/api/agents?scope=owned", headers=alice_headers).json()
    assert owned["agents"] == []


def test_global_agent_cannot_reference_admin_private_workspace(
    client, admin_headers
) -> None:
    private_workspace = client.post(
        "/api/workspaces",
        headers=admin_headers,
        json={**WORKSPACE_PAYLOAD, "name": "Admin private home"},
    ).json()

    response = client.post(
        "/api/admin/agents",
        headers=admin_headers,
        json={
            **AGENT_PAYLOAD,
            "config": {
                **AGENT_PAYLOAD["config"],
                "home_workspace_id": private_workspace["id"],
            },
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "resource_reference_invalid"


def test_ordinary_user_cannot_use_global_resource_admin_details(
    client, admin_headers, ordinary_user_headers
) -> None:
    workspace = client.post(
        "/api/admin/workspaces",
        headers=admin_headers,
        json=WORKSPACE_PAYLOAD,
    ).json()
    collection = client.post(
        "/api/admin/knowledge-collections",
        headers=admin_headers,
        json=COLLECTION_PAYLOAD,
    ).json()

    workspace_update = client.put(
        f"/api/admin/workspaces/{workspace['id']}",
        headers=ordinary_user_headers,
        json={
            "name": "Tampered",
            "config": workspace["config"],
            "is_active": True,
        },
    )
    collection_delete = client.delete(
        f"/api/admin/knowledge-collections/{collection['id']}",
        headers=ordinary_user_headers,
    )

    assert workspace_update.status_code == 403
    assert workspace_update.json()["detail"]["code"] == "admin_required"
    assert collection_delete.status_code == 403
    assert collection_delete.json()["detail"]["code"] == "admin_required"


def test_admin_dependency_precedes_global_resource_lookup(
    client, ordinary_user_headers
) -> None:
    workspace_update = client.put(
        "/api/admin/workspaces/not-a-resource",
        headers=ordinary_user_headers,
        json={**WORKSPACE_PAYLOAD, "is_active": True},
    )
    collection_delete = client.delete(
        "/api/admin/knowledge-collections/not-a-resource",
        headers=ordinary_user_headers,
    )

    assert workspace_update.status_code == 403
    assert workspace_update.json()["detail"]["code"] == "admin_required"
    assert collection_delete.status_code == 403
    assert collection_delete.json()["detail"]["code"] == "admin_required"


def test_workspace_and_collection_mutations_hide_scope_mismatches(
    client, admin_headers
) -> None:
    private_workspace = client.post(
        "/api/workspaces",
        headers=admin_headers,
        json={**WORKSPACE_PAYLOAD, "name": "Admin private home"},
    ).json()
    global_workspace = client.post(
        "/api/admin/workspaces",
        headers=admin_headers,
        json=WORKSPACE_PAYLOAD,
    ).json()
    private_collection = client.post(
        "/api/knowledge-collections",
        headers=admin_headers,
        json={**COLLECTION_PAYLOAD, "name": "Admin private handbook"},
    ).json()
    global_collection = client.post(
        "/api/admin/knowledge-collections",
        headers=admin_headers,
        json=COLLECTION_PAYLOAD,
    ).json()

    private_route_global_workspace = client.put(
        f"/api/workspaces/{global_workspace['id']}",
        headers=admin_headers,
        json={**WORKSPACE_PAYLOAD, "name": "Wrong route", "is_active": True},
    )
    admin_route_private_workspace = client.put(
        f"/api/admin/workspaces/{private_workspace['id']}",
        headers=admin_headers,
        json={
            **WORKSPACE_PAYLOAD,
            "name": "Wrong authority",
            "is_active": True,
        },
    )
    private_route_global_collection = client.delete(
        f"/api/knowledge-collections/{global_collection['id']}",
        headers=admin_headers,
    )
    admin_route_private_collection = client.delete(
        f"/api/admin/knowledge-collections/{private_collection['id']}",
        headers=admin_headers,
    )

    for response in (
        private_route_global_workspace,
        admin_route_private_workspace,
        private_route_global_collection,
        admin_route_private_collection,
    ):
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "resource_not_found"
