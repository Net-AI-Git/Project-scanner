"""Pydantic schemas for summarize and scan API: request, success response, and error response."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


# --- Summarize API ---


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


# --- Security scan API ---

Severity = Literal["High", "Medium", "Low"]


class ScanRequest(BaseModel):
    """Request body for POST /scan (security vulnerability scan)."""

    github_url: str = Field(..., description="Public GitHub repository URL to scan for vulnerabilities")

    @field_validator("github_url")
    @classmethod
    def github_url_non_empty(cls, v: str) -> str:
        if not (v and isinstance(v, str) and v.strip()):
            raise ValueError("github_url is required and must be a non-empty string")
        return v.strip()


class Finding(BaseModel):
    """A single security finding: file, location, severity, category, description, recommendation."""

    file_path: str = Field(..., description="Relative path of the file")
    line_or_region: str = Field(..., description="Line number or code region (e.g. '42' or '42-45')")
    severity: Severity = Field(..., description="High, Medium, or Low")
    category: str = Field(..., description="Vulnerability category (e.g. hardcoded_secret, sql_injection)")
    description: str = Field(..., description="Short description of the finding")
    recommendation: str = Field(..., description="How to fix or mitigate")


class ScanOutput(BaseModel):
    """Raw LLM output for one file: list of findings (file_path set by caller)."""

    findings: list[Finding] = Field(default_factory=list, description="Findings for the analyzed file")


class SectionFindings(BaseModel):
    """Findings for one section (one file). Used by workers and for queue → MD writer."""

    file_path: str = Field(..., description="Relative path of the scanned file")
    findings: list[Finding] = Field(default_factory=list, description="List of findings for this file")


class VulnerabilityReport(BaseModel):
    """Final scan report: report_path and aggregated findings (for API/state)."""

    report_path: str = Field(..., description="Path to the saved Markdown report file")
    findings: list[Finding] = Field(default_factory=list, description="All findings across files")


class StrategicPlan(BaseModel):
    """Planner output: goals, risk_focus, strategy for the security scan."""

    goals: list[str] = Field(default_factory=list, description="Main objectives for the scan")
    risk_focus: list[str] = Field(default_factory=list, description="Vulnerability categories to prioritize")
    strategy: str = Field(default="", description="How to approach the scan")


class Section(BaseModel):
    """One unit of work for a worker: one file to scan. SECTIONS pattern (multi-agent-systems)."""

    section_id: str = Field(..., description="Unique section identifier")
    task_id: str = Field(..., description="Parent task/correlation id")
    scope: str = Field(..., description="What to produce (e.g. scan this file for vulnerabilities)")
    inputs: dict[str, Any] = Field(default_factory=dict, description="e.g. file_path, content")
    constraints: list[str] = Field(default_factory=list, description="Limitations or rules")
    expected_output_shape: dict[str, Any] = Field(default_factory=dict, description="Expected output schema")
