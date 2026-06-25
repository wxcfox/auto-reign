from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


RetrievalPurpose = Literal[
    "question_generation",
    "answer_feedback",
    "follow_up_feedback",
    "generic",
]


@dataclass(frozen=True)
class RetrievalRequest:
    purpose: RetrievalPurpose
    query: str
    mode: str = "comprehensive"
    limit: int = 4


@dataclass(frozen=True)
class RetrievalQueryPlan:
    semantic_query: str
    artifact_kinds: tuple[str, ...]
    candidate_limit: int
    final_limit: int
    score_threshold: float
    max_per_artifact: int
    purpose: RetrievalPurpose


class RetrievalQueryPlanner:
    def plan(self, request: RetrievalRequest) -> RetrievalQueryPlan:
        query = request.query.strip()
        final_limit = max(1, request.limit)
        candidate_limit = final_limit * 3

        if request.mode == "project_deep_dive":
            return RetrievalQueryPlan(
                semantic_query=f"projects 项目 项目经历 {query}".strip(),
                artifact_kinds=("project", "knowledge", "practice"),
                candidate_limit=candidate_limit,
                final_limit=final_limit,
                score_threshold=0.25,
                max_per_artifact=2,
                purpose=request.purpose,
            )

        if request.purpose == "answer_feedback":
            return RetrievalQueryPlan(
                semantic_query=query,
                artifact_kinds=(
                    "knowledge",
                    "question_bank",
                    "project",
                    "high_frequency",
                    "practice",
                ),
                candidate_limit=candidate_limit,
                final_limit=final_limit,
                score_threshold=0.25,
                max_per_artifact=2,
                purpose=request.purpose,
            )

        if request.purpose == "follow_up_feedback":
            return RetrievalQueryPlan(
                semantic_query=query,
                artifact_kinds=("question_bank", "practice", "knowledge"),
                candidate_limit=candidate_limit,
                final_limit=final_limit,
                score_threshold=0.25,
                max_per_artifact=2,
                purpose=request.purpose,
            )

        return RetrievalQueryPlan(
            semantic_query=query,
            artifact_kinds=("question_bank", "knowledge", "project", "high_frequency"),
            candidate_limit=candidate_limit,
            final_limit=final_limit,
            score_threshold=0.25,
            max_per_artifact=2,
            purpose=request.purpose,
        )
