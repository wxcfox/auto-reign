from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from app.db import models
from app.repositories.artifact_repository import ArtifactRepository
from app.services.artifact_metadata import artifact_processing_status
from app.services.artifact_service import ArtifactService
from app.services.ingestion_service import IngestionService, UploadItem
from app.services.workspace_service import WorkspaceService

USER_ID = 1


def _stack(tmp_path: Path):
    workspace = WorkspaceService(tmp_path / "workspace")
    workspace.initialize()
    return workspace, ArtifactService(workspace), ArtifactRepository()


def _session(client):
    return client.app.state.session_factory()


def _create_user(session) -> models.User:
    user = models.User(username="alice", password_hash="hash")
    session.add(user)
    session.flush()
    assert user.id == USER_ID
    return user


def _docx_bytes(text: str) -> bytes:
    path = Path("document.xml")
    xml = (
        "<?xml version='1.0' encoding='UTF-8'?>"
        "<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'>"
        f"<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>"
    )
    import io

    buffer = io.BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as docx:
        docx.writestr(f"word/{path}", xml)
    return buffer.getvalue()


def test_ingest_markdown_stores_source_and_generates_knowledge(client, tmp_path: Path) -> None:
    workspace, artifacts, repository = _stack(tmp_path)
    service = IngestionService()

    with _session(client) as session:
        _create_user(session)
        result = service.ingest_uploads(
            session,
            USER_ID,
            workspace,
            artifacts,
            repository,
            [
                UploadItem(
                    filename="redis-note.md",
                    media_type="text/markdown",
                    content="# Redis\n\n缓存穿透和缓存击穿。".encode(),
                )
            ],
        )
        session.commit()

    assert len(result.sources) == 1
    assert result.sources[0].duplicate is False
    assert workspace.resolve_path(result.sources[0].relative_path).read_bytes().startswith(b"# Redis")
    knowledge_files = list((workspace.root / "knowledge").glob("*.md"))
    assert len(knowledge_files) == 1
    knowledge = artifacts.read_markdown(f"knowledge/{knowledge_files[0].name}")
    assert knowledge.front_matter.source_refs == [f"source:{result.sources[0].artifact_id}"]
    assert "Redis" in knowledge.body
    with _session(client) as session:
        rows = repository.list(session, user_id=USER_ID)
        assert {row.kind for row in rows} == {"source", "knowledge"}
        source_row = next(row for row in rows if row.kind == "source")
        assert source_row.id == result.sources[0].artifact_id
        assert artifact_processing_status(source_row) == "completed"


def test_ingest_duplicate_content_reuses_existing_source(client, tmp_path: Path) -> None:
    workspace, artifacts, repository = _stack(tmp_path)
    service = IngestionService()
    item = UploadItem(filename="same.txt", media_type="text/plain", content=b"same content")

    with _session(client) as session:
        _create_user(session)
        first = service.ingest_uploads(session, USER_ID, workspace, artifacts, repository, [item])
        second = service.ingest_uploads(session, USER_ID, workspace, artifacts, repository, [item])
        session.commit()

    assert second.sources[0].duplicate is True
    assert second.sources[0].artifact_id == first.sources[0].artifact_id
    assert len(list((workspace.root / "sources" / "documents").glob("same*"))) == 0
    assert len(list((workspace.root / "sources" / "documents").glob("*same.txt"))) == 1


def test_ingest_same_knowledge_slug_merges_without_overwriting(
    client,
    tmp_path: Path,
) -> None:
    workspace, artifacts, repository = _stack(tmp_path)
    service = IngestionService()

    with _session(client) as session:
        _create_user(session)
        first = service.ingest_uploads(
            session,
            USER_ID,
            workspace,
            artifacts,
            repository,
            [
                UploadItem(
                    filename="redis-note.md",
                    media_type="text/markdown",
                    content="# Redis\n\n第一次记录缓存击穿。".encode(),
                )
            ],
        )
        second = service.ingest_uploads(
            session,
            USER_ID,
            workspace,
            artifacts,
            repository,
            [
                UploadItem(
                    filename="redis-note.md",
                    media_type="text/markdown",
                    content="# Redis\n\n第二次记录布隆过滤器。".encode(),
                )
            ],
        )
        session.commit()

    knowledge_files = list((workspace.root / "knowledge").glob("redis-note.md"))
    assert len(knowledge_files) == 1
    knowledge = artifacts.read_markdown("knowledge/redis-note.md")
    assert "第一次记录缓存击穿" in knowledge.body
    assert "第二次记录布隆过滤器" in knowledge.body
    assert knowledge.front_matter.source_refs == [
        f"source:{first.sources[0].artifact_id}",
        f"source:{second.sources[0].artifact_id}",
    ]


def test_ingest_docx_writes_extracted_artifact(client, tmp_path: Path) -> None:
    workspace, artifacts, repository = _stack(tmp_path)
    service = IngestionService()

    with _session(client) as session:
        _create_user(session)
        result = service.ingest_uploads(
            session,
            USER_ID,
            workspace,
            artifacts,
            repository,
            [
                UploadItem(
                    filename="resume.docx",
                    media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    content=_docx_bytes("负责 FastAPI 面试系统"),
                )
            ],
        )
        session.commit()

    extracted_path = f"sources/extracted/{result.sources[0].artifact_id}.md"
    extracted = artifacts.read_markdown(extracted_path)
    assert extracted.front_matter.kind == "extracted"
    assert extracted.front_matter.source_refs == [f"source:{result.sources[0].artifact_id}"]
    assert "FastAPI" in extracted.body
    with _session(client) as session:
        assert {row.kind for row in repository.list(session, user_id=USER_ID)} >= {
            "source",
            "extracted",
        }


def test_ingest_rejects_oversized_file(client, tmp_path: Path) -> None:
    workspace, artifacts, repository = _stack(tmp_path)
    service = IngestionService(max_upload_bytes=4)

    with _session(client) as session, pytest.raises(ValueError, match="20 MiB"):
        _create_user(session)
        service.ingest_uploads(
            session,
            USER_ID,
            workspace,
            artifacts,
            repository,
            [UploadItem(filename="big.txt", media_type="text/plain", content=b"12345")],
        )


def test_ingest_routes_resume_and_job_description_to_profile_files(client, tmp_path: Path) -> None:
    workspace, artifacts, repository = _stack(tmp_path)
    service = IngestionService()

    with _session(client) as session:
        _create_user(session)
        service.ingest_uploads(
            session,
            USER_ID,
            workspace,
            artifacts,
            repository,
            [
                UploadItem(filename="resume.md", media_type="text/markdown", content=b"# Resume\nPython"),
                UploadItem(filename="jd.md", media_type="text/markdown", content="岗位要求：后端工程师".encode()),
            ],
        )
        session.commit()

    candidate = artifacts.read_markdown("profile/candidate.md")
    target = artifacts.read_markdown("profile/target.md")
    assert "候选人画像" in candidate.body
    assert "目标岗位" in target.body
