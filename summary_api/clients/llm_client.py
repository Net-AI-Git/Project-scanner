"""LLM client: Nebius Token Factory to summarize repo context (async, retry, circuit breaker)."""

from __future__ import annotations

import json
import logging
import re
from importlib.resources import files
from typing import Any

import httpx
from circuitbreaker import circuit
from jinja2 import Template
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_random_exponential,
)

logger = logging.getLogger(__name__)

# base_url and model must be passed from Settings (get_settings().NEBIUS_BASE_URL, NEBIUS_MODEL); no hardcoding here.
DEFAULT_TIMEOUT = 120.0
DEFAULT_MAX_TOKENS = 4096
RETRY_ATTEMPTS = 3
RETRY_MIN_WAIT = 1
RETRY_MAX_WAIT = 60

# Load Jinja2 prompt templates from summary_api.prompts package
_prompts_dir = files("summary_api.prompts")
_SYSTEM_TEMPLATE = Template((_prompts_dir / "system.j2").read_text(encoding="utf-8"))
_USER_TEMPLATE = Template((_prompts_dir / "user.j2").read_text(encoding="utf-8"))


def render_user_prompt_preview(placeholder: str = "<context>") -> str:
    """Render the user prompt with a placeholder for logging/debug (e.g. debug_repo_flow)."""
    return _USER_TEMPLATE.render(context=placeholder)


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
        {"role": "system", "content": _SYSTEM_TEMPLATE.render()},
        {"role": "user", "content": _USER_TEMPLATE.render(context=context)},
    ]


def _extract_json_text(content: str) -> str:
    """Extract JSON string from content, optionally inside markdown code fence."""
    text = (content or "").strip()
    code_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if code_match:
        return code_match.group(1).strip()
    return text


def _normalize_parsed_llm_data(data: dict[str, Any], fallback_summary: str) -> dict[str, Any]:
    """Normalize dict from LLM to keys summary, technologies, structure with correct types."""
    summary = data.get("summary")
    if summary is None:
        summary = str(data.get("description", "")) or fallback_summary
    if not isinstance(summary, str):
        summary = str(summary)

    technologies = data.get("technologies")
    if not isinstance(technologies, list):
        technologies = []
    technologies = [t for t in technologies if isinstance(t, str)]

    structure = data.get("structure")
    if structure is None:
        structure = ""
    if not isinstance(structure, str):
        structure = str(structure)

    return {"summary": summary, "technologies": technologies, "structure": structure}


def _parse_structured_response(content: str) -> dict[str, Any]:
    """Parse LLM response into dict with summary, technologies, structure.

    Tries JSON first (including optional markdown code fence). Falls back to
    free-text: summary=content, technologies=[], structure=''.
    """
    if not (content or "").strip():
        return {"summary": "", "technologies": [], "structure": ""}
    text = _extract_json_text(content)
    fallback = content.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {"summary": fallback, "technologies": [], "structure": ""}
    if not isinstance(data, dict):
        return {"summary": fallback, "technologies": [], "structure": ""}
    return _normalize_parsed_llm_data(data, fallback)


def _check_llm_response_status(response: httpx.Response) -> None:
    """Raise LLMClientError for non-2xx status; otherwise return None."""
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


def _extract_llm_content_from_response(response: httpx.Response) -> str:
    """Parse response JSON and return content string from choices[0].message.content."""
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
    if isinstance(first, dict) and first.get("finish_reason") == "length":
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
    return content


async def _call_nebius(
    context: str,
    api_key: str,
    base_url: str,
    model: str,
    timeout: float,
    max_tokens: int,
    *,
    client: httpx.AsyncClient | None = None,
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
    if client is not None:
        response = await client.post(url, json=payload, headers=headers, timeout=timeout)
    else:
        async with httpx.AsyncClient(timeout=timeout) as c:
            response = await c.post(url, json=payload, headers=headers)
    _check_llm_response_status(response)
    content = _extract_llm_content_from_response(response)
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
    base_url: str,
    model: str,
    timeout: float = DEFAULT_TIMEOUT,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Call the LLM API to summarize repository context (async, with retry and circuit breaker).

    Transient errors (429, 5xx, timeout, network) are retried with exponential backoff
    and jitter. Circuit breaker opens after 5 failures, 60s recovery timeout.

    When client is provided (e.g. app.state.http_client), uses it for connection
    pooling (R4); otherwise creates a new AsyncClient per call.

    Args:
        context: Prepared repo context string (from repo_processor).
        api_key: API key from config (NEBIUS_API_KEY), never hardcoded.
        base_url: API base URL; caller must pass from Settings.NEBIUS_BASE_URL.
        model: Model ID; caller must pass from Settings.NEBIUS_MODEL.
        timeout: Request timeout in seconds.
        max_tokens: Max tokens to generate.
        client: Optional shared AsyncClient for connection pooling.

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

    try:
        return await _call_nebius(
            context, api_key, base_url, model, timeout, max_tokens, client=client
        )
    except httpx.TimeoutException as e:
        raise LLMClientError(
            f"LLM API request timed out: {e}", is_transient=True
        ) from e
    except httpx.NetworkError as e:
        raise LLMClientError(
            f"LLM API network error: {e}", is_transient=True
        ) from e
