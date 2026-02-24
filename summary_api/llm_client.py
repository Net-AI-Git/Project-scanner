"""LLM client: Nebius Token Factory to summarize repo context (async, retry, circuit breaker)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx
from circuitbreaker import circuit
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_random_exponential,
)

logger = logging.getLogger(__name__)

# Nebius Token Factory (OpenAI-compatible). See https://tokenfactory.nebius.com/
NEBIUS_BASE_URL = "https://api.tokenfactory.nebius.com/v1"
NEBIUS_MODEL = "meta-llama/Llama-3.3-70B-Instruct"

DEFAULT_TIMEOUT = 120.0
DEFAULT_MAX_TOKENS = 4096
RETRY_ATTEMPTS = 3
RETRY_MIN_WAIT = 1
RETRY_MAX_WAIT = 60

# Prompt asks for structured JSON so we can parse summary, technologies, structure
SYSTEM_PROMPT = """You are a technical writer. Given repository file contents and structure, produce a short summary in the exact JSON format below. Use only the keys "summary", "technologies", and "structure". No other keys or markdown code fences.

Format:
{"summary": "1-3 sentences describing what the project does.", "technologies": ["Python", "FastAPI", ...], "structure": "Brief description of directory layout and key folders."}"""

USER_PROMPT_TEMPLATE = """Summarize this repository based on the following context.

{context}
"""


class LLMClientError(Exception):
    """Raised when the LLM API call fails.

    main.py can catch this and return an appropriate HTTP status and ErrorResponse.
    is_transient: True for errors that may succeed on retry (429, timeout, 5xx, network).
    """

    def __init__(self, message: str, is_transient: bool = False) -> None:
        self.message = message
        self.is_transient = is_transient
        super().__init__(message)


def _is_llm_transient(exc: BaseException) -> bool:
    """Return True if the exception is a transient LLM error (retryable)."""
    return isinstance(exc, LLMClientError) and getattr(exc, "is_transient", False)


def _build_messages(context: str) -> list[dict[str, str]]:
    """Build chat messages for OpenAI-compatible (Nebius) completion request."""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_PROMPT_TEMPLATE.format(context=context)},
    ]


def _parse_structured_response(content: str) -> dict[str, Any]:
    """Parse LLM response into dict with summary, technologies, structure.

    Tries JSON first (including optional markdown code fence). Falls back to
    free-text: summary=content, technologies=[], structure=''.
    """
    if not (content or "").strip():
        return {"summary": "", "technologies": [], "structure": ""}

    text = content.strip()
    code_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if code_match:
        text = code_match.group(1).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {
            "summary": content.strip(),
            "technologies": [],
            "structure": "",
        }

    if not isinstance(data, dict):
        return {"summary": content.strip(), "technologies": [], "structure": ""}

    summary = data.get("summary")
    technologies = data.get("technologies")
    structure = data.get("structure")

    if summary is None:
        summary = str(data.get("description", "")) or content.strip()
    if not isinstance(summary, str):
        summary = str(summary)

    if not isinstance(technologies, list):
        technologies = []
    technologies = [t for t in technologies if isinstance(t, str)]

    if structure is None:
        structure = ""
    if not isinstance(structure, str):
        structure = str(structure)

    return {"summary": summary, "technologies": technologies, "structure": structure}


async def _call_nebius(
    context: str,
    api_key: str,
    base_url: str,
    model: str,
    timeout: float,
    max_tokens: int,
) -> dict[str, Any]:
    """Call Nebius Token Factory (OpenAI-compatible) chat/completions API (async)."""
    messages = _build_messages(context)
    logger.info(
        "=== Sending to LLM (provider=nebius, model=%s) — full messages below ===\n%s\n=== end LLM messages ===",
        model,
        json.dumps(
            [{"role": m["role"], "content": m["content"]} for m in messages],
            ensure_ascii=False,
            indent=2,
        ),
    )
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, json=payload, headers=headers)

    if response.status_code == 401:
        raise LLMClientError(
            "LLM API authentication failed (invalid or missing API key).",
            is_transient=False,
        )
    if response.status_code == 429:
        raise LLMClientError(
            "LLM API rate limit exceeded. Try again later.", is_transient=True
        )
    if response.status_code >= 500:
        raise LLMClientError(
            f"LLM API server error: {response.status_code}.", is_transient=True
        )
    if response.status_code >= 400:
        try:
            body = response.json()
            msg = (
                body.get("error", body.get("message", response.text))
                or f"HTTP {response.status_code}"
            )
        except Exception:
            msg = response.text or f"HTTP {response.status_code}"
        raise LLMClientError(f"LLM API error: {msg}", is_transient=False)

    try:
        data = response.json()
    except Exception as e:
        raise LLMClientError(
            f"Invalid LLM API response (not JSON): {e}", is_transient=False
        ) from e
    choices = data.get("choices") or []
    if not choices or not isinstance(choices, list):
        raise LLMClientError(
            "Invalid LLM API response: missing or empty choices.", is_transient=False
        )
    first = choices[0]
    finish_reason = first.get("finish_reason") if isinstance(first, dict) else None
    if finish_reason == "length":
        logger.warning(
            "LLM response was truncated (finish_reason=length). Consider increasing max_tokens."
        )
    message = first.get("message") if isinstance(first, dict) else None
    if not message or not isinstance(message, dict):
        raise LLMClientError(
            "Invalid LLM API response: missing message in choices.", is_transient=False
        )
    content = message.get("content")
    if content is None:
        content = ""
    if not isinstance(content, str):
        content = str(content)
    return _parse_structured_response(content)


@circuit(failure_threshold=5, recovery_timeout=60, expected_exception=LLMClientError)
@retry(
    retry=retry_if_exception(_is_llm_transient),
    stop=stop_after_attempt(RETRY_ATTEMPTS),
    wait=wait_random_exponential(multiplier=1, min=RETRY_MIN_WAIT, max=RETRY_MAX_WAIT),
    reraise=True,
)
async def summarize_repo(
    context: str,
    *,
    api_key: str,
    base_url: str | None = None,
    model: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> dict[str, Any]:
    """Call the LLM API to summarize repository context (async, with retry and circuit breaker).

    Transient errors (429, 5xx, timeout, network) are retried with exponential backoff
    and jitter. Circuit breaker opens after 5 failures, 60s recovery timeout.

    Args:
        context: Prepared repo context string (from repo_processor).
        api_key: API key from config (NEBIUS_API_KEY), never hardcoded.
        base_url: Override API base URL (default NEBIUS_BASE_URL).
        model: Override model ID (default NEBIUS_MODEL).
        timeout: Request timeout in seconds.
        max_tokens: Max tokens to generate.

    Returns:
        Dict with keys: summary (str), technologies (list[str]), structure (str).

    Raises:
        LLMClientError: Missing API key, 401, or non-2xx after retries.
            is_transient True for retryable errors.
    """
    if not (api_key or "").strip():
        raise LLMClientError(
            "LLM API key is not configured. Set NEBIUS_API_KEY in the environment.",
            is_transient=False,
        )

    if base_url is None:
        base_url = NEBIUS_BASE_URL
    if model is None:
        model = NEBIUS_MODEL

    try:
        return await _call_nebius(
            context, api_key, base_url, model, timeout, max_tokens
        )
    except httpx.TimeoutException as e:
        raise LLMClientError(
            f"LLM API request timed out: {e}", is_transient=True
        ) from e
    except httpx.NetworkError as e:
        raise LLMClientError(
            f"LLM API network error: {e}", is_transient=True
        ) from e
