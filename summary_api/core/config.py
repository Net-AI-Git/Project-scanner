"""Application configuration loaded from environment. No API keys are hardcoded."""

from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve .env from project root (parent of summary_api) so it loads regardless of cwd
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"


def _default_audit_log_path() -> str:
    return str(_PROJECT_ROOT / "AUDIT.jsonl")


def _default_dlq_path() -> str:
    return str(_PROJECT_ROOT / "DLQ.jsonl")


def _default_scan_reports_dir() -> str:
    return str(_PROJECT_ROOT / "reports")


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

    # Paths: audit log and DLQ; defaults under project root. Override via AUDIT_LOG_PATH / DLQ_PATH env.
    AUDIT_LOG_PATH: str = ""
    DLQ_PATH: str = ""

    # Logging: set LOG_FORMAT=json for JSON structured logs.
    LOG_FORMAT: str = ""

    # Optional: GitHub API base URL (default public API). Override for enterprise or testing.
    GITHUB_API_BASE: str = "https://api.github.com"

    # Optional: max context chars for repo_processor. Default 60_000.
    MAX_CONTEXT_CHARS: int = 60_000

    # Context compression (context-compression-and-optimization): model limit in tokens. Default 128k.
    CONTEXT_LIMIT_TOKENS: int = 128_000

    # Security scan: directory where MD reports are saved. Default project root / reports.
    SCAN_REPORTS_DIR: str = ""

    @field_validator("NEBIUS_API_KEY", mode="after")
    @classmethod
    def nebius_api_key_non_whitespace_if_set(cls, v: SecretStr) -> SecretStr:
        """If NEBIUS_API_KEY is set, it must not be only whitespace (sane at startup)."""
        raw = v.get_secret_value()
        if raw and not raw.strip():
            raise ValueError("NEBIUS_API_KEY must not be only whitespace when set")
        return v

    @field_validator("AUDIT_LOG_PATH", mode="after")
    @classmethod
    def audit_log_path_default(cls, v: str) -> str:
        """Use project-root AUDIT.jsonl when not set."""
        return v.strip() or _default_audit_log_path()

    @field_validator("DLQ_PATH", mode="after")
    @classmethod
    def dlq_path_default(cls, v: str) -> str:
        """Use project-root DLQ.jsonl when not set."""
        return v.strip() or _default_dlq_path()

    @field_validator("SCAN_REPORTS_DIR", mode="after")
    @classmethod
    def scan_reports_dir_default(cls, v: str) -> str:
        """Use project-root/reports when not set."""
        return v.strip() or _default_scan_reports_dir()

    @field_validator("NEBIUS_MAX_TOKENS", "MAX_CONTEXT_CHARS", "CONTEXT_LIMIT_TOKENS", mode="after")
    @classmethod
    def positive_int(cls, v: int) -> int:
        """Numeric config must be positive."""
        if v is not None and v <= 0:
            raise ValueError("must be positive")
        return v


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
