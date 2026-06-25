from __future__ import annotations

from app.services.retrieval_query_planner import RetrievalQueryPlan
from app.services.workspace_vector_store import WorkspaceVectorHit


class RetrievalPostProcessor:
    def process(
        self,
        hits: list[WorkspaceVectorHit],
        plan: RetrievalQueryPlan,
    ) -> list[WorkspaceVectorHit]:
        filtered = [hit for hit in hits if hit.score >= plan.score_threshold]
        filtered.sort(key=lambda hit: hit.score, reverse=True)

        selected = self._select_by_score(filtered, plan)
        selected = self._prefer_kind_diversity(selected, filtered, plan)
        selected.sort(key=lambda hit: hit.score, reverse=True)
        return selected[: plan.final_limit]

    def _select_by_score(
        self,
        hits: list[WorkspaceVectorHit],
        plan: RetrievalQueryPlan,
    ) -> list[WorkspaceVectorHit]:
        per_artifact: dict[str, int] = {}
        selected: list[WorkspaceVectorHit] = []

        for hit in hits:
            artifact_key = self._artifact_key(hit)
            if artifact_key:
                count = per_artifact.get(artifact_key, 0)
                if count >= plan.max_per_artifact:
                    continue
                per_artifact[artifact_key] = count + 1

            selected.append(hit)
            if len(selected) >= plan.final_limit:
                break

        return selected

    def _prefer_kind_diversity(
        self,
        selected: list[WorkspaceVectorHit],
        hits: list[WorkspaceVectorHit],
        plan: RetrievalQueryPlan,
    ) -> list[WorkspaceVectorHit]:
        if len(selected) < plan.final_limit:
            return selected

        selected_by_identity = {id(hit) for hit in selected}
        for kind in plan.artifact_kinds:
            if self._kind_count(selected, kind) > 0:
                continue

            replacement = self._best_replacement(kind, hits, selected_by_identity, selected, plan)
            if replacement is None:
                continue

            replace_index = self._lowest_scoring_duplicate_kind_index(selected)
            if replace_index is None:
                return selected

            selected_by_identity.discard(id(selected[replace_index]))
            selected[replace_index] = replacement
            selected_by_identity.add(id(replacement))

        return selected

    def _best_replacement(
        self,
        kind: str,
        hits: list[WorkspaceVectorHit],
        selected_by_identity: set[int],
        selected: list[WorkspaceVectorHit],
        plan: RetrievalQueryPlan,
    ) -> WorkspaceVectorHit | None:
        per_artifact = self._artifact_counts(selected)
        for hit in hits:
            if id(hit) in selected_by_identity:
                continue
            if hit.metadata.get("artifact_kind") != kind:
                continue
            artifact_key = self._artifact_key(hit)
            if artifact_key and per_artifact.get(artifact_key, 0) >= plan.max_per_artifact:
                continue
            return hit
        return None

    def _lowest_scoring_duplicate_kind_index(
        self,
        selected: list[WorkspaceVectorHit],
    ) -> int | None:
        kind_counts = {
            kind: self._kind_count(selected, kind)
            for kind in {str(hit.metadata.get("artifact_kind", "")) for hit in selected}
        }
        duplicate_indexes = [
            index
            for index, hit in enumerate(selected)
            if kind_counts[str(hit.metadata.get("artifact_kind", ""))] > 1
        ]
        if not duplicate_indexes:
            return None
        return min(duplicate_indexes, key=lambda index: selected[index].score)

    def _artifact_counts(self, hits: list[WorkspaceVectorHit]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for hit in hits:
            artifact_key = self._artifact_key(hit)
            if artifact_key:
                counts[artifact_key] = counts.get(artifact_key, 0) + 1
        return counts

    def _kind_count(self, hits: list[WorkspaceVectorHit], kind: str) -> int:
        return sum(1 for hit in hits if hit.metadata.get("artifact_kind") == kind)

    def _artifact_key(self, hit: WorkspaceVectorHit) -> str:
        return str(hit.metadata.get("artifact_id") or hit.metadata.get("source_id") or "")
