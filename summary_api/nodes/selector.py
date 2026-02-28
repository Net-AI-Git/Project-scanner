"""Selector node: choose next batch of paths, no duplicates. READ → DO → WRITE → CONTROL."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from summary_api.infrastructure.audit import log_audit_step
from summary_api.services.selection import select_next_batch_by_budget

if TYPE_CHECKING:
    from summary_api.config import Settings
    from summary_api.models.state import SummaryGraphState

logger = logging.getLogger(__name__)


def selector_node(state: SummaryGraphState, settings: Any) -> dict[str, Any]:
    """READ all_repo_files, already_summarized_paths; DO select_next_batch; WRITE current_batch_paths; CONTROL → next node.

    If no paths left, returns current_batch_paths=[] so Summarizer can no-op and Decider will see done.
    """
    t0 = time.perf_counter()
    correlation_id = (state.get("correlation_id") or "")
    all_files = state.get("all_repo_files") or []
    already = state.get("already_summarized_paths") or []
    max_chars = getattr(settings, "SUMMARY_MAX_CONTEXT_CHARS_PER_BATCH", 50_000)
    max_files = getattr(settings, "SUMMARY_MAX_FILES_PER_BATCH", 50)
    max_chars_per_file = getattr(settings, "SUMMARY_MAX_CHARS_COUNT_PER_FILE", 25_000)

    already_set = set(already)
    paths = select_next_batch_by_budget(all_files, already_set, max_chars, max_files, max_chars_per_file)
    duration_ms = (time.perf_counter() - t0) * 1000

    total_eligible = len(all_files)  # after early filter in main (skip paths never in all_repo_files)
    remaining_eligible = total_eligible - len(already)

    log_audit_step(
        correlation_id,
        "selector",
        "success",
        step_index=None,
        input_summary={
            "file_count": total_eligible,
            "already_count": len(already),
            "remaining_eligible": remaining_eligible,
            "max_chars": max_chars,
            "max_files": max_files,
            "max_chars_per_file": max_chars_per_file,
        },
        output_summary={"selected_count": len(paths), "paths_sample": paths[:5] if paths else []},
        duration_ms=duration_ms,
    )
    return {"current_batch_paths": paths}
