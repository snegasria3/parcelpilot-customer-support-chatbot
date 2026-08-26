from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    host: str = "127.0.0.1"
    port: int = 8000
    session_secret: str = Field(default="replace-with-at-least-48-random-characters", repr=False)
    session_ttl_minutes: int = 480
    action_ttl_minutes: int = 15

    llm_provider: str = "groq"
    groq_api_key: str = Field(default="", repr=False)
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_chat_model: str = "openai/gpt-oss-120b"
    allow_safe_llm_fallback: bool = True

    embedding_provider: str = "fastembed"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    vector_backend: str = "chroma"
    embedding_cache_dir: Path = PROJECT_ROOT / "runtime" / "model_cache"

    log_level: str = "INFO"
    max_message_chars: int = 4000
    login_rate_limit_per_minute: int = 8

    data_dir: Path = PROJECT_ROOT / "data"
    runtime_dir: Path = PROJECT_ROOT / "runtime"
    frontend_dir: Path = PROJECT_ROOT / "frontend"

    @property
    def database_path(self) -> Path:
        return self.runtime_dir / "parcelpilot.db"

    @property
    def vector_path(self) -> Path:
        return self.runtime_dir / "chroma"

    @property
    def workbook_path(self) -> Path:
        return self.data_dir / "source_data" / "ParcelPilot_Assessment_Data.xlsx"

    @property
    def source_docs_path(self) -> Path:
        return self.data_dir / "source_docs"

    @property
    def registry_path(self) -> Path:
        return self.data_dir / "source_registry.json"

    @property
    def rules_path(self) -> Path:
        return self.data_dir / "rules" / "policy_rules.json"

    @property
    def users_seed_path(self) -> Path:
        return self.data_dir / "seed" / "customer_users.json"

    def warnings(self) -> list[str]:
        warnings: list[str] = []
        if self.session_secret.startswith("replace-") or len(self.session_secret) < 32:
            warnings.append("SESSION_SECRET is a development placeholder or too short")
        if self.llm_provider == "groq" and not self.groq_api_key:
            warnings.append("GROQ_API_KEY is missing; schema-validated deterministic phrasing is active")
        if self.embedding_provider != "fastembed":
            warnings.append(f"Unsupported EMBEDDING_PROVIDER={self.embedding_provider}; expected fastembed")
        if self.vector_backend != "chroma":
            warnings.append(f"Unsupported VECTOR_BACKEND={self.vector_backend}; expected chroma")
        return warnings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
