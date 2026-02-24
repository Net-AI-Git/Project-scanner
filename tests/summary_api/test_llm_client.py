"""Tests for summary_api.llm_client: API key from caller, parsing, and error handling (mocked HTTP)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from summary_api.llm_client import (
    LLMClientError,
    _parse_folder_summary_response,
    _parse_structured_response,
    summarize_folder,
    summarize_project_from_folders,
    summarize_repo,
)


# --- API key and validation ---


def test_summarize_repo_missing_api_key_raises() -> None:
    """Empty or missing API key raises LLMClientError (key must come from config, not hardcoded)."""
    async def _run() -> None:
        with pytest.raises(LLMClientError) as exc_info:
            await summarize_repo("some context", api_key="")
        assert "API key" in exc_info.value.message or "NEBIUS_API_KEY" in exc_info.value.message
        with pytest.raises(LLMClientError):
            await summarize_repo("context", api_key="   ")
    asyncio.run(_run())


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
    async def _run() -> None:
        with patch("summary_api.llm_client.httpx.AsyncClient") as mock_client:
            mock_post = AsyncMock(return_value=httpx.Response(401, text="Unauthorized"))
            mock_instance = MagicMock()
            mock_instance.post = mock_post
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance
            with pytest.raises(LLMClientError) as exc_info:
                await summarize_repo("context", api_key="fake-key")
            assert "401" in exc_info.value.message or "auth" in exc_info.value.message.lower()
    asyncio.run(_run())


def test_summarize_repo_429_raises() -> None:
    """429 response raises LLMClientError (rate limit)."""
    async def _run() -> None:
        with patch("summary_api.llm_client.httpx.AsyncClient") as mock_client:
            mock_post = AsyncMock(return_value=httpx.Response(429, text="Too Many Requests"))
            mock_instance = MagicMock()
            mock_instance.post = mock_post
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance
            with pytest.raises(LLMClientError) as exc_info:
                await summarize_repo("context", api_key="fake-key")
            assert "429" in exc_info.value.message or "rate" in exc_info.value.message.lower()
    asyncio.run(_run())


def test_summarize_repo_timeout_raises() -> None:
    """Timeout raises LLMClientError."""
    async def _run() -> None:
        with patch("summary_api.llm_client.httpx.AsyncClient") as mock_client:
            mock_instance = MagicMock()
            mock_instance.post = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance
            with pytest.raises(LLMClientError) as exc_info:
                await summarize_repo("context", api_key="fake-key")
            assert "timeout" in exc_info.value.message.lower() or "timed" in exc_info.value.message.lower()
    asyncio.run(_run())


# --- Success: response parsed to summary, technologies, structure ---


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
    async def _run() -> None:
        with patch("summary_api.llm_client.httpx.AsyncClient") as mock_client:
            mock_post = AsyncMock(return_value=httpx.Response(200, json=body))
            mock_instance = MagicMock()
            mock_instance.post = mock_post
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance
            result = await summarize_repo("repo context", api_key="fake-key")
            assert result["summary"] == "HTTP library."
            assert result["technologies"] == ["Python", "urllib3"]
            assert result["structure"] == "src/ and tests/."
    asyncio.run(_run())


# --- Folder summary parsing and API ---


def test_parse_folder_summary_response_valid_json() -> None:
    """Valid JSON with summary key is parsed correctly."""
    raw = '{"summary": "This folder contains the main application code."}'
    out = _parse_folder_summary_response(raw)
    assert out["summary"] == "This folder contains the main application code."


def test_parse_folder_summary_response_fallback() -> None:
    """Non-JSON response falls back to summary=content."""
    raw = "Plain text summary."
    out = _parse_folder_summary_response(raw)
    assert out["summary"] == "Plain text summary."


def test_summarize_folder_success_returns_summary() -> None:
    """summarize_folder returns dict with summary key."""
    body = {
        "choices": [{"message": {"content": '{"summary": "Root config and docs."}'}, "finish_reason": "stop"}],
    }
    async def _run() -> None:
        with patch("summary_api.llm_client.httpx.AsyncClient") as mock_client:
            mock_post = AsyncMock(return_value=httpx.Response(200, json=body))
            mock_instance = MagicMock()
            mock_instance.post = mock_post
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance
            result = await summarize_folder("ctx", "(root)", api_key="fake-key")
            assert result["summary"] == "Root config and docs."
    asyncio.run(_run())


def test_summarize_project_from_folders_success_returns_three_fields() -> None:
    """summarize_project_from_folders returns summary, technologies, structure."""
    body = {
        "choices": [
            {
                "message": {
                    "content": '{"summary": "A web API.", "technologies": ["FastAPI"], "structure": "src/ and tests/."}',
                },
                "finish_reason": "stop",
            }
        ],
    }
    async def _run() -> None:
        with patch("summary_api.llm_client.httpx.AsyncClient") as mock_client:
            mock_post = AsyncMock(return_value=httpx.Response(200, json=body))
            mock_instance = MagicMock()
            mock_instance.post = mock_post
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance
            result = await summarize_project_from_folders(
                [{"folder": "(root)", "summary": "Root."}, {"folder": "src", "summary": "Source."}],
                api_key="fake-key",
            )
            assert result["summary"] == "A web API."
            assert result["technologies"] == ["FastAPI"]
            assert result["structure"] == "src/ and tests/."
    asyncio.run(_run())


