"""Graph state for the repo summary 4-node workflow (Selector → Summarizer → Decider → Synthesizer)."""

from __future__ import annotations

from typing import Literal, TypedDict

from summary_api.clients.github_client import RepoFile


class SummaryGraphState(TypedDict, total=False):
    """State for the iterative repo summary graph.

    Flat schema per LangGraph rules. Each field has a single owner node except
    shared inputs (all_repo_files) and accumulated collections (summarized_chunks,
    already_summarized_paths, errors).
    """

    # Filled once after fetch; read by Selector, Summarizer, Decider.
    all_repo_files: list[RepoFile]
    # Paths already included in summarized_chunks; append-only from Summarizer.
    already_summarized_paths: list[str]
    # Each item: {"paths": list[str], "summary": str}; append-only from Summarizer.
    summarized_chunks: list[dict]
    # Owner: Selector. Next batch of paths to summarize.
    current_batch_paths: list[str]
    # Owner: Decider. "continue" | "done".
    decision: Literal["continue", "done"]
    # Owner: Synthesizer. {summary, technologies, structure} or None.
    final_summary: dict | None
    # Incremented each loop; used by Decider for max_iterations.
    iteration_count: int
    # Accumulated errors; append in nodes, reset on success per error-handling rule.
    errors: list[dict]
    # Set at start for audit/DLQ; read by nodes for log_audit_step.
    correlation_id: str
