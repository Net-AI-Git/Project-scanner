"""Application configuration loaded from environment. No API keys are hardcoded."""

from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve .env from project root (parent of summary_api) so it loads regardless of cwd
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
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

    # Per-folder context cap when summarizing by folder (default 0 = use DEFAULT_MAX_CONTEXT_CHARS / num_folders in repo_processor).
    SUMMARY_MAX_CONTEXT_PER_FOLDER: int = 0

    # 4-node graph: batch by character budget and cap; hard iteration limit (multi-agent mandate).
    SUMMARY_BATCH_SIZE: int = 30
    SUMMARY_MAX_ITERATIONS: int = 20
    # Context-safe batching: fill batch until this many chars (or max files). Default 50k leaves room for prompt.
    SUMMARY_MAX_CONTEXT_CHARS_PER_BATCH: int = 50_000
    SUMMARY_MAX_FILES_PER_BATCH: int = 50
    # Cap each file's "count" when filling batch so one huge file does not force a 1-file batch (more LLM calls).
    # E.g. 25_000 => at least 2 large files per batch; Summarizer truncates. 0 = no cap.
    SUMMARY_MAX_CHARS_COUNT_PER_FILE: int = 25_000
    # Decider: stop when this fraction of eligible files is covered (e.g. 0.8 = 80%).
    SUMMARY_COVERAGE_THRESHOLD: float = 0.8
    # Decider: use LLM to ask "does latest batch change what the project does?" (default True).
    # Set to False to use word-overlap heuristic instead (less accurate for early stop).
    DECIDER_USE_LLM: bool = True

    # Structure-then-batch flow: parallel blob fetch and LLM planning limits.
    BATCH_FETCH_MAX_CONCURRENCY: int = 25
    PLAN_BATCHES_MAX_BATCHES: int = 20
    PLAN_BATCHES_MAX_PATHS: int = 2000

    # Paths: audit log and DLQ (append-only files). Defaults = project root when not set in env.
    AUDIT_LOG_PATH: str = ""
    DLQ_PATH: str = ""
    # Logging: set LOG_FORMAT=json for JSON structured logs.
    LOG_FORMAT: str = ""

    @model_validator(mode="after")
    def _set_default_paths(self) -> "Settings":
        """When AUDIT_LOG_PATH or DLQ_PATH are empty, use project root paths."""
        if not (self.AUDIT_LOG_PATH or "").strip():
            object.__setattr__(self, "AUDIT_LOG_PATH", str(_PROJECT_ROOT / "AUDIT.jsonl"))
        if not (self.DLQ_PATH or "").strip():
            object.__setattr__(self, "DLQ_PATH", str(_PROJECT_ROOT / "DLQ.jsonl"))
        return self


def get_settings() -> Settings:
    """Return application settings (env-based)."""
    return Settings()


def get_env_file_path() -> Path:
    """Return path to .env file used for loading (for logging)."""
    return _ENV_FILE
