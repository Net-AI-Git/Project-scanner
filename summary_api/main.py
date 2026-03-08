"""Entry point: re-export app so uvicorn summary_api.main:app still works."""
from summary_api.api.main import app
