from __future__ import annotations

import hashlib
from threading import Lock
from typing import Any, NoReturn

from botocore.exceptions import BotoCoreError, ClientError

from app.core.limits import DEFAULT_OBJECT_STORE_MAX_READ_BYTES
from app.storage.object_store import (
    ObjectConflict,
    ObjectMetadata,
    ObjectNotFound,
    ObjectStoreError,
    ObjectStoreUnavailable,
    ObjectTooLarge,
    StoredObject,
    validate_object_key,
    validate_put_conditions,
)


_MISSING_CODES = frozenset({"NoSuchKey", "404", "NotFound"})
_CONFLICT_CODES = frozenset({"PreconditionFailed", "412", "ConditionalRequestConflict"})


class S3ObjectStore:
    """S3-compatible storage with process-local, same-key conditional writes."""

    def __init__(
        self,
        *,
        client: Any,
        bucket: str,
        key_prefix: str = "",
        max_read_bytes: int = DEFAULT_OBJECT_STORE_MAX_READ_BYTES,
    ) -> None:
        if not bucket.strip():
            raise ValueError("bucket must not be empty")
        if max_read_bytes <= 0:
            raise ValueError("max_read_bytes must be positive")
        normalized_prefix = key_prefix.strip("/")
        self.client = client
        self.bucket = bucket.strip()
        self.key_prefix = validate_object_key(normalized_prefix) if normalized_prefix else ""
        self.max_read_bytes = max_read_bytes
        self._locks = [Lock() for _ in range(256)]

    def put(
        self,
        key: str,
        data: bytes,
        if_none_match: bool = False,
        expected_etag: str | None = None,
    ) -> ObjectMetadata:
        validate_put_conditions(
            if_none_match=if_none_match,
            expected_etag=expected_etag,
        )
        logical = validate_object_key(key)
        if len(data) > self.max_read_bytes:
            raise ObjectTooLarge(logical)

        with self._key_lock(logical):
            if if_none_match or expected_etag is not None:
                current = self._head_if_present(logical)
                if if_none_match and current is not None:
                    raise ObjectConflict(logical)
                if expected_etag is not None and (current is None or current.etag != expected_etag):
                    raise ObjectConflict(logical)
            try:
                response = self.client.put_object(
                    Bucket=self.bucket,
                    Key=self._physical_key(logical),
                    Body=data,
                )
                return ObjectMetadata(
                    key=logical,
                    etag=self._required_etag(response, logical),
                    size_bytes=len(data),
                )
            except ClientError as exc:
                self._raise_client_error(exc, logical)
            except (BotoCoreError, OSError) as exc:
                raise ObjectStoreUnavailable(logical) from exc

    def get(self, key: str) -> StoredObject:
        logical = validate_object_key(key)
        with self._key_lock(logical):
            body: Any | None = None
            primary_error: BaseException | None = None
            try:
                response = self.client.get_object(
                    Bucket=self.bucket,
                    Key=self._physical_key(logical),
                )
                body = response["Body"]
                declared = int(response["ContentLength"])
                if declared < 0:
                    raise ObjectStoreUnavailable(logical)
                if declared > self.max_read_bytes:
                    raise ObjectTooLarge(logical)

                chunks: list[bytes] = []
                received = 0
                while received <= declared:
                    chunk = body.read(min(64 * 1024, declared + 1 - received))
                    if not chunk:
                        break
                    if not isinstance(chunk, bytes):
                        raise ObjectStoreUnavailable(logical)
                    chunks.append(chunk)
                    received += len(chunk)
                    if received > self.max_read_bytes:
                        raise ObjectTooLarge(logical)
                if received != declared:
                    raise ObjectStoreUnavailable(logical)
                data = b"".join(chunks)
                return StoredObject(
                    data=data,
                    metadata=ObjectMetadata(
                        key=logical,
                        etag=self._required_etag(response, logical),
                        size_bytes=declared,
                    ),
                )
            except ClientError as exc:
                primary_error = exc
                self._raise_client_error(exc, logical)
            except ObjectStoreError as exc:
                primary_error = exc
                raise
            except (BotoCoreError, OSError, KeyError, TypeError, ValueError) as exc:
                primary_error = exc
                raise ObjectStoreUnavailable(logical) from exc
            except BaseException as exc:
                primary_error = exc
                raise
            finally:
                if body is not None:
                    try:
                        body.close()
                    except (ClientError, BotoCoreError, OSError) as close_error:
                        if primary_error is None:
                            raise ObjectStoreUnavailable(logical) from close_error

    def head(self, key: str) -> ObjectMetadata:
        logical = validate_object_key(key)
        with self._key_lock(logical):
            current = self._head_if_present(logical)
            if current is None:
                raise ObjectNotFound(logical)
            return current

    def list(self, prefix: str) -> list[ObjectMetadata]:
        normalized_prefix = validate_object_key(prefix, allow_prefix=True)
        physical_prefix = self._physical_key(prefix, allow_prefix=True)
        token: str | None = None
        items: list[ObjectMetadata] = []
        try:
            while True:
                request: dict[str, object] = {
                    "Bucket": self.bucket,
                    "Prefix": physical_prefix,
                }
                if token is not None:
                    request["ContinuationToken"] = token
                response = self.client.list_objects_v2(**request)
                for item in response.get("Contents", []):
                    logical = self._logical_key(str(item["Key"]))
                    if normalized_prefix and not self._matches_prefix(
                        logical,
                        normalized_prefix,
                        directory=prefix.endswith("/"),
                    ):
                        raise ObjectStoreUnavailable(
                            "provider returned an object outside the requested prefix"
                        )
                    items.append(
                        self._metadata_from_response(
                            item,
                            key=logical,
                            size_field="Size",
                        )
                    )
                if not response.get("IsTruncated"):
                    items.sort(key=lambda item: item.key)
                    return items
                next_token = response["NextContinuationToken"]
                if not isinstance(next_token, str) or not next_token:
                    raise ObjectStoreUnavailable(prefix)
                token = next_token
        except ClientError as exc:
            self._raise_client_error(exc, prefix)
        except ObjectStoreError:
            raise
        except (BotoCoreError, OSError, KeyError, TypeError, ValueError) as exc:
            raise ObjectStoreUnavailable(prefix) from exc

    def delete(self, key: str) -> None:
        logical = validate_object_key(key)
        with self._key_lock(logical):
            try:
                self.client.delete_object(
                    Bucket=self.bucket,
                    Key=self._physical_key(logical),
                )
            except ClientError as exc:
                code = self._error_code(exc)
                if code in _MISSING_CODES:
                    return
                self._raise_client_error(exc, logical)
            except (BotoCoreError, OSError) as exc:
                raise ObjectStoreUnavailable(logical) from exc

    def _head_if_present(self, key: str) -> ObjectMetadata | None:
        try:
            response = self.client.head_object(
                Bucket=self.bucket,
                Key=self._physical_key(key),
            )
            return self._metadata_from_response(
                response,
                key=key,
                size_field="ContentLength",
            )
        except ClientError as exc:
            if self._error_code(exc) in _MISSING_CODES:
                return None
            self._raise_client_error(exc, key)
        except ObjectStoreError:
            raise
        except (BotoCoreError, OSError, KeyError, TypeError, ValueError) as exc:
            raise ObjectStoreUnavailable(key) from exc

    def _raise_client_error(self, exc: ClientError, key: str) -> NoReturn:
        code = self._error_code(exc)
        if code in _MISSING_CODES:
            raise ObjectNotFound(key) from exc
        if code in _CONFLICT_CODES:
            raise ObjectConflict(key) from exc
        raise ObjectStoreUnavailable(key) from exc

    @staticmethod
    def _error_code(exc: ClientError) -> str:
        return str(exc.response.get("Error", {}).get("Code", ""))

    @staticmethod
    def _required_etag(response: dict[str, object], key: str) -> str:
        etag = response.get("ETag")
        if not isinstance(etag, str) or not etag:
            raise ObjectStoreUnavailable(key)
        return etag

    def _metadata_from_response(
        self,
        response: dict[str, object],
        *,
        key: str,
        size_field: str,
    ) -> ObjectMetadata:
        size = int(response[size_field])
        if size < 0:
            raise ObjectStoreUnavailable(key)
        if size > self.max_read_bytes:
            raise ObjectTooLarge(key)
        return ObjectMetadata(
            key=key,
            etag=self._required_etag(response, key),
            size_bytes=size,
        )

    def _key_lock(self, key: str) -> Lock:
        digest = hashlib.sha256(validate_object_key(key).encode("utf-8")).digest()
        return self._locks[int.from_bytes(digest[:8], "big") % len(self._locks)]

    def _physical_key(self, key: str, *, allow_prefix: bool = False) -> str:
        logical = validate_object_key(key, allow_prefix=allow_prefix)
        physical = f"{self.key_prefix}/{logical}" if self.key_prefix else logical
        if allow_prefix and key.endswith("/") and logical:
            return f"{physical}/"
        if allow_prefix and not logical and self.key_prefix:
            return f"{self.key_prefix}/"
        return physical

    def _logical_key(self, key: str) -> str:
        prefix = f"{self.key_prefix}/" if self.key_prefix else ""
        if prefix and not key.startswith(prefix):
            raise ObjectStoreUnavailable("object escaped configured key prefix")
        return validate_object_key(key.removeprefix(prefix))

    @staticmethod
    def _matches_prefix(
        key: str,
        prefix: str,
        *,
        directory: bool,
    ) -> bool:
        if directory:
            return key.startswith(f"{prefix}/")
        return key.startswith(prefix)
