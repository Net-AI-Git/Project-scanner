"""Application configuration loaded from environment. No API keys are hardcoded."""

from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve .env from project root (parent of summary_api) so it loads regardless of cwd
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    """Settings loaded from environment variables. Sensitive fields use SecretStr (no leak in logs)."""

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM: Nebius Token Factory. Set NEBIUS_API_KEY — get key at https://tokenfactory.nebius.com/
    NEBIUS_API_KEY: SecretStr = SecretStr("")
    NEBIUS_BASE_URL: str = "https://api.tokenfactory.nebius.com/v1"
    NEBIUS_MODEL: str = "meta-llama/Llama-3.3-70B-Instruct"
    # Max tokens for LLM response (summary + technologies + structure). Default 4096; increase if response is truncated.
    NEBIUS_MAX_TOKENS: int = 4096

    # Optional: GitHub token for higher API rate limit (5000/h vs 60/h). Set GITHUB_TOKEN to run real integration tests.
    GITHUB_TOKEN: SecretStr = SecretStr("")


def get_settings() -> Settings:
    """Return application settings loaded from environment and .env file.

    Why: Single source of truth for API keys and config; pydantic-settings handles validation.
    What: Instantiates Settings with env_file at project root; sensitive fields use SecretStr.

    Returns:
        Settings instance with NEBIUS_*, GITHUB_TOKEN, etc.

    Raises:
        ValidationError: If required env vars fail validation (pydantic-settings).
    """
    return Settings()


def get_env_file_path() -> Path:
    """Return path to .env file used for loading (for logging and diagnostics).

    Why: Callers can log which env file is in use without hardcoding paths.
    What: Returns the Path resolved from this module's parent (project root).

    Returns:
        Path to .env file (may or may not exist).

    Raises:
        None.
    """
    return _ENV_FILE
