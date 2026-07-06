from fastapi.testclient import TestClient

from app.db import models
from app.db.session import session_scope
from app.services.index_service import IndexService
from app.services.retrieval_query_planner import RetrievalRequest
from app.services.workspace_retrieval_service import WorkspaceRetrievalService
from app.services.workspace_vector_store import WorkspaceVectorHit


def _register(client: TestClient, username: str) -> str:
    response = client.post(
        "/api/auth/register",
        json={"username": username, "password": "correct horse battery staple"},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class RecordingVectorStore:
    def __init__(self) -> None:
        self.collections = {
            "auto_reign_user_1__active",
            "auto_reign_user_1__old",
            "auto_reign_user_2__old",
            "auto_reign_default__old",
        }
        self.deleted_collections: list[str] = []
        self.checked_collections: list[str] = []
        self.searched_collections: list[str] = []

    def list_collections(self) -> list[str]:
        return sorted(self.collections)

    def delete_collection(self, collection_name: str) -> None:
        self.deleted_collections.append(collection_name)
        self.collections.discard(collection_name)

    def has_searchable_content(self, collection_name: str) -> bool:
        self.checked_collections.append(collection_name)
        return True

    def search(self, collection_name: str, query: str, *, limit: int, metadata_filter=None):
        del query, limit, metadata_filter
        self.searched_collections.append(collection_name)
        return [
            WorkspaceVectorHit(
                content="Redis cache stampede",
                score=0.9,
                metadata={
                    "artifact_id": "artifact-1",
                    "artifact_kind": "knowledge",
                    "source_type": "artifact",
                    "relative_path": "knowledge/redis.md",
                },
            )
        ]


def test_index_rebuild_uses_user_collection_prefix(client: TestClient) -> None:
    token = _register(client, "alice")

    response = client.post("/api/workspace/rebuild-index", headers=_auth(token))

    assert response.status_code == 200
    assert response.json()["collection"].startswith("auto_reign_user_1__")

    with session_scope(client.app.state.session_factory) as session:
        user = session.query(models.User).filter_by(username="alice").one()
        assert user.settings_json["active_collection"] == response.json()["collection"]


def test_two_users_get_different_active_collections(client: TestClient) -> None:
    alice = _register(client, "alice")
    bob = _register(client, "bob")

    first = client.post("/api/workspace/rebuild-index", headers=_auth(alice))
    second = client.post("/api/workspace/rebuild-index", headers=_auth(bob))

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["collection"].startswith("auto_reign_user_1__")
    assert second.json()["collection"].startswith("auto_reign_user_2__")
    assert first.json()["collection"] != second.json()["collection"]


def test_sweep_orphan_collections_only_deletes_current_user_prefix(client: TestClient) -> None:
    _register(client, "alice")
    _register(client, "bob")
    store = RecordingVectorStore()

    with session_scope(client.app.state.session_factory) as session:
        alice = session.query(models.User).filter_by(username="alice").one()
        bob = session.query(models.User).filter_by(username="bob").one()
        alice.settings_json = {
            **(alice.settings_json or {}),
            "active_collection": "auto_reign_user_1__active",
        }
        bob.settings_json = {
            **(bob.settings_json or {}),
            "active_collection": "auto_reign_user_2__old",
        }

    IndexService(vector_store=store).sweep_orphan_collections(
        client.app.state.session_factory,
        user_id=1,
        qdrant_prefix="auto_reign_user_1",
    )

    assert store.deleted_collections == ["auto_reign_user_1__old"]
    assert "auto_reign_user_1__active" in store.collections
    assert "auto_reign_user_2__old" in store.collections
    assert "auto_reign_default__old" in store.collections


def test_scoped_retrieval_reads_current_user_active_collection(client: TestClient) -> None:
    _register(client, "alice")
    _register(client, "bob")
    store = RecordingVectorStore()

    with session_scope(client.app.state.session_factory) as session:
        bob = session.query(models.User).filter_by(username="bob").one()
        bob.settings_json = {
            **(bob.settings_json or {}),
            "active_collection": "auto_reign_user_2__old",
        }

    with session_scope(client.app.state.session_factory) as session:
        hits = WorkspaceRetrievalService(vector_store=store, user_id=2).search(
            session,
            RetrievalRequest(
                purpose="question_generation",
                query="Redis",
                mode="comprehensive",
                limit=2,
            ),
        )

    assert hits[0]["source_id"] == "artifact-1"
    assert store.checked_collections == ["auto_reign_user_2__old"]
    assert store.searched_collections == ["auto_reign_user_2__old"]


def test_scoped_retrieval_without_active_collection_returns_empty(client: TestClient) -> None:
    _register(client, "alice")
    store = RecordingVectorStore()

    with session_scope(client.app.state.session_factory) as session:
        alice = session.query(models.User).filter_by(username="alice").one()
        alice.settings_json = {**(alice.settings_json or {}), "active_collection": ""}

    with session_scope(client.app.state.session_factory) as session:
        hits = WorkspaceRetrievalService(vector_store=store, user_id=1).search(
            session,
            RetrievalRequest(
                purpose="question_generation",
                query="Redis",
                mode="comprehensive",
                limit=2,
            ),
        )

    assert hits == []
    assert store.checked_collections == []
    assert store.searched_collections == []
