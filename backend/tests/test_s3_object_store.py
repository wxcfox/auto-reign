from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock

from botocore.exceptions import ClientError, EndpointConnectionError
import pytest

from app.core.config import Settings
from app.storage.factory import build_object_store
from app.storage.local_object_store import LocalObjectStore
from app.storage.object_store import (
    ObjectConflict,
    ObjectMetadata,
    ObjectNotFound,
    ObjectStoreUnavailable,
    ObjectTooLarge,
)
from app.storage.s3_object_store import S3ObjectStore


def _client_error(code: str, operation: str) -> ClientError:
    return ClientError({"Error": {"Code": code}}, operation)


class RecordingS3Client:
    def __init__(self) -> None:
        self.put_calls: list[dict[str, object]] = []
        self.get_calls: list[dict[str, object]] = []
        self.head_calls: list[dict[str, object]] = []
        self.list_calls: list[dict[str, object]] = []
        self.delete_calls: list[dict[str, object]] = []
        self.body: object | None = None

    def put_object(self, **kwargs: object) -> dict[str, object]:
        self.put_calls.append(kwargs)
        return {"ETag": '"etag-1"'}

    def head_object(self, **kwargs: object) -> dict[str, object]:
        self.head_calls.append(kwargs)
        return {"ETag": '"etag-1"', "ContentLength": 3}

    def get_object(self, **kwargs: object) -> dict[str, object]:
        self.get_calls.append(kwargs)

        class Body:
            def __init__(self) -> None:
                self.closed = False
                self.remaining = b"new"
                self.read_sizes: list[int] = []

            def read(self, size: int = -1) -> bytes:
                self.read_sizes.append(size)
                count = min(size, 1, len(self.remaining))
                chunk, self.remaining = self.remaining[:count], self.remaining[count:]
                return chunk

            def close(self) -> None:
                self.closed = True

        self.body = Body()
        return {
            "Body": self.body,
            "ETag": '"etag-1"',
            "ContentLength": 3,
        }

    def list_objects_v2(self, **kwargs: object) -> dict[str, object]:
        self.list_calls.append(kwargs)
        return {"Contents": [], "IsTruncated": False}

    def delete_object(self, **kwargs: object) -> dict[str, object]:
        self.delete_calls.append(kwargs)
        return {}


def test_s3_store_checks_absence_then_puts_without_conditional_headers() -> None:
    class MissingHeadClient(RecordingS3Client):
        def head_object(self, **kwargs: object) -> dict[str, object]:
            self.head_calls.append(kwargs)
            raise _client_error("NoSuchKey", "HeadObject")

    client = MissingHeadClient()
    store = S3ObjectStore(
        client=client,
        bucket="bucket",
        key_prefix="auto-reign",
    )

    created = store.put("users/1/a.txt", b"new", if_none_match=True)

    assert created == ObjectMetadata(
        key="users/1/a.txt",
        etag='"etag-1"',
        size_bytes=3,
    )
    assert client.head_calls == [{"Bucket": "bucket", "Key": "auto-reign/users/1/a.txt"}]
    assert client.put_calls[0]["Key"] == "auto-reign/users/1/a.txt"
    assert "IfNoneMatch" not in client.put_calls[0]
    assert "IfMatch" not in client.put_calls[0]


def test_s3_store_compares_expected_etag_opaquely_then_returns_put_etag() -> None:
    client = RecordingS3Client()
    store = S3ObjectStore(client=client, bucket="bucket")

    updated = store.put(
        "users/1/a.txt",
        b"new",
        expected_etag='"etag-1"',
    )

    assert updated.etag == '"etag-1"'
    assert client.head_calls == [{"Bucket": "bucket", "Key": "users/1/a.txt"}]
    assert len(client.put_calls) == 1


def test_s3_store_rejects_stale_or_existing_conditions_before_put() -> None:
    client = RecordingS3Client()
    store = S3ObjectStore(client=client, bucket="bucket")

    with pytest.raises(ObjectConflict):
        store.put("users/1/a.txt", b"new", expected_etag='"stale"')
    with pytest.raises(ObjectConflict):
        store.put("users/1/a.txt", b"new", if_none_match=True)

    assert client.put_calls == []


def test_same_key_create_if_absent_is_serialized_in_one_store_instance() -> None:
    class StatefulClient(RecordingS3Client):
        def __init__(self) -> None:
            super().__init__()
            self.data: bytes | None = None
            self.state_lock = Lock()

        def head_object(self, **kwargs: object) -> dict[str, object]:
            self.head_calls.append(kwargs)
            with self.state_lock:
                if self.data is None:
                    raise _client_error("NoSuchKey", "HeadObject")
                return {"ETag": '"created"', "ContentLength": len(self.data)}

        def put_object(self, **kwargs: object) -> dict[str, object]:
            self.put_calls.append(kwargs)
            with self.state_lock:
                body = kwargs["Body"]
                assert isinstance(body, bytes)
                self.data = body
            return {"ETag": '"created"'}

    client = StatefulClient()
    store = S3ObjectStore(client=client, bucket="exclusive-bucket")
    start = Barrier(2)

    def create(data: bytes) -> str:
        start.wait()
        try:
            store.put("users/1/a.txt", data, if_none_match=True)
            return "created"
        except ObjectConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, [b"one", b"two"]))

    assert sorted(results) == ["conflict", "created"]
    assert len(client.put_calls) == 1
    assert len(client.head_calls) == 2
    for index in range(1_000):
        store._key_lock(f"users/1/{index}.txt")
    assert len(store._locks) == 256


def test_s3_store_get_reads_short_chunks_and_closes_body() -> None:
    client = RecordingS3Client()
    store = S3ObjectStore(
        client=client,
        bucket="bucket",
        key_prefix="auto-reign",
    )

    stored = store.get("users/1/a.txt")

    assert stored.data == b"new"
    assert stored.metadata == ObjectMetadata(
        key="users/1/a.txt",
        etag='"etag-1"',
        size_bytes=3,
    )
    assert client.get_calls == [{"Bucket": "bucket", "Key": "auto-reign/users/1/a.txt"}]
    assert client.body is not None
    assert client.body.remaining == b""  # type: ignore[attr-defined]
    assert client.body.closed is True  # type: ignore[attr-defined]


def test_s3_get_rejects_length_mismatch_and_closes_body() -> None:
    client = RecordingS3Client()
    original_get = client.get_object

    def mismatched_get(**kwargs: object) -> dict[str, object]:
        response = original_get(**kwargs)
        response["ContentLength"] = 4
        return response

    client.get_object = mismatched_get  # type: ignore[method-assign]

    with pytest.raises(ObjectStoreUnavailable):
        S3ObjectStore(client=client, bucket="bucket").get("users/1/a.txt")

    assert client.body is not None
    assert client.body.closed is True  # type: ignore[attr-defined]


def test_s3_get_closes_body_when_stream_read_fails() -> None:
    client = RecordingS3Client()
    original_get = client.get_object

    def failing_get(**kwargs: object) -> dict[str, object]:
        response = original_get(**kwargs)
        response["Body"].read = lambda _size: (_ for _ in ()).throw(  # type: ignore[union-attr]
            OSError("read")
        )
        return response

    client.get_object = failing_get  # type: ignore[method-assign]

    with pytest.raises(ObjectStoreUnavailable):
        S3ObjectStore(client=client, bucket="bucket").get("users/1/a.txt")

    assert client.body is not None
    assert client.body.closed is True  # type: ignore[attr-defined]


def test_s3_close_failure_does_not_mask_a_primary_base_exception() -> None:
    class InterruptingClient(RecordingS3Client):
        def get_object(self, **kwargs: object) -> dict[str, object]:
            self.get_calls.append(kwargs)

            class Body:
                def read(self, _size: int) -> bytes:
                    raise KeyboardInterrupt

                def close(self) -> None:
                    raise OSError("close failed")

            self.body = Body()
            return {
                "Body": self.body,
                "ETag": '"etag-1"',
                "ContentLength": 1,
            }

    with pytest.raises(KeyboardInterrupt):
        S3ObjectStore(client=InterruptingClient(), bucket="bucket").get("users/1/a.txt")


def test_s3_rejects_oversized_declared_length_before_read_and_closes_body() -> None:
    client = RecordingS3Client()
    original_get = client.get_object

    def oversized_get(**kwargs: object) -> dict[str, object]:
        response = original_get(**kwargs)
        response["ContentLength"] = 5
        return response

    client.get_object = oversized_get  # type: ignore[method-assign]
    store = S3ObjectStore(client=client, bucket="bucket", max_read_bytes=4)

    with pytest.raises(ObjectTooLarge):
        store.get("users/1/a.txt")

    assert client.body is not None
    assert client.body.read_sizes == []  # type: ignore[attr-defined]
    assert client.body.closed is True  # type: ignore[attr-defined]


def test_s3_rejects_oversized_put_before_head_or_network() -> None:
    client = RecordingS3Client()
    store = S3ObjectStore(client=client, bucket="bucket", max_read_bytes=4)

    with pytest.raises(ObjectTooLarge):
        store.put("users/1/a.txt", b"12345", if_none_match=True)

    assert client.head_calls == []
    assert client.put_calls == []


def test_s3_put_requires_etag_from_put_response_without_post_head() -> None:
    client = RecordingS3Client()
    client.put_object = lambda **_kwargs: {}  # type: ignore[method-assign]

    with pytest.raises(ObjectStoreUnavailable):
        S3ObjectStore(client=client, bucket="bucket").put("users/1/a.txt", b"x")

    assert client.head_calls == []


def test_s3_store_lists_pages_as_sorted_logical_metadata() -> None:
    class PagedClient(RecordingS3Client):
        def list_objects_v2(self, **kwargs: object) -> dict[str, object]:
            self.list_calls.append(kwargs)
            if "ContinuationToken" not in kwargs:
                return {
                    "Contents": [
                        {
                            "Key": "namespace/users/2/b.txt",
                            "ETag": '"b"',
                            "Size": 1,
                        }
                    ],
                    "IsTruncated": True,
                    "NextContinuationToken": "next",
                }
            return {
                "Contents": [
                    {
                        "Key": "namespace/users/1/a.txt",
                        "ETag": '"a"',
                        "Size": 2,
                    }
                ],
                "IsTruncated": False,
            }

    client = PagedClient()
    store = S3ObjectStore(
        client=client,
        bucket="bucket",
        key_prefix="namespace",
    )

    assert store.list("users/") == [
        ObjectMetadata(key="users/1/a.txt", etag='"a"', size_bytes=2),
        ObjectMetadata(key="users/2/b.txt", etag='"b"', size_bytes=1),
    ]
    assert client.list_calls == [
        {"Bucket": "bucket", "Prefix": "namespace/users/"},
        {
            "Bucket": "bucket",
            "Prefix": "namespace/users/",
            "ContinuationToken": "next",
        },
    ]


def test_s3_directory_prefix_does_not_match_a_sibling_with_the_same_text() -> None:
    class FilteringClient(RecordingS3Client):
        objects = {
            "namespace/users/1/a.txt": b"a",
            "namespace/users2/x.txt": b"x",
        }

        def list_objects_v2(self, **kwargs: object) -> dict[str, object]:
            self.list_calls.append(kwargs)
            prefix = kwargs["Prefix"]
            assert isinstance(prefix, str)
            return {
                "Contents": [
                    {"Key": key, "ETag": f'"{key}"', "Size": len(value)}
                    for key, value in self.objects.items()
                    if key.startswith(prefix)
                ],
                "IsTruncated": False,
            }

    client = FilteringClient()
    store = S3ObjectStore(
        client=client,
        bucket="bucket",
        key_prefix="namespace",
    )

    assert [item.key for item in store.list("users/")] == ["users/1/a.txt"]
    assert client.list_calls[0]["Prefix"] == "namespace/users/"


def test_s3_store_rejects_list_item_outside_configured_prefix() -> None:
    class EscapingClient(RecordingS3Client):
        def list_objects_v2(self, **kwargs: object) -> dict[str, object]:
            self.list_calls.append(kwargs)
            return {
                "Contents": [{"Key": "other/users/1/a.txt", "ETag": '"a"', "Size": 1}],
                "IsTruncated": False,
            }

    with pytest.raises(ObjectStoreUnavailable):
        S3ObjectStore(
            client=EscapingClient(),
            bucket="bucket",
            key_prefix="namespace",
        ).list("")


@pytest.mark.parametrize(
    ("operation", "error"),
    [
        ("head", EndpointConnectionError(endpoint_url="https://s3.invalid")),
        ("put", OSError("socket")),
    ],
)
def test_s3_store_maps_head_and_put_transport_failures(
    operation: str,
    error: Exception,
) -> None:
    class FailingClient(RecordingS3Client):
        def head_object(self, **_kwargs: object) -> dict[str, object]:
            if operation == "head":
                raise error
            raise _client_error("NoSuchKey", "HeadObject")

        def put_object(self, **_kwargs: object) -> dict[str, object]:
            if operation == "put":
                raise error
            return {"ETag": '"etag"'}

    with pytest.raises(ObjectStoreUnavailable):
        S3ObjectStore(client=FailingClient(), bucket="bucket").put(
            "users/1/a",
            b"x",
            if_none_match=True,
        )


def test_s3_store_maps_missing_and_precondition_provider_errors() -> None:
    class FailingClient(RecordingS3Client):
        def get_object(self, **_kwargs: object) -> dict[str, object]:
            raise _client_error("NoSuchKey", "GetObject")

        def put_object(self, **_kwargs: object) -> dict[str, object]:
            raise _client_error("PreconditionFailed", "PutObject")

    store = S3ObjectStore(client=FailingClient(), bucket="bucket")

    with pytest.raises(ObjectNotFound):
        store.get("users/1/missing")
    with pytest.raises(ObjectConflict):
        store.put("users/1/a", b"x")


def test_s3_delete_is_idempotent_for_provider_missing_key() -> None:
    class MissingClient(RecordingS3Client):
        def delete_object(self, **kwargs: object) -> dict[str, object]:
            self.delete_calls.append(kwargs)
            raise _client_error("NoSuchKey", "DeleteObject")

    client = MissingClient()
    store = S3ObjectStore(client=client, bucket="bucket")

    store.delete("users/1/missing")
    store.delete("users/1/missing")

    assert len(client.delete_calls) == 2


def test_factory_selects_local_store(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        app_env="development",
        data_dir=tmp_path,
        object_store_backend="local",
    )

    assert isinstance(build_object_store(settings), LocalObjectStore)


def test_factory_rejects_local_store_or_multiple_instances_in_production(
    tmp_path,
) -> None:
    with pytest.raises(ValueError, match="OBJECT_STORE_BACKEND=s3"):
        build_object_store(
            Settings(
                _env_file=None,
                app_env="production",
                jwt_secret_key="production-test-secret",
                data_dir=tmp_path,
                object_store_backend="local",
            )
        )
    with pytest.raises(ValueError, match="single FastAPI instance"):
        build_object_store(
            Settings(
                _env_file=None,
                app_env="production",
                jwt_secret_key="production-test-secret",
                backend_instance_count=2,
                object_store_backend="s3",
                s3_bucket="exclusive-bucket",
                s3_namespace_app_exclusive=True,
            )
        )


def test_factory_requires_bucket_and_explicit_exclusive_namespace() -> None:
    with pytest.raises(ValueError, match="S3_BUCKET"):
        build_object_store(
            Settings(
                _env_file=None,
                object_store_backend="s3",
                s3_bucket="",
            )
        )
    with pytest.raises(ValueError, match="S3_NAMESPACE_APP_EXCLUSIVE"):
        build_object_store(
            Settings(
                _env_file=None,
                object_store_backend="s3",
                s3_bucket="shared-bucket",
            )
        )


def test_factory_rejects_path_style_for_aliyun_oss() -> None:
    with pytest.raises(ValueError, match="virtual-hosted"):
        build_object_store(
            Settings(
                _env_file=None,
                object_store_backend="s3",
                s3_endpoint_url="https://oss-cn-hangzhou.aliyuncs.com",
                s3_bucket="exclusive-bucket",
                s3_namespace_app_exclusive=True,
                s3_addressing_style="path",
                s3_access_key_id="test-id",
                s3_secret_access_key="test-secret",
            )
        )


def test_factory_builds_virtual_hosted_oss_request_url_without_network() -> None:
    store = build_object_store(
        Settings(
            _env_file=None,
            object_store_backend="s3",
            s3_endpoint_url="https://oss-cn-hangzhou.aliyuncs.com",
            s3_bucket="exclusive-bucket",
            s3_namespace_app_exclusive=True,
            s3_addressing_style="virtual",
            s3_access_key_id="test-id",
            s3_secret_access_key="test-secret",
        )
    )

    url = store.client.generate_presigned_url(
        "get_object",
        Params={"Bucket": "exclusive-bucket", "Key": "probe"},
    )

    assert store.client.meta.config.s3["addressing_style"] == "virtual"
    assert store.client.meta.config.request_checksum_calculation == "when_required"
    assert store.client.meta.config.response_checksum_validation == "when_required"
    assert url.startswith("https://exclusive-bucket.oss-cn-hangzhou.aliyuncs.com/probe?")
