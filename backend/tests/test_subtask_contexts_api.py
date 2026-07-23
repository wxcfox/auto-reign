from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import Mock

from fastapi import FastAPI, Header
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.api.dependencies import get_current_user
from app.api.subtask_contexts import router
from app.db import models
from app.services.extraction_service import ExtractionService
from app.services.subtask_context_service import SubtaskContextService
from app.services.upload_validation_service import UploadValidationService, default_upload_policy


@pytest.fixture
def api(tmp_path) -> Iterator[tuple[TestClient, sessionmaker[Session]]]:
    engine = create_engine(f"sqlite:///{tmp_path / 'subtask-context-api.db'}")
    models.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        session.add_all(
            [
                models.User(
                    id=user_id,
                    username=f"user-{user_id}",
                    password_hash="unused",
                    display_name=f"User {user_id}",
                    role="user",
                    is_active=True,
                    token_version=1,
                    settings_json={},
                )
                for user_id in (1, 2)
            ]
        )

    app = FastAPI()
    app.include_router(router)
    app.state.session_factory = factory
    app.state.subtask_context_service = SubtaskContextService(
        session_factory=factory,
        extraction=ExtractionService(),
    )
    app.state.upload_validation_service = UploadValidationService(chunk_bytes=4)
    app.state.attachment_upload_policy = default_upload_policy(max_bytes=16)

    def current_user(x_test_user: int = Header(default=1)) -> models.User:
        with factory() as session:
            user = session.get(models.User, x_test_user)
            assert user is not None
            session.expunge(user)
            return user

    app.dependency_overrides[get_current_user] = current_user
    try:
        with TestClient(app) as client:
            yield client, factory
    finally:
        engine.dispose()


def test_upload_list_content_and_delete_are_safe(
    api: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _factory = api
    uploaded = client.post(
        "/api/subtask-contexts/attachments",
        files={"file": ("客户 notes.txt", b"hello", "text/plain")},
    )
    assert uploaded.status_code == 201
    brief = uploaded.json()
    assert brief["status"] == "ready"
    assert brief["text_length"] == 5
    assert not ({"binary_data", "image_base64", "extracted_text", "error_message"} & brief.keys())

    listed = client.get("/api/subtask-contexts/drafts")
    assert listed.status_code == 200
    assert listed.json()["items"] == [brief]
    body = str(listed.json())
    assert "hello" not in body

    content = client.get(f"/api/subtask-contexts/{brief['id']}/content")
    assert content.status_code == 200
    assert content.content == b"hello"
    assert content.headers["cache-control"] == "private, no-store"
    assert content.headers["x-content-type-options"] == "nosniff"
    disposition = content.headers["content-disposition"]
    assert disposition.startswith("inline;")
    assert 'filename="notes.txt"' in disposition
    assert "filename*=UTF-8''" in disposition

    download = client.get(
        f"/api/subtask-contexts/{brief['id']}/content?disposition=attachment"
    )
    assert download.status_code == 200
    assert download.content == b"hello"
    assert download.headers["content-disposition"].startswith("attachment;")
    assert download.headers["cache-control"] == "private, no-store"
    assert download.headers["x-content-type-options"] == "nosniff"

    cross_owner = client.get(
        f"/api/subtask-contexts/{brief['id']}/content",
        headers={"X-Test-User": "2"},
    )
    assert cross_owner.status_code == 404
    assert cross_owner.json()["detail"]["code"] == "context_not_found"

    cross_owner_delete = client.delete(
        f"/api/subtask-contexts/{brief['id']}",
        headers={"X-Test-User": "2"},
    )
    assert cross_owner_delete.status_code == 404
    assert cross_owner_delete.json()["detail"]["code"] == "context_not_found"
    missing_delete = client.delete("/api/subtask-contexts/999999")
    assert missing_delete.status_code == 404
    assert missing_delete.json()["detail"]["code"] == "context_not_found"

    deleted = client.delete(f"/api/subtask-contexts/{brief['id']}")
    assert deleted.status_code == 204
    assert client.get("/api/subtask-contexts/drafts").json()["items"] == []


def test_content_disposition_encodes_corrupt_mysql_context_name(
    api: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, factory = api
    uploaded = client.post(
        "/api/subtask-contexts/attachments",
        files={"file": ("safe.txt", b"hello", "text/plain")},
    ).json()
    with factory.begin() as session:
        row = session.get(models.SubtaskContext, uploaded["id"])
        assert row is not None
        row.name = 'evil"\r\nX-Test: injected.txt'

    response = client.get(
        f"/api/subtask-contexts/{uploaded['id']}/content?disposition=attachment"
    )

    assert response.status_code == 200
    assert response.content == b"hello"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert "\r" not in disposition
    assert "\n" not in disposition
    assert "X-Test:" not in disposition
    assert (
        "filename*=UTF-8''evil%22%0D%0AX-Test%3A%20injected.txt"
        in disposition
    )


def test_invalid_disposition_is_rejected_before_mysql_content_read(
    api: tuple[TestClient, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _factory = api
    uploaded = client.post(
        "/api/subtask-contexts/attachments",
        files={"file": ("safe.txt", b"hello", "text/plain")},
    ).json()
    service = client.app.state.subtask_context_service
    get_content = Mock(wraps=service.get_content)
    monkeypatch.setattr(service, "get_content", get_content)

    response = client.get(
        f"/api/subtask-contexts/{uploaded['id']}/content?disposition=execute"
    )

    assert response.status_code == 422
    get_content.assert_not_called()


def test_upload_validation_limits_and_failed_parse_are_controlled(
    api: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, factory = api
    too_large = client.post(
        "/api/subtask-contexts/attachments",
        files={"file": ("large.txt", b"x" * 17, "text/plain")},
    )
    assert too_large.status_code == 413
    unsupported = client.post(
        "/api/subtask-contexts/attachments",
        files={"file": ("data.exe", b"hello", "application/octet-stream")},
    )
    assert unsupported.status_code == 415

    failed = client.post(
        "/api/subtask-contexts/attachments",
        files={"file": ("invalid.txt", b"\xff", "text/plain")},
    )
    assert failed.status_code == 201
    payload = failed.json()
    assert payload["status"] == "failed"
    assert "Unicode" not in str(payload)
    with factory() as session:
        row = session.get(models.SubtaskContext, payload["id"])
        assert row is not None
        assert row.binary_data == b"\xff"
        assert row.error_message == "extraction_invalid"


def test_bound_context_cannot_be_deleted_by_draft_endpoint(
    api: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, factory = api
    uploaded = client.post(
        "/api/subtask-contexts/attachments",
        files={"file": ("bound.txt", b"bound", "text/plain")},
    ).json()
    with factory.begin() as session:
        row = session.get(models.SubtaskContext, uploaded["id"])
        assert row is not None
        row.subtask_id = 123

    response = client.delete(f"/api/subtask-contexts/{uploaded['id']}")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "context_not_ready"
