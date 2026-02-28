"""Synthesizer node: merge all summarized_chunks into final_summary via LLM."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from summary_api.infrastructure.audit import log_audit_step
from summary_api.clients.llm_client import LLMClientError, summarize_project_from_folders

if TYPE_CHECKING:
    from summary_api.models.state import SummaryGraphState

logger = logging.getLogger(__name__)


def _chunks_to_folder_summaries(chunks: list[dict]) -> list[dict[str, str]]:
    """Map summarized_chunks to [{"folder": label, "summary": str}] for summarize_project_from_folders."""
    result: list[dict[str, str]] = []
    for i, c in enumerate(chunks):
        paths = c.get("paths") or []
        summary = c.get("summary") or ""
        label = ", ".join(paths[:2]) + ("..." if len(paths) > 2 else "") if paths else f"batch {i + 1}"
        result.append({"folder": label, "summary": summary})
    return result


def _llm_kwargs_from_settings(settings: Any) -> dict[str, Any]:
    """Build kwargs for LLM calls from Settings."""
    api_key = getattr(settings, "NEBIUS_API_KEY", None) or ""
    if hasattr(api_key, "get_secret_value"):
        api_key = api_key.get_secret_value() or ""
    return {
        "api_key": api_key,
        "base_url": getattr(settings, "NEBIUS_BASE_URL", ""),
        "model": getattr(settings, "NEBIUS_MODEL", ""),
        "max_tokens": getattr(settings, "NEBIUS_MAX_TOKENS", 4096),
    }


async def synthesizer_node(state: SummaryGraphState, settings: Any) -> dict[str, Any]:
    """READ summarized_chunks; DO summarize_project_from_folders; WRITE final_summary."""
    t0 = time.perf_counter()
    correlation_id = state.get("correlation_id") or ""
    chunks = state.get("summarized_chunks") or []

    if not chunks:
        duration_ms = (time.perf_counter() - t0) * 1000
        log_audit_step(correlation_id, "synthesizer", "success", output_summary={"empty": True}, duration_ms=duration_ms)
        return {"final_summary": {"summary": "", "technologies": [], "structure": ""}}

    folder_summaries = _chunks_to_folder_summaries(chunks)
    try:
        result = await summarize_project_from_folders(folder_summaries, **_llm_kwargs_from_settings(settings))
    except LLMClientError as e:
        duration_ms = (time.perf_counter() - t0) * 1000
        log_audit_step(
            correlation_id, "synthesizer", "failure",
            error_detail={"message": e.message, "where": "summary_api.nodes.synthesizer", "error_classification": "transient" if getattr(e, "is_transient", False) else "permanent"},
            duration_ms=duration_ms,
        )
        return {"errors": (state.get("errors") or []) + [{"node": "synthesizer", "message": e.message}]}

    duration_ms = (time.perf_counter() - t0) * 1000
    log_audit_step(
        correlation_id, "synthesizer", "success",
        input_summary={"chunk_count": len(chunks)},
        output_summary={"summary_length": len(result.get("summary") or "")},
        duration_ms=duration_ms,
    )
    return {"final_summary": result}
