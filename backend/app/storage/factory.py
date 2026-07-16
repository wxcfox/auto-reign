from __future__ import annotations

from urllib.parse import urlparse

import boto3
from botocore.config import Config

from app.core.config import Settings
from app.storage.local_object_store import LocalObjectStore
from app.storage.object_store import ObjectStore
from app.storage.s3_object_store import S3ObjectStore


def build_object_store(settings: Settings) -> ObjectStore:
    if settings.app_env == "production" and settings.backend_instance_count != 1:
        raise ValueError("v1 production requires a single FastAPI instance")
    if settings.app_env == "production" and settings.object_store_backend != "s3":
        raise ValueError("production requires OBJECT_STORE_BACKEND=s3")

    if settings.object_store_backend == "local":
        root = settings.object_store_local_root or settings.data_dir / "objects"
        return LocalObjectStore(
            root,
            max_read_bytes=settings.object_store_max_read_bytes,
        )

    bucket = settings.s3_bucket.strip()
    if not bucket:
        raise ValueError("S3_BUCKET is required for the s3 object store")
    if not settings.s3_namespace_app_exclusive:
        raise ValueError("S3_NAMESPACE_APP_EXCLUSIVE=true is required by the v1 CAS contract")
    endpoint_url = (settings.s3_endpoint_url or "").strip() or None
    if _is_aliyun_oss_endpoint(endpoint_url) and settings.s3_addressing_style != "virtual":
        raise ValueError("Alibaba Cloud OSS requires virtual-hosted S3 addressing")
    if bool(settings.s3_access_key_id) != bool(settings.s3_secret_access_key):
        raise ValueError("S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY must be configured together")

    client = boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key,
        aws_session_token=settings.s3_session_token,
        config=Config(
            s3={"addressing_style": settings.s3_addressing_style},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )
    return S3ObjectStore(
        client=client,
        bucket=bucket,
        key_prefix=settings.s3_key_prefix,
        max_read_bytes=settings.object_store_max_read_bytes,
    )


def _is_aliyun_oss_endpoint(endpoint_url: str | None) -> bool:
    if endpoint_url is None:
        return False
    hostname = (urlparse(endpoint_url).hostname or "").lower()
    return hostname == "aliyuncs.com" or hostname.endswith(".aliyuncs.com")
