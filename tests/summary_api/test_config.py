"""Targeted tests for summary_api.config: settings loading and no secret leakage."""

import os
from unittest.mock import patch

import pytest

from summary_api.config import Settings, get_settings


def test_get_settings_returns_settings_instance() -> None:
    """get_settings() returns an instance of Settings."""
    # Act
    settings = get_settings()
    # Assert
    assert isinstance(settings, Settings), f"Expected Settings instance, got {type(settings)}"


def test_settings_nebius_api_key_is_secret_str() -> None:
    """NEBIUS_API_KEY is SecretStr type so it is not leaked in logs/repr."""
    settings = get_settings()
    assert hasattr(settings.NEBIUS_API_KEY, "get_secret_value"), (
        "NEBIUS_API_KEY should be SecretStr with get_secret_value"
    )


def test_settings_loads_nebius_api_key_from_env() -> None:
    """When NEBIUS_API_KEY is set in env, Settings loads it (via get_secret_value)."""
    # Arrange
    with patch.dict(os.environ, {"NEBIUS_API_KEY": "test-key-123"}, clear=False):
        # Act
        settings = Settings()
        # Assert
        assert settings.NEBIUS_API_KEY.get_secret_value() == "test-key-123", (
            "Expected NEBIUS_API_KEY to be loaded from env"
        )


def test_settings_default_empty_key_when_env_not_set() -> None:
    """When NEBIUS_API_KEY is not set, default is empty string (for dev)."""
    # Arrange: ensure env does not set it (or clear it for this test)
    with patch.dict(os.environ, {"NEBIUS_API_KEY": ""}, clear=False):
        settings = Settings()
        # Assert
        assert settings.NEBIUS_API_KEY.get_secret_value() == ""


def test_settings_repr_does_not_contain_raw_key() -> None:
    """Settings repr/str must not expose raw API key (SecretStr masks it)."""
    with patch.dict(os.environ, {"NEBIUS_API_KEY": "secret-key-xyz"}, clear=False):
        settings = Settings()
        repr_str = repr(settings)
        # Assert: raw value must not appear in repr
        assert "secret-key-xyz" not in repr_str, "API key must not appear in repr (SecretStr masking)"
