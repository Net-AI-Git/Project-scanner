"""Summarizer node: build context for current batch, call LLM, append to summarized_chunks."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from summary_api.infrastructure.audit import log_audit_step
from summary_api.clients.github_client import RepoFile
from summary_api.clients.llm_client import LLMClientError, summarize_batch
from summary_api.services.repo_processor import _build_context_for_files  # noqa: PLC2701

if TYPE_CHECKING:
    from summary_api.models.state import SummaryGraphState

logger = logging.getLogger(__name__)


def _files_for_paths(all_repo_files: list[RepoFile], paths: list[str]) -> list[RepoFile]:
    """Return RepoFile list for given paths; order preserved."""
    path_to_file = {f.path: f for f in all_repo_files if f.path}
    return [path_to_file[p] for p in paths if p in path_to_file]


def _llm_kwargs_from_settings(settings: Any) -> dict[str, Any]:
    """Build kwargs for LLM calls from Settings (no secrets in logs)."""
    api_key = (getattr(settings, "NEBIUS_API_KEY", None) or "")
    if hasattr(api_key, "get_secret_value"):
        api_key = api_key.get_secret_value() or ""
    return {
        "api_key": api_key,
        "base_url": getattr(settings, "NEBIUS_BASE_URL", ""),
        "model": getattr(settings, "NEBIUS_MODEL", ""),
        "max_tokens": getattr(settings, "NEBIUS_MAX_TOKENS", 4096),
    }


async def summarizer_node(state: SummaryGraphState, settings: Any) -> dict[str, Any]:
    """READ current_batch_paths, all_repo_files; DO context + LLM; WRITE append summarized_chunks, extend already_summarized_paths."""
    t0 = time.perf_counter()
    correlation_id = state.get("correlation_id") or ""
    current_paths = state.get("current_batch_paths") or []
    all_files = state.get("all_repo_files") or []
    chunks = list(state.get("summarized_chunks") or [])
    already = list(state.get("already_summarized_paths") or [])

    if not current_paths:
        duration_ms = (time.perf_counter() - t0) * 1000
        log_audit_step(correlation_id, "summarizer", "success", output_summary={"skipped": True}, duration_ms=duration_ms)
        return {}

    batch_files = _files_for_paths(all_files, current_paths)
    max_chars = getattr(settings, "SUMMARY_MAX_CONTEXT_CHARS_PER_BATCH", 50_000)
    context = _build_context_for_files(batch_files, max_chars)
    batch_label = ", ".join(current_paths[:3]) + ("..." if len(current_paths) > 3 else "")

    try:
        result = await summarize_batch(context, batch_label, **_llm_kwargs_from_settings(settings))
        summary_text = (result.get("summary") or "").strip()
    except LLMClientError as e:
        duration_ms = (time.perf_counter() - t0) * 1000
        log_audit_step(
            correlation_id, "summarizer", "failure",
            error_detail={"message": e.message, "where": "summary_api.nodes.summarizer", "error_classification": "transient" if getattr(e, "is_transient", False) else "permanent"},
            duration_ms=duration_ms,
        )
        return {"errors": (state.get("errors") or []) + [{"node": "summarizer", "message": e.message}]}

    chunks.append({"paths": current_paths, "summary": summary_text})
    already.extend(current_paths)
    duration_ms = (time.perf_counter() - t0) * 1000
    log_audit_step(
        correlation_id, "summarizer", "success",
        input_summary={"batch_size": len(current_paths)},
        output_summary={"summary_length": len(summary_text)},
        duration_ms=duration_ms,
    )
    return {"summarized_chunks": chunks, "already_summarized_paths": already}
