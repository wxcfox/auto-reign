from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    data_dir: Path = Path("data")
    sqlite_path: Path = Path("data/app.db")
    chroma_dir: Path = Path("data/chroma")
    default_collection: str = "default"
    embedding_provider: str = "openai"
    embedding_model: str = "text-embedding-3-small"
    openai_api_key: str | None = None
    deepseek_api_key: str | None = None
    qwen_api_key: str | None = None
    openai_chat_models: str = "gpt-4.1-mini,gpt-4.1"
    deepseek_chat_models: str = "deepseek-chat"
    qwen_chat_models: str = "qwen-plus,qwen-max"
    deepseek_base_url: str = "https://api.deepseek.com"
    qwen_base_url: str = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    deterministic_model_fallback: bool = False

    def ensure_data_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "uploads").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "reports").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "memory").mkdir(parents=True, exist_ok=True)
        self.chroma_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_data_dirs()
    return settings
