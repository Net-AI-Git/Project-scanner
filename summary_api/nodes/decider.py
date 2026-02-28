"""Decider node: decide continue or done (heuristic: max iterations, coverage, or chunk limit)."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from summary_api.infrastructure.audit import log_audit_step
from summary_api.services.repo_processor import should_skip_path

if TYPE_CHECKING:
    from summary_api.models.state import SummaryGraphState

logger = logging.getLogger(__name__)

# Heuristic: done when this many chunks (fallback if coverage not from config).
MAX_CHUNKS_BEFORE_DONE = 15


def _eligible_file_count(all_repo_files: list) -> int:
    """Count repo files that are not skipped (same as selection pool)."""
    count = 0
    for f in all_repo_files:
        path = getattr(f, "path", "") or ""
        if path and not should_skip_path(path):
            count += 1
    return count


def decider_node(state: SummaryGraphState, settings: Any) -> dict[str, Any]:
    """READ summarized_chunks, already_summarized_paths, all_repo_files, iteration_count; DO heuristic; WRITE decision, iteration_count+1."""
    t0 = time.perf_counter()
    correlation_id = state.get("correlation_id") or ""
    chunks = state.get("summarized_chunks") or []
    already = state.get("already_summarized_paths") or []
    all_files = state.get("all_repo_files") or []
    iteration = state.get("iteration_count") or 0
    max_iterations = getattr(settings, "SUMMARY_MAX_ITERATIONS", 20)

    total_eligible = _eligible_file_count(all_files)
    coverage_threshold = getattr(settings, "SUMMARY_COVERAGE_THRESHOLD", 0.8)
    threshold_count = int(total_eligible * coverage_threshold) if total_eligible else 0

    decision: str = "continue"
    if iteration >= max_iterations:
        decision = "done"
    elif total_eligible == 0:
        decision = "done"
    elif len(already) >= total_eligible:
        decision = "done"
    elif len(already) >= threshold_count:
        decision = "done"
    elif len(chunks) >= MAX_CHUNKS_BEFORE_DONE:
        decision = "done"

    duration_ms = (time.perf_counter() - t0) * 1000
    log_audit_step(
        correlation_id, "decider", "success",
        input_summary={"iteration": iteration, "chunks": len(chunks), "already": len(already), "total_eligible": total_eligible},
        output_summary={"decision": decision},
        duration_ms=duration_ms,
    )
    return {"decision": decision, "iteration_count": iteration + 1}
