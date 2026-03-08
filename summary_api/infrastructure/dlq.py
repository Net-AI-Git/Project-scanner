"""Dead Letter Queue: append failed requests after all retries for later reprocessing."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


def write_to_dlq(
    correlation_id: str,
    step_name: str,
    request_summary: dict[str, Any],
    error_detail: dict[str, Any],
    *,
    dlq_path: str,
) -> None:
    """Append one failed request to the DLQ file (append-only).

    Why: Failed requests after all retries must be captured for review/reprocess (error-handling rule).
    What: Writes one JSON line to DLQ file; catches and ignores any IO or encoding error.

    Args:
        correlation_id: Request/session UUID for traceability.
        step_name: Step that failed (e.g. fetch_repo_files, summarize_repo).
        request_summary: Sanitized request info (e.g. github_url; no secrets).
        error_detail: Error message, where, traceback, error_classification.
        dlq_path: Path to DLQ file. Caller must pass from Settings.DLQ_PATH (get_settings().DLQ_PATH).

    Returns:
        None.

    Raises:
        None. All exceptions are caught and swallowed so the API response is never broken.
    """
    path = dlq_path
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "correlation_id": correlation_id,
        "step_name": step_name,
        "request_summary": dict(request_summary),
        "error_detail": dict(error_detail),
    }
    try:
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass
