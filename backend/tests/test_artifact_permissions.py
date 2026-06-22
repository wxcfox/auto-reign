import pytest

from app.core.artifact_permissions import (
    PLAN_MAX_TASKS,
    ArtifactPermissionError,
    assert_operation_allowed,
    validate_plan_task_count,
)


def test_operation_permissions_match_artifact_semantics() -> None:
    for kind in ("candidate_profile", "target_profile", "knowledge", "plan", "report"):
        assert_operation_allowed(kind, "replace_body")

    assert_operation_allowed("practice", "append_supplement")
    assert_operation_allowed("mastery", "annotate")
    assert_operation_allowed("mastery", "pause")
    assert_operation_allowed("mastery", "request_reeval")


@pytest.mark.parametrize("kind", ["source", "extracted"])
def test_sources_and_extracted_text_are_read_only(kind: str) -> None:
    with pytest.raises(ArtifactPermissionError):
        assert_operation_allowed(kind, "replace_body")


def test_practice_is_append_only() -> None:
    with pytest.raises(ArtifactPermissionError):
        assert_operation_allowed("practice", "replace_body")


def test_plan_keeps_at_most_three_tasks() -> None:
    validate_plan_task_count(["a", "b", "c"])
    with pytest.raises(ArtifactPermissionError):
        validate_plan_task_count(["a", "b", "c", "d"])
    assert PLAN_MAX_TASKS == 3
