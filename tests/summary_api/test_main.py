"""Targeted tests for summary_api.main: root and POST /summarize endpoints (real GitHub + LLM when tokens set)."""

import pytest
from fastapi.testclient import TestClient

from summary_api.config import get_settings
from summary_api.main import app

client = TestClient(app)


def _has_github_token() -> bool:
    """True if GITHUB_TOKEN is set via Settings (for real API rate limit)."""
    return bool((get_settings().GITHUB_TOKEN.get_secret_value() or "").strip())


def _has_llm_key() -> bool:
    """True if Nebius LLM API key is set via Settings (for full summarize flow)."""
    return bool((get_settings().NEBIUS_API_KEY.get_secret_value() or "").strip())


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


@pytest.mark.skipif(
    not (_has_github_token() and _has_llm_key()),
    reason="Set GITHUB_TOKEN and NEBIUS_API_KEY for full flow",
)
def test_summarize_happy_path_real_api() -> None:
    """POST /summarize with valid github_url: real GitHub + LLM. 200 + spec fields, or 429 (rate limit) + error body."""
    response = client.post("/summarize", json={"github_url": "https://github.com/Net-AI-Git/Project-scanner"})
    data = response.json()
    if response.status_code == 200:
        assert "summary" in data, "Response must contain 'summary'"
        assert "technologies" in data, "Response must contain 'technologies'"
        assert "structure" in data, "Response must contain 'structure'"
        assert isinstance(data["technologies"], list), "technologies must be a list"
        assert isinstance(data["summary"], str) and len(data["summary"]) > 0
        assert isinstance(data["structure"], str) and len(data["structure"]) > 0
    elif response.status_code == 429:
        # Rate limit is per account/key, not per test run — previous usage can exhaust quota
        assert data.get("status") == "error" and "message" in data
    else:
        pytest.fail(f"Unexpected status {response.status_code}: {data}")


def test_summarize_missing_github_url_returns_400_and_error_body() -> None:
    """POST /summarize without github_url returns 400 and spec error body (status, message)."""
    response = client.post("/summarize", json={})
    assert response.status_code == 400, f"Expected 400, got {response.status_code}"
    data = response.json()
    assert data.get("status") == "error"
    assert "message" in data


def test_summarize_github_url_not_string_returns_400_and_error_body() -> None:
    """POST /summarize with github_url not a string (e.g. number) returns 400 and spec error body."""
    response = client.post("/summarize", json={"github_url": 123})
    assert response.status_code == 400
    data = response.json()
    assert data.get("status") == "error"
    assert "message" in data


def test_summarize_github_url_empty_string_returns_400_and_error_body() -> None:
    """POST /summarize with empty github_url returns 400 and spec error body."""
    response = client.post("/summarize", json={"github_url": ""})
    assert response.status_code == 400
    data = response.json()
    assert data.get("status") == "error"
    assert "message" in data


def test_summarize_invalid_body_not_json_returns_400_or_422() -> None:
    """POST /summarize with non-JSON body returns 422 or 400."""
    response = client.post(
        "/summarize",
        content=b"not json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code in (422, 400), f"Expected 422 or 400, got {response.status_code}"


def test_summarize_invalid_github_url_returns_400_and_error_body() -> None:
    """POST /summarize with non-GitHub URL returns 400 and spec error body (status, message)."""
    response = client.post(
        "/summarize",
        json={"github_url": "https://gitlab.com/owner/repo"},
    )
    assert response.status_code == 400
    data = response.json()
    assert data.get("status") == "error"
    assert "message" in data
    assert "Invalid GitHub URL" in data["message"]


@pytest.mark.skipif(not _has_github_token(), reason="Set GITHUB_TOKEN to run real GitHub API tests")
def test_summarize_nonexistent_repo_returns_404_and_error_body() -> None:
    """POST /summarize with nonexistent repo returns 404 and spec error body (real API)."""
    response = client.post(
        "/summarize",
        json={"github_url": "https://github.com/this-org-xyz-123/this-repo-xyz-456"},
    )
    assert response.status_code == 404
    data = response.json()
    assert data.get("status") == "error"
    assert "message" in data
