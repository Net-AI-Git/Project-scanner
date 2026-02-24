"""LLM client: Google AI Studio (Gemini) or Nebius Token Factory to summarize repo context."""

from __future__ import annotations

import json
import re
from typing import Any, Literal

import httpx

# Google AI Studio (Gemini) defaults
GOOGLE_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
GOOGLE_MODEL = "gemini-2.0-flash"
# Nebius Token Factory (OpenAI-compatible) fallback
NEBIUS_BASE_URL = "https://api.tokenfactory.nebius.com/v1"
NEBIUS_MODEL = "meta-llama/Meta-Llama-3.1-70B-Instruct"

DEFAULT_TIMEOUT = 60.0
DEFAULT_MAX_TOKENS = 2048

# Prompt asks for structured JSON so we can parse summary, technologies, structure
SYSTEM_PROMPT = """You are a technical writer. Given repository file contents and structure, produce a short summary in the exact JSON format below. Use only the keys "summary", "technologies", and "structure". No other keys or markdown code fences.

Format:
{"summary": "1-3 sentences describing what the project does.", "technologies": ["Python", "FastAPI", ...], "structure": "Brief description of directory layout and key folders."}"""

USER_PROMPT_TEMPLATE = """Summarize this repository based on the following context.

{context}
"""


class LLMClientError(Exception):
    """Raised when the LLM API call fails: missing key, 401, 429, timeout, or invalid response.

    main.py can catch this and return an appropriate HTTP status and ErrorResponse.
    """

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def _build_messages(context: str) -> list[dict[str, str]]:
    """Build chat messages for OpenAI-compatible (Nebius) completion request."""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_PROMPT_TEMPLATE.format(context=context)},
    ]


def _build_gemini_prompt(context: str) -> str:
    """Single prompt text for Gemini (system + user merged)."""
    return f"{SYSTEM_PROMPT}\n\n{USER_PROMPT_TEMPLATE.format(context=context)}"


def _parse_structured_response(content: str) -> dict[str, Any]:
    """Parse LLM response into dict with summary, technologies, structure.

    Tries JSON first (including optional markdown code fence). Falls back to
    free-text: summary=content, technologies=[], structure=''.
    """
    if not (content or "").strip():
        return {"summary": "", "technologies": [], "structure": ""}

    text = content.strip()

    # Try to extract JSON from markdown code block if present
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


def _call_gemini(
    context: str,
    api_key: str,
    base_url: str,
    model: str,
    timeout: float,
    max_tokens: int,
) -> dict[str, Any]:
    """Call Google AI Studio (Gemini) generateContent API."""
    url = f"{base_url.rstrip('/')}/models/{model}:generateContent"
    headers = {"x-goog-api-key": api_key.strip(), "Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": _build_gemini_prompt(context)}]}],
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": 0.3,
            "responseMimeType": "application/json",
        },
    }
    with httpx.Client(timeout=timeout) as client:
        response = client.post(url, json=payload, headers=headers)
    if response.status_code == 401:
        raise LLMClientError("LLM API authentication failed (invalid or missing API key).")
    if response.status_code == 429:
        raise LLMClientError("LLM API rate limit exceeded. Try again later.")
    if response.status_code >= 500:
        raise LLMClientError(f"LLM API server error: {response.status_code}.")
    if response.status_code >= 400:
        try:
            body = response.json()
            err = body.get("error", {}) if isinstance(body.get("error"), dict) else {}
            msg = err.get("message", body.get("message", response.text)) or f"HTTP {response.status_code}"
        except Exception:
            msg = response.text or f"HTTP {response.status_code}"
        raise LLMClientError(f"LLM API error: {msg}")
    try:
        data = response.json()
    except Exception as e:
        raise LLMClientError(f"Invalid LLM API response (not JSON): {e}") from e
    candidates = data.get("candidates") or []
    if not candidates or not isinstance(candidates, list):
        raise LLMClientError("Invalid LLM API response: missing or empty candidates.")
    first = candidates[0]
    if not isinstance(first, dict):
        raise LLMClientError("Invalid LLM API response: invalid candidate.")
    content = first.get("content") or {}
    parts = content.get("parts") or []
    if not parts:
        raise LLMClientError("Invalid LLM API response: no content parts.")
    text = parts[0].get("text") if isinstance(parts[0], dict) else ""
    if text is None:
        text = ""
    if not isinstance(text, str):
        text = str(text)
    return _parse_structured_response(text)


def _call_nebius(
    context: str,
    api_key: str,
    base_url: str,
    model: str,
    timeout: float,
    max_tokens: int,
) -> dict[str, Any]:
    """Call Nebius Token Factory (OpenAI-compatible) chat/completions API."""
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": _build_messages(context),
        "max_tokens": max_tokens,
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
    }
    with httpx.Client(timeout=timeout) as client:
        response = client.post(url, json=payload, headers=headers)
    if response.status_code == 401:
        raise LLMClientError("LLM API authentication failed (invalid or missing API key).")
    if response.status_code == 429:
        raise LLMClientError("LLM API rate limit exceeded. Try again later.")
    if response.status_code >= 500:
        raise LLMClientError(f"LLM API server error: {response.status_code}.")
    if response.status_code >= 400:
        try:
            body = response.json()
            msg = body.get("error", body.get("message", response.text)) or f"HTTP {response.status_code}"
        except Exception:
            msg = response.text or f"HTTP {response.status_code}"
        raise LLMClientError(f"LLM API error: {msg}")
    try:
        data = response.json()
    except Exception as e:
        raise LLMClientError(f"Invalid LLM API response (not JSON): {e}") from e
    choices = data.get("choices") or []
    if not choices or not isinstance(choices, list):
        raise LLMClientError("Invalid LLM API response: missing or empty choices.")
    first = choices[0]
    message = first.get("message") if isinstance(first, dict) else None
    if not message or not isinstance(message, dict):
        raise LLMClientError("Invalid LLM API response: missing message in choices.")
    content = message.get("content")
    if content is None:
        content = ""
    if not isinstance(content, str):
        content = str(content)
    return _parse_structured_response(content)


def summarize_repo(
    context: str,
    *,
    api_key: str,
    provider: Literal["google", "nebius"] = "google",
    base_url: str | None = None,
    model: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> dict[str, Any]:
    """Call the LLM API to summarize repository context; return dict with summary, technologies, structure.

    Args:
        context: Prepared repo context string (from repo_processor).
        api_key: API key from config (GOOGLE_API_KEY or NEBIUS_API_KEY), never hardcoded.
        provider: "google" for Google AI Studio (Gemini), "nebius" for Nebius Token Factory.
        base_url: Override API base URL (defaults per provider if not set).
        model: Override model ID (defaults per provider if not set).
        timeout: Request timeout in seconds.
        max_tokens: Max tokens to generate.

    Returns:
        Dict with keys: summary (str), technologies (list[str]), structure (str).

    Raises:
        LLMClientError: Missing API key, 401, 429, timeout, or non-2xx response.
    """
    if not (api_key or "").strip():
        key_hint = "GOOGLE_API_KEY" if provider == "google" else "NEBIUS_API_KEY"
        raise LLMClientError(f"LLM API key is not configured. Set {key_hint} in the environment.")

    if base_url is None:
        base_url = GOOGLE_BASE_URL if provider == "google" else NEBIUS_BASE_URL
    if model is None:
        model = GOOGLE_MODEL if provider == "google" else NEBIUS_MODEL

    try:
        if provider == "google":
            return _call_gemini(context, api_key, base_url, model, timeout, max_tokens)
        return _call_nebius(context, api_key, base_url, model, timeout, max_tokens)
    except httpx.TimeoutException as e:
        raise LLMClientError(f"LLM API request timed out: {e}") from e
    except httpx.NetworkError as e:
        raise LLMClientError(f"LLM API network error: {e}") from e
