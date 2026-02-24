"""Pydantic schemas for summarize API: request, success response, and error response."""

from typing import Literal

from pydantic import BaseModel, Field


class SummarizeRequest(BaseModel):
    """Request body for POST /summarize."""

    github_url: str = Field(..., description="Public GitHub repository URL to summarize")


class SummarizeResponse(BaseModel):
    """Success response: summary, technologies list, and structure description."""

    summary: str = Field(..., description="Short summary of the repository")
    technologies: list[str] = Field(..., description="List of technologies used")
    structure: str = Field(..., description="Description of repository structure")


class ErrorResponse(BaseModel):
    """Error response body: status and message."""

    status: Literal["error"] = Field(..., description="Always 'error' for error responses")
    message: str = Field(..., description="Human-readable error message")
