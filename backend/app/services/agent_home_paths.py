from __future__ import annotations

from pathlib import PurePosixPath
import unicodedata


_MAX_WORKSPACE_ID_LENGTH = 36
_MAX_PATH_SEGMENT_BYTES = 255
_MAX_OBJECT_KEY_BYTES = 1024


def _utf8_size(value: str, *, error_message: str) -> int:
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ValueError(error_message) from exc


def _contains_control_character(value: str) -> bool:
    return any(unicodedata.category(character) == "Cc" for character in value)


def normalize_home_path(path: str) -> str:
    """Validate and preserve one strict POSIX path relative to an Agent Home."""
    error_message = "invalid workspace path"
    if not isinstance(path, str) or not path:
        raise ValueError(error_message)
    if path.startswith("/") or "\\" in path or _contains_control_character(path):
        raise ValueError(error_message)

    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(error_message)
    if any(
        _utf8_size(part, error_message=error_message) > _MAX_PATH_SEGMENT_BYTES for part in parts
    ):
        raise ValueError(error_message)
    if _utf8_size(path, error_message=error_message) > _MAX_OBJECT_KEY_BYTES:
        raise ValueError(error_message)

    pure = PurePosixPath(path)
    normalized = pure.as_posix()
    if pure.is_absolute() or normalized != path:
        raise ValueError(error_message)
    return normalized


def normalize_home_directory(directory: str) -> str:
    """Normalize a list directory, allowing only the empty Agent Home root."""
    if directory == "":
        return ""
    return normalize_home_path(directory)


def agent_home_prefix(*, user_id: int, workspace_id: str) -> str:
    """Return the physical prefix for one user's instance of a Workspace."""
    error_message = "invalid workspace identity"
    if (
        not isinstance(user_id, int)
        or isinstance(user_id, bool)
        or user_id <= 0
        or not isinstance(workspace_id, str)
        or not workspace_id
        or len(workspace_id) > _MAX_WORKSPACE_ID_LENGTH
        or workspace_id in {".", ".."}
        or "/" in workspace_id
        or "\\" in workspace_id
        or _contains_control_character(workspace_id)
        or _utf8_size(workspace_id, error_message=error_message) > _MAX_PATH_SEGMENT_BYTES
    ):
        raise ValueError(error_message)

    try:
        prefix = f"users/{user_id}/workspaces/{workspace_id}/"
    except (ValueError, OverflowError) as exc:
        raise ValueError(error_message) from exc
    if _utf8_size(prefix, error_message=error_message) > _MAX_OBJECT_KEY_BYTES:
        raise ValueError(error_message)
    return prefix


def agent_home_key(*, user_id: int, workspace_id: str, path: str) -> str:
    """Return the bounded physical object key for one Agent Home file."""
    normalized_path = normalize_home_path(path)
    key = f"{agent_home_prefix(user_id=user_id, workspace_id=workspace_id)}{normalized_path}"
    if _utf8_size(key, error_message="invalid workspace path") > _MAX_OBJECT_KEY_BYTES:
        raise ValueError("invalid workspace path")
    return key
