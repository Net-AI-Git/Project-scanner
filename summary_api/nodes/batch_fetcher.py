"""Batch fetcher node: download content only for current_batch_paths via Blob API (parallel)."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from summary_api.clients.github_client import (
    GitHubClientError,
    TreeEntry,
    fetch_blob_contents_for_entries,
)
from summary_api.infrastructure.audit import log_audit_step

if TYPE_CHECKING:
    from summary_api.models.state import SummaryGraphState

logger = logging.getLogger(__name__)


def _entries_for_batch(
    repo_tree_entries: list[Any], current_batch_paths: list[str]
) -> list[TreeEntry]:
    """Build list of TreeEntry for paths in current batch; entries without sha are skipped."""
    path_to_entry: dict[str, TreeEntry] = {}
    for e in repo_tree_entries or []:
        if hasattr(e, "path") and getattr(e, "path", None):
            path_to_entry[e.path] = (
                e
                if isinstance(e, TreeEntry)
                else TreeEntry(
                    path=e.get("path", ""),
                    size=e.get("size") if isinstance(e, dict) else None,
                    sha=e.get("sha") if isinstance(e, dict) else None,
                )
            )
        elif isinstance(e, dict) and e.get("path"):
            path_to_entry[e["path"]] = TreeEntry(
                path=e["path"],
                size=e.get("size"),
                sha=e.get("sha"),
            )
    return [path_to_entry[p] for p in current_batch_paths if p in path_to_entry and path_to_entry[p].sha]


async def batch_fetcher_node(
    state: SummaryGraphState, settings: Any
) -> dict[str, Any]:
    """READ current_batch_paths, repo_tree_entries, repo_github_url; fetch blob contents in parallel; WRITE current_batch_files."""
    t0 = time.perf_counter()
    correlation_id = state.get("correlation_id") or ""
    current_paths = state.get("current_batch_paths") or []
    tree_entries = state.get("repo_tree_entries") or []
    github_url = (state.get("repo_github_url") or "").strip()

    if not github_url:
        duration_ms = (time.perf_counter() - t0) * 1000
        log_audit_step(
            correlation_id,
            "batch_fetcher",
            "failure",
            output_summary={"error": "missing repo_github_url"},
            duration_ms=duration_ms,
        )
        return {
            "current_batch_files": [],
            "errors": (state.get("errors") or [])
            + [{"node": "batch_fetcher", "message": "missing repo_github_url"}],
        }

    entries = _entries_for_batch(tree_entries, current_paths)
    if not entries:
        duration_ms = (time.perf_counter() - t0) * 1000
        log_audit_step(
            correlation_id,
            "batch_fetcher",
            "success",
            input_summary={"current_batch_paths": len(current_paths), "entries_resolved": 0},
            output_summary={"current_batch_files": 0},
            duration_ms=duration_ms,
        )
        return {"current_batch_files": []}

    github_token = None
    if hasattr(settings, "GITHUB_TOKEN") and settings.GITHUB_TOKEN:
        token_val = getattr(settings.GITHUB_TOKEN, "get_secret_value", lambda: None)()
        github_token = token_val or None
    max_concurrency = getattr(
        settings, "BATCH_FETCH_MAX_CONCURRENCY", 25
    )

    try:
        files = await fetch_blob_contents_for_entries(
            github_url,
            entries,
            github_token=github_token,
            max_concurrency=max_concurrency,
        )
        duration_ms = (time.perf_counter() - t0) * 1000
        log_audit_step(
            correlation_id,
            "batch_fetcher",
            "success",
            input_summary={
                "current_batch_paths": len(current_paths),
                "entries_requested": len(entries),
            },
            output_summary={
                "current_batch_files": len(files),
            },
            duration_ms=duration_ms,
        )
        return {"current_batch_files": files}
    except GitHubClientError as e:
        duration_ms = (time.perf_counter() - t0) * 1000
        log_audit_step(
            correlation_id,
            "batch_fetcher",
            "failure",
            error_detail={
                "message": e.message,
                "where": "summary_api.nodes.batch_fetcher",
                "error_classification": "transient" if getattr(e, "is_transient", False) else "permanent",
            },
            duration_ms=duration_ms,
        )
        raise
