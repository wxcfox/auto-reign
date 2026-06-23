import pytest

from app.core.artifact_permissions import (
    ArtifactPermissionError,
    assert_operation_allowed,
)


def test_operation_permissions_match_artifact_semantics() -> None:
    for kind in (
        "candidate_profile",
        "target_profile",
        "knowledge",
        "review_status",
        "report",
    ):
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


def test_legacy_plan_is_not_an_editable_current_artifact() -> None:
    with pytest.raises(ArtifactPermissionError):
        assert_operation_allowed("plan", "replace_body")
