from __future__ import annotations

import asyncio
from threading import Event

import anyio
from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import select

from app.db import models
from app.db.session import session_scope
from app.services.extraction_service import ExtractedContent
from app.services.upload_validation_service import UploadPolicy
from app.storage.object_store import (
    ObjectNotFound,
    ObjectStoreUnavailable,
    ObjectTooLarge,
)
from tests.fake_object_store import FakeObjectStore


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def other_user_headers(create_user) -> dict[str, str]:
    _user, headers = create_user(username="bob")
    return headers


def _upload_text(
    client: TestClient,
    headers: dict[str, str],
    *,
    filename: str = "notes.txt",
    content: bytes = b"hello",
) -> dict[str, object]:
    response = client.post(
        "/api/attachments",
        headers=headers,
        files={"file": (filename, content, "text/plain")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _source_key(store: FakeObjectStore, attachment_id: str) -> str:
    prefix = f"/{attachment_id}/"
    return next(
        key
        for key in store.keys()
        if prefix in key and not key.endswith("/parsed.txt")
    )


def _bind_attachment_to_message(session_factory, attachment_id: str) -> None:
    with session_scope(session_factory) as session:
        attachment = session.get(models.Attachment, attachment_id)
        assert attachment is not None
        agent = session.scalar(
            select(models.Resource).where(models.Resource.resource_type == "agent")
        )
        assert agent is not None
        conversation = models.Conversation(
            user_id=attachment.user_id,
            agent_id=agent.id,
        )
        session.add(conversation)
        session.flush()
        message = models.Message(
            user_id=attachment.user_id,
            conversation_id=conversation.id,
            sequence=1,
            role="user",
            status="completed",
            content="attached",
        )
        session.add(message)
        session.flush()
        attachment.message_id = message.id


def test_upload_commits_before_response_and_refresh_recovers_private_draft(
    client: TestClient,
    ordinary_user_headers: dict[str, str],
    session_factory,
) -> None:
    response = client.post(
        "/api/attachments",
        headers=ordinary_user_headers,
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )

    assert response.status_code == 201
    attachment = response.json()
    assert "object_key" not in attachment
    assert "parsed_object_key" not in attachment
    with session_scope(session_factory) as independent:
        assert independent.get(models.Attachment, attachment["id"]) is not None

    drafts = client.get(
        "/api/attachments/drafts",
        headers=ordinary_user_headers,
    )
    assert drafts.status_code == 200
    assert [item["id"] for item in drafts.json()["items"]] == [attachment["id"]]
    assert "object_key" not in drafts.text
    assert "parsed_object_key" not in drafts.text
    assert client.app.state.attachment_service.store is client.app.state.object_store


def test_preview_download_and_delete_use_safe_private_content_headers(
    client: TestClient,
    ordinary_user_headers: dict[str, str],
) -> None:
    attachment = _upload_text(
        client,
        ordinary_user_headers,
        filename="学习 记录.txt",
    )

    preview = client.get(
        f"/api/attachments/{attachment['id']}/content?disposition=inline",
        headers=ordinary_user_headers,
    )
    assert preview.status_code == 200
    assert preview.content == b"hello"
    assert preview.headers["content-disposition"].startswith('inline; filename="')
    assert "filename*=UTF-8''%E5%AD%A6%E4%B9%A0%20%E8%AE%B0%E5%BD%95.txt" in (
        preview.headers["content-disposition"]
    )
    assert preview.headers["x-content-type-options"] == "nosniff"
    assert preview.headers["cache-control"] == "private, no-store"

    download = client.get(
        f"/api/attachments/{attachment['id']}/content?disposition=attachment",
        headers=ordinary_user_headers,
    )
    assert download.status_code == 200
    assert download.headers["content-disposition"].startswith("attachment;")

    deleted = client.delete(
        f"/api/attachments/{attachment['id']}",
        headers=ordinary_user_headers,
    )
    assert deleted.status_code == 204
    assert client.get(
        "/api/attachments/drafts",
        headers=ordinary_user_headers,
    ).json()["items"] == []


def test_content_disposition_encodes_even_corrupt_database_filename(
    client: TestClient,
    ordinary_user_headers: dict[str, str],
    session_factory,
) -> None:
    attachment = _upload_text(client, ordinary_user_headers)
    with session_scope(session_factory) as session:
        row = session.get(models.Attachment, attachment["id"])
        assert row is not None
        row.original_filename = 'evil"\r\nX-Test: injected.txt'

    response = client.get(
        f"/api/attachments/{attachment['id']}/content",
        headers=ordinary_user_headers,
    )

    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    assert "\r" not in disposition
    assert "\n" not in disposition
    assert "X-Test:" not in disposition
    assert "filename*=UTF-8''evil%22%0D%0AX-Test%3A%20injected.txt" in disposition


def test_attachment_api_hides_another_users_drafts_and_content(
    client: TestClient,
    ordinary_user_headers: dict[str, str],
    other_user_headers: dict[str, str],
) -> None:
    attachment = _upload_text(client, ordinary_user_headers, filename="private.txt")

    assert client.get(
        "/api/attachments/drafts",
        headers=other_user_headers,
    ).json()["items"] == []
    hidden = client.get(
        f"/api/attachments/{attachment['id']}/content",
        headers=other_user_headers,
    )
    assert hidden.status_code == 404
    assert hidden.json()["detail"]["code"] == "attachment_not_found"
    rejected_delete = client.delete(
        f"/api/attachments/{attachment['id']}",
        headers=other_user_headers,
    )
    assert rejected_delete.status_code == 409
    assert rejected_delete.json()["detail"]["code"] == "attachment_not_ready"


@pytest.mark.parametrize(
    ("filename", "content_type", "content", "status_code", "code"),
    [
        ("../note.txt", "text/plain", b"x", 400, "upload_filename_invalid"),
        ("empty.txt", "text/plain", b"", 400, "upload_empty"),
        ("archive.zip", "application/zip", b"zip", 415, "upload_type_invalid"),
    ],
)
def test_upload_validation_errors_have_stable_status_and_code(
    client: TestClient,
    ordinary_user_headers: dict[str, str],
    filename: str,
    content_type: str,
    content: bytes,
    status_code: int,
    code: str,
) -> None:
    response = client.post(
        "/api/attachments",
        headers=ordinary_user_headers,
        files={"file": (filename, content, content_type)},
    )

    assert response.status_code == status_code
    assert response.json()["detail"]["code"] == code
    assert "object_key" not in response.text


def test_oversized_upload_is_413_before_service_execution(
    client: TestClient,
    ordinary_user_headers: dict[str, str],
) -> None:
    state = client.app.state
    original_policy = state.attachment_upload_policy
    state.attachment_upload_policy = UploadPolicy(
        max_bytes=3,
        allowed_mime_types=frozenset({"text/plain"}),
        allowed_extensions=frozenset({".txt"}),
    )
    try:
        response = client.post(
            "/api/attachments",
            headers=ordinary_user_headers,
            files={"file": ("large.txt", b"1234", "text/plain")},
        )
    finally:
        state.attachment_upload_policy = original_policy

    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "upload_too_large"


def test_invalid_document_extraction_is_a_stable_400(
    client: TestClient,
    ordinary_user_headers: dict[str, str],
) -> None:
    response = client.post(
        "/api/attachments",
        headers=ordinary_user_headers,
        files={"file": ("broken.pdf", b"not-a-pdf", "application/pdf")},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "extraction_invalid"


@pytest.mark.parametrize(
    ("code", "status_code"),
    [
        ("extraction_empty", 400),
        ("extraction_too_large", 413),
        ("extraction_unsupported", 415),
    ],
)
def test_extraction_errors_are_mapped_without_leaking_internal_details(
    client: TestClient,
    ordinary_user_headers: dict[str, str],
    code: str,
    status_code: int,
) -> None:
    from app.services.extraction_service import ExtractionError

    class FailingExtraction:
        def extract_required(self, *_args, **_kwargs):
            raise ExtractionError(code, "secret parser detail")

    service = client.app.state.attachment_service
    original_extraction = service.extraction
    service.extraction = FailingExtraction()
    try:
        response = client.post(
            "/api/attachments",
            headers=ordinary_user_headers,
            files={"file": ("notes.txt", b"text", "text/plain")},
        )
    finally:
        service.extraction = original_extraction

    assert response.status_code == status_code
    assert response.json()["detail"]["code"] == code
    assert "secret parser detail" not in response.text


def test_upload_maps_uncertain_object_put_to_sanitized_503(
    client: TestClient,
    ordinary_user_headers: dict[str, str],
    fake_object_store: FakeObjectStore,
) -> None:
    fake_object_store.put_then_raise_on_call = len(fake_object_store.put_calls) + 1

    response = client.post(
        "/api/attachments",
        headers=ordinary_user_headers,
        files={"file": ("notes.txt", b"text", "text/plain")},
    )
    fake_object_store.put_then_raise_on_call = None

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "attachment_unavailable"
    assert "uncertain put" not in response.text
    assert fake_object_store.keys() == []


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (ObjectNotFound("secret-key"), "attachment_unavailable"),
        (ObjectStoreUnavailable("secret-endpoint"), "attachment_unavailable"),
        (ObjectTooLarge("secret-key"), "attachment_corrupt"),
    ],
)
def test_preview_maps_store_failures_to_stable_sanitized_errors(
    client: TestClient,
    ordinary_user_headers: dict[str, str],
    fake_object_store: FakeObjectStore,
    failure: Exception,
    code: str,
) -> None:
    attachment = _upload_text(client, ordinary_user_headers)
    fake_object_store.get_error = failure

    response = client.get(
        f"/api/attachments/{attachment['id']}/content",
        headers=ordinary_user_headers,
    )
    fake_object_store.get_error = None

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == code
    assert "secret" not in response.text
    assert "users/" not in response.text


def test_preview_detects_corrupt_content_and_delete_maps_store_outage(
    client: TestClient,
    ordinary_user_headers: dict[str, str],
    fake_object_store: FakeObjectStore,
    session_factory,
) -> None:
    attachment = _upload_text(client, ordinary_user_headers)
    fake_object_store.replace(
        _source_key(fake_object_store, str(attachment["id"])),
        b"tampered",
    )

    corrupt = client.get(
        f"/api/attachments/{attachment['id']}/content",
        headers=ordinary_user_headers,
    )
    assert corrupt.status_code == 503
    assert corrupt.json()["detail"]["code"] == "attachment_corrupt"

    fake_object_store.delete_error = ObjectStoreUnavailable("secret endpoint")
    unavailable = client.delete(
        f"/api/attachments/{attachment['id']}",
        headers=ordinary_user_headers,
    )
    fake_object_store.delete_error = None
    assert unavailable.status_code == 503
    assert unavailable.json()["detail"]["code"] == "attachment_unavailable"
    assert "secret" not in unavailable.text
    with session_scope(session_factory) as session:
        assert session.get(models.Attachment, attachment["id"]) is not None


def test_bound_attachment_remains_readable_but_cannot_be_deleted(
    client: TestClient,
    ordinary_user_headers: dict[str, str],
    session_factory,
) -> None:
    attachment = _upload_text(client, ordinary_user_headers)
    _bind_attachment_to_message(session_factory, str(attachment["id"]))

    preview = client.get(
        f"/api/attachments/{attachment['id']}/content",
        headers=ordinary_user_headers,
    )
    rejected_delete = client.delete(
        f"/api/attachments/{attachment['id']}",
        headers=ordinary_user_headers,
    )

    assert preview.status_code == 200
    assert preview.content == b"hello"
    assert rejected_delete.status_code == 409
    assert rejected_delete.json()["detail"]["code"] == "attachment_not_ready"
    assert client.get(
        "/api/attachments/drafts",
        headers=ordinary_user_headers,
    ).json()["items"] == []


def test_unknown_disposition_is_rejected_without_reading_object(
    client: TestClient,
    ordinary_user_headers: dict[str, str],
    fake_object_store: FakeObjectStore,
) -> None:
    attachment = _upload_text(client, ordinary_user_headers)
    get_calls = list(fake_object_store.get_calls)

    response = client.get(
        f"/api/attachments/{attachment['id']}/content?disposition=execute",
        headers=ordinary_user_headers,
    )

    assert response.status_code == 422
    assert fake_object_store.get_calls == get_calls


class BlockingExtraction:
    def __init__(self) -> None:
        self.entered = Event()
        self.release = Event()
        self.pdf_bytes = b"blocking-pdf"

    def extract_required(
        self,
        _filename: str,
        mime_type: str,
        _content: bytes,
    ) -> ExtractedContent:
        self.entered.set()
        if not self.release.wait(timeout=5):
            raise RuntimeError("blocking extraction timed out")
        return ExtractedContent(kind="text", mime_type=mime_type, text="parsed")


@pytest.mark.anyio
async def test_blocking_parser_does_not_block_concurrent_health_request(
    client: TestClient,
    ordinary_user_headers: dict[str, str],
) -> None:
    blocking = BlockingExtraction()
    service = client.app.state.attachment_service
    original_extraction = service.extraction
    service.extraction = blocking
    transport = httpx.ASGITransport(app=client.app)
    try:
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as async_client:
            upload_task = asyncio.create_task(
                async_client.post(
                    "/api/attachments",
                    headers=ordinary_user_headers,
                    files={
                        "file": (
                            "slow.pdf",
                            blocking.pdf_bytes,
                            "application/pdf",
                        )
                    },
                )
            )
            entered = await anyio.to_thread.run_sync(blocking.entered.wait, 2)
            assert entered
            with anyio.fail_after(1):
                health = await async_client.get("/api/health")
            blocking.release.set()
            uploaded = await upload_task
    finally:
        blocking.release.set()
        service.extraction = original_extraction

    assert health.status_code == 200
    assert uploaded.status_code == 201


def test_health_and_openapi_expose_only_safe_attachment_capabilities(
    client: TestClient,
) -> None:
    health = client.get("/api/health").json()
    assert health["storage"]["object_store"] == "local"
    assert "bucket" not in str(health).lower()
    assert "endpoint" not in str(health).lower()

    paths = set(client.get("/openapi.json").json()["paths"])
    assert "/api/attachments" in paths
    assert "/api/attachments/drafts" in paths
    assert "/api/attachments/{attachment_id}/content" in paths
    assert not any("knowledge" in path and "attachment" in path for path in paths)
