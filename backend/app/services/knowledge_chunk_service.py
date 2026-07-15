from __future__ import annotations

from dataclasses import dataclass

from app.core.limits import (
    DEFAULT_KNOWLEDGE_CHUNK_OVERLAP,
    DEFAULT_KNOWLEDGE_CHUNK_SIZE,
)
from app.schemas.knowledge_collections import KnowledgeCollectionConfig


@dataclass(frozen=True)
class KnowledgeChunk:
    content: str
    metadata: dict[str, object]


class KnowledgeChunkService:
    _SEPARATORS = ("\n\n", "\n", "。", ".", " ")

    def __init__(
        self,
        *,
        chunk_size: int = DEFAULT_KNOWLEDGE_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_KNOWLEDGE_CHUNK_OVERLAP,
    ) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if chunk_overlap < 0 or chunk_overlap * 2 > chunk_size:
            raise ValueError("chunk_overlap must not exceed half of chunk_size")
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    @classmethod
    def from_config(
        cls,
        config: KnowledgeCollectionConfig,
    ) -> KnowledgeChunkService:
        return cls(
            chunk_size=config.chunk_size,
            chunk_overlap=config.chunk_overlap,
        )

    def split(self, **source: object) -> list[KnowledgeChunk]:
        text = source.pop("text", None)
        if not isinstance(text, str):
            raise ValueError("knowledge source text must be a string")
        if not text.strip():
            return []
        if "generation" in source:
            if "index_generation" in source:
                raise ValueError("knowledge source generation is ambiguous")
            source["index_generation"] = source.pop("generation")

        chunks: list[KnowledgeChunk] = []
        for start, end in self._source_ranges(text):
            content = text[start:end]
            if not content.strip():
                continue
            chunks.append(
                KnowledgeChunk(
                    content=content,
                    metadata={
                        **source,
                        "chunk_index": len(chunks),
                        "source_start": start,
                        "source_end": end,
                    },
                )
            )
        return chunks

    def _source_ranges(self, text: str) -> list[tuple[int, int]]:
        """Return ordered, gap-free source ranges with exact configured overlap."""
        ranges: list[tuple[int, int]] = []
        start = 0
        source_length = len(text)
        minimum_boundary = max(self.chunk_overlap + 1, self.chunk_size // 2)

        while start < source_length:
            maximum_end = min(start + self.chunk_size, source_length)
            end = maximum_end
            if maximum_end < source_length:
                for separator in self._SEPARATORS:
                    position = text.rfind(separator, start, maximum_end)
                    if position < 0:
                        continue
                    candidate_end = position + len(separator)
                    if candidate_end - start >= minimum_boundary:
                        end = candidate_end
                        break

            ranges.append((start, end))
            if end == source_length:
                break
            start = end - self.chunk_overlap

        return ranges
