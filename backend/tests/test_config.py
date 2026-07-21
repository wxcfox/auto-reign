from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def _complete_production_settings() -> dict[str, object]:
    return {
        "_env_file": None,
        "app_env": "production",
        "jwt_secret_key": "production-signing-secret",
        "object_store_backend": "s3",
        "s3_bucket": "auto-reign",
        "s3_endpoint_url": "https://oss-cn-hangzhou.aliyuncs.com",
        "s3_region": "cn-hangzhou",
        "s3_access_key_id": "test-access-key",
        "s3_secret_access_key": "test-secret-key",
        "s3_key_prefix": "production",
        "s3_namespace_app_exclusive": True,
        "s3_addressing_style": "virtual",
        "backend_instance_count": 1,
    }


def test_settings_creates_only_the_data_directory(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path / "data")

    settings.ensure_data_dirs()

    assert settings.data_dir.exists()
    assert list(settings.data_dir.iterdir()) == []


def test_settings_does_not_expose_removed_registration_or_workspace_defaults(
    tmp_path: Path,
) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path / "data")

    assert not hasattr(settings, "registration_enabled")
    assert not hasattr(settings, "workspace_dir")
    assert not hasattr(settings, "default_manifest_path")


def test_settings_generates_stable_local_jwt_secret(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", jwt_secret_key=None)

    secret = settings.resolve_jwt_secret_key()
    reloaded = Settings(data_dir=tmp_path / "data", jwt_secret_key=None)

    assert secret == reloaded.resolve_jwt_secret_key()
    assert secret != "auto-reign-local-dev-secret-change-me"
    assert len(secret) >= 32
    assert (tmp_path / "data" / ".secrets" / "jwt_secret").read_text(
        encoding="utf-8"
    ).strip() == secret


def test_model_request_timeout_has_a_positive_bounded_default(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path / "data")

    assert settings.model_request_timeout_seconds == 30.0
    assert (
        Settings(
            _env_file=None,
            data_dir=tmp_path / "custom",
            model_request_timeout_seconds=0.25,
        ).model_request_timeout_seconds
        == 0.25
    )

    for invalid in (0, -0.1, float("inf"), float("nan")):
        with pytest.raises(ValidationError):
            Settings(
                _env_file=None,
                data_dir=tmp_path / "invalid",
                model_request_timeout_seconds=invalid,
            )


def test_runtime_limits_are_explicit_and_bounded() -> None:
    settings = Settings(_env_file=None)

    assert settings.knowledge_max_results == 10
    assert settings.knowledge_max_query_chars == 2_000
    assert settings.runtime_max_tool_rounds == 8

    with pytest.raises(ValidationError):
        Settings(_env_file=None, knowledge_max_results=11)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, knowledge_max_query_chars=20_001)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, runtime_max_tool_rounds=17)


def test_object_store_defaults_are_local_and_bounded_for_development(
    tmp_path: Path,
) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path / "data")

    assert settings.app_env == "development"
    assert settings.backend_instance_count == 1
    assert settings.object_store_backend == "local"
    assert settings.object_store_local_root is None
    assert settings.object_store_max_read_bytes == 32 * 1024 * 1024
    assert settings.agent_home_max_file_bytes == 1_000_000
    assert settings.s3_addressing_style == "virtual"
    assert settings.log_level == "INFO"


@pytest.mark.parametrize("value", ["TRACE", "warn", "", "fatal"])
def test_settings_rejects_unknown_log_levels(value: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, log_level=value)


@pytest.mark.parametrize(
    "jwt_secret_key",
    [None, "", "   ", "auto-reign-local-dev-secret-change-me"],
)
def test_production_requires_an_explicit_nonlegacy_jwt_secret(
    jwt_secret_key: str | None,
) -> None:
    values = _complete_production_settings()
    values["jwt_secret_key"] = jwt_secret_key

    with pytest.raises(ValidationError, match="JWT_SECRET_KEY"):
        Settings(**values)


def test_production_requires_s3_object_storage() -> None:
    values = _complete_production_settings()
    values["object_store_backend"] = "local"

    with pytest.raises(ValidationError, match="OBJECT_STORE_BACKEND=s3"):
        Settings(**values)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("s3_bucket", "", "S3_BUCKET"),
        ("s3_bucket", "   ", "S3_BUCKET"),
        ("s3_endpoint_url", None, "S3_ENDPOINT_URL"),
        ("s3_endpoint_url", "   ", "S3_ENDPOINT_URL"),
        ("s3_access_key_id", None, "S3_ACCESS_KEY_ID"),
        ("s3_access_key_id", "   ", "S3_ACCESS_KEY_ID"),
        ("s3_secret_access_key", None, "S3_SECRET_ACCESS_KEY"),
        ("s3_secret_access_key", "   ", "S3_SECRET_ACCESS_KEY"),
    ],
)
def test_production_rejects_incomplete_s3_configuration(
    field: str,
    value: object,
    message: str,
) -> None:
    values = _complete_production_settings()
    values[field] = value

    with pytest.raises(ValidationError, match=message):
        Settings(**values)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"s3_namespace_app_exclusive": False}, "S3_NAMESPACE_APP_EXCLUSIVE"),
        ({"backend_instance_count": 2}, "single FastAPI instance"),
        ({"s3_addressing_style": "path"}, "virtual"),
    ],
)
def test_production_rejects_unsafe_runtime_topology(
    overrides: dict[str, object],
    message: str,
) -> None:
    values = _complete_production_settings() | overrides

    with pytest.raises(ValidationError, match=message):
        Settings(**values)


def test_production_accepts_one_complete_s3_backend(tmp_path: Path) -> None:
    values = _complete_production_settings()
    values["data_dir"] = tmp_path / "data"
    settings = Settings(**values)

    assert settings.app_env == "production"
    assert settings.object_store_backend == "s3"
    assert settings.backend_instance_count == 1
    assert settings.resolve_jwt_secret_key() == "production-signing-secret"
    assert not (tmp_path / "data").exists()


def test_empty_optional_object_store_environment_values_use_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OBJECT_STORE_LOCAL_ROOT", "")
    monkeypatch.setenv("S3_ENDPOINT_URL", "")
    monkeypatch.setenv("S3_ACCESS_KEY_ID", "")
    monkeypatch.setenv("S3_SECRET_ACCESS_KEY", "")

    settings = Settings(_env_file=None)

    assert settings.object_store_local_root is None
    assert settings.s3_endpoint_url is None
    assert settings.s3_access_key_id is None
    assert settings.s3_secret_access_key is None


def test_settings_rejects_business_limits_above_object_store_cap() -> None:
    with pytest.raises(ValidationError, match="OBJECT_STORE_MAX_READ_BYTES"):
        Settings(
            _env_file=None,
            object_store_max_read_bytes=4,
            attachment_max_bytes=5,
            attachment_max_parsed_chars=1,
        )

    with pytest.raises(ValidationError, match="OBJECT_STORE_MAX_READ_BYTES"):
        Settings(
            _env_file=None,
            object_store_max_read_bytes=7,
            attachment_max_bytes=1,
            attachment_max_parsed_chars=2,
        )


def test_agent_home_limit_cannot_exceed_object_store_read_limit() -> None:
    with pytest.raises(ValidationError, match="agent_home_max_file_bytes"):
        Settings(
            _env_file=None,
            object_store_max_read_bytes=999_999,
            attachment_max_bytes=1,
            attachment_max_parsed_chars=1,
            agent_home_max_file_bytes=1_000_000,
            knowledge_document_max_bytes=1,
            knowledge_max_parsed_chars=1,
        )


def test_agent_home_limit_may_equal_object_store_read_limit() -> None:
    settings = Settings(
        _env_file=None,
        object_store_max_read_bytes=1_000_000,
        attachment_max_bytes=1,
        attachment_max_parsed_chars=1,
        agent_home_max_file_bytes=1_000_000,
        knowledge_document_max_bytes=1,
        knowledge_max_parsed_chars=1,
    )

    assert settings.agent_home_max_file_bytes == settings.object_store_max_read_bytes


@pytest.mark.parametrize("reserve", [-1, 0, 16_000, 20_000])
def test_tool_result_reserve_must_be_positive_and_below_chat_budget(
    reserve: int,
) -> None:
    with pytest.raises(ValidationError, match="tool_result_token_reserve"):
        Settings(
            _env_file=None,
            chat_context_token_budget=16_000,
            tool_result_token_reserve=reserve,
        )


def test_tool_result_reserve_defaults_to_4096() -> None:
    assert Settings(_env_file=None).tool_result_token_reserve == 4_096


@pytest.mark.parametrize("reserve", [-1, 0, 16_000, 20_000])
def test_image_input_reserve_must_be_positive_and_below_chat_budget(
    reserve: int,
) -> None:
    with pytest.raises(ValidationError, match="image_input_token_reserve"):
        Settings(
            _env_file=None,
            chat_context_token_budget=16_000,
            image_input_token_reserve=reserve,
        )


def test_image_input_reserve_defaults_to_4096_per_image() -> None:
    assert Settings(_env_file=None).image_input_token_reserve == 4_096


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("backend_instance_count", 0),
        ("object_store_max_read_bytes", 0),
        ("attachment_max_bytes", 0),
        ("attachment_max_parsed_chars", 0),
        ("attachment_max_decompressed_bytes", 0),
        ("attachment_max_pdf_pages", 0),
        ("agent_home_max_file_bytes", 0),
        ("knowledge_document_max_bytes", 0),
        ("knowledge_max_parsed_chars", 0),
        ("knowledge_max_decompressed_bytes", 0),
        ("knowledge_max_pdf_pages", 0),
        ("knowledge_worker_processing_timeout_seconds", 0),
        ("knowledge_worker_poll_interval_seconds", 0),
    ],
)
def test_settings_rejects_non_positive_storage_limits(
    field: str,
    value: int,
) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


@pytest.mark.parametrize(
    "settings_kwargs",
    [
        {
            "object_store_max_read_bytes": 99,
            "knowledge_document_max_bytes": 100,
            "knowledge_max_parsed_chars": 1,
        },
        {
            "object_store_max_read_bytes": 399,
            "knowledge_document_max_bytes": 1,
            "knowledge_max_parsed_chars": 100,
        },
    ],
)
def test_object_store_read_limit_must_cover_knowledge_objects(
    settings_kwargs: dict[str, int],
) -> None:
    settings_kwargs.update(
        {
            "attachment_max_bytes": 1,
            "attachment_max_parsed_chars": 1,
            "agent_home_max_file_bytes": 1,
        }
    )
    with pytest.raises(ValidationError, match="OBJECT_STORE_MAX_READ_BYTES"):
        Settings(_env_file=None, **settings_kwargs)


def test_object_store_read_limit_accepts_exact_knowledge_boundary() -> None:
    settings = Settings(
        _env_file=None,
        object_store_max_read_bytes=400,
        knowledge_document_max_bytes=400,
        knowledge_max_parsed_chars=100,
        attachment_max_bytes=1,
        attachment_max_parsed_chars=1,
        agent_home_max_file_bytes=1,
    )

    assert settings.object_store_max_read_bytes == 400
