import pytest

from app.db import models


def _workspace_payload(
    name: str,
    *,
    instructions: str = "# 规则",
    is_active: bool | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": name,
        "config": {
            "workspace_type": "agent_home",
            "initial_agents_md": instructions,
        },
    }
    if is_active is not None:
        payload["is_active"] = is_active
    return payload


def _add_workspace_agent_reference(
    client,
    *,
    owner_id: int,
    workspace_id: str,
    name: str,
    is_active: bool = True,
) -> str:
    with client.app.state.session_factory() as session:
        agent = models.Resource(
            user_id=owner_id,
            resource_type="agent",
            name=name,
            config_json={
                "system_prompt": "Help the user.",
                "default_model": None,
                "home_workspace_id": workspace_id,
                "knowledge_scopes": [],
            },
            is_active=is_active,
        )
        session.add(agent)
        session.commit()
        return agent.id


def _set_resource_active(client, resource_id: str, is_active: bool) -> None:
    with client.app.state.session_factory() as session:
        resource = session.get(models.Resource, resource_id)
        assert resource is not None
        resource.is_active = is_active
        session.commit()


def test_private_workspace_is_visible_only_to_owner(client, create_user) -> None:
    _alice, alice_headers = create_user("alice")
    _bob, bob_headers = create_user("bob")

    created = client.post(
        "/api/workspaces",
        headers=alice_headers,
        json=_workspace_payload("成长记录"),
    )

    assert created.status_code == 201
    assert created.json()["scope"] == "private"
    assert created.json()["can_manage"] is True
    response = client.get(
        f"/api/workspaces/{created.json()['id']}",
        headers=bob_headers,
    )
    assert response.status_code == 404

def test_admin_global_workspace_has_no_visibility_field(
    client,
    admin_headers,
) -> None:
    response = client.post(
        "/api/admin/workspaces",
        headers=admin_headers,
        json=_workspace_payload("全局成长空间", instructions="# 全局规则"),
    )

    assert response.status_code == 201
    assert response.json()["scope"] == "global"
    assert response.json()["can_manage"] is True
    assert "visibility" not in response.json()


def test_workspace_global_is_readable_by_users_but_only_admin_can_write(
    client,
    admin_headers,
    ordinary_user_headers,
) -> None:
    created = client.post(
        "/api/admin/workspaces",
        headers=admin_headers,
        json=_workspace_payload("全局工作区"),
    )
    assert created.status_code == 201
    workspace_id = created.json()["id"]

    visible = client.get(
        f"/api/workspaces/{workspace_id}",
        headers=ordinary_user_headers,
    )
    assert visible.status_code == 200
    assert visible.json()["scope"] == "global"
    assert visible.json()["can_manage"] is False

    forbidden_update = client.put(
        f"/api/workspaces/{workspace_id}",
        headers=ordinary_user_headers,
        json=_workspace_payload("越权改名", is_active=True),
    )
    assert forbidden_update.status_code == 404
    assert forbidden_update.json()["detail"]["code"] == "resource_not_found"
    assert client.delete(
        f"/api/workspaces/{workspace_id}",
        headers=ordinary_user_headers,
    ).status_code == 404

    admin_on_private_route = client.put(
        f"/api/workspaces/{workspace_id}",
        headers=admin_headers,
        json=_workspace_payload("全局工作区 2", instructions="# 新规则"),
    )
    assert admin_on_private_route.status_code == 404
    assert admin_on_private_route.json()["detail"]["code"] == "resource_not_found"

    updated = client.put(
        f"/api/admin/workspaces/{workspace_id}",
        headers=admin_headers,
        json=_workspace_payload("全局工作区 2", instructions="# 新规则"),
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "全局工作区 2"
    assert updated.json()["can_manage"] is True

    ordinary_global_create = client.post(
        "/api/admin/workspaces",
        headers=ordinary_user_headers,
        json=_workspace_payload("不允许"),
    )
    assert ordinary_global_create.status_code == 403

    deleted = client.delete(
        f"/api/admin/workspaces/{workspace_id}",
        headers=admin_headers,
    )
    assert deleted.status_code == 200
    assert deleted.json() == {"id": workspace_id, "status": "deleted"}


def test_workspace_lists_enforce_visible_owned_and_global_scopes(
    client,
    admin_headers,
    create_user,
) -> None:
    _alice, alice_headers = create_user("alice")
    _bob, bob_headers = create_user("bob")
    alice_workspace = client.post(
        "/api/workspaces",
        headers=alice_headers,
        json=_workspace_payload("Alice 空间"),
    ).json()
    bob_workspace = client.post(
        "/api/workspaces",
        headers=bob_headers,
        json=_workspace_payload("Bob 空间"),
    ).json()
    global_workspace = client.post(
        "/api/admin/workspaces",
        headers=admin_headers,
        json=_workspace_payload("全局列表空间"),
    ).json()

    visible = client.get(
        "/api/workspaces?scope=visible",
        headers=alice_headers,
    ).json()["workspaces"]
    owned = client.get(
        "/api/workspaces?scope=owned",
        headers=alice_headers,
    ).json()["workspaces"]
    global_items = client.get(
        "/api/workspaces?scope=global",
        headers=alice_headers,
    ).json()["workspaces"]

    visible_ids = {item["id"] for item in visible}
    assert alice_workspace["id"] in visible_ids
    assert global_workspace["id"] in visible_ids
    assert bob_workspace["id"] not in visible_ids
    assert {item["id"] for item in owned} == {alice_workspace["id"]}
    assert global_workspace["id"] in {item["id"] for item in global_items}
    assert all(item["scope"] == "global" for item in global_items)
    assert all(item["can_manage"] is False for item in global_items)

    invalid_scope = client.get(
        "/api/workspaces?scope=everything",
        headers=alice_headers,
    )
    assert invalid_scope.status_code == 422


def test_workspace_put_is_full_replacement_and_supports_reactivation(
    client,
    ordinary_user_headers,
) -> None:
    created = client.post(
        "/api/workspaces",
        headers=ordinary_user_headers,
        json=_workspace_payload("原空间", instructions="# 原规则"),
    ).json()
    workspace_id = created["id"]

    missing_config = client.put(
        f"/api/workspaces/{workspace_id}",
        headers=ordinary_user_headers,
        json={"name": "缺配置", "is_active": True},
    )
    assert missing_config.status_code == 422
    wrong_type = client.put(
        f"/api/workspaces/{workspace_id}",
        headers=ordinary_user_headers,
        json={
            "name": "改变类型",
            "config": {
                "workspace_type": "code",
                "initial_agents_md": "# 规则",
            },
            "is_active": True,
        },
    )
    assert wrong_type.status_code == 422

    deactivated = client.put(
        f"/api/workspaces/{workspace_id}",
        headers=ordinary_user_headers,
        json=_workspace_payload(
            "新空间",
            instructions="# 全量新规则",
            is_active=False,
        ),
    )
    assert deactivated.status_code == 200
    assert deactivated.json()["is_active"] is False
    assert deactivated.json()["config"]["initial_agents_md"] == "# 全量新规则"
    assert client.get(
        f"/api/workspaces/{workspace_id}",
        headers=ordinary_user_headers,
    ).status_code == 404

    reactivated = client.put(
        f"/api/workspaces/{workspace_id}",
        headers=ordinary_user_headers,
        json=_workspace_payload(
            "新空间",
            instructions="# 再次启用",
            is_active=True,
        ),
    )
    assert reactivated.status_code == 200
    assert reactivated.json()["is_active"] is True


@pytest.mark.parametrize("method", ["POST", "PUT"])
@pytest.mark.parametrize(
    "payload",
    [
        _workspace_payload("   "),
        _workspace_payload("有名称", instructions="   "),
    ],
)
def test_workspace_mutations_reject_whitespace_only_strings(
    client,
    ordinary_user_headers,
    method: str,
    payload: dict[str, object],
) -> None:
    if method == "POST":
        url = "/api/workspaces"
    else:
        workspace = client.post(
            "/api/workspaces",
            headers=ordinary_user_headers,
            json=_workspace_payload("待更新空间"),
        ).json()
        url = f"/api/workspaces/{workspace['id']}"
        payload = {**payload, "is_active": True}

    response = client.request(
        method,
        url,
        headers=ordinary_user_headers,
        json=payload,
    )

    assert response.status_code == 422


def test_workspace_mutations_trim_strings_before_persisting(
    client,
    ordinary_user_headers,
) -> None:
    created = client.post(
        "/api/workspaces",
        headers=ordinary_user_headers,
        json=_workspace_payload(
            "  成长空间  ",
            instructions="  # 初始规则  ",
        ),
    )

    assert created.status_code == 201
    assert created.json()["name"] == "成长空间"
    assert created.json()["config"]["initial_agents_md"] == "# 初始规则"
    workspace_id = created.json()["id"]

    updated = client.put(
        f"/api/workspaces/{workspace_id}",
        headers=ordinary_user_headers,
        json=_workspace_payload(
            "  更新空间  ",
            instructions="  # 更新规则  ",
            is_active=False,
        ),
    )

    assert updated.status_code == 200
    assert updated.json()["name"] == "更新空间"
    assert updated.json()["config"]["initial_agents_md"] == "# 更新规则"
    assert updated.json()["is_active"] is False
    with client.app.state.session_factory() as session:
        persisted = session.get(models.Resource, workspace_id)
        assert persisted is not None
        assert persisted.name == "更新空间"
        assert persisted.config_json == {
            "workspace_type": "agent_home",
            "initial_agents_md": "# 更新规则",
        }
        assert persisted.is_active is False


def test_workspace_duplicate_and_tombstone_names_are_reserved(
    client,
    create_user,
) -> None:
    _alice, alice_headers = create_user("alice")
    _bob, bob_headers = create_user("bob")
    first = client.post(
        "/api/workspaces",
        headers=alice_headers,
        json=_workspace_payload("保留名"),
    )
    assert first.status_code == 201

    duplicate = client.post(
        "/api/workspaces",
        headers=alice_headers,
        json=_workspace_payload("  保留名  "),
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "resource_name_taken"
    second = client.post(
        "/api/workspaces",
        headers=alice_headers,
        json=_workspace_payload("第二个名称"),
    )
    rename_collision = client.put(
        f"/api/workspaces/{second.json()['id']}",
        headers=alice_headers,
        json=_workspace_payload(
            "  保留名  ",
            instructions="# 冲突规则",
            is_active=False,
        ),
    )
    assert rename_collision.status_code == 409
    assert rename_collision.json()["detail"]["code"] == "resource_name_taken"
    unchanged = client.get(
        f"/api/workspaces/{second.json()['id']}",
        headers=alice_headers,
    )
    assert unchanged.status_code == 200
    assert unchanged.json()["name"] == "第二个名称"
    assert unchanged.json()["config"] == {
        "workspace_type": "agent_home",
        "initial_agents_md": "# 规则",
    }
    assert unchanged.json()["is_active"] is True
    assert client.post(
        "/api/workspaces",
        headers=bob_headers,
        json=_workspace_payload("保留名"),
    ).status_code == 201

    deleted = client.delete(
        f"/api/workspaces/{first.json()['id']}",
        headers=alice_headers,
    )
    assert deleted.status_code == 200
    assert deleted.json() == {"id": first.json()["id"], "status": "deleted"}
    assert client.get(
        f"/api/workspaces/{first.json()['id']}",
        headers=alice_headers,
    ).status_code == 404
    assert client.put(
        f"/api/workspaces/{first.json()['id']}",
        headers=alice_headers,
        json=_workspace_payload("保留名", is_active=True),
    ).status_code == 404
    assert client.delete(
        f"/api/workspaces/{first.json()['id']}",
        headers=alice_headers,
    ).status_code == 404

    recreated = client.post(
        "/api/workspaces",
        headers=alice_headers,
        json=_workspace_payload("保留名"),
    )
    assert recreated.status_code == 409
    assert recreated.json()["detail"]["code"] == "resource_name_taken"


def test_workspace_type_cannot_change_after_creation(
    client,
    create_user,
) -> None:
    alice, alice_headers = create_user("alice")
    with client.app.state.session_factory() as session:
        workspace = models.Resource(
            user_id=alice["id"],
            resource_type="workspace",
            name="未来类型空间",
            config_json={
                "workspace_type": "future_type",
                "initial_agents_md": "# 未来规则",
            },
        )
        session.add(workspace)
        session.commit()
        workspace_id = workspace.id

    response = client.put(
        f"/api/workspaces/{workspace_id}",
        headers=alice_headers,
        json=_workspace_payload("未来类型空间", is_active=True),
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "workspace_type_immutable"


def test_active_agent_blocks_workspace_deactivation_and_deletion(
    client,
    create_user,
) -> None:
    alice, alice_headers = create_user("alice")
    workspace = client.post(
        "/api/workspaces",
        headers=alice_headers,
        json=_workspace_payload("被引用空间"),
    ).json()
    agent_id = _add_workspace_agent_reference(
        client,
        owner_id=alice["id"],
        workspace_id=workspace["id"],
        name="Alice Agent",
    )

    deactivated = client.put(
        f"/api/workspaces/{workspace['id']}",
        headers=alice_headers,
        json=_workspace_payload("被引用空间", is_active=False),
    )
    assert deactivated.status_code == 409
    assert deactivated.json()["detail"]["code"] == "resource_in_use"
    deleted = client.delete(
        f"/api/workspaces/{workspace['id']}",
        headers=alice_headers,
    )
    assert deleted.status_code == 409
    assert deleted.json()["detail"]["code"] == "resource_in_use"

    _set_resource_active(client, agent_id, False)
    assert client.delete(
        f"/api/workspaces/{workspace['id']}",
        headers=alice_headers,
    ).status_code == 200


def test_private_agent_of_another_user_blocks_global_workspace_removal(
    client,
    admin_headers,
    create_user,
) -> None:
    bob, _bob_headers = create_user("bob")
    workspace = client.post(
        "/api/admin/workspaces",
        headers=admin_headers,
        json=_workspace_payload("跨用户引用空间"),
    ).json()
    agent_id = _add_workspace_agent_reference(
        client,
        owner_id=bob["id"],
        workspace_id=workspace["id"],
        name="Bob Private Agent",
    )

    deactivated = client.put(
        f"/api/admin/workspaces/{workspace['id']}",
        headers=admin_headers,
        json=_workspace_payload("跨用户引用空间", is_active=False),
    )
    assert deactivated.status_code == 409
    assert deactivated.json()["detail"]["code"] == "resource_in_use"
    deleted = client.delete(
        f"/api/admin/workspaces/{workspace['id']}",
        headers=admin_headers,
    )
    assert deleted.status_code == 409
    assert deleted.json()["detail"]["code"] == "resource_in_use"

    _set_resource_active(client, agent_id, False)
    assert client.delete(
        f"/api/admin/workspaces/{workspace['id']}",
        headers=admin_headers,
    ).status_code == 200


def test_admin_cannot_read_or_manage_another_users_private_workspace(
    client,
    admin_headers,
    ordinary_user_headers,
) -> None:
    workspace = client.post(
        "/api/workspaces",
        headers=ordinary_user_headers,
        json=_workspace_payload("私有边界"),
    ).json()
    workspace_url = f"/api/workspaces/{workspace['id']}"

    assert client.get(workspace_url, headers=admin_headers).status_code == 404
    assert client.put(
        workspace_url,
        headers=admin_headers,
        json=_workspace_payload("管理员越权", is_active=True),
    ).status_code == 404
    assert client.delete(workspace_url, headers=admin_headers).status_code == 404


def test_workspace_routes_require_authentication(client) -> None:
    assert client.get("/api/workspaces").status_code == 401
    assert client.post(
        "/api/workspaces",
        json=_workspace_payload("未登录"),
    ).status_code == 401


@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE"])
@pytest.mark.parametrize("resource_id", ["%20%20%20", "x" * 37])
def test_workspace_detail_routes_reject_invalid_resource_ids(
    client,
    ordinary_user_headers,
    method: str,
    resource_id: str,
) -> None:
    response = client.request(
        method,
        f"/api/workspaces/{resource_id}",
        headers=ordinary_user_headers,
        json=_workspace_payload("有效名称", is_active=True)
        if method == "PUT"
        else None,
    )

    assert response.status_code == 422
