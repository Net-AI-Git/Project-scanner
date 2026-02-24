"""Shared pytest fixtures for Summary API tests."""

from pathlib import Path

import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient

from summary_api.main import app

# Load .env from project root so GITHUB_TOKEN (and others) are available in os.environ for tests
load_dotenv(Path(__file__).resolve().parent.parent / ".env")


@pytest.fixture
def client() -> TestClient:
    """FastAPI TestClient for hitting endpoints without starting a server."""
    return TestClient(app)
