import pytest

from app.services.agent_home_paths import (
    agent_home_key,
    agent_home_prefix,
    normalize_home_directory,
    normalize_home_path,
)


def _path_with_utf8_size(size_bytes: int) -> str:
    segments: list[str] = []
    remaining = size_bytes
    while remaining:
        if segments:
            remaining -= 1
        segment_size = min(255, remaining)
        segments.append("a" * segment_size)
        remaining -= segment_size
    return "/".join(segments)


def test_agent_home_keys_use_only_effective_user_and_workspace_ids() -> None:
    assert agent_home_prefix(user_id=7, workspace_id="ws-1") == ("users/7/workspaces/ws-1/")
    assert (
        agent_home_key(
            user_id=7,
            workspace_id="ws-1",
            path="notes/python.md",
        )
        == "users/7/workspaces/ws-1/notes/python.md"
    )


def test_agent_home_prefix_is_a_stable_directory_list_prefix() -> None:
    first_user = agent_home_prefix(user_id=7, workspace_id="shared")
    second_user = agent_home_prefix(user_id=8, workspace_id="shared")
    second_workspace = agent_home_prefix(user_id=7, workspace_id="other")

    assert first_user == "users/7/workspaces/shared/"
    assert first_user != second_user
    assert first_user != second_workspace
    assert agent_home_key(
        user_id=7,
        workspace_id="shared",
        path="AGENTS.md",
    ).startswith(first_user)


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/etc/passwd",
        "//server/share",
        "../escape",
        "notes/../../escape",
        "notes/..",
        "./AGENTS.md",
        "notes/.",
        "a//b",
        "notes/",
        "notes\\escape",
        "a/\x00b",
        "a/line\nbreak",
        "a/\x7fb",
    ],
)
def test_normalize_home_path_rejects_unsafe_paths(path: str) -> None:
    with pytest.raises(ValueError, match="workspace path"):
        normalize_home_path(path)


@pytest.mark.parametrize(
    "directory",
    ["/", ".", "..", "notes/..", "notes//deep", "notes\\deep", "notes/\x1f"],
)
def test_normalize_home_directory_rejects_unsafe_non_root_paths(
    directory: str,
) -> None:
    with pytest.raises(ValueError, match="workspace path"):
        normalize_home_directory(directory)


def test_normalize_home_path_preserves_safe_posix_paths_and_root_directory() -> None:
    assert normalize_home_path("notes/python.md") == "notes/python.md"
    assert normalize_home_path("AGENTS.md") == "AGENTS.md"
    assert normalize_home_path("notes/AGENTS.md") == "notes/AGENTS.md"
    assert normalize_home_directory("") == ""
    assert normalize_home_directory("notes") == "notes"
    assert normalize_home_directory("notes/deep") == "notes/deep"


@pytest.mark.parametrize(
    ("user_id", "workspace_id"),
    [
        (0, "ws"),
        (-1, "ws"),
        (True, "ws"),
        (7, ""),
        (7, "."),
        (7, ".."),
        (7, "bad/id"),
        (7, "bad\\id"),
        (7, "bad\x00id"),
        (7, "w" * 37),
    ],
)
def test_agent_home_prefix_rejects_invalid_physical_identity(
    user_id: int,
    workspace_id: str,
) -> None:
    with pytest.raises(ValueError, match="workspace identity"):
        agent_home_prefix(user_id=user_id, workspace_id=workspace_id)


def test_home_path_limits_are_measured_in_utf8_bytes() -> None:
    assert normalize_home_path("a" * 255) == "a" * 255
    with pytest.raises(ValueError, match="workspace path"):
        normalize_home_path("a" * 256)
    assert normalize_home_path("界" * 85) == "界" * 85
    with pytest.raises(ValueError, match="workspace path"):
        normalize_home_path("界" * 86)

    exactly_1024_bytes = "/".join(["a" * 204] * 5)
    assert len(exactly_1024_bytes.encode("utf-8")) == 1024
    assert normalize_home_path(exactly_1024_bytes) == exactly_1024_bytes
    with pytest.raises(ValueError, match="workspace path"):
        normalize_home_path(f"{exactly_1024_bytes}a")


def test_agent_home_key_enforces_the_full_object_key_limit() -> None:
    prefix = agent_home_prefix(user_id=7, workspace_id="ws")
    available_path_bytes = 1024 - len(prefix.encode("utf-8"))
    exact_path = _path_with_utf8_size(available_path_bytes)

    exact_key = agent_home_key(user_id=7, workspace_id="ws", path=exact_path)
    assert len(exact_key.encode("utf-8")) == 1024
    with pytest.raises(ValueError, match="workspace path"):
        agent_home_key(
            user_id=7,
            workspace_id="ws",
            path=f"{exact_path}a",
        )
