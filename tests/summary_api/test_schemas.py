"""Targeted tests for summary_api.schemas: request/response models and serialization."""

import pytest

from summary_api.models.schemas import ErrorResponse


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
