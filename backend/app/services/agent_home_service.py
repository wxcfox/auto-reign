from __future__ import annotations

from dataclasses import dataclass

from app.core.limits import DEFAULT_AGENT_HOME_MAX_FILE_BYTES
from app.services.agent_home_paths import (
    agent_home_key,
    agent_home_prefix,
    normalize_home_directory,
    normalize_home_path,
)
from app.storage.object_store import (
    ObjectConflict,
    ObjectMetadata,
    ObjectNotFound,
    ObjectStore,
    ObjectStoreError,
)


class WorkspaceUnavailable(RuntimeError):
    """The authoritative Agent Home object cannot be used safely."""


class WorkspaceFileNotUtf8(RuntimeError):
    """An Agent Home file is not valid UTF-8 text."""


class WorkspaceConflict(RuntimeError):
    """An Agent Home mutation lost an optimistic-concurrency race."""

    def __init__(self, path: str) -> None:
        self.path = path
        super().__init__()


@dataclass(frozen=True)
class AgentHomeFile:
    path: str
    content: str
    etag: str
    size_bytes: int


@dataclass(frozen=True)
class AgentHomeFileItem:
    path: str
    name: str
    is_directory: bool
    size_bytes: int | None
    etag: str | None


class AgentHomeService:
    def __init__(
        self,
        *,
        store: ObjectStore,
        max_file_bytes: int = DEFAULT_AGENT_HOME_MAX_FILE_BYTES,
    ) -> None:
        if (
            not isinstance(max_file_bytes, int)
            or isinstance(max_file_bytes, bool)
            or max_file_bytes <= 0
        ):
            raise ValueError("max_file_bytes must be positive")
        self.store = store
        self.max_file_bytes = max_file_bytes

    def ensure_agents_md(
        self,
        *,
        user_id: int,
        workspace_id: str,
        initial_content: str,
    ) -> AgentHomeFile:
        key = agent_home_key(
            user_id=user_id,
            workspace_id=workspace_id,
            path="AGENTS.md",
        )
        data = self.validate_content(initial_content)
        try:
            metadata = self.store.put(key, data, if_none_match=True)
        except ObjectConflict:
            pass
        except ObjectStoreError:
            raise WorkspaceUnavailable() from None
        else:
            self._validate_metadata(
                metadata,
                expected_key=key,
                expected_size=len(data),
            )

        try:
            return self.read_file(
                user_id=user_id,
                workspace_id=workspace_id,
                path="AGENTS.md",
            )
        except (ObjectNotFound, ObjectStoreError, WorkspaceFileNotUtf8):
            raise WorkspaceUnavailable() from None

    def read_file(
        self,
        *,
        user_id: int,
        workspace_id: str,
        path: str,
    ) -> AgentHomeFile:
        normalized = normalize_home_path(path)
        key = agent_home_key(
            user_id=user_id,
            workspace_id=workspace_id,
            path=normalized,
        )
        try:
            stored = self.store.get(key)
        except ObjectNotFound:
            raise ObjectNotFound() from None
        except ObjectStoreError:
            raise WorkspaceUnavailable() from None
        if not isinstance(stored.data, bytes):
            raise WorkspaceUnavailable()
        self._validate_metadata(
            stored.metadata,
            expected_key=key,
            expected_size=len(stored.data),
        )
        try:
            content = stored.data.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise WorkspaceFileNotUtf8() from None
        return AgentHomeFile(
            path=normalized,
            content=content,
            etag=stored.metadata.etag,
            size_bytes=stored.metadata.size_bytes,
        )

    def list_files(
        self,
        *,
        user_id: int,
        workspace_id: str,
        directory: str,
    ) -> list[AgentHomeFileItem]:
        normalized_directory = normalize_home_directory(directory)
        home_prefix = agent_home_prefix(
            user_id=user_id,
            workspace_id=workspace_id,
        )
        list_prefix = home_prefix
        if normalized_directory:
            list_prefix = f"{home_prefix}{normalized_directory}/"

        try:
            metadata_items = self.store.list(list_prefix)
        except ObjectNotFound:
            raise ObjectNotFound() from None
        except ObjectStoreError:
            raise WorkspaceUnavailable() from None

        directories: set[str] = set()
        files: list[AgentHomeFileItem] = []
        for metadata in metadata_items:
            self._validate_metadata(metadata)
            key = metadata.key
            if not key.startswith(list_prefix):
                raise WorkspaceUnavailable()
            relative_path = key[len(list_prefix) :]
            parts = relative_path.split("/")
            if not relative_path or any(not part for part in parts):
                raise WorkspaceUnavailable()

            direct_name = parts[0]
            direct_path = (
                f"{normalized_directory}/{direct_name}"
                if normalized_directory
                else direct_name
            )
            try:
                direct_path = normalize_home_path(direct_path)
            except ValueError:
                raise WorkspaceUnavailable() from None

            if len(parts) > 1:
                directories.add(direct_path)
                continue
            files.append(
                AgentHomeFileItem(
                    path=direct_path,
                    name=direct_name,
                    is_directory=False,
                    size_bytes=metadata.size_bytes,
                    etag=metadata.etag,
                )
            )

        directory_items = [
            AgentHomeFileItem(
                path=path,
                name=path.rsplit("/", maxsplit=1)[-1],
                is_directory=True,
                size_bytes=None,
                etag=None,
            )
            for path in directories
        ]
        return sorted(
            [*directory_items, *files],
            key=lambda item: (not item.is_directory, item.name, item.path),
        )

    def create_file(
        self,
        *,
        user_id: int,
        workspace_id: str,
        path: str,
        content: str,
    ) -> AgentHomeFile:
        normalized = normalize_home_path(path)
        key = agent_home_key(
            user_id=user_id,
            workspace_id=workspace_id,
            path=normalized,
        )
        data = self.validate_content(content)
        try:
            metadata = self.store.put(key, data, if_none_match=True)
        except ObjectConflict:
            raise WorkspaceConflict(normalized) from None
        except ObjectNotFound:
            raise ObjectNotFound() from None
        except ObjectStoreError:
            raise WorkspaceUnavailable() from None
        self._validate_metadata(
            metadata,
            expected_key=key,
            expected_size=len(data),
        )
        return self.read_file(
            user_id=user_id,
            workspace_id=workspace_id,
            path=normalized,
        )

    def write_file(
        self,
        *,
        user_id: int,
        workspace_id: str,
        path: str,
        content: str,
        expected_etag: str,
    ) -> AgentHomeFile:
        normalized = normalize_home_path(path)
        key = agent_home_key(
            user_id=user_id,
            workspace_id=workspace_id,
            path=normalized,
        )
        data = self.validate_content(content)
        self._validate_expected_etag(expected_etag)
        try:
            metadata = self.store.put(
                key,
                data,
                expected_etag=expected_etag,
            )
        except ObjectConflict:
            raise WorkspaceConflict(normalized) from None
        except ObjectNotFound:
            raise ObjectNotFound() from None
        except ObjectStoreError:
            raise WorkspaceUnavailable() from None
        self._validate_metadata(
            metadata,
            expected_key=key,
            expected_size=len(data),
        )
        return self.read_file(
            user_id=user_id,
            workspace_id=workspace_id,
            path=normalized,
        )

    def delete_file(
        self,
        *,
        user_id: int,
        workspace_id: str,
        path: str,
    ) -> None:
        normalized = normalize_home_path(path)
        if normalized == "AGENTS.md":
            raise ValueError("AGENTS.md cannot be deleted")
        key = agent_home_key(
            user_id=user_id,
            workspace_id=workspace_id,
            path=normalized,
        )
        try:
            self.store.delete(key)
        except ObjectConflict:
            raise WorkspaceConflict(normalized) from None
        except ObjectNotFound:
            raise ObjectNotFound() from None
        except ObjectStoreError:
            raise WorkspaceUnavailable() from None

    def validate_content(self, content: str) -> bytes:
        if not isinstance(content, str):
            raise ValueError("workspace content must be text")
        try:
            data = content.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            raise ValueError("workspace content must be valid UTF-8") from None
        if len(data) > self.max_file_bytes:
            raise ValueError("workspace file exceeds size limit")
        return data

    @staticmethod
    def _validate_expected_etag(etag: str) -> None:
        if not isinstance(etag, str):
            raise ValueError("invalid workspace etag")
        try:
            size_bytes = len(etag.encode("utf-8", errors="strict"))
        except UnicodeEncodeError:
            raise ValueError("invalid workspace etag") from None
        if not 1 <= size_bytes <= 256:
            raise ValueError("invalid workspace etag")

    @staticmethod
    def _validate_store_etag(etag: object) -> None:
        if not isinstance(etag, str):
            raise WorkspaceUnavailable()
        try:
            size_bytes = len(etag.encode("utf-8", errors="strict"))
        except UnicodeEncodeError:
            raise WorkspaceUnavailable() from None
        if not 1 <= size_bytes <= 256:
            raise WorkspaceUnavailable()

    @classmethod
    def _validate_metadata(
        cls,
        metadata: ObjectMetadata,
        *,
        expected_key: str | None = None,
        expected_size: int | None = None,
    ) -> None:
        if not isinstance(metadata, ObjectMetadata):
            raise WorkspaceUnavailable()
        if not isinstance(metadata.key, str) or not metadata.key:
            raise WorkspaceUnavailable()
        if expected_key is not None and metadata.key != expected_key:
            raise WorkspaceUnavailable()
        if (
            not isinstance(metadata.size_bytes, int)
            or isinstance(metadata.size_bytes, bool)
            or metadata.size_bytes < 0
        ):
            raise WorkspaceUnavailable()
        if expected_size is not None and metadata.size_bytes != expected_size:
            raise WorkspaceUnavailable()
        cls._validate_store_etag(metadata.etag)
