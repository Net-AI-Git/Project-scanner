"""Pydantic schemas for summarize API: request, success response, and error response."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class SummarizeRequest(BaseModel):
    """Request body for POST /summarize."""

    github_url: str = Field(..., description="Public GitHub repository URL to summarize")

    @field_validator("github_url")
    @classmethod
    def github_url_non_empty(cls, v: str) -> str:
        """Reject missing or empty github_url so validation returns a clear error.

        Why: API contract requires a non-empty string; Pydantic surfaces this as 400.
        What: Strips whitespace; raises ValueError if missing, not a string, or empty.

        Args:
            v: Raw value for github_url from request body.

        Returns:
            Stripped non-empty github_url string.

        Raises:
            ValueError: If v is missing, not a str, or empty/whitespace-only.
        """
        if not (v and isinstance(v, str) and v.strip()):
            raise ValueError("github_url is required and must be a non-empty string")
        return v.strip()


class SummarizeResponse(BaseModel):
    """Success response: summary, technologies list, and structure description."""

    summary: str = Field(..., description="Short summary of the repository")
    technologies: list[str] = Field(..., description="List of technologies used")
    structure: str = Field(..., description="Description of repository structure")


class ErrorResponse(BaseModel):
    """Error response body: status and message."""

    status: Literal["error"] = Field(..., description="Always 'error' for error responses")
    message: str = Field(..., description="Human-readable error message")
