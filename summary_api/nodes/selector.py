"""Selector node: set current batch from planned_batches by index. READ → WRITE → CONTROL."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from summary_api.infrastructure.audit import log_audit_step

if TYPE_CHECKING:
    from summary_api.config import Settings
    from summary_api.models.state import SummaryGraphState

logger = logging.getLogger(__name__)


def selector_node(state: SummaryGraphState, settings: Any) -> dict[str, Any]:
    """READ planned_batches, current_batch_index; WRITE current_batch_paths from plan.

    Does not advance current_batch_index (Decider does on continue). If index is out of
    range, returns current_batch_paths=[] so Summarizer no-ops and Decider sees done.
    """
    t0 = time.perf_counter()
    correlation_id = state.get("correlation_id") or ""
    planned = state.get("planned_batches") or []
    index = state.get("current_batch_index") or 0

    if index >= len(planned):
        paths: list[str] = []
    else:
        paths = list(planned[index])

    duration_ms = (time.perf_counter() - t0) * 1000
    log_audit_step(
        correlation_id,
        "selector",
        "success",
        step_index=None,
        input_summary={
            "planned_batch_count": len(planned),
            "current_batch_index": index,
        },
        output_summary={"selected_count": len(paths), "paths_sample": paths[:5] if paths else []},
        duration_ms=duration_ms,
    )
    return {"current_batch_paths": paths}
