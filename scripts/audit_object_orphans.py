from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import sys
from typing import Literal, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db import models
from app.db.session import create_engine_for_settings, make_session_factory
from app.services.agent_home_paths import agent_home_prefix
from app.storage.factory import build_object_store
from app.storage.object_store import validate_object_key


BackendName = Literal["local", "s3"]
_CLEANUP_ERROR_CODES = frozenset(
    {"knowledge_cleanup_pending", "knowledge_cleanup_failed"}
)


class ListedObject(Protocol):
    @property
    def key(self) -> str: ...


class ObjectLister(Protocol):
    def list(self, prefix: str) -> list[ListedObject]: ...


class EmptyObjectLister:
    """Represent an absent local object root without creating it."""

    def list(self, prefix: str) -> list[ListedObject]:
        validate_object_key(prefix, allow_prefix=True)
        return []


class RemoteAuditRefused(RuntimeError):
    pass


@dataclass(frozen=True)
class ObjectReferences:
    exact_keys: frozenset[str]
    protected_prefixes: tuple[str, ...]
    cleanup_pending_exact_keys: frozenset[str]
    cleanup_pending_prefixes: tuple[str, ...]


@dataclass(frozen=True)
class OrphanAuditReport:
    audited_at: datetime
    backend: BackendName
    referenced_count: int
    stored_count: int
    candidate_orphan_keys: tuple[str, ...]
    missing_referenced_keys: tuple[str, ...]
    cleanup_pending_keys: tuple[str, ...]


def utc_now() -> datetime:
    return datetime.now(UTC)


def _add_key(target: set[str], value: object) -> None:
    if isinstance(value, str) and value:
        target.add(value)


def _knowledge_document_prefix(
    *,
    user_id: int,
    collection_id: str,
    document_id: str,
) -> str | None:
    candidate = f"users/{user_id}/knowledge/{collection_id}/{document_id}/"
    try:
        normalized = validate_object_key(candidate, allow_prefix=True)
    except ValueError:
        return None
    return f"{normalized}/"


def load_object_references(
    session_factory: sessionmaker[Session],
) -> ObjectReferences:
    exact_keys: set[str] = set()
    protected_prefixes: set[str] = set()
    cleanup_pending_exact_keys: set[str] = set()
    cleanup_pending_prefixes: set[str] = set()

    with session_factory() as session:
        attachment_rows = session.execute(
            select(
                models.Attachment.object_key,
                models.Attachment.parsed_object_key,
            )
        ).all()
        document_rows = session.execute(
            select(
                models.KnowledgeDocument.id,
                models.KnowledgeDocument.user_id,
                models.KnowledgeDocument.collection_id,
                models.KnowledgeDocument.source_object_key,
                models.KnowledgeDocument.parsed_object_key,
                models.KnowledgeDocument.error_code,
                models.KnowledgeDocument.is_active,
            )
        ).all()
        user_ids = tuple(
            user_id
            for user_id in session.scalars(select(models.User.id))
            if isinstance(user_id, int) and user_id > 0
        )
        workspace_rows = session.execute(
            select(models.Resource.id, models.Resource.user_id).where(
                models.Resource.resource_type == "workspace",
                models.Resource.deleted_at.is_(None),
            )
        ).all()

    for object_key, parsed_object_key in attachment_rows:
        _add_key(exact_keys, object_key)
        _add_key(exact_keys, parsed_object_key)

    for row in document_rows:
        if row.is_active:
            _add_key(exact_keys, row.source_object_key)
            _add_key(exact_keys, row.parsed_object_key)
            continue
        if row.error_code not in _CLEANUP_ERROR_CODES:
            continue
        _add_key(cleanup_pending_exact_keys, row.source_object_key)
        _add_key(cleanup_pending_exact_keys, row.parsed_object_key)
        prefix = _knowledge_document_prefix(
            user_id=row.user_id,
            collection_id=row.collection_id,
            document_id=row.id,
        )
        if prefix is not None:
            cleanup_pending_prefixes.add(prefix)

    for workspace_id, owner_id in workspace_rows:
        if owner_id == 0:
            workspace_user_ids = user_ids
        elif isinstance(owner_id, int) and owner_id > 0:
            workspace_user_ids = (owner_id,)
        else:
            workspace_user_ids = ()
        for user_id in workspace_user_ids:
            try:
                protected_prefixes.add(
                    agent_home_prefix(
                        user_id=user_id,
                        workspace_id=workspace_id,
                    )
                )
            except ValueError:
                continue

    return ObjectReferences(
        exact_keys=frozenset(exact_keys),
        protected_prefixes=tuple(sorted(protected_prefixes)),
        cleanup_pending_exact_keys=frozenset(cleanup_pending_exact_keys),
        cleanup_pending_prefixes=tuple(sorted(cleanup_pending_prefixes)),
    )


def _matches_prefix(key: str, prefixes: tuple[str, ...]) -> bool:
    return any(key.startswith(prefix) for prefix in prefixes)


def audit_object_orphans(
    *,
    session_factory: sessionmaker[Session],
    object_store: ObjectLister,
    backend: BackendName,
    clock: Callable[[], datetime] = utc_now,
) -> OrphanAuditReport:
    if backend not in {"local", "s3"}:
        raise ValueError("unsupported object store backend")

    references = load_object_references(session_factory)
    stored_keys = {
        item.key
        for item in object_store.list("")
        if isinstance(item.key, str) and item.key
    }
    candidates = sorted(
        key
        for key in stored_keys - references.exact_keys
        if key not in references.cleanup_pending_exact_keys
        and not _matches_prefix(key, references.protected_prefixes)
        and not _matches_prefix(key, references.cleanup_pending_prefixes)
    )
    missing = sorted(references.exact_keys - stored_keys)
    cleanup_pending = sorted(
        key
        for key in stored_keys
        if key in references.cleanup_pending_exact_keys
        or _matches_prefix(key, references.cleanup_pending_prefixes)
    )
    return OrphanAuditReport(
        audited_at=clock(),
        backend=backend,
        referenced_count=len(references.exact_keys),
        stored_count=len(stored_keys),
        candidate_orphan_keys=tuple(candidates),
        missing_referenced_keys=tuple(missing),
        cleanup_pending_keys=tuple(cleanup_pending),
    )


def _report_payload(
    report: OrphanAuditReport,
    *,
    show_keys: bool,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "audited_at": report.audited_at.isoformat(),
        "backend": report.backend,
        "referenced_count": report.referenced_count,
        "stored_count": report.stored_count,
        "candidate_orphan_count": len(report.candidate_orphan_keys),
        "missing_referenced_count": len(report.missing_referenced_keys),
        "cleanup_pending_count": len(report.cleanup_pending_keys),
    }
    if show_keys:
        payload.update(
            {
                "candidate_orphan_keys": report.candidate_orphan_keys,
                "missing_referenced_keys": report.missing_referenced_keys,
                "cleanup_pending_keys": report.cleanup_pending_keys,
            }
        )
    return payload


def render_report(report: OrphanAuditReport, *, show_keys: bool) -> str:
    return json.dumps(
        _report_payload(report, show_keys=show_keys),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _has_findings(report: OrphanAuditReport) -> bool:
    return bool(
        report.candidate_orphan_keys
        or report.missing_referenced_keys
        or report.cleanup_pending_keys
    )


def run_audit(
    *,
    settings: Settings,
    allow_remote_read: bool,
) -> OrphanAuditReport:
    if settings.object_store_backend == "s3" and not allow_remote_read:
        raise RemoteAuditRefused(
            "Remote object audit requires explicit --allow-remote-read."
        )

    engine = create_engine_for_settings(settings)
    try:
        session_factory = make_session_factory(engine)
        local_root = settings.object_store_local_root or settings.data_dir / "objects"
        if settings.object_store_backend == "local" and not local_root.exists():
            object_store: ObjectLister = EmptyObjectLister()
        else:
            object_store = build_object_store(settings)
        return audit_object_orphans(
            session_factory=session_factory,
            object_store=object_store,
            backend=settings.object_store_backend,
        )
    finally:
        engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Report ObjectStore/MySQL reference differences without mutation."
    )
    parser.add_argument(
        "--show-keys",
        action="store_true",
        help="Include logical object keys in the local terminal report.",
    )
    parser.add_argument(
        "--allow-remote-read",
        action="store_true",
        help="Explicitly permit a read-only scan of a configured remote backend.",
    )
    parser.add_argument(
        "--fail-on-findings",
        action="store_true",
        help="Exit with status 1 when the report contains any findings.",
    )
    return parser


def _render_error(code: str) -> str:
    return json.dumps(
        {"error": code},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = Settings()
        report = run_audit(
            settings=settings,
            allow_remote_read=args.allow_remote_read,
        )
    except RemoteAuditRefused:
        print(_render_error("remote_read_requires_explicit_permission"), file=sys.stderr)
        return 2
    except Exception:
        print(_render_error("audit_failed"), file=sys.stderr)
        return 2

    print(render_report(report, show_keys=args.show_keys))
    if args.fail_on_findings and _has_findings(report):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
