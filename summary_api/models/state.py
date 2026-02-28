"""Graph state for the repo summary 4-node workflow (Selector → Batch Fetcher → Summarizer → Decider → Synthesizer)."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from summary_api.clients.github_client import RepoFile


class SummaryGraphState(TypedDict, total=False):
    """State for the iterative repo summary graph.

    Flat schema per LangGraph rules. Structure-first flow: repo_tree_entries and
    planned_batches are set at start; each iteration fetches only current_batch_files.
    """

    # Filled once at start (structure + LLM planning). Read by Batch Fetcher.
    repo_github_url: str
    repo_tree_entries: list[Any]  # TreeEntry (path, sha, size) after filter
    # Precomputed list of path batches (from LLM plan_batches_from_structure).
    planned_batches: list[list[str]]
    # Index into planned_batches; advanced by Decider on continue.
    current_batch_index: int
    # Owner: Selector. Next batch of paths to summarize.
    current_batch_paths: list[str]
    # Owner: Batch Fetcher. Content for current batch only (RepoFile list).
    current_batch_files: list[RepoFile]
    # Paths already included in summarized_chunks; append-only from Summarizer.
    already_summarized_paths: list[str]
    # Each item: {"paths": list[str], "summary": str}; append-only from Summarizer.
    summarized_chunks: list[dict]
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
