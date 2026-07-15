from __future__ import annotations

from dataclasses import replace

import pytest

from app.services.agent_home_paths import agent_home_key
from app.services.agent_home_service import (
    AgentHomeService,
    WorkspaceConflict,
    WorkspaceFileNotUtf8,
    WorkspaceUnavailable,
)
from app.storage.object_store import (
    ObjectConflict,
    ObjectMetadata,
    ObjectNotFound,
    ObjectStoreUnavailable,
    StoredObject,
)
from tests.fake_object_store import FakeObjectStore


@pytest.fixture
def store() -> FakeObjectStore:
    return FakeObjectStore()


@pytest.fixture
def service(store: FakeObjectStore) -> AgentHomeService:
    return AgentHomeService(store=store)


def test_agents_md_is_created_once_and_template_updates_do_not_overwrite() -> None:
    store = FakeObjectStore()
    service = AgentHomeService(store=store)

    first = service.ensure_agents_md(
        user_id=7,
        workspace_id="ws-1",
        initial_content="# First",
    )
    key = agent_home_key(
        user_id=7,
        workspace_id="ws-1",
        path="AGENTS.md",
    )
    store.put(key, b"# Evolved", expected_etag=first.etag)
    second = service.ensure_agents_md(
        user_id=7,
        workspace_id="ws-1",
        initial_content="# New template",
    )

    assert second.content == "# Evolved"
    assert second.path == "AGENTS.md"
    assert len(store.put_calls) == 2


def test_global_workspace_instances_are_isolated_by_effective_user() -> None:
    store = FakeObjectStore()
    service = AgentHomeService(store=store)

    service.ensure_agents_md(
        user_id=7,
        workspace_id="global-ws",
        initial_content="# User 7",
    )
    service.ensure_agents_md(
        user_id=8,
        workspace_id="global-ws",
        initial_content="# User 8",
    )

    assert service.read_file(
        user_id=7,
        workspace_id="global-ws",
        path="AGENTS.md",
    ).content == "# User 7"
    assert service.read_file(
        user_id=8,
        workspace_id="global-ws",
        path="AGENTS.md",
    ).content == "# User 8"


def test_agents_md_size_limit_uses_utf8_bytes_before_store_write() -> None:
    store = FakeObjectStore()
    service = AgentHomeService(store=store, max_file_bytes=5)

    with pytest.raises(ValueError, match="size limit"):
        service.ensure_agents_md(
            user_id=7,
            workspace_id="ws-1",
            initial_content="中文",
        )

    assert store.put_calls == []


def test_read_file_reports_non_utf8_without_object_identity() -> None:
    store = FakeObjectStore()
    key = agent_home_key(
        user_id=7,
        workspace_id="ws-1",
        path="AGENTS.md",
    )
    store.put(key, b"\xff")

    with pytest.raises(WorkspaceFileNotUtf8) as captured:
        AgentHomeService(store=store).read_file(
            user_id=7,
            workspace_id="ws-1",
            path="AGENTS.md",
        )

    assert str(captured.value) == ""
    assert key not in str(captured.value)


@pytest.mark.parametrize(
    "store",
    [
        FakeObjectStore(get_error=ObjectStoreUnavailable("secret endpoint")),
        FakeObjectStore(put_then_raise_on_call=1),
    ],
)
def test_agents_md_store_failure_is_stable_and_does_not_leak(store) -> None:
    if store.get_error is not None:
        key = agent_home_key(
            user_id=7,
            workspace_id="ws-1",
            path="AGENTS.md",
        )
        store.put(key, b"# Existing")

    with pytest.raises(WorkspaceUnavailable) as captured:
        AgentHomeService(store=store).ensure_agents_md(
            user_id=7,
            workspace_id="ws-1",
            initial_content="# Initial",
        )

    assert str(captured.value) == ""
    assert "secret" not in str(captured.value)


def test_conflict_followed_by_missing_authoritative_object_is_unavailable() -> None:
    class ConflictThenMissingStore:
        def put(self, *_args, **_kwargs):
            raise ObjectConflict("secret key")

        def get(self, *_args, **_kwargs):
            raise ObjectNotFound("secret key")

    with pytest.raises(WorkspaceUnavailable) as captured:
        AgentHomeService(store=ConflictThenMissingStore()).ensure_agents_md(  # type: ignore[arg-type]
            user_id=7,
            workspace_id="ws-1",
            initial_content="# Initial",
        )

    assert str(captured.value) == ""


def test_list_files_returns_direct_children_without_physical_keys(
    service: AgentHomeService,
) -> None:
    service.ensure_agents_md(
        user_id=7,
        workspace_id="ws",
        initial_content="# Rules",
    )
    service.create_file(
        user_id=7,
        workspace_id="ws",
        path="notes/a.md",
        content="A",
    )
    service.create_file(
        user_id=7,
        workspace_id="ws",
        path="notes/deep/b.md",
        content="B",
    )
    service.create_file(
        user_id=7,
        workspace_id="ws",
        path="notes/z.md",
        content="Z",
    )

    root = service.list_files(user_id=7, workspace_id="ws", directory="")
    notes = service.list_files(
        user_id=7,
        workspace_id="ws",
        directory="notes",
    )

    assert [(item.path, item.is_directory) for item in root] == [
        ("notes", True),
        ("AGENTS.md", False),
    ]
    assert [(item.path, item.is_directory) for item in notes] == [
        ("notes/deep", True),
        ("notes/a.md", False),
        ("notes/z.md", False),
    ]
    assert notes[0].name == "deep"
    assert notes[0].size_bytes is None
    assert notes[0].etag is None
    assert notes[1].name == "a.md"
    assert notes[1].size_bytes == 1
    assert notes[1].etag
    assert all("users/7/workspaces" not in item.path for item in (*root, *notes))


def test_create_file_is_create_only_and_does_not_overwrite(
    service: AgentHomeService,
) -> None:
    created = service.create_file(
        user_id=7,
        workspace_id="ws",
        path="notes/a.md",
        content="A",
    )

    with pytest.raises(WorkspaceConflict) as captured:
        service.create_file(
            user_id=7,
            workspace_id="ws",
            path="notes/a.md",
            content="B",
        )

    assert captured.value.path == "notes/a.md"
    assert "users/7/workspaces" not in str(captured.value)
    assert service.read_file(
        user_id=7,
        workspace_id="ws",
        path="notes/a.md",
    ) == created


def test_write_file_requires_the_current_opaque_etag(
    service: AgentHomeService,
) -> None:
    created = service.create_file(
        user_id=7,
        workspace_id="ws",
        path="notes/a.md",
        content="A",
    )

    with pytest.raises(WorkspaceConflict):
        service.write_file(
            user_id=7,
            workspace_id="ws",
            path="notes/a.md",
            content="B",
            expected_etag="stale",
        )
    updated = service.write_file(
        user_id=7,
        workspace_id="ws",
        path="notes/a.md",
        content="B",
        expected_etag=created.etag,
    )

    assert updated.content == "B"
    assert updated.etag != created.etag


def test_delete_file_protects_root_agents_md_and_deletes_other_files(
    service: AgentHomeService,
    store: FakeObjectStore,
) -> None:
    service.ensure_agents_md(
        user_id=7,
        workspace_id="ws",
        initial_content="# Rules",
    )
    service.create_file(
        user_id=7,
        workspace_id="ws",
        path="notes/a.md",
        content="A",
    )
    before = list(store.delete_calls)

    with pytest.raises(ValueError, match="AGENTS.md"):
        service.delete_file(
            user_id=7,
            workspace_id="ws",
            path="AGENTS.md",
        )
    assert store.delete_calls == before

    service.delete_file(
        user_id=7,
        workspace_id="ws",
        path="notes/a.md",
    )
    with pytest.raises(ObjectNotFound):
        service.read_file(
            user_id=7,
            workspace_id="ws",
            path="notes/a.md",
        )


def test_validate_content_is_side_effect_free_and_uses_utf8_bytes(
    store: FakeObjectStore,
) -> None:
    service = AgentHomeService(store=store, max_file_bytes=5)

    assert service.validate_content("中a") == "中a".encode("utf-8")
    assert store.put_calls == []
    with pytest.raises(ValueError, match="size limit"):
        service.validate_content("中文")
    assert store.put_calls == []


@pytest.mark.parametrize("etag", ["", "e" * 257, "界" * 86, "\ud800"])
def test_invalid_store_etag_is_workspace_unavailable(etag: str) -> None:
    class InvalidPutEtagStore(FakeObjectStore):
        def put(self, *args, **kwargs) -> ObjectMetadata:
            metadata = super().put(*args, **kwargs)
            return replace(metadata, etag=etag)

    with pytest.raises(WorkspaceUnavailable) as captured:
        AgentHomeService(store=InvalidPutEtagStore()).create_file(
            user_id=7,
            workspace_id="ws",
            path="notes/a.md",
            content="A",
        )

    assert str(captured.value) == ""


def test_read_and_list_reject_invalid_store_etags() -> None:
    class InvalidReadEtagStore(FakeObjectStore):
        def get(self, key: str) -> StoredObject:
            stored = super().get(key)
            return replace(
                stored,
                metadata=replace(stored.metadata, etag="e" * 257),
            )

        def list(self, prefix: str) -> list[ObjectMetadata]:
            return [
                replace(metadata, etag="e" * 257)
                for metadata in super().list(prefix)
            ]

    store = InvalidReadEtagStore()
    key = agent_home_key(user_id=7, workspace_id="ws", path="notes/a.md")
    FakeObjectStore.put(store, key, b"A")
    service = AgentHomeService(store=store)

    with pytest.raises(WorkspaceUnavailable):
        service.read_file(
            user_id=7,
            workspace_id="ws",
            path="notes/a.md",
        )
    with pytest.raises(WorkspaceUnavailable):
        service.list_files(user_id=7, workspace_id="ws", directory="notes")


def test_invalid_expected_etag_is_rejected_before_store_write(
    service: AgentHomeService,
    store: FakeObjectStore,
) -> None:
    created = service.create_file(
        user_id=7,
        workspace_id="ws",
        path="notes/a.md",
        content="A",
    )
    before = list(store.put_calls)

    with pytest.raises(ValueError, match="etag"):
        service.write_file(
            user_id=7,
            workspace_id="ws",
            path="notes/a.md",
            content="B",
            expected_etag="界" * 86,
        )

    assert store.put_calls == before
    assert service.read_file(
        user_id=7,
        workspace_id="ws",
        path="notes/a.md",
    ) == created


def test_read_preserves_missing_and_maps_store_unavailable_without_leaks() -> None:
    missing = AgentHomeService(store=FakeObjectStore())
    with pytest.raises(ObjectNotFound):
        missing.read_file(
            user_id=7,
            workspace_id="ws",
            path="notes/missing.md",
        )

    unavailable = AgentHomeService(
        store=FakeObjectStore(
            get_error=ObjectStoreUnavailable("secret endpoint")
        )
    )
    with pytest.raises(WorkspaceUnavailable) as captured:
        unavailable.read_file(
            user_id=7,
            workspace_id="ws",
            path="notes/a.md",
        )
    assert str(captured.value) == ""
    assert "secret" not in str(captured.value)


def test_list_and_delete_map_store_unavailable_without_leaks() -> None:
    class UnavailableListStore(FakeObjectStore):
        def list(self, _prefix: str) -> list[ObjectMetadata]:
            raise ObjectStoreUnavailable("secret bucket")

    with pytest.raises(WorkspaceUnavailable) as listed:
        AgentHomeService(store=UnavailableListStore()).list_files(
            user_id=7,
            workspace_id="ws",
            directory="",
        )
    with pytest.raises(WorkspaceUnavailable) as deleted:
        AgentHomeService(
            store=FakeObjectStore(
                delete_error=ObjectStoreUnavailable("secret endpoint")
            )
        ).delete_file(
            user_id=7,
            workspace_id="ws",
            path="notes/a.md",
        )

    assert str(listed.value) == ""
    assert str(deleted.value) == ""
