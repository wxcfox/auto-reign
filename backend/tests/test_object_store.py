from typing import get_type_hints

import pytest

from app.storage.object_store import (
    ObjectMetadata,
    ObjectStoreError,
    ObjectTooLarge,
    StoredObject,
    validate_object_key,
    validate_put_conditions,
)


def test_object_metadata_keeps_only_non_secret_object_facts() -> None:
    metadata = ObjectMetadata(
        key="users/7/attachments/a/file.txt",
        etag="sha256:abc",
        size_bytes=4,
    )

    assert metadata.key.endswith("file.txt")
    assert metadata.etag == "sha256:abc"
    assert metadata.size_bytes == 4
    assert set(get_type_hints(ObjectMetadata)) == {"key", "etag", "size_bytes"}
    assert StoredObject(data=b"body", metadata=metadata).metadata is metadata


def test_put_conditions_are_mutually_exclusive() -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        validate_put_conditions(if_none_match=True, expected_etag="sha256:old")


@pytest.mark.parametrize(
    "key",
    ["", "/etc/passwd", "../escape", "users/1/../../escape", "users//1/file"],
)
def test_object_key_validation_is_shared_and_rejects_unsafe_keys(key: str) -> None:
    with pytest.raises(ValueError, match="object key"):
        validate_object_key(key)


def test_object_key_validation_preserves_logical_key() -> None:
    assert validate_object_key("users/7/attachments/a/file.txt") == (
        "users/7/attachments/a/file.txt"
    )
    assert validate_object_key("users/7/attachments/", allow_prefix=True) == (
        "users/7/attachments"
    )
    assert validate_object_key("", allow_prefix=True) == ""


def test_object_too_large_is_a_stable_store_error() -> None:
    assert issubclass(ObjectTooLarge, ObjectStoreError)
