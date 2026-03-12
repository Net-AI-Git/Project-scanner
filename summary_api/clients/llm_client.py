"""LLM client: shared exception for LLM/agent calls (scan and other services use their own LLM clients)."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


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
