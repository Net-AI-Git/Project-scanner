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

    # LLM: Google AI Studio (Gemini). Set GOOGLE_API_KEY — get key at https://aistudio.google.com/apikey
    GOOGLE_API_KEY: SecretStr = SecretStr("")
    # Optional: override model (default gemini-2.0-flash). Examples: gemini-1.5-flash, gemini-1.5-pro, gemini-2.0-flash
    GOOGLE_MODEL: str = "gemini-2.0-flash"
    # Optional: override base URL (default Generative Language API)
    GOOGLE_BASE_URL: str = "https://generativelanguage.googleapis.com/v1beta"

    # Fallback: Nebius Token Factory (for evaluators). Used only if GOOGLE_API_KEY is not set.
    NEBIUS_API_KEY: SecretStr = SecretStr("")
    NEBIUS_BASE_URL: str = "https://api.tokenfactory.nebius.com/v1"
    NEBIUS_MODEL: str = "meta-llama/Meta-Llama-3.1-70B-Instruct"

    # Optional: GitHub token for higher API rate limit (5000/h vs 60/h). Set GITHUB_TOKEN to run real integration tests.
    GITHUB_TOKEN: SecretStr = SecretStr("")


def get_settings() -> Settings:
    """Return application settings (env-based)."""
    return Settings()
