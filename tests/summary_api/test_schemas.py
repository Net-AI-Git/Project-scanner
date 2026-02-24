"""Targeted tests for summary_api.schemas: request/response models and serialization."""

import pytest
from pydantic import ValidationError

from summary_api.schemas import ErrorResponse, SummarizeRequest, SummarizeResponse


# --- SummarizeRequest ---


def test_summarize_request_happy_path() -> None:
    """SummarizeRequest accepts valid github_url and serializes to expected JSON."""
    # Arrange
    url = "https://github.com/psf/requests"
    # Act
    req = SummarizeRequest(github_url=url)
    dumped = req.model_dump()
    # Assert
    assert dumped["github_url"] == url, f"Expected github_url {url!r}, got {dumped.get('github_url')!r}"


def test_summarize_request_model_dump_has_github_url_key() -> None:
    """SummarizeRequest.model_dump() contains exactly the github_url key per API spec."""
    req = SummarizeRequest(github_url="https://github.com/a/b")
    keys = list(req.model_dump().keys())
    assert keys == ["github_url"], f"Expected keys ['github_url'], got {keys}"


def test_summarize_request_empty_string_rejected() -> None:
    """SummarizeRequest rejects empty string for github_url (edge case: returns 400 in API)."""
    with pytest.raises(ValidationError) as exc_info:
        SummarizeRequest(github_url="")
    assert "github_url" in str(exc_info.value) or "non-empty" in str(exc_info.value).lower()


def test_summarize_request_missing_github_url_raises() -> None:
    """SummarizeRequest raises when github_url is omitted."""
    with pytest.raises(Exception):  # Pydantic ValidationError
        SummarizeRequest()  # type: ignore[call-arg]


# --- SummarizeResponse ---


def test_summarize_response_happy_path() -> None:
    """SummarizeResponse holds summary, technologies, structure and serializes per spec."""
    # Arrange
    summary = "A library"
    technologies = ["Python", "httpx"]
    structure = "src/"
    # Act
    resp = SummarizeResponse(summary=summary, technologies=technologies, structure=structure)
    dumped = resp.model_dump()
    # Assert
    assert dumped["summary"] == summary
    assert dumped["technologies"] == technologies
    assert dumped["structure"] == structure


def test_summarize_response_model_dump_has_required_keys() -> None:
    """SummarizeResponse.model_dump() has exactly summary, technologies, structure per API spec."""
    resp = SummarizeResponse(summary="x", technologies=[], structure="y")
    keys = set(resp.model_dump().keys())
    required = {"summary", "technologies", "structure"}
    assert keys == required, f"Expected keys {required}, got {keys}"


def test_summarize_response_empty_technologies_list() -> None:
    """SummarizeResponse accepts empty technologies list."""
    resp = SummarizeResponse(summary="s", technologies=[], structure="t")
    assert resp.model_dump()["technologies"] == []


# --- ErrorResponse ---


def test_error_response_happy_path() -> None:
    """ErrorResponse has status 'error' and message, serializes per spec."""
    # Arrange
    message = "Not found"
    # Act
    err = ErrorResponse(status="error", message=message)
    dumped = err.model_dump()
    # Assert
    assert dumped["status"] == "error", f"Expected status 'error', got {dumped.get('status')!r}"
    assert dumped["message"] == message


def test_error_response_model_dump_has_status_and_message() -> None:
    """ErrorResponse.model_dump() has exactly status and message per API spec."""
    err = ErrorResponse(status="error", message="x")
    keys = set(err.model_dump().keys())
    assert keys == {"status", "message"}, f"Expected keys {{'status','message'}}, got {keys}"


def test_error_response_status_literal_rejects_non_error() -> None:
    """ErrorResponse accepts only status='error' (Literal)."""
    with pytest.raises(Exception):  # Pydantic ValidationError
        ErrorResponse(status="success", message="x")  # type: ignore[arg-type]
