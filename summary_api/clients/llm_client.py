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

# Operational constants (not API keys, model names, or paths — per configuration rule).
DEFAULT_TIMEOUT = 120.0
DEFAULT_MAX_TOKENS = 4096
RETRY_ATTEMPTS = 3
RETRY_MIN_WAIT = 1
RETRY_MAX_WAIT = 60

from summary_api.prompts import prompts as _prompts

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
    """Build chat messages for full-repo summarization (legacy)."""
    return _prompts.build_repo_messages(context)


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


def _parse_folder_summary_response(content: str) -> dict[str, Any]:
    """Parse folder-summary LLM response to dict with single key 'summary'."""
    if not (content or "").strip():
        return {"summary": ""}
    text = content.strip()
    code_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if code_match:
        text = code_match.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {"summary": content.strip()}
    if not isinstance(data, dict):
        return {"summary": content.strip()}
    summary = data.get("summary")
    if not isinstance(summary, str):
        summary = str(summary) if summary is not None else ""
    return {"summary": summary}


def _parse_chat_response_content(response: httpx.Response) -> str:
    """Extract content string from chat completion response; raise LLMClientError on bad status or body."""
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
    return content


async def _post_messages(
    messages: list[dict[str, str]],
    api_key: str,
    base_url: str,
    model: str,
    timeout: float,
    max_tokens: int,
    response_format_json: bool = True,
) -> str:
    """Send messages to Nebius chat/completions; return raw content string. Shared by repo/folder/project/decider calls."""
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
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }
    if response_format_json:
        payload["response_format"] = {"type": "json_object"}
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, json=payload, headers=headers)
    return _parse_chat_response_content(response)


async def _call_nebius(
    context: str,
    api_key: str,
    base_url: str,
    model: str,
    timeout: float,
    max_tokens: int,
) -> dict[str, Any]:
    """Call Nebius for full-repo summarization (legacy); returns summary, technologies, structure."""
    messages = _build_messages(context)
    content = await _post_messages(messages, api_key, base_url, model, timeout, max_tokens)
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
) -> dict[str, Any]:
    """Call the LLM API to summarize repository context (async, with retry and circuit breaker).

    Transient errors (429, 5xx, timeout, network) are retried with exponential backoff
    and jitter. Circuit breaker opens after 5 failures, 60s recovery timeout.

    Args:
        context: Prepared repo context string (from repo_processor).
        api_key: API key from config (NEBIUS_API_KEY), never hardcoded.
        base_url: API base URL (from Settings.NEBIUS_BASE_URL).
        model: Model ID (from Settings.NEBIUS_MODEL).
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
    if not (base_url or "").strip():
        raise LLMClientError(
            "LLM base_url is not configured. Set NEBIUS_BASE_URL in the environment.",
            is_transient=False,
        )
    if not (model or "").strip():
        raise LLMClientError(
            "LLM model is not configured. Set NEBIUS_MODEL in the environment.",
            is_transient=False,
        )

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


@circuit(failure_threshold=5, recovery_timeout=60, expected_exception=LLMClientError)
@retry(
    retry=retry_if_exception(_is_llm_transient),
    stop=stop_after_attempt(RETRY_ATTEMPTS),
    wait=wait_random_exponential(multiplier=1, min=RETRY_MIN_WAIT, max=RETRY_MAX_WAIT),
    reraise=True,
)
async def summarize_folder(
    context: str,
    folder_name: str,
    *,
    api_key: str,
    base_url: str,
    model: str,
    timeout: float = DEFAULT_TIMEOUT,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> dict[str, Any]:
    """Call LLM to summarize one folder's context; returns dict with key 'summary'.

    Same retry and circuit breaker as summarize_repo.
    base_url and model must be provided from Settings (no hardcoded defaults).
    """
    if not (api_key or "").strip():
        raise LLMClientError(
            "LLM API key is not configured. Set NEBIUS_API_KEY in the environment.",
            is_transient=False,
        )
    if not (base_url or "").strip() or not (model or "").strip():
        raise LLMClientError(
            "base_url and model must be provided from Settings (NEBIUS_BASE_URL, NEBIUS_MODEL).",
            is_transient=False,
        )
    messages = _prompts.build_folder_summary_messages(folder_name, context)
    try:
        content = await _post_messages(messages, api_key, base_url, model, timeout, max_tokens)
        return _parse_folder_summary_response(content)
    except httpx.TimeoutException as e:
        raise LLMClientError(f"LLM API request timed out: {e}", is_transient=True) from e
    except httpx.NetworkError as e:
        raise LLMClientError(f"LLM API network error: {e}", is_transient=True) from e


@circuit(failure_threshold=5, recovery_timeout=60, expected_exception=LLMClientError)
@retry(
    retry=retry_if_exception(_is_llm_transient),
    stop=stop_after_attempt(RETRY_ATTEMPTS),
    wait=wait_random_exponential(multiplier=1, min=RETRY_MIN_WAIT, max=RETRY_MAX_WAIT),
    reraise=True,
)
async def summarize_batch(
    context: str,
    batch_label: str,
    *,
    api_key: str,
    base_url: str,
    model: str,
    timeout: float = DEFAULT_TIMEOUT,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> dict[str, Any]:
    """Call LLM to summarize one batch of file context; returns dict with key 'summary'.

    Used by the 4-node graph Summarizer node. Same retry and circuit breaker as summarize_folder.
    """
    if not (api_key or "").strip():
        raise LLMClientError(
            "LLM API key is not configured. Set NEBIUS_API_KEY in the environment.",
            is_transient=False,
        )
    if not (base_url or "").strip() or not (model or "").strip():
        raise LLMClientError(
            "base_url and model must be provided from Settings (NEBIUS_BASE_URL, NEBIUS_MODEL).",
            is_transient=False,
        )
    messages = _prompts.build_batch_summary_messages(batch_label, context)
    try:
        content = await _post_messages(messages, api_key, base_url, model, timeout, max_tokens)
        return _parse_folder_summary_response(content)
    except httpx.TimeoutException as e:
        raise LLMClientError(f"LLM API request timed out: {e}", is_transient=True) from e
    except httpx.NetworkError as e:
        raise LLMClientError(f"LLM API network error: {e}", is_transient=True) from e


@circuit(failure_threshold=5, recovery_timeout=60, expected_exception=LLMClientError)
@retry(
    retry=retry_if_exception(_is_llm_transient),
    stop=stop_after_attempt(RETRY_ATTEMPTS),
    wait=wait_random_exponential(multiplier=1, min=RETRY_MIN_WAIT, max=RETRY_MAX_WAIT),
    reraise=True,
)
async def summarize_project_from_folders(
    folder_summaries: list[dict[str, str]],
    *,
    api_key: str,
    base_url: str,
    model: str,
    timeout: float = DEFAULT_TIMEOUT,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> dict[str, Any]:
    """Call LLM to synthesize project summary from folder summaries; returns summary, technologies, structure.
    base_url and model must be provided from Settings (no hardcoded defaults).
    """
    if not (api_key or "").strip():
        raise LLMClientError(
            "LLM API key is not configured. Set NEBIUS_API_KEY in the environment.",
            is_transient=False,
        )
    if not (base_url or "").strip() or not (model or "").strip():
        raise LLMClientError(
            "base_url and model must be provided from Settings (NEBIUS_BASE_URL, NEBIUS_MODEL).",
            is_transient=False,
        )
    messages = _prompts.build_project_from_folders_messages(folder_summaries)
    try:
        content = await _post_messages(messages, api_key, base_url, model, timeout, max_tokens)
        return _parse_structured_response(content)
    except httpx.TimeoutException as e:
        raise LLMClientError(f"LLM API request timed out: {e}", is_transient=True) from e
    except httpx.NetworkError as e:
        raise LLMClientError(f"LLM API network error: {e}", is_transient=True) from e


async def decide_continue_or_done(
    previous_summaries: list[str],
    current_summary: str,
    *,
    api_key: str,
    base_url: str,
    model: str,
    timeout: float = DEFAULT_TIMEOUT,
    max_tokens: int = 32,
) -> str:
    """Call LLM to decide whether the latest batch summary adds enough new information to continue.

    Returns "continue" or "done". Uses plain-text response (no JSON) so the model can reply with one word.
    """
    if not (api_key or "").strip():
        raise LLMClientError(
            "LLM API key is not configured. Set NEBIUS_API_KEY in the environment.",
            is_transient=False,
        )
    if not (base_url or "").strip() or not (model or "").strip():
        raise LLMClientError(
            "base_url and model must be provided from Settings (NEBIUS_BASE_URL, NEBIUS_MODEL).",
            is_transient=False,
        )
    messages = _prompts.build_decider_messages(previous_summaries, current_summary)
    content = await _post_messages(
        messages, api_key, base_url, model, timeout, max_tokens, response_format_json=False
    )
    text = (content or "").strip().lower()
    if "done" in text:
        return "done"
    return "continue"


def _parse_plan_batches_response(
    content: str, allowed_paths: set[str], max_batches: int
) -> list[list[str]]:
    """Parse LLM plan-batches response to list of path batches. Validates paths are in allowed_paths; truncates to max_batches."""
    if not (content or "").strip():
        return []
    text = content.strip()
    code_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if code_match:
        text = code_match.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    raw = data.get("batches")
    if not isinstance(raw, list):
        return []
    batches: list[list[str]] = []
    for batch in raw[:max_batches]:
        if not isinstance(batch, list):
            continue
        valid = [p for p in batch if isinstance(p, str) and p in allowed_paths]
        if valid:
            batches.append(valid)
    return batches


@circuit(failure_threshold=5, recovery_timeout=60, expected_exception=LLMClientError)
@retry(
    retry=retry_if_exception(_is_llm_transient),
    stop=stop_after_attempt(RETRY_ATTEMPTS),
    wait=wait_random_exponential(multiplier=1, min=RETRY_MIN_WAIT, max=RETRY_MAX_WAIT),
    reraise=True,
)
async def plan_batches_from_structure(
    structure_text: str,
    paths: list[str],
    *,
    api_key: str,
    base_url: str,
    model: str,
    timeout: float = DEFAULT_TIMEOUT,
    max_tokens: int = 8192,
    max_batches: int = 20,
) -> list[list[str]]:
    """Call LLM to plan batches from repo structure; returns ordered list of path batches (subset of paths)."""
    if not (api_key or "").strip():
        raise LLMClientError(
            "LLM API key is not configured. Set NEBIUS_API_KEY in the environment.",
            is_transient=False,
        )
    if not (base_url or "").strip() or not (model or "").strip():
        raise LLMClientError(
            "base_url and model must be provided from Settings (NEBIUS_BASE_URL, NEBIUS_MODEL).",
            is_transient=False,
        )
    allowed = set(paths)
    messages = _prompts.build_plan_batches_messages(structure_text, paths)
    try:
        content = await _post_messages(
            messages, api_key, base_url, model, timeout, max_tokens
        )
        return _parse_plan_batches_response(content, allowed, max_batches)
    except httpx.TimeoutException as e:
        raise LLMClientError(
            f"LLM API request timed out: {e}", is_transient=True
        ) from e
    except httpx.NetworkError as e:
        raise LLMClientError(
            f"LLM API network error: {e}", is_transient=True
        ) from e
