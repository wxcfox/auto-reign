from __future__ import annotations


PLAN_MAX_TASKS = 3


class ArtifactPermissionError(ValueError):
    pass


ALLOWED_OPERATIONS: dict[str, set[str]] = {
    "source": set(),
    "extracted": set(),
    "candidate_profile": {"replace_body"},
    "target_profile": {"replace_body"},
    "knowledge": {"replace_body"},
    "practice": {"append_supplement"},
    "mastery": {"annotate", "pause", "request_reeval"},
    "plan": {"replace_body", "reorder"},
    "report": {"replace_body"},
}


def assert_operation_allowed(kind: str, operation: str) -> None:
    if operation not in ALLOWED_OPERATIONS.get(kind, set()):
        raise ArtifactPermissionError(f"operation {operation!r} is not allowed for {kind!r}")


def validate_plan_task_count(tasks: list[str]) -> None:
    if len(tasks) > PLAN_MAX_TASKS:
        raise ArtifactPermissionError("plans can contain at most three active tasks")
