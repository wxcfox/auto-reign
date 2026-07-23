from __future__ import annotations

import json
import math


MAX_JSON_NESTING_DEPTH = 64
MAX_JSON_NODE_COUNT = 50_000
MAX_JSON_STRING_CHARS = 1_000_000
MAX_JSON_CANONICAL_BYTES = 4 * 1024 * 1024


class JsonSafetyError(ValueError):
    pass


def canonical_json(value: object) -> str:
    """Validate a persistence-bound JSON value and return canonical JSON."""

    active_path: set[int] = set()
    stack: list[tuple[bool, object, int]] = [(False, value, 0)]
    node_count = 0
    raw_string_bytes = 0

    while stack:
        exiting, current, depth = stack.pop()
        if exiting:
            active_path.remove(current)
            continue

        node_count += 1
        if node_count > MAX_JSON_NODE_COUNT:
            raise JsonSafetyError("json_node_limit")
        if current is None or isinstance(current, bool):
            continue
        if isinstance(current, str):
            raw_string_bytes += _string_bytes(current)
            if raw_string_bytes > MAX_JSON_CANONICAL_BYTES:
                raise JsonSafetyError("json_size_limit")
            continue
        if isinstance(current, int):
            try:
                raw_string_bytes += len(str(current))
            except (ValueError, OverflowError):
                raise JsonSafetyError("json_invalid_number") from None
            if raw_string_bytes > MAX_JSON_CANONICAL_BYTES:
                raise JsonSafetyError("json_size_limit")
            continue
        if isinstance(current, float):
            if not math.isfinite(current):
                raise JsonSafetyError("json_invalid_number")
            continue
        if not isinstance(current, list | dict):
            raise JsonSafetyError("json_invalid_type")
        if depth >= MAX_JSON_NESTING_DEPTH:
            raise JsonSafetyError("json_depth_limit")

        identity = id(current)
        if identity in active_path:
            raise JsonSafetyError("json_cycle")
        active_path.add(identity)
        stack.append((True, identity, depth))
        if isinstance(current, list):
            stack.extend(
                (False, item, depth + 1) for item in reversed(current)
            )
            continue
        for key in current:
            node_count += 1
            if node_count > MAX_JSON_NODE_COUNT:
                raise JsonSafetyError("json_node_limit")
            if not isinstance(key, str):
                raise JsonSafetyError("json_invalid_key")
            raw_string_bytes += _string_bytes(key)
            if raw_string_bytes > MAX_JSON_CANONICAL_BYTES:
                raise JsonSafetyError("json_size_limit")
        stack.extend(
            (False, item, depth + 1)
            for item in reversed(tuple(current.values()))
        )

    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        size = len(serialized.encode("utf-8", errors="strict"))
    except (TypeError, ValueError, UnicodeError, RecursionError, OverflowError):
        raise JsonSafetyError("json_serialization_failed") from None
    if size > MAX_JSON_CANONICAL_BYTES:
        raise JsonSafetyError("json_size_limit")
    return serialized


def validate_json_value(value: object) -> None:
    canonical_json(value)


def _string_bytes(value: str) -> int:
    if len(value) > MAX_JSON_STRING_CHARS:
        raise JsonSafetyError("json_string_limit")
    try:
        return len(value.encode("utf-8", errors="strict"))
    except UnicodeError:
        raise JsonSafetyError("json_invalid_string") from None
