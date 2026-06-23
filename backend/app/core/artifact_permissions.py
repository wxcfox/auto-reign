from __future__ import annotations

class ArtifactPermissionError(ValueError):
    pass


ALLOWED_OPERATIONS: dict[str, set[str]] = {
    "source": set(),
    "extracted": set(),
    "candidate_profile": {"replace_body"},
    "target_profile": {"replace_body"},
    "knowledge": {"replace_body"},
    "question_bank": {"replace_body"},
    "project": {"replace_body"},
    "interview_record": set(),
    "high_frequency": {"replace_body"},
    "review_status": {"replace_body"},
    "practice": {"append_supplement"},
    "mastery": {"annotate", "pause", "request_reeval"},
    "report": {"replace_body"},
}


def assert_operation_allowed(kind: str, operation: str) -> None:
    if operation not in ALLOWED_OPERATIONS.get(kind, set()):
        raise ArtifactPermissionError(f"operation {operation!r} is not allowed for {kind!r}")
