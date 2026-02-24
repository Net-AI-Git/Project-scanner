"""Dead Letter Queue: append failed requests after all retries for later reprocessing."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

# Default: project root, or set DLQ_PATH in env
DEFAULT_DLQ_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "DLQ.jsonl",
)


def write_to_dlq(
    correlation_id: str,
    step_name: str,
    request_summary: dict[str, Any],
    error_detail: dict[str, Any],
    *,
    dlq_path: str | None = None,
) -> None:
    """Append one failed request to the DLQ file (append-only).

    Called when a step fails after all retries so the request can be reviewed
    or reprocessed later. Does not raise; logs and swallows write errors.

    Args:
        correlation_id: Request/session UUID for traceability.
        step_name: Step that failed (e.g. fetch_repo_files, summarize_repo).
        request_summary: Sanitized request info (e.g. github_url; no secrets).
        error_detail: Error message, where, traceback, error_classification.
        dlq_path: Override path (default from DLQ_PATH env or DEFAULT_DLQ_PATH).

    Returns:
        None. Swallows exceptions so the API response is never broken.
    """
    path = dlq_path or os.environ.get("DLQ_PATH", DEFAULT_DLQ_PATH)
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
