"""Tests for summary_api.llm_client: LLMClientError (shared by scan and other services)."""

import pytest

from summary_api.clients.llm_client import LLMClientError, _is_llm_transient


def test_llm_client_error_has_message_and_is_transient() -> None:
    """LLMClientError stores message and is_transient."""
    err = LLMClientError("Rate limit", is_transient=True)
    assert err.message == "Rate limit"
    assert err.is_transient is True
    err2 = LLMClientError("Auth failed", is_transient=False)
    assert err2.message == "Auth failed"
    assert err2.is_transient is False


def test_is_llm_transient_true_for_transient_llm_error() -> None:
    """_is_llm_transient returns True for LLMClientError with is_transient=True."""
    err = LLMClientError("rate limit", is_transient=True)
    assert _is_llm_transient(err) is True


def test_is_llm_transient_false_for_permanent_llm_error() -> None:
    """_is_llm_transient returns False for LLMClientError with is_transient=False."""
    err = LLMClientError("auth failed", is_transient=False)
    assert _is_llm_transient(err) is False


def test_is_llm_transient_false_for_other_exception() -> None:
    """_is_llm_transient returns False for non-LLMClientError."""
    assert _is_llm_transient(ValueError("other")) is False
