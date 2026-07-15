from __future__ import annotations

from datetime import UTC, datetime
import importlib.util
from pathlib import Path
import sys
from uuid import uuid4

import pytest
from sqlalchemy import create_engine

from app.core.config import Settings
from app.db import models
from app.db.session import make_session_factory, session_scope
from tests.fake_object_store import FakeObjectStore


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "audit_object_orphans.py"
FIXED_TIME = datetime(2026, 7, 13, 8, 0, tzinfo=UTC)


@pytest.fixture
def audit_module():
    module_name = f"audit_object_orphans_test_{uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(module_name, None)


@pytest.fixture
def audit_session_factory(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'audit.db'}")
    models.Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    try:
        yield factory
    finally:
        engine.dispose()


def _create_user(factory, *, username: str, active: bool = True) -> int:
    with session_scope(factory) as session:
        user = models.User(
            username=username,
            display_name=username.title(),
            password_hash="not-used",
            is_active=active,
        )
        session.add(user)
        session.flush()
        return user.id


def _create_collection(factory, *, owner_id: int, name: str) -> str:
    with session_scope(factory) as session:
        resource = models.Resource(
            user_id=owner_id,
            resource_type="knowledge_collection",
            name=name,
            config_json={},
        )
        session.add(resource)
        session.flush()
        return resource.id


def _create_document(
    factory,
    *,
    owner_id: int,
    collection_id: str,
    active: bool,
    error_code: str | None = None,
) -> models.KnowledgeDocument:
    document_id = str(uuid4())
    prefix = f"users/{owner_id}/knowledge/{collection_id}/{document_id}/"
    with session_scope(factory) as session:
        document = models.KnowledgeDocument(
            id=document_id,
            user_id=owner_id,
            collection_id=collection_id,
            name="source.txt",
            source_object_key=f"{prefix}source",
            parsed_object_key=f"{prefix}parsed/2",
            mime_type="text/plain",
            size_bytes=6,
            content_hash="hash",
            status="ready" if active else "failed",
            index_generation=2,
            error_code=error_code,
            is_active=active,
        )
        session.add(document)
        session.flush()
        return document


def _create_attachment(factory, *, owner_id: int) -> models.Attachment:
    attachment_id = str(uuid4())
    prefix = f"users/{owner_id}/attachments/{attachment_id}/"
    with session_scope(factory) as session:
        attachment = models.Attachment(
            id=attachment_id,
            user_id=owner_id,
            message_id=None,
            original_filename="notes.txt",
            object_key=f"{prefix}notes.txt",
            parsed_object_key=f"{prefix}parsed.txt",
            mime_type="text/plain",
            size_bytes=5,
            content_hash="hash",
            parsed_size_bytes=5,
            parsed_content_hash="parsed-hash",
        )
        session.add(attachment)
        session.flush()
        return attachment


def _put_all(store: FakeObjectStore, *keys: str) -> None:
    for key in keys:
        store.put(key, b"object")


def test_orphan_audit_reports_exact_references_without_deleting(
    audit_module,
    audit_session_factory,
) -> None:
    owner_id = _create_user(audit_session_factory, username="alice")
    collection_id = _create_collection(
        audit_session_factory,
        owner_id=owner_id,
        name="资料库",
    )
    attachment = _create_attachment(audit_session_factory, owner_id=owner_id)
    document = _create_document(
        audit_session_factory,
        owner_id=owner_id,
        collection_id=collection_id,
        active=True,
    )
    assert attachment.parsed_object_key is not None
    assert document.parsed_object_key is not None
    orphan_key = "users/1/attachments/orphan/source.txt"
    store = FakeObjectStore()
    _put_all(
        store,
        attachment.object_key,
        attachment.parsed_object_key,
        document.source_object_key,
        document.parsed_object_key,
        orphan_key,
    )

    report = audit_module.audit_object_orphans(
        session_factory=audit_session_factory,
        object_store=store,
        backend="local",
        clock=lambda: FIXED_TIME,
    )

    assert report.audited_at == FIXED_TIME
    assert report.backend == "local"
    assert report.referenced_count == 4
    assert report.stored_count == 5
    assert report.candidate_orphan_keys == (orphan_key,)
    assert report.missing_referenced_keys == ()
    assert report.cleanup_pending_keys == ()
    assert store.delete_calls == []


def test_active_reference_missing_from_store_is_reported(
    audit_module,
    audit_session_factory,
) -> None:
    owner_id = _create_user(audit_session_factory, username="alice")
    attachment = _create_attachment(audit_session_factory, owner_id=owner_id)
    store = FakeObjectStore()
    store.put(attachment.object_key, b"source")
    assert attachment.parsed_object_key is not None

    report = audit_module.audit_object_orphans(
        session_factory=audit_session_factory,
        object_store=store,
        backend="local",
        clock=lambda: FIXED_TIME,
    )

    assert report.missing_referenced_keys == (attachment.parsed_object_key,)


def test_successfully_cleaned_inactive_document_is_not_reported_missing(
    audit_module,
    audit_session_factory,
) -> None:
    owner_id = _create_user(audit_session_factory, username="alice")
    collection_id = _create_collection(
        audit_session_factory,
        owner_id=owner_id,
        name="资料库",
    )
    document = _create_document(
        audit_session_factory,
        owner_id=owner_id,
        collection_id=collection_id,
        active=False,
    )

    report = audit_module.audit_object_orphans(
        session_factory=audit_session_factory,
        object_store=FakeObjectStore(),
        backend="local",
        clock=lambda: FIXED_TIME,
    )

    assert document.source_object_key not in report.missing_referenced_keys
    assert report.referenced_count == 0
    assert report.cleanup_pending_keys == ()


@pytest.mark.parametrize(
    "error_code",
    ["knowledge_cleanup_pending", "knowledge_cleanup_failed"],
)
def test_pending_or_failed_cleanup_protects_the_entire_document_prefix(
    audit_module,
    audit_session_factory,
    error_code: str,
) -> None:
    owner_id = _create_user(audit_session_factory, username="alice")
    collection_id = _create_collection(
        audit_session_factory,
        owner_id=owner_id,
        name="资料库",
    )
    document = _create_document(
        audit_session_factory,
        owner_id=owner_id,
        collection_id=collection_id,
        active=False,
        error_code=error_code,
    )
    prefix = f"users/{owner_id}/knowledge/{collection_id}/{document.id}/"
    protected_keys = (
        f"{prefix}source",
        f"{prefix}parsed/1",
        f"{prefix}parsed/2",
        f"{prefix}parsed/not-canonical",
        f"{prefix}unexpected/residue.bin",
    )
    orphan_key = "users/1/knowledge/orphan/source"
    sibling_key = f"{prefix.removesuffix('/')}-sibling/source"
    store = FakeObjectStore()
    _put_all(store, *protected_keys, orphan_key, sibling_key)

    report = audit_module.audit_object_orphans(
        session_factory=audit_session_factory,
        object_store=store,
        backend="local",
        clock=lambda: FIXED_TIME,
    )

    assert report.candidate_orphan_keys == tuple(sorted((orphan_key, sibling_key)))
    assert report.missing_referenced_keys == ()
    assert report.cleanup_pending_keys == tuple(sorted(protected_keys))
    assert store.delete_calls == []


def test_agent_home_prefixes_remain_protected_until_workspace_tombstone(
    audit_module,
    audit_session_factory,
) -> None:
    active_user = _create_user(audit_session_factory, username="alice")
    disabled_user = _create_user(
        audit_session_factory,
        username="bob",
        active=False,
    )
    with session_scope(audit_session_factory) as session:
        private_workspace = models.Resource(
            user_id=disabled_user,
            resource_type="workspace",
            name="Private Home",
            config_json={},
            is_active=False,
        )
        global_workspace = models.Resource(
            user_id=0,
            resource_type="workspace",
            name="Global Home",
            config_json={},
            is_active=False,
        )
        tombstoned_workspace = models.Resource(
            user_id=active_user,
            resource_type="workspace",
            name="Deleted Home",
            config_json={},
            is_active=False,
            deleted_at=FIXED_TIME,
        )
        session.add_all(
            [private_workspace, global_workspace, tombstoned_workspace]
        )
        session.flush()
        private_id = private_workspace.id
        global_id = global_workspace.id
        tombstoned_id = tombstoned_workspace.id

    protected_keys = (
        f"users/{disabled_user}/workspaces/{private_id}/notes.md",
        f"users/{active_user}/workspaces/{global_id}/AGENTS.md",
        f"users/{disabled_user}/workspaces/{global_id}/notes.md",
    )
    tombstoned_key = (
        f"users/{active_user}/workspaces/{tombstoned_id}/old.md"
    )
    store = FakeObjectStore()
    _put_all(store, *protected_keys, tombstoned_key)

    report = audit_module.audit_object_orphans(
        session_factory=audit_session_factory,
        object_store=store,
        backend="local",
        clock=lambda: FIXED_TIME,
    )

    assert report.candidate_orphan_keys == (tombstoned_key,)
    assert all(key not in report.candidate_orphan_keys for key in protected_keys)


def test_default_report_only_outputs_counts_and_show_keys_is_explicit(
    audit_module,
) -> None:
    secret_key = "users/1/attachments/private-customer-name/source.txt"
    report = audit_module.OrphanAuditReport(
        audited_at=FIXED_TIME,
        backend="s3",
        referenced_count=0,
        stored_count=1,
        candidate_orphan_keys=(secret_key,),
        missing_referenced_keys=(),
        cleanup_pending_keys=(),
    )

    default_output = audit_module.render_report(report, show_keys=False)
    explicit_output = audit_module.render_report(report, show_keys=True)

    assert secret_key not in default_output
    assert '"candidate_orphan_count":1' in default_output
    assert secret_key in explicit_output
    for secret in (
        "secret-access-key",
        "private-bucket",
        "oss-cn-hangzhou.aliyuncs.com",
        "mysql-password",
    ):
        assert secret not in default_output


def test_remote_audit_refuses_before_database_client_or_object_store(
    audit_module,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None,
        object_store_backend="s3",
        s3_bucket="private-bucket",
        s3_namespace_app_exclusive=True,
    )

    def unexpected_call(*_args, **_kwargs):
        raise AssertionError("remote refusal happened too late")

    monkeypatch.setattr(audit_module, "create_engine_for_settings", unexpected_call)
    monkeypatch.setattr(audit_module, "build_object_store", unexpected_call)

    with pytest.raises(
        audit_module.RemoteAuditRefused,
        match="--allow-remote-read",
    ):
        audit_module.run_audit(
            settings=settings,
            allow_remote_read=False,
        )


def test_local_audit_does_not_create_an_absent_object_root(
    audit_module,
    audit_session_factory,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    owner_id = _create_user(audit_session_factory, username="alice")
    attachment = _create_attachment(audit_session_factory, owner_id=owner_id)
    assert attachment.parsed_object_key is not None
    object_root = tmp_path / "absent-objects"
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        object_store_local_root=object_root,
    )
    engine = audit_session_factory.kw["bind"]
    monkeypatch.setattr(
        audit_module,
        "create_engine_for_settings",
        lambda _settings: engine,
    )

    def unexpected_store_construction(*_args, **_kwargs):
        raise AssertionError("missing local root must not construct a writable store")

    monkeypatch.setattr(
        audit_module,
        "build_object_store",
        unexpected_store_construction,
    )

    assert not object_root.exists()
    report = audit_module.run_audit(
        settings=settings,
        allow_remote_read=False,
    )

    assert not object_root.exists()
    assert report.stored_count == 0
    assert report.missing_referenced_keys == tuple(
        sorted((attachment.object_key, attachment.parsed_object_key))
    )


def test_local_cli_outputs_one_stable_json_line_and_fail_on_findings(
    audit_module,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    key = "users/1/attachments/orphan/source.txt"
    report = audit_module.OrphanAuditReport(
        audited_at=FIXED_TIME,
        backend="local",
        referenced_count=0,
        stored_count=1,
        candidate_orphan_keys=(key,),
        missing_referenced_keys=(),
        cleanup_pending_keys=(),
    )
    monkeypatch.setattr(audit_module, "Settings", lambda: object())
    monkeypatch.setattr(audit_module, "run_audit", lambda **_kwargs: report)

    exit_code = audit_module.main(["--show-keys", "--fail-on-findings"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert captured.err == ""
    assert captured.out.count("\n") == 1
    assert captured.out.strip() == audit_module.render_report(report, show_keys=True)
    assert key in captured.out


def test_audit_cli_has_no_delete_or_confirmation_mode(audit_module) -> None:
    parser = audit_module.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["--delete-orphans", "--yes"])
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert ".delete(" not in source
    assert "delete_object" not in source
