"""Targeted tests for summary_api.main: root and POST /summarize endpoints."""

import pytest
from fastapi.testclient import TestClient

from summary_api.main import app

client = TestClient(app)


# --- GET / ---


def test_root_returns_200() -> None:
    """GET / returns HTTP 200."""
    # Act
    response = client.get("/")
    # Assert
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"


def test_root_returns_json_with_message_and_docs() -> None:
    """GET / returns JSON with message and docs keys."""
    response = client.get("/")
    data = response.json()
    assert "message" in data, "Response must contain 'message'"
    assert "docs" in data, "Response must contain 'docs'"
    assert data["docs"] == "/docs", f"Expected docs '/docs', got {data.get('docs')!r}"


# --- POST /summarize ---


def test_summarize_happy_path_returns_200() -> None:
    """POST /summarize with valid github_url returns 200."""
    # Arrange
    body = {"github_url": "https://github.com/psf/requests"}
    # Act
    response = client.post("/summarize", json=body)
    # Assert
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"


def test_summarize_returns_spec_fields() -> None:
    """POST /summarize response has summary, technologies, structure per API spec."""
    response = client.post("/summarize", json={"github_url": "https://github.com/a/b"})
    data = response.json()
    assert "summary" in data, "Response must contain 'summary'"
    assert "technologies" in data, "Response must contain 'technologies'"
    assert "structure" in data, "Response must contain 'structure'"
    assert isinstance(data["technologies"], list), "technologies must be a list"


def test_summarize_stub_content() -> None:
    """POST /summarize stub returns fixed stub text in summary and structure."""
    response = client.post("/summarize", json={"github_url": "https://github.com/x/y"})
    data = response.json()
    assert "Stub" in data["summary"], f"Expected stub summary, got {data['summary']!r}"
    assert "Stub" in data["structure"], f"Expected stub structure, got {data['structure']!r}"


def test_summarize_missing_github_url_returns_422() -> None:
    """POST /summarize without github_url returns 422 Unprocessable Entity."""
    # Arrange: body missing required field
    response = client.post("/summarize", json={})
    # Assert
    assert response.status_code == 422, f"Expected 422 for validation error, got {response.status_code}"


def test_summarize_invalid_body_not_json_returns_422() -> None:
    """POST /summarize with non-JSON or invalid type for github_url returns 422."""
    response = client.post(
        "/summarize",
        content=b"not json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code in (422, 400), f"Expected 422 or 400, got {response.status_code}"
