from functools import lru_cache
from pathlib import Path
import secrets
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.limits import (
    DEFAULT_AGENT_HOME_MAX_FILE_BYTES,
    DEFAULT_ATTACHMENT_MAX_BYTES,
    DEFAULT_ATTACHMENT_MAX_DECOMPRESSED_BYTES,
    DEFAULT_ATTACHMENT_MAX_PARSED_CHARS,
    DEFAULT_ATTACHMENT_MAX_PDF_PAGES,
    DEFAULT_CHAT_CONTEXT_TOKEN_BUDGET,
    DEFAULT_IMAGE_INPUT_TOKEN_RESERVE,
    DEFAULT_KNOWLEDGE_DOCUMENT_MAX_BYTES,
    DEFAULT_KNOWLEDGE_MAX_DECOMPRESSED_BYTES,
    DEFAULT_KNOWLEDGE_MAX_QUERY_CHARS,
    DEFAULT_KNOWLEDGE_MAX_PARSED_CHARS,
    DEFAULT_KNOWLEDGE_MAX_PDF_PAGES,
    DEFAULT_KNOWLEDGE_MAX_RESULTS,
    DEFAULT_KNOWLEDGE_WORKER_POLL_INTERVAL_SECONDS,
    DEFAULT_KNOWLEDGE_WORKER_PROCESSING_TIMEOUT_SECONDS,
    DEFAULT_MODEL_REQUEST_TIMEOUT_SECONDS,
    DEFAULT_OBJECT_STORE_MAX_READ_BYTES,
    DEFAULT_RUNTIME_MAX_TOOL_ROUNDS,
    DEFAULT_TOOL_RESULT_TOKEN_RESERVE,
    MAX_AGENT_HOME_FILE_CONTENT_BYTES,
    MAX_KNOWLEDGE_QUERY_CHARS,
    MAX_KNOWLEDGE_TOP_K,
    MAX_RUNTIME_MAX_TOOL_ROUNDS,
)

_LEGACY_DEFAULT_JWT_SECRET = "auto-reign-local-dev-secret-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_ignore_empty=True,
        extra="ignore",
    )

    data_dir: Path = Path("data")
    init_data_dir: Path = Path("init_data")
    app_env: Literal["development", "production"] = "development"
    backend_instance_count: int = Field(default=1, ge=1)
    object_store_backend: Literal["local", "s3"] = "local"
    object_store_local_root: Path | None = None
    object_store_max_read_bytes: int = Field(
        default=DEFAULT_OBJECT_STORE_MAX_READ_BYTES,
        gt=0,
    )
    s3_endpoint_url: str | None = None
    s3_bucket: str = ""
    s3_namespace_app_exclusive: bool = False
    s3_region: str = "auto"
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    s3_session_token: str | None = None
    s3_key_prefix: str = ""
    s3_addressing_style: Literal["virtual", "path"] = "virtual"
    attachment_max_bytes: int = Field(default=DEFAULT_ATTACHMENT_MAX_BYTES, gt=0)
    attachment_max_parsed_chars: int = Field(
        default=DEFAULT_ATTACHMENT_MAX_PARSED_CHARS,
        gt=0,
    )
    attachment_max_decompressed_bytes: int = Field(
        default=DEFAULT_ATTACHMENT_MAX_DECOMPRESSED_BYTES,
        gt=0,
    )
    attachment_max_pdf_pages: int = Field(
        default=DEFAULT_ATTACHMENT_MAX_PDF_PAGES,
        gt=0,
    )
    agent_home_max_file_bytes: int = Field(
        default=DEFAULT_AGENT_HOME_MAX_FILE_BYTES,
        gt=0,
        le=MAX_AGENT_HOME_FILE_CONTENT_BYTES,
    )
    knowledge_document_max_bytes: int = Field(
        default=DEFAULT_KNOWLEDGE_DOCUMENT_MAX_BYTES,
        gt=0,
    )
    knowledge_max_parsed_chars: int = Field(
        default=DEFAULT_KNOWLEDGE_MAX_PARSED_CHARS,
        gt=0,
    )
    knowledge_max_decompressed_bytes: int = Field(
        default=DEFAULT_KNOWLEDGE_MAX_DECOMPRESSED_BYTES,
        gt=0,
    )
    knowledge_max_pdf_pages: int = Field(
        default=DEFAULT_KNOWLEDGE_MAX_PDF_PAGES,
        gt=0,
    )
    knowledge_worker_processing_timeout_seconds: int = Field(
        default=DEFAULT_KNOWLEDGE_WORKER_PROCESSING_TIMEOUT_SECONDS,
        gt=0,
    )
    knowledge_worker_poll_interval_seconds: float = Field(
        default=DEFAULT_KNOWLEDGE_WORKER_POLL_INTERVAL_SECONDS,
        gt=0,
        allow_inf_nan=False,
    )
    knowledge_max_results: int = Field(
        default=DEFAULT_KNOWLEDGE_MAX_RESULTS,
        ge=1,
        le=MAX_KNOWLEDGE_TOP_K,
    )
    knowledge_max_query_chars: int = Field(
        default=DEFAULT_KNOWLEDGE_MAX_QUERY_CHARS,
        gt=0,
        le=MAX_KNOWLEDGE_QUERY_CHARS,
    )
    database_url: str = "mysql+pymysql://auto_reign:auto_reign@127.0.0.1:13306/auto_reign"
    qdrant_url: str = "http://127.0.0.1:16333"
    qdrant_collection: str = "auto_reign_default"
    qdrant_api_key: str | None = None
    elasticsearch_url: str = "http://127.0.0.1:19200"
    elasticsearch_index: str = "auto_reign_knowledge"
    elasticsearch_username: str | None = None
    elasticsearch_password: str | None = None
    elasticsearch_api_key: str | None = None
    elasticsearch_request_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        allow_inf_nan=False,
    )
    embedding_provider: str = "qwen"
    embedding_model: str = "text-embedding-v4"
    openai_api_key: str | None = None
    deepseek_api_key: str | None = None
    qwen_api_key: str | None = None
    openai_chat_models: str = "gpt-4.1-mini,gpt-4.1"
    deepseek_chat_models: str = "deepseek-chat"
    qwen_chat_models: str = "qwen3.7-plus,qwen3.7-max"
    default_chat_provider: str = "qwen"
    deepseek_base_url: str = "https://api.deepseek.com"
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    jwt_secret_key: str | None = None
    access_token_expire_minutes: int = 60 * 24 * 7
    app_version: str = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    chat_context_token_budget: int = Field(
        default=DEFAULT_CHAT_CONTEXT_TOKEN_BUDGET,
        gt=0,
    )
    tool_result_token_reserve: int = Field(
        default=DEFAULT_TOOL_RESULT_TOKEN_RESERVE,
        gt=0,
    )
    image_input_token_reserve: int = Field(
        default=DEFAULT_IMAGE_INPUT_TOKEN_RESERVE,
        gt=0,
    )
    model_request_timeout_seconds: float = Field(
        default=DEFAULT_MODEL_REQUEST_TIMEOUT_SECONDS,
        gt=0,
        allow_inf_nan=False,
    )
    runtime_max_tool_rounds: int = Field(
        default=DEFAULT_RUNTIME_MAX_TOOL_ROUNDS,
        ge=1,
        le=MAX_RUNTIME_MAX_TOOL_ROUNDS,
    )

    @model_validator(mode="after")
    def validate_object_store_capacity(self) -> "Settings":
        required_bytes = max(
            self.attachment_max_bytes,
            self.attachment_max_parsed_chars * 4,
            self.agent_home_max_file_bytes,
            self.knowledge_document_max_bytes,
            self.knowledge_max_parsed_chars * 4,
        )
        if self.object_store_max_read_bytes < required_bytes:
            raise ValueError(
                "OBJECT_STORE_MAX_READ_BYTES must cover attachment limits, "
                "agent_home_max_file_bytes, and Knowledge persisted object limits"
            )
        if self.tool_result_token_reserve >= self.chat_context_token_budget:
            raise ValueError(
                "tool_result_token_reserve must be below chat_context_token_budget"
            )
        if self.image_input_token_reserve >= self.chat_context_token_budget:
            raise ValueError(
                "image_input_token_reserve must be below chat_context_token_budget"
            )
        return self

    @model_validator(mode="after")
    def validate_runtime_environment(self) -> "Settings":
        if self.app_env != "production":
            return self

        jwt_secret = (self.jwt_secret_key or "").strip()
        if not jwt_secret or jwt_secret == _LEGACY_DEFAULT_JWT_SECRET:
            raise ValueError(
                "JWT_SECRET_KEY must be explicitly configured in production."
            )
        if self.object_store_backend != "s3":
            raise ValueError("Production requires OBJECT_STORE_BACKEND=s3.")
        if self.backend_instance_count != 1:
            raise ValueError("v1 production requires a single FastAPI instance.")
        if not self.s3_namespace_app_exclusive:
            raise ValueError(
                "S3_NAMESPACE_APP_EXCLUSIVE=true is required in production."
            )
        if self.s3_addressing_style != "virtual":
            raise ValueError(
                "Production S3/OSS requires virtual addressing style."
            )

        required = {
            "S3_BUCKET": self.s3_bucket,
            "S3_ENDPOINT_URL": self.s3_endpoint_url,
            "S3_ACCESS_KEY_ID": self.s3_access_key_id,
            "S3_SECRET_ACCESS_KEY": self.s3_secret_access_key,
        }
        missing = [
            name
            for name, value in required.items()
            if not isinstance(value, str) or not value.strip()
        ]
        if missing:
            raise ValueError(
                "Missing production object storage setting: "
                + ", ".join(missing)
            )
        return self

    def ensure_data_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def resolve_jwt_secret_key(self) -> str:
        configured = (self.jwt_secret_key or "").strip()
        if configured and configured != _LEGACY_DEFAULT_JWT_SECRET:
            return configured

        secret_path = self.data_dir / ".secrets" / "jwt_secret"
        if secret_path.exists():
            return secret_path.read_text(encoding="utf-8").strip()

        secret_path.parent.mkdir(parents=True, exist_ok=True)
        secret = secrets.token_urlsafe(48)
        secret_path.write_text(f"{secret}\n", encoding="utf-8")
        secret_path.chmod(0o600)
        return secret


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_data_dirs()
    return settings
