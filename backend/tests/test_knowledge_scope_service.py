from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from types import MappingProxyType

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import event

from app.db import models
from app.schemas.knowledge_collections import KnowledgeCollectionConfig
from app.services.agent_service import ResolvedKnowledgeScope
from app.services.knowledge_document_service import KnowledgeDocumentService
from app.services.knowledge_scope_service import KnowledgeScopeService


_SNAPSHOT_TIME = datetime(2026, 7, 14, tzinfo=UTC)


def _config(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "chunk_size": 900,
        "chunk_overlap": 120,
        "top_k": 8,
        "score_threshold": None,
    }
    values.update(overrides)
    return KnowledgeCollectionConfig.model_validate(values).model_dump(mode="json")


def _snapshot(
    collection_id: str,
    *,
    owner_user_id: int,
    document_ids: tuple[str, ...] | None = None,
    config: dict[str, object] | None = None,
) -> ResolvedKnowledgeScope:
    return ResolvedKnowledgeScope(
        collection_id=collection_id,
        owner_user_id=owner_user_id,
        document_ids=document_ids,
        config_json=MappingProxyType(config or _config()),
        updated_at=_SNAPSHOT_TIME,
    )


def _add_collection(
    session,
    *,
    collection_id: str,
    owner_user_id: int,
    config: dict[str, object] | None = None,
) -> models.Resource:
    collection = models.Resource(
        id=collection_id,
        user_id=owner_user_id,
        resource_type="knowledge_collection",
        name=f"Collection {collection_id}",
        config_json=config or _config(),
        is_active=True,
    )
    session.add(collection)
    session.flush()
    return collection


def _add_document(
    session,
    *,
    document_id: str,
    collection_id: str,
    owner_user_id: int,
    status: str = "ready",
    is_active: bool = True,
    generation: int = 1,
    parsed_object_key: str | None = None,
    content_hash: str | None = None,
    filename: str | None = None,
) -> models.KnowledgeDocument:
    canonical_key = KnowledgeDocumentService.parsed_key(
        owner_user_id,
        collection_id,
        document_id,
        generation,
    )
    document = models.KnowledgeDocument(
        id=document_id,
        user_id=owner_user_id,
        collection_id=collection_id,
        name=f"{document_id}.md" if filename is None else filename,
        source_object_key=KnowledgeDocumentService.source_key(
            owner_user_id,
            collection_id,
            document_id,
        ),
        parsed_object_key=(canonical_key if parsed_object_key is None else parsed_object_key),
        mime_type="text/markdown",
        size_bytes=10,
        content_hash=(f"sha256-{document_id}" if content_hash is None else content_hash),
        status=status,
        index_generation=generation,
        is_active=is_active,
    )
    session.add(document)
    session.flush()
    return document


def test_whole_collection_scope_includes_documents_that_became_ready_later(
    client,
    create_user,
    session_factory,
) -> None:
    del client
    user, _headers = create_user("scope-alice")
    owner_id = int(user["id"])
    collection_id = "collection-whole"
    turn_snapshot = _snapshot(
        collection_id,
        owner_user_id=owner_id,
        config=_config(top_k=3),
    )

    with session_factory() as session:
        collection = _add_collection(
            session,
            collection_id=collection_id,
            owner_user_id=owner_id,
            config=_config(top_k=3),
        )
        _add_document(
            session,
            document_id="doc-before",
            collection_id=collection_id,
            owner_user_id=owner_id,
        )
        session.commit()

        # A whole-Collection binding is dynamic at document level. Documents that
        # finish indexing after the turn's Agent config was resolved are included.
        _add_document(
            session,
            document_id="doc-later",
            collection_id=collection_id,
            owner_user_id=owner_id,
        )
        collection.config_json = _config(top_k=30)
        collection.is_active = False
        session.commit()

        resolved = KnowledgeScopeService().resolve(
            session,
            user_id=owner_id,
            knowledge_scopes=(turn_snapshot,),
        )

    assert len(resolved) == 1
    assert {item.document_id for item in resolved[0].documents} == {
        "doc-before",
        "doc-later",
    }
    # Collection state/config is the immutable turn snapshot. The resolver must
    # not re-read resources while refreshing ready Document generations.
    assert resolved[0].config.top_k == 3
    with pytest.raises(ValidationError):
        resolved[0].config.top_k = 30


def test_document_subset_never_expands_to_other_ready_documents(
    client,
    create_user,
    session_factory,
) -> None:
    del client
    user, _headers = create_user("scope-subset")
    owner_id = int(user["id"])
    collection_id = "collection-subset"
    with session_factory() as session:
        _add_collection(
            session,
            collection_id=collection_id,
            owner_user_id=owner_id,
        )
        for document_id in ("doc-selected", "doc-not-selected"):
            _add_document(
                session,
                document_id=document_id,
                collection_id=collection_id,
                owner_user_id=owner_id,
            )
        session.commit()

        resolved = KnowledgeScopeService().resolve(
            session,
            user_id=owner_id,
            knowledge_scopes=(
                _snapshot(
                    collection_id,
                    owner_user_id=owner_id,
                    document_ids=("doc-selected",),
                ),
            ),
        )

    assert [item.document_id for group in resolved for item in group.documents] == ["doc-selected"]


def test_all_scopes_are_refreshed_by_one_paired_condition_query(
    client,
    create_user,
    session_factory,
) -> None:
    del client
    user, _headers = create_user("scope-paired")
    owner_id = int(user["id"])
    global_collection_id = "collection-global"
    private_collection_id = "collection-private"

    with session_factory() as session:
        _add_collection(
            session,
            collection_id=global_collection_id,
            owner_user_id=0,
        )
        _add_collection(
            session,
            collection_id=private_collection_id,
            owner_user_id=owner_id,
        )
        _add_document(
            session,
            document_id="doc-global",
            collection_id=global_collection_id,
            owner_user_id=0,
        )
        _add_document(
            session,
            document_id="doc-private",
            collection_id=private_collection_id,
            owner_user_id=owner_id,
        )

        # SQLite intentionally does not enforce the composite FK in this test
        # fixture. These corrupt cross-owner rows expose a query that independently
        # combines Collection and owner IN lists instead of preserving each pair.
        _add_document(
            session,
            document_id="doc-cross-global",
            collection_id=global_collection_id,
            owner_user_id=owner_id,
        )
        _add_document(
            session,
            document_id="doc-cross-private",
            collection_id=private_collection_id,
            owner_user_id=0,
        )
        session.commit()
        session.expunge_all()

        statements: list[object] = []

        @event.listens_for(session, "do_orm_execute")
        def capture(state) -> None:
            if state.is_select:
                statements.append(state.statement)

        try:
            resolved = KnowledgeScopeService().resolve(
                session,
                user_id=owner_id,
                knowledge_scopes=(
                    _snapshot(
                        global_collection_id,
                        owner_user_id=0,
                        document_ids=("doc-global",),
                    ),
                    _snapshot(
                        private_collection_id,
                        owner_user_id=owner_id,
                    ),
                ),
            )
        finally:
            event.remove(session, "do_orm_execute", capture)

    assert len(statements) == 1
    sql = " ".join(str(statements[0]).split())
    paired_predicate = " OR " in sql or (
        "(knowledge_documents.collection_id, knowledge_documents.user_id)" in sql
    )
    assert paired_predicate, sql
    assert {item.document_id for group in resolved for item in group.documents} == {
        "doc-global",
        "doc-private",
    }


@pytest.mark.parametrize("configured_owner", [-1, 1_000_000, True])
def test_scope_owner_must_be_global_or_the_current_user(
    client,
    create_user,
    session_factory,
    configured_owner,
) -> None:
    del client
    user, _headers = create_user(f"scope-owner-{str(configured_owner).lower()}")
    owner_id = int(user["id"])
    with session_factory() as session:
        with pytest.raises(HTTPException) as error:
            KnowledgeScopeService().resolve(
                session,
                user_id=owner_id,
                knowledge_scopes=(
                    _snapshot(
                        "untrusted-collection",
                        owner_user_id=configured_owner,
                    ),
                ),
            )

    assert error.value.status_code == 503
    assert error.value.detail["code"] == "knowledge_unavailable"


def test_duplicate_collection_or_document_bindings_fail_closed(
    client,
    create_user,
    session_factory,
) -> None:
    del client
    user, _headers = create_user("scope-duplicates")
    owner_id = int(user["id"])
    duplicate_collection = (
        _snapshot("duplicate", owner_user_id=owner_id),
        _snapshot("duplicate", owner_user_id=owner_id),
    )
    duplicate_document = (
        _snapshot(
            "single",
            owner_user_id=owner_id,
            document_ids=("doc-1", "doc-1"),
        ),
    )
    duplicate_document_across_scopes = (
        _snapshot(
            "first",
            owner_user_id=owner_id,
            document_ids=("doc-shared",),
        ),
        _snapshot(
            "second",
            owner_user_id=owner_id,
            document_ids=("doc-shared",),
        ),
    )

    with session_factory() as session:
        for scopes in (
            duplicate_collection,
            duplicate_document,
            duplicate_document_across_scopes,
        ):
            with pytest.raises(HTTPException) as error:
                KnowledgeScopeService().resolve(
                    session,
                    user_id=owner_id,
                    knowledge_scopes=scopes,
                )
            assert error.value.detail["code"] == "knowledge_unavailable"


def test_only_active_ready_documents_are_returned_and_empty_groups_are_skipped(
    client,
    create_user,
    session_factory,
) -> None:
    del client
    user, _headers = create_user("scope-status")
    owner_id = int(user["id"])
    collection_id = "collection-status"
    empty_collection_id = "collection-empty"
    with session_factory() as session:
        _add_collection(
            session,
            collection_id=collection_id,
            owner_user_id=owner_id,
        )
        _add_collection(
            session,
            collection_id=empty_collection_id,
            owner_user_id=owner_id,
        )
        _add_document(
            session,
            document_id="doc-ready",
            collection_id=collection_id,
            owner_user_id=owner_id,
        )
        _add_document(
            session,
            document_id="doc-queued",
            collection_id=collection_id,
            owner_user_id=owner_id,
            status="queued",
        )
        _add_document(
            session,
            document_id="doc-inactive",
            collection_id=collection_id,
            owner_user_id=owner_id,
            is_active=False,
        )
        session.commit()

        resolved = KnowledgeScopeService().resolve(
            session,
            user_id=owner_id,
            knowledge_scopes=(
                _snapshot(collection_id, owner_user_id=owner_id),
                _snapshot(empty_collection_id, owner_user_id=owner_id),
            ),
        )

    assert len(resolved) == 1
    assert [item.document_id for item in resolved[0].documents] == ["doc-ready"]


@pytest.mark.parametrize(
    ("generation", "content_hash", "filename"),
    [
        (0, "sha256-doc", "guide.md"),
        (1, "", "guide.md"),
        (1, "sha256-doc", ""),
    ],
)
def test_invalid_ready_document_projection_fails_closed(
    client,
    create_user,
    session_factory,
    generation,
    content_hash,
    filename,
) -> None:
    del client
    user, _headers = create_user(f"scope-invalid-{generation}-{len(content_hash)}-{len(filename)}")
    owner_id = int(user["id"])
    collection_id = f"invalid-{owner_id}"
    with session_factory() as session:
        _add_collection(
            session,
            collection_id=collection_id,
            owner_user_id=owner_id,
        )
        _add_document(
            session,
            document_id=f"doc-invalid-{owner_id}",
            collection_id=collection_id,
            owner_user_id=owner_id,
            generation=generation,
            content_hash=content_hash,
            filename=filename,
        )
        session.commit()

        with pytest.raises(HTTPException) as error:
            KnowledgeScopeService().resolve(
                session,
                user_id=owner_id,
                knowledge_scopes=(_snapshot(collection_id, owner_user_id=owner_id),),
            )

    assert error.value.detail["code"] == "knowledge_unavailable"


def test_ready_document_requires_its_canonical_generation_key(
    client,
    create_user,
    session_factory,
) -> None:
    del client
    user, _headers = create_user("scope-key")
    owner_id = int(user["id"])
    collection_id = "collection-key"
    with session_factory() as session:
        _add_collection(
            session,
            collection_id=collection_id,
            owner_user_id=owner_id,
        )
        _add_document(
            session,
            document_id="doc-key",
            collection_id=collection_id,
            owner_user_id=owner_id,
            generation=2,
            parsed_object_key=(f"users/{owner_id}/knowledge/{collection_id}/doc-key/parsed/1"),
        )
        session.commit()

        with pytest.raises(HTTPException) as error:
            KnowledgeScopeService().resolve(
                session,
                user_id=owner_id,
                knowledge_scopes=(_snapshot(collection_id, owner_user_id=owner_id),),
            )

    assert error.value.detail["code"] == "knowledge_unavailable"


def test_resolved_scope_is_an_immutable_detached_projection(
    client,
    create_user,
    session_factory,
) -> None:
    del client
    user, _headers = create_user("scope-detached")
    owner_id = int(user["id"])
    collection_id = "collection-detached"
    with session_factory() as session:
        _add_collection(
            session,
            collection_id=collection_id,
            owner_user_id=owner_id,
        )
        _add_document(
            session,
            document_id="doc-detached",
            collection_id=collection_id,
            owner_user_id=owner_id,
            generation=4,
        )
        session.commit()
        resolved = KnowledgeScopeService().resolve(
            session,
            user_id=owner_id,
            knowledge_scopes=(_snapshot(collection_id, owner_user_id=owner_id),),
        )

    document = resolved[0].documents[0]
    assert document.document_id == "doc-detached"
    assert document.index_generation == 4
    assert isinstance(resolved[0].documents, tuple)
    with pytest.raises(FrozenInstanceError):
        document.filename = "mutated.md"  # type: ignore[misc]
