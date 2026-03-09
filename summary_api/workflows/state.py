"""Workflow state TypedDict for summarize and scan LangGraph workflows.

Implements: .cursor/rules/agents/langgraph-architecture-and-nodes (State Definition).
"""

from __future__ import annotations

from typing import Any, TypedDict

from summary_api.clients.github_client import RepoFile

# Queue type for MD writer: not serializable; passed at runtime
# from asyncio import Queue -> state holds Optional[Any] for queue ref


class SummarizeState(TypedDict, total=False):
    """State for the fetch → process → summarize workflow.

    Field ownership: fetch_node → files; process_node → context; summarize_node → result.
    """

    # Input / config (set by main before invoke)
    correlation_id: str
    github_url: str
    github_token: str | None
    github_api_base: str
    audit_path: str
    dlq_path: str
    max_context_chars: int
    context_limit_tokens: int
    # LLM config (from Settings, set before invoke)
    nebius_api_key: str
    nebius_base_url: str
    nebius_model: str
    nebius_max_tokens: int
    # Optional shared HTTP client for connection pooling (not serializable)
    http_client: Any

    # Errors (nodes append; reset on success per rule)
    errors: list[dict[str, Any]]
    ERROR_COUNT: int

    # Node outputs
    files: list[RepoFile]
    context: str
    result: dict[str, Any]  # summary, technologies, structure

    # When a node fails: status_code, content, correlation_id for main to return
    error_response: dict[str, Any]


class ScanState(TypedDict, total=False):
    """State for the scan workflow: fetch → process → planner → orchestrator → workers → md_writer → synthesizer.

    Field ownership: fetch_node → files; process_node → context; planner_node → strategic_plan;
    orchestrator_node → sections; workers → worker_results + queue; md_writer → report_path; synthesizer_node → result.
    """

    # Input / config (set by main before invoke)
    correlation_id: str
    github_url: str
    github_token: str | None
    github_api_base: str
    audit_path: str
    dlq_path: str
    max_context_chars: int
    context_limit_tokens: int
    scan_goal: str
    scan_reports_dir: str
    # LLM config
    nebius_api_key: str
    nebius_base_url: str
    nebius_model: str
    nebius_max_tokens: int
    http_client: Any

    # Errors
    errors: list[dict[str, Any]]
    ERROR_COUNT: int

    # Node outputs
    files: list[RepoFile]
    context: str
    strategic_plan: dict[str, Any]
    sections: list[dict[str, Any]]  # list of Section (serialized)
    worker_results: list[dict[str, Any]]  # list of SectionFindings (serialized)
    report_path: str
    result: dict[str, Any]  # VulnerabilityReport (report_path + findings)

    # Queue for MD writer: workers put SectionFindings here; md_writer consumes (runtime only, not serialized)
    md_queue: Any

    error_response: dict[str, Any]
