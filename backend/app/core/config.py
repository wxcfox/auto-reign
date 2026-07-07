from functools import lru_cache
from pathlib import Path
import secrets

from pydantic_settings import BaseSettings, SettingsConfigDict


_LEGACY_DEFAULT_JWT_SECRET = "auto-reign-local-dev-secret-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    data_dir: Path = Path("data")
    database_url: str = (
        "mysql+pymysql://auto_reign:auto_reign@127.0.0.1:13306/auto_reign"
    )
    qdrant_url: str = "http://127.0.0.1:16333"
    qdrant_collection: str = "auto_reign_default"
    embedding_provider: str = "qwen"
    embedding_model: str = "text-embedding-v4"
    openai_api_key: str | None = None
    deepseek_api_key: str | None = None
    qwen_api_key: str | None = None
    openai_chat_models: str = "gpt-4.1-mini,gpt-4.1"
    deepseek_chat_models: str = "deepseek-chat"
    qwen_chat_models: str = "qwen-plus,qwen-max"
    deepseek_base_url: str = "https://api.deepseek.com"
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    jwt_secret_key: str | None = None
    access_token_expire_minutes: int = 60 * 24 * 7

    @property
    def workspace_dir(self) -> Path:
        return self.data_dir / "workspace"

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
