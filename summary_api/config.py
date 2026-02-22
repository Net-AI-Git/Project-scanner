"""Application configuration loaded from environment. No API keys are hardcoded."""

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings loaded from environment variables. Sensitive fields use SecretStr (no leak in logs)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM API key — set via NEBIUS_API_KEY (or alternative provider env var when testing)
    NEBIUS_API_KEY: SecretStr = SecretStr("")


def get_settings() -> Settings:
    """Return application settings (env-based)."""
    return Settings()
