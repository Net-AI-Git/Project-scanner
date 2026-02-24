"""Shared pytest fixtures for Summary API tests."""

import pytest
from fastapi.testclient import TestClient

from summary_api.main import app


@pytest.fixture
def client() -> TestClient:
    """FastAPI TestClient for hitting endpoints without starting a server."""
    return TestClient(app)
