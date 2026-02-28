"""Decider node: decide continue or done from content (LLM or heuristic); advance batch index on continue."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from summary_api.infrastructure.audit import log_audit_step

if TYPE_CHECKING:
    from summary_api.models.state import SummaryGraphState

logger = logging.getLogger(__name__)

# Heuristic: word overlap ratio above this → consider redundant → done.
HEURISTIC_OVERLAP_THRESHOLD = 0.7


def _heuristic_continue_or_done(previous_summaries: list[str], current_summary: str) -> str:
    """Simple overlap heuristic: if current is mostly contained in previous text, return done."""
    if not previous_summaries:
        return "continue"
    combined = " ".join(previous_summaries).lower().split()
    current_words = set((current_summary or "").lower().split())
    if not current_words:
        return "done"
    overlap = sum(1 for w in current_words if w in combined)
    ratio = overlap / len(current_words)
    return "done" if ratio >= HEURISTIC_OVERLAP_THRESHOLD else "continue"


async def decider_node(state: SummaryGraphState, settings: Any) -> dict[str, Any]:
    """READ summarized_chunks, planned_batches, current_batch_index; content-based decision; WRITE decision, current_batch_index+1 on continue.

    If DECIDER_USE_LLM: call LLM to ask if latest summary adds substantial new information.
    Else: heuristic (word overlap). On continue, advance current_batch_index; if no more batches, force done.
    """
    t0 = time.perf_counter()
    correlation_id = state.get("correlation_id") or ""
    chunks = state.get("summarized_chunks") or []
    planned = state.get("planned_batches") or []
    index = state.get("current_batch_index") or 0

    if not chunks:
        duration_ms = (time.perf_counter() - t0) * 1000
        log_audit_step(
            correlation_id, "decider", "success",
            input_summary={"chunks": 0},
            output_summary={"decision": "done"},
            duration_ms=duration_ms,
        )
        return {"decision": "done"}

    current_chunk = chunks[-1]
    current_summary = (current_chunk.get("summary") or "").strip()
    previous_summaries = [c.get("summary") or "" for c in chunks[:-1]]

    use_llm = getattr(settings, "DECIDER_USE_LLM", False)
    if use_llm:
        try:
            from summary_api.clients.llm_client import (
                LLMClientError,
                decide_continue_or_done,
            )
            api_key = (getattr(settings, "NEBIUS_API_KEY", None) or "")
            if hasattr(api_key, "get_secret_value"):
                api_key = api_key.get_secret_value() or ""
            decision = await decide_continue_or_done(
                previous_summaries,
                current_summary,
                api_key=api_key,
                base_url=getattr(settings, "NEBIUS_BASE_URL", ""),
                model=getattr(settings, "NEBIUS_MODEL", ""),
                timeout=60.0,
                max_tokens=32,
            )
        except LLMClientError as e:
            logger.warning("Decider LLM failed (%s), falling back to heuristic", e.message)
            decision = _heuristic_continue_or_done(previous_summaries, current_summary)
    else:
        decision = _heuristic_continue_or_done(previous_summaries, current_summary)

    next_index = index + 1
    if decision == "continue" and next_index >= len(planned):
        decision = "done"

    duration_ms = (time.perf_counter() - t0) * 1000
    log_audit_step(
        correlation_id, "decider", "success",
        input_summary={"chunks": len(chunks), "current_batch_index": index, "planned_batches": len(planned)},
        output_summary={"decision": decision, "next_batch_index": next_index if decision == "continue" else index},
        duration_ms=duration_ms,
    )
    out: dict[str, Any] = {"decision": decision}
    if decision == "continue":
        out["current_batch_index"] = next_index
    return out
