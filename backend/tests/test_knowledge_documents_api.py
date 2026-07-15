from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db import models
from app.storage import ObjectMetadata, ObjectStoreUnavailable, StoredObject


def _collection(
    client,
    headers: dict[str, str],
    *,
    name: str = "资料库",
    global_collection: bool = False,
) -> dict[str, object]:
    prefix = "/api/admin" if global_collection else "/api"
    response = client.post(
        f"{prefix}/knowledge-collections",
        headers=headers,
        json={"name": name, "config": {}},
    )
    assert response.status_code == 201
    return response.json()


def _upload(
    client,
    headers: dict[str, str],
    collection_id: str,
    *,
    filename: str = "note.txt",
    content: bytes = b"source",
    mime_type: str = "text/plain",
):
    return client.post(
        f"/api/knowledge-collections/{collection_id}/documents",
        headers=headers,
        files={"file": (filename, content, mime_type)},
    )


def _make_ready(client, document_id: str, parsed: bytes = b"parsed source") -> str:
    with client.app.state.session_factory() as session:
        document = session.get(models.KnowledgeDocument, document_id)
        assert document is not None
        parsed_key = (
            f"users/{document.user_id}/knowledge/{document.collection_id}/"
            f"{document.id}/parsed/{document.index_generation}"
        )
        document.status = "ready"
        document.parsed_object_key = parsed_key
        document.indexed_at = models._now()
        session.commit()
    client.app.state.object_store.put(parsed_key, parsed, if_none_match=True)
    return parsed_key


def test_upload_lists_gets_downloads_and_reindexes_document(
    client,
    ordinary_user_headers,
) -> None:
    collection = _collection(client, ordinary_user_headers)
    uploaded = _upload(
        client,
        ordinary_user_headers,
        collection["id"],
        filename="学习 笔记.md",
        content=b"# Original",
        mime_type="text/markdown",
    )

    assert uploaded.status_code == 201
    body = uploaded.json()
    assert body["collection_id"] == collection["id"]
    assert body["name"] == "学习 笔记.md"
    assert body["status"] == "queued"
    assert body["index_generation"] == 1
    assert "user_id" not in body
    assert "source_object_key" not in body

    listed = client.get(
        f"/api/knowledge-collections/{collection['id']}/documents",
        headers=ordinary_user_headers,
    )
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["documents"]] == [body["id"]]

    detail = client.get(
        f"/api/knowledge-collections/{collection['id']}/documents/{body['id']}",
        headers=ordinary_user_headers,
    )
    assert detail.status_code == 200
    assert detail.json() == body

    download = client.get(
        f"/api/knowledge-collections/{collection['id']}/documents/"
        f"{body['id']}/download",
        headers=ordinary_user_headers,
    )
    assert download.status_code == 200
    assert download.content == b"# Original"
    assert "UTF-8''%E5%AD%A6%E4%B9%A0%20%E7%AC%94%E8%AE%B0.md" in (
        download.headers["content-disposition"]
    )
    assert download.headers["cache-control"] == "private, no-store"

    parsed_key = _make_ready(
        client,
        body["id"],
        "完整解析原文".encode(),
    )
    content = client.get(
        f"/api/knowledge-collections/{collection['id']}/documents/"
        f"{body['id']}/content",
        headers=ordinary_user_headers,
    )
    assert content.status_code == 200
    assert content.json() == {
        "document_id": body["id"],
        "content": "完整解析原文",
    }

    reindexed = client.post(
        f"/api/knowledge-collections/{collection['id']}/documents/"
        f"{body['id']}/reindex",
        headers=ordinary_user_headers,
    )
    assert reindexed.status_code == 200
    assert reindexed.json()["status"] == "queued"
    assert reindexed.json()["index_generation"] == 2
    with client.app.state.session_factory() as session:
        document = session.get(models.KnowledgeDocument, body["id"])
        assert document is not None
        assert document.parsed_object_key is None
    assert parsed_key in client.app.state.object_store.keys()


def test_upload_endpoint_offloads_object_store_and_db_work(
    client,
    ordinary_user_headers,
    monkeypatch,
) -> None:
    import app.api.knowledge as knowledge_api

    collection = _collection(client, ordinary_user_headers)
    original = knowledge_api.run_in_threadpool
    calls: list[str] = []

    async def spy(function, *args, **kwargs):
        calls.append(function.__qualname__)
        return await original(function, *args, **kwargs)

    monkeypatch.setattr(knowledge_api, "run_in_threadpool", spy)

    response = _upload(client, ordinary_user_headers, collection["id"])

    assert response.status_code == 201
    assert calls == ["KnowledgeDocumentService.upload_committed"]


def test_global_collection_upload_requires_admin_but_remains_visible(
    client,
    admin_headers,
    ordinary_user_headers,
) -> None:
    collection = _collection(
        client,
        admin_headers,
        name="全局资料",
        global_collection=True,
    )

    forbidden = _upload(client, ordinary_user_headers, collection["id"])
    assert forbidden.status_code == 404
    assert forbidden.json()["detail"]["code"] == "knowledge_collection_not_found"

    uploaded = _upload(client, admin_headers, collection["id"])
    assert uploaded.status_code == 201
    with client.app.state.session_factory() as session:
        document = session.get(models.KnowledgeDocument, uploaded.json()["id"])
        assert document is not None
        assert document.user_id == 0

    visible = client.get(
        f"/api/knowledge-collections/{collection['id']}/documents",
        headers=ordinary_user_headers,
    )
    assert visible.status_code == 200
    assert [item["id"] for item in visible.json()["documents"]] == [
        uploaded.json()["id"]
    ]


def test_private_documents_are_not_visible_across_users(client, create_user) -> None:
    _alice, alice_headers = create_user("alice")
    _bob, bob_headers = create_user("bob")
    collection = _collection(client, alice_headers)
    document = _upload(client, alice_headers, collection["id"]).json()

    assert client.get(
        f"/api/knowledge-collections/{collection['id']}/documents",
        headers=bob_headers,
    ).status_code == 404
    assert client.get(
        f"/api/knowledge-collections/{collection['id']}/documents/{document['id']}",
        headers=bob_headers,
    ).status_code == 404
    assert _upload(client, bob_headers, collection["id"]).status_code == 404


def test_include_inactive_requires_manager_and_filters_by_default(
    client,
    create_user,
) -> None:
    _alice, alice_headers = create_user("alice")
    _bob, bob_headers = create_user("bob")
    collection = _collection(client, alice_headers)
    document = _upload(client, alice_headers, collection["id"]).json()
    with client.app.state.session_factory() as session:
        row = session.get(models.KnowledgeDocument, document["id"])
        assert row is not None
        row.is_active = False
        row.error_code = None
        session.commit()

    active = client.get(
        f"/api/knowledge-collections/{collection['id']}/documents",
        headers=alice_headers,
    )
    assert active.status_code == 200
    assert active.json()["documents"] == []
    inactive = client.get(
        f"/api/knowledge-collections/{collection['id']}/documents?include_inactive=true",
        headers=alice_headers,
    )
    assert inactive.status_code == 200
    assert [item["id"] for item in inactive.json()["documents"]] == [document["id"]]
    assert client.get(
        f"/api/knowledge-collections/{collection['id']}/documents?include_inactive=true",
        headers=bob_headers,
    ).status_code == 404


@pytest.mark.parametrize(
    "corruption",
    [
        "missing_key",
        "missing",
        "unavailable",
        "invalid_utf8",
        "size_mismatch",
        "oversized",
    ],
)
def test_parsed_content_corruption_is_stable_knowledge_unavailable(
    client,
    ordinary_user_headers,
    corruption: str,
    monkeypatch,
) -> None:
    collection = _collection(client, ordinary_user_headers)
    document = _upload(client, ordinary_user_headers, collection["id"]).json()
    parsed_key = _make_ready(client, document["id"])
    store = client.app.state.object_store

    if corruption == "missing_key":
        with client.app.state.session_factory() as session:
            row = session.get(models.KnowledgeDocument, document["id"])
            assert row is not None
            row.parsed_object_key = None
            session.commit()
    elif corruption == "missing":
        store.delete(parsed_key)
    elif corruption == "unavailable":
        store.get_error = ObjectStoreUnavailable("down")
    elif corruption == "invalid_utf8":
        store.replace(parsed_key, b"\xff\xfe")
    elif corruption == "oversized":
        store.replace(parsed_key, b"x" * 2_000_001)
    else:
        original_get = store.get

        def corrupt_get(key: str) -> StoredObject:
            stored = original_get(key)
            if key != parsed_key:
                return stored
            return StoredObject(
                data=stored.data,
                metadata=ObjectMetadata(
                    key=stored.metadata.key,
                    etag=stored.metadata.etag,
                    size_bytes=stored.metadata.size_bytes + 1,
                ),
            )

        monkeypatch.setattr(store, "get", corrupt_get)

    response = client.get(
        f"/api/knowledge-collections/{collection['id']}/documents/"
        f"{document['id']}/content",
        headers=ordinary_user_headers,
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "knowledge_unavailable"
    assert parsed_key not in response.text


@pytest.mark.parametrize(
    ("filename", "mime_type", "status_code", "code"),
    [
        ("image.png", "image/png", 415, "upload_type_invalid"),
        ("../bad.txt", "text/plain", 400, "upload_filename_invalid"),
        ("empty.txt", "text/plain", 400, "upload_empty"),
    ],
)
def test_upload_rejects_invalid_knowledge_files(
    client,
    ordinary_user_headers,
    filename: str,
    mime_type: str,
    status_code: int,
    code: str,
) -> None:
    collection = _collection(client, ordinary_user_headers)
    response = _upload(
        client,
        ordinary_user_headers,
        collection["id"],
        filename=filename,
        content=b"" if code == "upload_empty" else b"content",
        mime_type=mime_type,
    )

    assert response.status_code == status_code
    assert response.json()["detail"]["code"] == code


def test_document_routes_require_auth_and_validate_nested_ids(
    client,
    ordinary_user_headers,
) -> None:
    collection = _collection(client, ordinary_user_headers)
    assert client.get(
        f"/api/knowledge-collections/{collection['id']}/documents"
    ).status_code == 401
    assert client.get(
        "/api/knowledge-collections/%20%20/documents",
        headers=ordinary_user_headers,
    ).status_code == 422
    assert client.get(
        f"/api/knowledge-collections/{collection['id']}/documents/{'x' * 37}",
        headers=ordinary_user_headers,
    ).status_code == 422


def test_reindex_rejects_document_from_another_collection(
    client,
    ordinary_user_headers,
) -> None:
    first = _collection(client, ordinary_user_headers, name="第一资料库")
    second = _collection(client, ordinary_user_headers, name="第二资料库")
    document = _upload(client, ordinary_user_headers, first["id"]).json()

    response = client.post(
        f"/api/knowledge-collections/{second['id']}/documents/"
        f"{document['id']}/reindex",
        headers=ordinary_user_headers,
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "knowledge_document_not_found"


def test_source_integrity_failure_does_not_expose_object_key(
    client,
    ordinary_user_headers,
) -> None:
    collection = _collection(client, ordinary_user_headers)
    document = _upload(client, ordinary_user_headers, collection["id"]).json()
    with client.app.state.session_factory() as session:
        row = session.get(models.KnowledgeDocument, document["id"])
        assert row is not None
        key = row.source_object_key
        client.app.state.object_store.replace(key, b"tampered")

    response = client.get(
        f"/api/knowledge-collections/{collection['id']}/documents/"
        f"{document['id']}/download",
        headers=ordinary_user_headers,
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "knowledge_unavailable"
    assert key not in response.text


def test_noncanonical_source_pointer_is_rejected_before_object_read(
    client,
    ordinary_user_headers,
) -> None:
    collection = _collection(client, ordinary_user_headers)
    document = _upload(client, ordinary_user_headers, collection["id"]).json()
    store = client.app.state.object_store
    with client.app.state.session_factory() as session:
        row = session.get(models.KnowledgeDocument, document["id"])
        assert row is not None
        polluted_key = f"{row.source_object_key}-polluted"
        row.source_object_key = polluted_key
        session.commit()
    store.put(polluted_key, b"source", if_none_match=True)

    response = client.get(
        f"/api/knowledge-collections/{collection['id']}/documents/"
        f"{document['id']}/download",
        headers=ordinary_user_headers,
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "knowledge_unavailable"
    assert store.get_calls == []


def test_noncanonical_parsed_pointer_is_rejected_before_object_read(
    client,
    ordinary_user_headers,
) -> None:
    collection = _collection(client, ordinary_user_headers)
    document = _upload(client, ordinary_user_headers, collection["id"]).json()
    parsed_key = _make_ready(client, document["id"])
    store = client.app.state.object_store
    polluted_key = f"{parsed_key}-polluted"
    store.put(polluted_key, b"polluted", if_none_match=True)
    with client.app.state.session_factory() as session:
        row = session.get(models.KnowledgeDocument, document["id"])
        assert row is not None
        row.parsed_object_key = polluted_key
        session.commit()

    response = client.get(
        f"/api/knowledge-collections/{collection['id']}/documents/"
        f"{document['id']}/content",
        headers=ordinary_user_headers,
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "knowledge_unavailable"
    assert store.get_calls == []


def test_upload_persists_no_client_controlled_owner_or_key_fields(
    client,
    ordinary_user_headers,
) -> None:
    collection = _collection(client, ordinary_user_headers)
    response = client.post(
        f"/api/knowledge-collections/{collection['id']}/documents",
        headers=ordinary_user_headers,
        data={"user_id": "0", "source_object_key": "users/0/forged"},
        files={"file": ("note.txt", b"source", "text/plain")},
    )
    assert response.status_code == 201
    with client.app.state.session_factory() as session:
        actor = session.scalar(
            select(models.User).where(models.User.username == "alice")
        )
        document = session.get(models.KnowledgeDocument, response.json()["id"])
        assert actor is not None and document is not None
        assert document.user_id == actor.id
        assert document.source_object_key.startswith(
            f"users/{actor.id}/knowledge/{collection['id']}/"
        )
        assert document.source_object_key != "users/0/forged"


def test_delete_isolates_document_then_cleans_all_projections(
    client,
    ordinary_user_headers,
    fake_knowledge_vector_store,
) -> None:
    collection = _collection(client, ordinary_user_headers)
    document = _upload(client, ordinary_user_headers, collection["id"]).json()
    parsed_key = _make_ready(client, document["id"])
    with client.app.state.session_factory() as session:
        row = session.get(models.KnowledgeDocument, document["id"])
        assert row is not None
        source_key = row.source_object_key

    response = client.delete(
        f"/api/knowledge-collections/{collection['id']}/documents/{document['id']}",
        headers=ordinary_user_headers,
    )

    assert response.status_code == 204
    assert source_key not in client.app.state.object_store.keys()
    assert parsed_key not in client.app.state.object_store.keys()
    assert len(fake_knowledge_vector_store.delete_document_calls) == 1
    scope = fake_knowledge_vector_store.delete_document_calls[0]
    assert (scope.collection_id, scope.document_id) == (
        collection["id"],
        document["id"],
    )
    assert client.get(
        f"/api/knowledge-collections/{collection['id']}/documents",
        headers=ordinary_user_headers,
    ).json()["documents"] == []
    with client.app.state.session_factory() as session:
        row = session.get(models.KnowledgeDocument, document["id"])
        assert row is not None
        assert row.is_active is False
        assert row.error_code is None


def test_delete_derives_canonical_source_key_and_ignores_polluted_pointer(
    client,
    ordinary_user_headers,
) -> None:
    collection = _collection(client, ordinary_user_headers)
    document = _upload(client, ordinary_user_headers, collection["id"]).json()
    store = client.app.state.object_store
    with client.app.state.session_factory() as session:
        row = session.get(models.KnowledgeDocument, document["id"])
        assert row is not None
        canonical_key = (
            f"users/{row.user_id}/knowledge/{row.collection_id}/{row.id}/source"
        )
        polluted_key = f"{canonical_key}-polluted"
        row.source_object_key = polluted_key
        session.commit()
    store.put(polluted_key, b"do-not-delete", if_none_match=True)

    response = client.delete(
        f"/api/knowledge-collections/{collection['id']}/documents/{document['id']}",
        headers=ordinary_user_headers,
    )

    assert response.status_code == 204
    assert canonical_key not in store.keys()
    assert polluted_key in store.keys()
    assert polluted_key not in store.delete_calls


def test_delete_cleanup_failure_is_discoverable_and_explicitly_retryable(
    client,
    ordinary_user_headers,
    fake_knowledge_vector_store,
) -> None:
    collection = _collection(client, ordinary_user_headers)
    document = _upload(client, ordinary_user_headers, collection["id"]).json()
    with client.app.state.session_factory() as session:
        row = session.get(models.KnowledgeDocument, document["id"])
        assert row is not None
        source_key = row.source_object_key
    fake_knowledge_vector_store.fail("delete_document")

    first = client.delete(
        f"/api/knowledge-collections/{collection['id']}/documents/{document['id']}",
        headers=ordinary_user_headers,
    )

    assert first.status_code == 202
    assert first.json() == {
        "document_id": document["id"],
        "status": "cleanup_pending",
    }
    assert source_key not in client.app.state.object_store.keys()
    active = client.get(
        f"/api/knowledge-collections/{collection['id']}/documents",
        headers=ordinary_user_headers,
    )
    assert active.json()["documents"] == []
    manager = client.get(
        f"/api/knowledge-collections/{collection['id']}/documents"
        "?include_inactive=true",
        headers=ordinary_user_headers,
    )
    assert manager.status_code == 200
    assert manager.json()["documents"][0]["error_code"] == (
        "knowledge_cleanup_failed"
    )

    fake_knowledge_vector_store.recover()
    retry = client.delete(
        f"/api/knowledge-collections/{collection['id']}/documents/{document['id']}",
        headers=ordinary_user_headers,
    )
    assert retry.status_code == 204
    manager = client.get(
        f"/api/knowledge-collections/{collection['id']}/documents"
        "?include_inactive=true",
        headers=ordinary_user_headers,
    )
    assert manager.json()["documents"][0]["error_code"] is None


def test_delete_object_failure_does_not_skip_vector_cleanup(
    client,
    ordinary_user_headers,
    fake_knowledge_vector_store,
) -> None:
    collection = _collection(client, ordinary_user_headers)
    document = _upload(client, ordinary_user_headers, collection["id"]).json()
    client.app.state.object_store.delete_error = ObjectStoreUnavailable("down")

    response = client.delete(
        f"/api/knowledge-collections/{collection['id']}/documents/{document['id']}",
        headers=ordinary_user_headers,
    )

    assert response.status_code == 202
    assert len(fake_knowledge_vector_store.delete_document_calls) == 1


def test_non_manager_cannot_request_inactive_global_documents(
    client,
    admin_headers,
    ordinary_user_headers,
) -> None:
    collection = _collection(
        client,
        admin_headers,
        name="全局资料",
        global_collection=True,
    )
    document = _upload(client, admin_headers, collection["id"]).json()
    with client.app.state.session_factory() as session:
        row = session.get(models.KnowledgeDocument, document["id"])
        assert row is not None
        row.is_active = False
        row.error_code = "knowledge_cleanup_failed"
        session.commit()

    response = client.get(
        f"/api/knowledge-collections/{collection['id']}/documents"
        "?include_inactive=true",
        headers=ordinary_user_headers,
    )

    assert response.status_code == 404
