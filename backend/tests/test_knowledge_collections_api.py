import pytest
from sqlalchemy import select

from app.db import models


DEFAULT_KNOWLEDGE_CONFIG = {
    "retriever_type": "elasticsearch",
    "retrieval_mode": "vector",
    "chunk_size": 900,
    "chunk_overlap": 120,
    "top_k": 5,
    "score_threshold": 0.5,
    "vector_weight": 0.7,
    "keyword_weight": 0.3,
}


def _collection_payload(
    name: str,
    *,
    is_active: bool | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {"name": name, "config": {}}
    if is_active is not None:
        payload["is_active"] = is_active
    return payload


def _add_collection_agent_reference(
    client,
    *,
    owner_id: int,
    collection_id: str,
    name: str,
    document_ids: list[str] | None = None,
    is_active: bool = True,
) -> str:
    with client.app.state.session_factory() as session:
        agent = models.Resource(
            user_id=owner_id,
            resource_type="agent",
            name=name,
            config_json={
                "system_prompt": "Use the knowledge collection.",
                "default_model": None,
                "home_workspace_id": None,
                "knowledge_scopes": [
                    {
                        "collection_id": collection_id,
                        "document_ids": document_ids,
                    }
                ],
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


def _add_knowledge_document(
    client,
    *,
    owner_id: int,
    collection_id: str,
    is_active: bool,
    error_code: str | None = None,
) -> str:
    with client.app.state.session_factory() as session:
        document = models.KnowledgeDocument(
            user_id=owner_id,
            collection_id=collection_id,
            name="document.txt",
            source_object_key=f"users/{owner_id}/knowledge/{collection_id}/source",
            parsed_object_key=None,
            mime_type="text/plain",
            size_bytes=6,
            content_hash="hash",
            status="queued",
            index_generation=1,
            error_code=error_code,
            is_active=is_active,
        )
        session.add(document)
        session.commit()
        return document.id


def _add_ready_document(
    client,
    *,
    collection_id: str,
    retriever_type: str,
    generation: int = 2,
) -> str:
    with client.app.state.session_factory() as session:
        collection = session.get(models.Resource, collection_id)
        assert collection is not None
        document = models.KnowledgeDocument(
            user_id=collection.user_id,
            collection_id=collection_id,
            name="document.txt",
            source_object_key="source",
            parsed_object_key="parsed",
            mime_type="text/plain",
            size_bytes=4,
            content_hash="hash",
            status="ready",
            index_generation=generation,
            retriever_type=retriever_type,
            is_active=True,
        )
        session.add(document)
        session.commit()
        return document.id


@pytest.mark.parametrize("config", [{"unknown": True}, {"top_k": 99}])
def test_collection_rejects_invalid_config(
    client,
    ordinary_user_headers,
    config,
) -> None:
    response = client.post(
        "/api/knowledge-collections",
        headers=ordinary_user_headers,
        json={"name": "手册", "config": config},
    )

    assert response.status_code == 422


def test_private_collection_is_visible_only_to_owner(client, create_user) -> None:
    _alice, alice_headers = create_user("alice")
    _bob, bob_headers = create_user("bob")
    created = client.post(
        "/api/knowledge-collections",
        headers=alice_headers,
        json=_collection_payload("个人手册"),
    )

    assert created.status_code == 201
    assert created.json()["scope"] == "private"
    assert created.json()["can_manage"] is True
    assert created.json()["config"] == DEFAULT_KNOWLEDGE_CONFIG
    assert client.get(
        f"/api/knowledge-collections/{created.json()['id']}",
        headers=bob_headers,
    ).status_code == 404


@pytest.mark.parametrize("retrieval_mode", ["vector", "keyword", "hybrid"])
def test_elasticsearch_accepts_every_supported_retrieval_mode(
    client,
    ordinary_user_headers,
    retrieval_mode: str,
) -> None:
    response = client.post(
        "/api/knowledge-collections",
        headers=ordinary_user_headers,
        json={
            "name": f"Elasticsearch {retrieval_mode}",
            "config": {
                "retriever_type": "elasticsearch",
                "retrieval_mode": retrieval_mode,
            },
        },
    )

    assert response.status_code == 201
    assert response.json()["config"]["retrieval_mode"] == retrieval_mode


def test_qdrant_vector_is_persisted(client, ordinary_user_headers) -> None:
    response = client.post(
        "/api/knowledge-collections",
        headers=ordinary_user_headers,
        json={
            "name": "Qdrant vectors",
            "config": {"retriever_type": "qdrant", "retrieval_mode": "vector"},
        },
    )

    assert response.status_code == 201
    assert response.json()["config"]["retriever_type"] == "qdrant"


@pytest.mark.parametrize("retrieval_mode", ["keyword", "hybrid"])
def test_qdrant_rejects_non_vector_modes(
    client,
    ordinary_user_headers,
    retrieval_mode: str,
) -> None:
    response = client.post(
        "/api/knowledge-collections",
        headers=ordinary_user_headers,
        json={
            "name": f"Invalid {retrieval_mode}",
            "config": {
                "retriever_type": "qdrant",
                "retrieval_mode": retrieval_mode,
            },
        },
    )

    assert response.status_code == 422
    assert "Qdrant supports only vector retrieval" in response.text


@pytest.mark.parametrize(
    ("original_retriever", "requested_retriever", "requested_mode"),
    [
        ("elasticsearch", "qdrant", "vector"),
        ("qdrant", "elasticsearch", "vector"),
        ("elasticsearch", "qdrant", "hybrid"),
        ("qdrant", "elasticsearch", "hybrid"),
    ],
)
def test_retriever_change_is_rejected_without_mutating_collection_or_document(
    client,
    ordinary_user_headers,
    original_retriever: str,
    requested_retriever: str,
    requested_mode: str,
) -> None:
    created = client.post(
        "/api/knowledge-collections",
        headers=ordinary_user_headers,
        json={
            "name": "Immutable Retriever",
            "config": {
                "retriever_type": original_retriever,
                "retrieval_mode": "vector",
            },
        },
    )
    assert created.status_code == 201
    collection = created.json()
    document_id = _add_ready_document(
        client,
        collection_id=collection["id"],
        retriever_type=original_retriever,
    )

    updated = client.put(
        f"/api/knowledge-collections/{collection['id']}",
        headers=ordinary_user_headers,
        json={
            "name": "Should not be saved",
            "config": {
                "retriever_type": requested_retriever,
                "retrieval_mode": requested_mode,
                "top_k": 9,
            },
            "is_active": True,
        },
    )

    assert updated.status_code == 409
    assert updated.json()["detail"] == {
        "code": "knowledge_retriever_immutable",
        "message": "Knowledge Retriever cannot be changed after creation.",
    }
    with client.app.state.session_factory() as session:
        resource = session.get(models.Resource, collection["id"])
        document = session.get(models.KnowledgeDocument, document_id)
        assert resource is not None and document is not None
        assert resource.name == collection["name"]
        assert resource.config_json == collection["config"]
        assert document.status == "ready"
        assert document.index_generation == 2
        assert document.retriever_type == original_retriever
        assert document.parsed_object_key == "parsed"


def test_qdrant_update_rejects_non_vector_mode_after_immutability_check(
    client,
    ordinary_user_headers,
) -> None:
    collection = client.post(
        "/api/knowledge-collections",
        headers=ordinary_user_headers,
        json={
            "name": "Qdrant capability",
            "config": {"retriever_type": "qdrant", "retrieval_mode": "vector"},
        },
    ).json()

    updated = client.put(
        f"/api/knowledge-collections/{collection['id']}",
        headers=ordinary_user_headers,
        json={
            "name": collection["name"],
            "config": {
                "retriever_type": "qdrant",
                "retrieval_mode": "hybrid",
            },
            "is_active": True,
        },
    )

    assert updated.status_code == 400
    assert updated.json()["detail"] == {
        "code": "knowledge_retrieval_mode_unsupported",
        "message": "Qdrant supports only vector retrieval.",
    }


def test_same_retriever_allows_query_setting_updates_without_reindex(
    client,
    ordinary_user_headers,
) -> None:
    collection = client.post(
        "/api/knowledge-collections",
        headers=ordinary_user_headers,
        json=_collection_payload("Search settings"),
    ).json()
    document_id = _add_ready_document(
        client,
        collection_id=collection["id"],
        retriever_type="elasticsearch",
    )

    updated = client.put(
        f"/api/knowledge-collections/{collection['id']}",
        headers=ordinary_user_headers,
        json={
            "name": "Updated search settings",
            "config": {
                "retriever_type": "elasticsearch",
                "retrieval_mode": "hybrid",
                "top_k": 8,
                "score_threshold": 0.4,
                "vector_weight": 0.6,
                "keyword_weight": 0.4,
            },
            "is_active": True,
        },
    )

    assert updated.status_code == 200
    assert updated.json()["config"] == {
        **DEFAULT_KNOWLEDGE_CONFIG,
        "retrieval_mode": "hybrid",
        "top_k": 8,
        "score_threshold": 0.4,
        "vector_weight": 0.6,
        "keyword_weight": 0.4,
    }
    with client.app.state.session_factory() as session:
        document = session.get(models.KnowledgeDocument, document_id)
        assert document is not None
        assert document.status == "ready"
        assert document.index_generation == 2
        assert document.parsed_object_key == "parsed"


def test_chunk_setting_update_requeues_documents_without_changing_retriever(
    client,
    ordinary_user_headers,
) -> None:
    collection = client.post(
        "/api/knowledge-collections",
        headers=ordinary_user_headers,
        json={
            "name": "Chunk settings",
            "config": {"retriever_type": "qdrant", "retrieval_mode": "vector"},
        },
    ).json()
    document_id = _add_ready_document(
        client,
        collection_id=collection["id"],
        retriever_type="qdrant",
    )

    updated = client.put(
        f"/api/knowledge-collections/{collection['id']}",
        headers=ordinary_user_headers,
        json={
            "name": collection["name"],
            "config": {
                "retriever_type": "qdrant",
                "retrieval_mode": "vector",
                "chunk_size": 700,
                "chunk_overlap": 80,
            },
            "is_active": True,
        },
    )

    assert updated.status_code == 200
    with client.app.state.session_factory() as session:
        document = session.get(models.KnowledgeDocument, document_id)
        assert document is not None
        assert document.status == "queued"
        assert document.index_generation == 3
        assert document.retriever_type == "qdrant"
        assert document.parsed_object_key is None


def test_global_collection_is_readable_by_users_but_only_admin_can_write(
    client,
    admin_headers,
    ordinary_user_headers,
) -> None:
    created = client.post(
        "/api/admin/knowledge-collections",
        headers=admin_headers,
        json=_collection_payload("全局手册"),
    )
    assert created.status_code == 201
    collection_id = created.json()["id"]
    assert created.json()["scope"] == "global"
    assert created.json()["can_manage"] is True
    assert "visibility" not in created.json()

    visible = client.get(
        f"/api/knowledge-collections/{collection_id}",
        headers=ordinary_user_headers,
    )
    assert visible.status_code == 200
    assert visible.json()["scope"] == "global"
    assert visible.json()["can_manage"] is False

    ordinary_update = client.put(
        f"/api/knowledge-collections/{collection_id}",
        headers=ordinary_user_headers,
        json=_collection_payload("越权改名", is_active=True),
    )
    assert ordinary_update.status_code == 404
    assert ordinary_update.json()["detail"]["code"] == "resource_not_found"
    assert client.delete(
        f"/api/knowledge-collections/{collection_id}",
        headers=ordinary_user_headers,
    ).status_code == 404

    admin_on_private_route = client.put(
        f"/api/knowledge-collections/{collection_id}",
        headers=admin_headers,
        json=_collection_payload("全局手册 2", is_active=True),
    )
    assert admin_on_private_route.status_code == 404
    assert admin_on_private_route.json()["detail"]["code"] == "resource_not_found"

    updated = client.put(
        f"/api/admin/knowledge-collections/{collection_id}",
        headers=admin_headers,
        json=_collection_payload("全局手册 2", is_active=True),
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "全局手册 2"
    assert updated.json()["can_manage"] is True

    ordinary_global_create = client.post(
        "/api/admin/knowledge-collections",
        headers=ordinary_user_headers,
        json=_collection_payload("不允许"),
    )
    assert ordinary_global_create.status_code == 403

    deleted = client.delete(
        f"/api/admin/knowledge-collections/{collection_id}",
        headers=admin_headers,
    )
    assert deleted.status_code == 200
    assert deleted.json() == {"id": collection_id, "status": "deleted"}


def test_collection_lists_enforce_visible_owned_and_global_scopes(
    client,
    admin_headers,
    create_user,
) -> None:
    _alice, alice_headers = create_user("alice")
    _bob, bob_headers = create_user("bob")
    alice_collection = client.post(
        "/api/knowledge-collections",
        headers=alice_headers,
        json=_collection_payload("Alice 资料库"),
    ).json()
    bob_collection = client.post(
        "/api/knowledge-collections",
        headers=bob_headers,
        json=_collection_payload("Bob 资料库"),
    ).json()
    global_collection = client.post(
        "/api/admin/knowledge-collections",
        headers=admin_headers,
        json=_collection_payload("全局资料库"),
    ).json()

    visible = client.get(
        "/api/knowledge-collections?scope=visible",
        headers=alice_headers,
    ).json()["collections"]
    owned = client.get(
        "/api/knowledge-collections?scope=owned",
        headers=alice_headers,
    ).json()["collections"]
    global_items = client.get(
        "/api/knowledge-collections?scope=global",
        headers=alice_headers,
    ).json()["collections"]

    visible_ids = {item["id"] for item in visible}
    assert alice_collection["id"] in visible_ids
    assert global_collection["id"] in visible_ids
    assert bob_collection["id"] not in visible_ids
    assert {item["id"] for item in owned} == {alice_collection["id"]}
    assert global_collection["id"] in {item["id"] for item in global_items}
    assert all(item["scope"] == "global" for item in global_items)
    assert all(item["can_manage"] is False for item in global_items)

    invalid_scope = client.get(
        "/api/knowledge-collections?scope=everything",
        headers=alice_headers,
    )
    assert invalid_scope.status_code == 422


def test_collection_put_is_full_replacement_and_supports_reactivation(
    client,
    ordinary_user_headers,
) -> None:
    created = client.post(
        "/api/knowledge-collections",
        headers=ordinary_user_headers,
        json=_collection_payload("原资料库"),
    ).json()
    collection_id = created["id"]

    missing_name = client.put(
        f"/api/knowledge-collections/{collection_id}",
        headers=ordinary_user_headers,
        json={"config": {}, "is_active": False},
    )
    assert missing_name.status_code == 422

    deactivated = client.put(
        f"/api/knowledge-collections/{collection_id}",
        headers=ordinary_user_headers,
        json=_collection_payload("新资料库", is_active=False),
    )
    assert deactivated.status_code == 200
    assert deactivated.json()["is_active"] is False
    assert deactivated.json()["config"] == DEFAULT_KNOWLEDGE_CONFIG
    assert client.get(
        f"/api/knowledge-collections/{collection_id}",
        headers=ordinary_user_headers,
    ).status_code == 404

    reactivated = client.put(
        f"/api/knowledge-collections/{collection_id}",
        headers=ordinary_user_headers,
        json=_collection_payload("新资料库", is_active=True),
    )
    assert reactivated.status_code == 200
    assert reactivated.json()["is_active"] is True


def test_collection_mutations_reject_whitespace_only_names(
    client,
    ordinary_user_headers,
) -> None:
    created = client.post(
        "/api/knowledge-collections",
        headers=ordinary_user_headers,
        json=_collection_payload("   "),
    )
    assert created.status_code == 422

    collection = client.post(
        "/api/knowledge-collections",
        headers=ordinary_user_headers,
        json=_collection_payload("待更新资料库"),
    ).json()
    updated = client.put(
        f"/api/knowledge-collections/{collection['id']}",
        headers=ordinary_user_headers,
        json=_collection_payload("   ", is_active=True),
    )
    assert updated.status_code == 422


def test_collection_mutations_trim_names_before_persisting(
    client,
    ordinary_user_headers,
) -> None:
    created = client.post(
        "/api/knowledge-collections",
        headers=ordinary_user_headers,
        json=_collection_payload("  初始资料库  "),
    )

    assert created.status_code == 201
    assert created.json()["name"] == "初始资料库"
    collection_id = created.json()["id"]

    updated = client.put(
        f"/api/knowledge-collections/{collection_id}",
        headers=ordinary_user_headers,
        json=_collection_payload("  更新资料库  ", is_active=False),
    )

    assert updated.status_code == 200
    assert updated.json()["name"] == "更新资料库"
    assert updated.json()["config"] == DEFAULT_KNOWLEDGE_CONFIG
    assert updated.json()["is_active"] is False
    with client.app.state.session_factory() as session:
        persisted = session.get(models.Resource, collection_id)
        assert persisted is not None
        assert persisted.name == "更新资料库"
        assert persisted.config_json == DEFAULT_KNOWLEDGE_CONFIG
        assert persisted.is_active is False


def test_collection_duplicate_and_tombstone_names_are_reserved(
    client,
    create_user,
) -> None:
    _alice, alice_headers = create_user("alice")
    _bob, bob_headers = create_user("bob")
    first = client.post(
        "/api/knowledge-collections",
        headers=alice_headers,
        json=_collection_payload("保留名"),
    )
    assert first.status_code == 201

    duplicate = client.post(
        "/api/knowledge-collections",
        headers=alice_headers,
        json=_collection_payload("  保留名  "),
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "resource_name_taken"
    second = client.post(
        "/api/knowledge-collections",
        headers=alice_headers,
        json=_collection_payload("第二个名称"),
    )
    rename_collision = client.put(
        f"/api/knowledge-collections/{second.json()['id']}",
        headers=alice_headers,
        json=_collection_payload("  保留名  ", is_active=False),
    )
    assert rename_collision.status_code == 409
    assert rename_collision.json()["detail"]["code"] == "resource_name_taken"
    unchanged = client.get(
        f"/api/knowledge-collections/{second.json()['id']}",
        headers=alice_headers,
    )
    assert unchanged.status_code == 200
    assert unchanged.json()["name"] == "第二个名称"
    assert unchanged.json()["config"] == DEFAULT_KNOWLEDGE_CONFIG
    assert unchanged.json()["is_active"] is True
    assert client.post(
        "/api/knowledge-collections",
        headers=bob_headers,
        json=_collection_payload("保留名"),
    ).status_code == 201

    deleted = client.delete(
        f"/api/knowledge-collections/{first.json()['id']}",
        headers=alice_headers,
    )
    assert deleted.status_code == 200
    assert deleted.json() == {"id": first.json()["id"], "status": "deleted"}
    assert client.get(
        f"/api/knowledge-collections/{first.json()['id']}",
        headers=alice_headers,
    ).status_code == 404
    assert client.put(
        f"/api/knowledge-collections/{first.json()['id']}",
        headers=alice_headers,
        json=_collection_payload("保留名", is_active=True),
    ).status_code == 404
    assert client.delete(
        f"/api/knowledge-collections/{first.json()['id']}",
        headers=alice_headers,
    ).status_code == 404

    recreated = client.post(
        "/api/knowledge-collections",
        headers=alice_headers,
        json=_collection_payload("保留名"),
    )
    assert recreated.status_code == 409
    assert recreated.json()["detail"]["code"] == "resource_name_taken"


def test_active_agent_blocks_collection_deactivation_and_deletion(
    client,
    create_user,
) -> None:
    alice, alice_headers = create_user("alice")
    collection = client.post(
        "/api/knowledge-collections",
        headers=alice_headers,
        json=_collection_payload("被引用资料库"),
    ).json()
    agent_id = _add_collection_agent_reference(
        client,
        owner_id=alice["id"],
        collection_id=collection["id"],
        name="Alice Agent",
        document_ids=["document-1"],
    )

    deactivated = client.put(
        f"/api/knowledge-collections/{collection['id']}",
        headers=alice_headers,
        json=_collection_payload("被引用资料库", is_active=False),
    )
    assert deactivated.status_code == 409
    assert deactivated.json()["detail"]["code"] == "resource_in_use"
    deleted = client.delete(
        f"/api/knowledge-collections/{collection['id']}",
        headers=alice_headers,
    )
    assert deleted.status_code == 409
    assert deleted.json()["detail"]["code"] == "resource_in_use"

    _set_resource_active(client, agent_id, False)
    assert client.delete(
        f"/api/knowledge-collections/{collection['id']}",
        headers=alice_headers,
    ).status_code == 200


def test_private_agent_of_another_user_blocks_global_collection_removal(
    client,
    admin_headers,
    create_user,
) -> None:
    bob, _bob_headers = create_user("bob")
    collection = client.post(
        "/api/admin/knowledge-collections",
        headers=admin_headers,
        json=_collection_payload("跨用户引用资料库"),
    ).json()
    agent_id = _add_collection_agent_reference(
        client,
        owner_id=bob["id"],
        collection_id=collection["id"],
        name="Bob Private Agent",
    )

    deactivated = client.put(
        f"/api/admin/knowledge-collections/{collection['id']}",
        headers=admin_headers,
        json=_collection_payload("跨用户引用资料库", is_active=False),
    )
    assert deactivated.status_code == 409
    assert deactivated.json()["detail"]["code"] == "resource_in_use"
    deleted = client.delete(
        f"/api/admin/knowledge-collections/{collection['id']}",
        headers=admin_headers,
    )
    assert deleted.status_code == 409
    assert deleted.json()["detail"]["code"] == "resource_in_use"

    _set_resource_active(client, agent_id, False)
    assert client.delete(
        f"/api/admin/knowledge-collections/{collection['id']}",
        headers=admin_headers,
    ).status_code == 200


@pytest.mark.parametrize("scope", ["private", "global"])
def test_active_document_blocks_collection_deactivation_and_deletion(
    client,
    admin_headers,
    create_user,
    scope: str,
) -> None:
    user, user_headers = create_user(f"owner-{scope}")
    if scope == "global":
        headers = admin_headers
        collection = client.post(
            "/api/admin/knowledge-collections",
            headers=headers,
            json=_collection_payload("全局含文档资料库"),
        ).json()
        owner_id = 0
    else:
        headers = user_headers
        collection = client.post(
            "/api/knowledge-collections",
            headers=headers,
            json=_collection_payload("私有含文档资料库"),
        ).json()
        owner_id = user["id"]
    _add_knowledge_document(
        client,
        owner_id=owner_id,
        collection_id=collection["id"],
        is_active=True,
    )
    mutation_base = (
        "/api/admin/knowledge-collections"
        if scope == "global"
        else "/api/knowledge-collections"
    )

    deactivated = client.put(
        f"{mutation_base}/{collection['id']}",
        headers=headers,
        json=_collection_payload(collection["name"], is_active=False),
    )
    deleted = client.delete(
        f"{mutation_base}/{collection['id']}",
        headers=headers,
    )

    assert deactivated.status_code == 409
    assert deactivated.json()["detail"]["code"] == "resource_in_use"
    assert deleted.status_code == 409
    assert deleted.json()["detail"]["code"] == "resource_in_use"


@pytest.mark.parametrize(
    "error_code",
    ["knowledge_cleanup_pending", "knowledge_cleanup_failed"],
)
def test_pending_or_failed_document_cleanup_blocks_collection_delete(
    client,
    ordinary_user_headers,
    error_code: str,
) -> None:
    collection = client.post(
        "/api/knowledge-collections",
        headers=ordinary_user_headers,
        json=_collection_payload(f"清理状态 {error_code}"),
    ).json()
    with client.app.state.session_factory() as session:
        actor = session.scalar(
            select(models.User).where(models.User.username == "alice")
        )
        assert actor is not None
        owner_id = actor.id
    _add_knowledge_document(
        client,
        owner_id=owner_id,
        collection_id=collection["id"],
        is_active=False,
        error_code=error_code,
    )

    response = client.delete(
        f"/api/knowledge-collections/{collection['id']}",
        headers=ordinary_user_headers,
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "resource_in_use"


def test_completed_inactive_document_cleanup_does_not_block_collection_delete(
    client,
    ordinary_user_headers,
) -> None:
    collection = client.post(
        "/api/knowledge-collections",
        headers=ordinary_user_headers,
        json=_collection_payload("已完成清理资料库"),
    ).json()
    with client.app.state.session_factory() as session:
        actor = session.scalar(
            select(models.User).where(models.User.username == "alice")
        )
        assert actor is not None
        owner_id = actor.id
    _add_knowledge_document(
        client,
        owner_id=owner_id,
        collection_id=collection["id"],
        is_active=False,
        error_code=None,
    )

    response = client.delete(
        f"/api/knowledge-collections/{collection['id']}",
        headers=ordinary_user_headers,
    )

    assert response.status_code == 200


def test_admin_cannot_read_or_manage_another_users_private_collection(
    client,
    admin_headers,
    ordinary_user_headers,
) -> None:
    collection = client.post(
        "/api/knowledge-collections",
        headers=ordinary_user_headers,
        json=_collection_payload("私有边界"),
    ).json()
    collection_url = f"/api/knowledge-collections/{collection['id']}"

    assert client.get(collection_url, headers=admin_headers).status_code == 404
    assert client.put(
        collection_url,
        headers=admin_headers,
        json=_collection_payload("管理员越权", is_active=True),
    ).status_code == 404
    assert client.delete(collection_url, headers=admin_headers).status_code == 404


def test_collection_routes_require_authentication(client) -> None:
    assert client.get("/api/knowledge-collections").status_code == 401
    assert client.post(
        "/api/knowledge-collections",
        json=_collection_payload("未登录"),
    ).status_code == 401


@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE"])
@pytest.mark.parametrize("resource_id", ["%20%20%20", "x" * 37])
def test_collection_detail_routes_reject_invalid_resource_ids(
    client,
    ordinary_user_headers,
    method: str,
    resource_id: str,
) -> None:
    response = client.request(
        method,
        f"/api/knowledge-collections/{resource_id}",
        headers=ordinary_user_headers,
        json=_collection_payload("有效名称", is_active=True)
        if method == "PUT"
        else None,
    )

    assert response.status_code == 422
