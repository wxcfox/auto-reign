from __future__ import annotations

import pytest

from app.storage.object_store import ObjectStoreUnavailable


def _workspace_payload(name: str, instructions: str = "# Initial rules") -> dict[str, object]:
    return {
        "name": name,
        "config": {
            "workspace_type": "agent_home",
            "initial_agents_md": instructions,
        },
    }


@pytest.fixture
def auth_headers(create_user) -> dict[str, str]:
    _user, headers = create_user("workspace-alice")
    return headers


@pytest.fixture
def other_auth_headers(create_user) -> dict[str, str]:
    _user, headers = create_user("workspace-bob")
    return headers


@pytest.fixture
def private_workspace(client, auth_headers) -> dict[str, object]:
    response = client.post(
        "/api/workspaces",
        headers=auth_headers,
        json=_workspace_payload("Private Agent Home"),
    )
    assert response.status_code == 201
    return response.json()


@pytest.fixture
def global_workspace(client, admin_headers) -> dict[str, object]:
    response = client.post(
        "/api/admin/workspaces",
        headers=admin_headers,
        json=_workspace_payload("Global Agent Home", "# Global initial rules"),
    )
    assert response.status_code == 201
    return response.json()


def test_workspace_file_api_initializes_and_updates_agents_md(
    client,
    auth_headers,
    private_workspace,
) -> None:
    workspace_id = private_workspace["id"]
    listing = client.get(
        f"/api/workspaces/{workspace_id}/files?directory=",
        headers=auth_headers,
    )

    assert listing.status_code == 200
    assert listing.json() == {
        "directory": "",
        "items": [
            {
                "path": "AGENTS.md",
                "name": "AGENTS.md",
                "is_directory": False,
                "size_bytes": len("# Initial rules"),
                "etag": listing.json()["items"][0]["etag"],
            }
        ],
    }

    opened = client.get(
        f"/api/workspaces/{workspace_id}/files/content?path=AGENTS.md",
        headers=auth_headers,
    )
    assert opened.status_code == 200
    assert opened.json()["content"] == "# Initial rules"

    updated = client.put(
        f"/api/workspaces/{workspace_id}/files/content",
        headers=auth_headers,
        json={
            "path": "AGENTS.md",
            "content": "# Evolved",
            "expected_etag": opened.json()["etag"],
        },
    )
    assert updated.status_code == 200
    assert updated.json()["content"] == "# Evolved"

    protected = client.delete(
        f"/api/workspaces/{workspace_id}/files?path=AGENTS.md",
        headers=auth_headers,
    )
    assert protected.status_code == 400
    assert protected.json()["detail"]["code"] == "workspace_file_invalid"


def test_workspace_file_api_creates_lists_reads_updates_and_deletes_file(
    client,
    auth_headers,
    private_workspace,
) -> None:
    workspace_id = private_workspace["id"]
    created = client.post(
        f"/api/workspaces/{workspace_id}/files/content",
        headers=auth_headers,
        json={"path": "notes/first.md", "content": "First"},
    )
    assert created.status_code == 201
    assert created.json()["path"] == "notes/first.md"
    assert created.json()["name"] == "first.md"
    assert created.json()["content"] == "First"

    root = client.get(
        f"/api/workspaces/{workspace_id}/files",
        headers=auth_headers,
    )
    assert root.status_code == 200
    assert [(item["path"], item["is_directory"]) for item in root.json()["items"]] == [
        ("notes", True),
        ("AGENTS.md", False),
    ]
    assert root.json()["items"][0]["size_bytes"] is None
    assert root.json()["items"][0]["etag"] is None

    notes = client.get(
        f"/api/workspaces/{workspace_id}/files?directory=notes",
        headers=auth_headers,
    )
    assert notes.status_code == 200
    assert [item["path"] for item in notes.json()["items"]] == ["notes/first.md"]

    stale = client.put(
        f"/api/workspaces/{workspace_id}/files/content",
        headers=auth_headers,
        json={
            "path": "notes/first.md",
            "content": "Changed",
            "expected_etag": "stale-etag",
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "workspace_conflict"

    updated = client.put(
        f"/api/workspaces/{workspace_id}/files/content",
        headers=auth_headers,
        json={
            "path": "notes/first.md",
            "content": "Changed",
            "expected_etag": created.json()["etag"],
        },
    )
    assert updated.status_code == 200
    assert updated.json()["content"] == "Changed"

    deleted = client.delete(
        f"/api/workspaces/{workspace_id}/files?path=notes/first.md",
        headers=auth_headers,
    )
    assert deleted.status_code == 204
    assert client.get(
        f"/api/workspaces/{workspace_id}/files/content?path=notes/first.md",
        headers=auth_headers,
    ).status_code == 404


def test_workspace_file_api_hides_other_users_private_workspace(
    client,
    auth_headers,
    other_auth_headers,
    private_workspace,
) -> None:
    workspace_id = private_workspace["id"]
    response = client.get(
        f"/api/workspaces/{workspace_id}/files",
        headers=other_auth_headers,
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "resource_not_found"


def test_admin_file_api_uses_admin_prefix_for_global_workspace(
    client,
    admin_headers,
    global_workspace,
    fake_object_store,
) -> None:
    workspace_id = global_workspace["id"]
    response = client.get(
        f"/api/admin/workspaces/{workspace_id}/files",
        headers=admin_headers,
    )

    assert response.status_code == 200
    current = client.get("/api/auth/me", headers=admin_headers)
    assert current.status_code == 200
    admin_id = current.json()["id"]
    assert fake_object_store.keys() == [
        f"users/{admin_id}/workspaces/{workspace_id}/AGENTS.md"
    ]
    assert all("users/0/" not in key for key in fake_object_store.keys())


def test_global_workspace_has_an_isolated_file_instance_per_actor(
    client,
    admin_headers,
    auth_headers,
    global_workspace,
) -> None:
    workspace_id = global_workspace["id"]
    admin_opened = client.get(
        f"/api/admin/workspaces/{workspace_id}/files/content?path=AGENTS.md",
        headers=admin_headers,
    )
    assert admin_opened.status_code == 200
    assert client.put(
        f"/api/admin/workspaces/{workspace_id}/files/content",
        headers=admin_headers,
        json={
            "path": "AGENTS.md",
            "content": "# Admin evolved",
            "expected_etag": admin_opened.json()["etag"],
        },
    ).status_code == 200

    user_opened = client.get(
        f"/api/workspaces/{workspace_id}/files/content?path=AGENTS.md",
        headers=auth_headers,
    )
    assert user_opened.status_code == 200
    assert user_opened.json()["content"] == "# Global initial rules"


def test_admin_file_api_requires_global_workspace(
    client,
    admin_headers,
    auth_headers,
    private_workspace,
) -> None:
    response = client.get(
        f"/api/admin/workspaces/{private_workspace['id']}/files",
        headers=admin_headers,
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "resource_not_found"
    assert client.get(
        f"/api/admin/workspaces/{private_workspace['id']}/files",
        headers=auth_headers,
    ).status_code == 403


def test_private_and_admin_routes_reuse_the_app_agent_home_service(
    client,
    auth_headers,
    admin_headers,
    private_workspace,
    global_workspace,
) -> None:
    service = client.app.state.agent_home_service
    private = client.get(
        f"/api/workspaces/{private_workspace['id']}/files",
        headers=auth_headers,
    )
    global_response = client.get(
        f"/api/admin/workspaces/{global_workspace['id']}/files",
        headers=admin_headers,
    )

    assert private.status_code == 200
    assert global_response.status_code == 200
    assert client.app.state.agent_home_service is service
    assert service.store is client.app.state.object_store


@pytest.mark.parametrize(
    ("method", "payload"),
    [
        ("post", {"path": "notes/new.md", "content": "x", "unexpected": True}),
        (
            "put",
            {
                "path": "notes/existing.md",
                "content": "x",
                "expected_etag": "etag-1",
                "unexpected": True,
            },
        ),
    ],
)
def test_workspace_file_writes_forbid_extra_fields_before_object_store(
    client,
    auth_headers,
    private_workspace,
    fake_object_store,
    method,
    payload,
) -> None:
    before = len(fake_object_store.put_calls)
    response = getattr(client, method)(
        f"/api/workspaces/{private_workspace['id']}/files/content",
        headers=auth_headers,
        json=payload,
    )

    assert response.status_code == 422
    assert len(fake_object_store.put_calls) == before


def test_workspace_write_rejects_multibyte_oversized_etag_before_object_store(
    client,
    auth_headers,
    private_workspace,
    fake_object_store,
) -> None:
    before = len(fake_object_store.put_calls)
    response = client.put(
        f"/api/workspaces/{private_workspace['id']}/files/content",
        headers=auth_headers,
        json={
            "path": "notes/existing.md",
            "content": "x",
            "expected_etag": "界" * 86,
        },
    )

    assert response.status_code == 422
    assert len(fake_object_store.put_calls) == before


def test_workspace_file_api_maps_conflict_missing_and_store_failure(
    client,
    auth_headers,
    private_workspace,
    fake_object_store,
) -> None:
    workspace_id = private_workspace["id"]
    first = client.post(
        f"/api/workspaces/{workspace_id}/files/content",
        headers=auth_headers,
        json={"path": "notes/same.md", "content": "First"},
    )
    assert first.status_code == 201

    duplicate = client.post(
        f"/api/workspaces/{workspace_id}/files/content",
        headers=auth_headers,
        json={"path": "notes/same.md", "content": "Second"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "workspace_conflict"

    missing = client.get(
        f"/api/workspaces/{workspace_id}/files/content?path=notes/missing.md",
        headers=auth_headers,
    )
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "workspace_file_not_found"

    fake_object_store.get_error = ObjectStoreUnavailable("secret endpoint")
    unavailable = client.get(
        f"/api/workspaces/{workspace_id}/files/content?path=AGENTS.md",
        headers=auth_headers,
    )
    assert unavailable.status_code == 503
    assert unavailable.json()["detail"] == {
        "code": "workspace_unavailable",
        "message": "Workspace storage is unavailable.",
    }
    assert "secret" not in unavailable.text


def test_workspace_file_api_rejects_invalid_mutations_without_store_side_effects(
    client,
    auth_headers,
    private_workspace,
    fake_object_store,
) -> None:
    before = len(fake_object_store.put_calls)
    response = client.post(
        f"/api/workspaces/{private_workspace['id']}/files/content",
        headers=auth_headers,
        json={"path": "../secret.md", "content": "x"},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "workspace_file_invalid"
    assert len(fake_object_store.put_calls) == before

    client.app.state.agent_home_service.max_file_bytes = 5
    oversized = client.post(
        f"/api/workspaces/{private_workspace['id']}/files/content",
        headers=auth_headers,
        json={"path": "notes/oversized.md", "content": "中文"},
    )
    assert oversized.status_code == 400
    assert oversized.json()["detail"]["code"] == "workspace_file_invalid"
    assert len(fake_object_store.put_calls) == before
