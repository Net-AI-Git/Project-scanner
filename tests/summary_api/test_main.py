"""Targeted tests for summary_api.main: root and health endpoints."""

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


# --- GET /health/live, /health/ready ---


def test_health_live_returns_200_and_ok() -> None:
    """GET /health/live returns 200 and status ok (liveness probe)."""
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_ready_returns_200_when_ready() -> None:
    """GET /health/ready returns 200 and status ok when app is ready (readiness probe)."""
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
