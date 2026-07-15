from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import hashlib
from threading import RLock


class DocumentOperationCoordinator:
    """Single-process, bounded lock coordinator for Knowledge Document mutations."""

    def __init__(self, *, shard_count: int = 256) -> None:
        if shard_count <= 0:
            raise ValueError("shard_count must be positive")
        self._locks = tuple(RLock() for _ in range(shard_count))

    @contextmanager
    def hold(self, document_id: str) -> Iterator[None]:
        if not isinstance(document_id, str) or not document_id:
            raise ValueError("document_id must not be empty")
        digest = hashlib.sha256(document_id.encode("utf-8")).digest()
        index = int.from_bytes(digest[:8], "big") % len(self._locks)
        with self._locks[index]:
            yield
