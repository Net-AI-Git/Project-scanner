"""Tests for summary_api.llm_client: API key from caller, parsing, and error handling (mocked HTTP)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from summary_api.clients.llm_client import (
    LLMClientError,
    _parse_structured_response,
    summarize_repo,
)


# --- API key and validation ---


def test_summarize_repo_missing_api_key_raises() -> None:
    """Empty or missing API key raises LLMClientError (key must come from config, not hardcoded)."""
    with pytest.raises(LLMClientError) as exc_info:
        asyncio.run(summarize_repo("some context", api_key=""))
    assert "API key" in exc_info.value.message or "NEBIUS_API_KEY" in exc_info.value.message

    with pytest.raises(LLMClientError):
        asyncio.run(summarize_repo("context", api_key="   "))


# --- Success: response parsed to summary, technologies, structure ---
# Run before 401/429/timeout tests so the circuit breaker is still closed.


def test_summarize_repo_success_nebius_returns_three_fields() -> None:
    """Successful Nebius (OpenAI-shaped) response is parsed into summary, technologies, structure."""
    body = {
        "choices": [
            {
                "message": {
                    "content": '{"summary": "HTTP library.", "technologies": ["Python", "urllib3"], "structure": "src/ and tests/."}',
                },
                "finish_reason": "stop",
            }
        ],
    }
    with patch("summary_api.clients.llm_client.httpx.AsyncClient") as mock_async_client:
        mock_instance = MagicMock()
        mock_instance.post = AsyncMock(return_value=httpx.Response(200, json=body))
        mock_async_client.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_async_client.return_value.__aexit__ = AsyncMock(return_value=None)

        result = asyncio.run(summarize_repo("repo context", api_key="fake-key"))
        assert result["summary"] == "HTTP library."
        assert result["technologies"] == ["Python", "urllib3"]
        assert result["structure"] == "src/ and tests/."


# --- Parsing: structured output ---


def test_parse_structured_response_valid_json() -> None:
    """Valid JSON with summary, technologies, structure is parsed correctly."""
    raw = '{"summary": "A library.", "technologies": ["Python"], "structure": "Flat."}'
    out = _parse_structured_response(raw)
    assert out["summary"] == "A library."
    assert out["technologies"] == ["Python"]
    assert out["structure"] == "Flat."


def test_parse_structured_response_json_with_code_fence() -> None:
    """JSON inside markdown code block is extracted and parsed."""
    raw = '```json\n{"summary": "X", "technologies": [], "structure": "Y"}\n```'
    out = _parse_structured_response(raw)
    assert out["summary"] == "X"
    assert out["structure"] == "Y"


def test_parse_structured_response_fallback_free_text() -> None:
    """Non-JSON response falls back to summary=content, technologies=[], structure=''."""
    raw = "Just plain text summary."
    out = _parse_structured_response(raw)
    assert out["summary"] == "Just plain text summary."
    assert out["technologies"] == []
    assert out["structure"] == ""


def test_parse_structured_response_partial_dict() -> None:
    """Dict missing some keys uses defaults for missing fields."""
    raw = '{"summary": "Only summary"}'
    out = _parse_structured_response(raw)
    assert out["summary"] == "Only summary"
    assert out["technologies"] == []
    assert out["structure"] == ""


# --- HTTP errors: 401, 429, timeout ---


def test_summarize_repo_401_raises() -> None:
    """401 response raises LLMClientError with auth message."""
    with patch("summary_api.clients.llm_client.httpx.AsyncClient") as mock_async_client:
        mock_instance = MagicMock()
        mock_instance.post = AsyncMock(return_value=httpx.Response(401, text="Unauthorized"))
        mock_async_client.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_async_client.return_value.__aexit__ = AsyncMock(return_value=None)

        with pytest.raises(LLMClientError) as exc_info:
            asyncio.run(summarize_repo("context", api_key="fake-key"))
        assert "401" in exc_info.value.message or "auth" in exc_info.value.message.lower()


def test_summarize_repo_429_raises() -> None:
    """429 response raises LLMClientError (rate limit)."""
    with patch("summary_api.clients.llm_client.httpx.AsyncClient") as mock_async_client:
        mock_instance = MagicMock()
        mock_instance.post = AsyncMock(return_value=httpx.Response(429, text="Too Many Requests"))
        mock_async_client.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_async_client.return_value.__aexit__ = AsyncMock(return_value=None)

        with pytest.raises(LLMClientError) as exc_info:
            asyncio.run(summarize_repo("context", api_key="fake-key"))
        assert "429" in exc_info.value.message or "rate" in exc_info.value.message.lower()


def test_summarize_repo_timeout_raises() -> None:
    """Timeout raises LLMClientError."""
    with patch("summary_api.clients.llm_client.httpx.AsyncClient") as mock_async_client:
        mock_instance = MagicMock()
        mock_instance.post = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
        mock_async_client.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_async_client.return_value.__aexit__ = AsyncMock(return_value=None)

        with pytest.raises(LLMClientError) as exc_info:
            asyncio.run(summarize_repo("context", api_key="fake-key"))
        assert "timeout" in exc_info.value.message.lower() or "timed" in exc_info.value.message.lower()

