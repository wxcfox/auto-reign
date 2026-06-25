from __future__ import annotations


class ContextAssembler:
    def __init__(self, *, max_characters: int = 12000) -> None:
        self.max_characters = max_characters

    def assemble(
        self,
        *,
        direct_context: list[str],
        project_context: list[str],
        retrieved_context: list[str],
    ) -> list[str]:
        selected: list[str] = []
        used = 0
        for item in [*direct_context, *project_context, *retrieved_context]:
            if not item:
                continue
            remaining = self.max_characters - used
            if remaining <= 0:
                break
            clipped = item if len(item) <= remaining else item[:remaining].rstrip()
            if clipped:
                selected.append(clipped)
                used += len(clipped)
        return selected
