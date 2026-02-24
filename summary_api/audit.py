"""Audit logging for Summary API.

Implements: .cursor/rules/security/audit-protocol/RULE.mdc
- Mandatory Audit Events: API Requests (log all incoming API requests)
- Audit Log Structure: required fields (timestamp, event_type, actor_id, actor_type,
  resource, action, result, correlation_id, tenant_id, metadata)
- Immutable Logs: append-only, cryptographic hash for integrity
- Structured Logging: JSON (Section 3)
- Correlation IDs: UUID format, included in every entry

Supports LLM-as-Judge: execution_step events record every operation (input, output,
error, where, duration) so a Judge can analyze full traces from AUDIT.jsonl.
"""

from __future__ import annotations

import hashlib
import json
import os
import traceback
from datetime import datetime, timezone
from typing import Any

# Default: project root, or set AUDIT_LOG_PATH in env
DEFAULT_AUDIT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "AUDIT.jsonl",
)


def _timestamp_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_audit(
    event_type: str,
    resource: str,
    action: str,
    result: str,
    correlation_id: str,
    metadata: dict[str, Any] | None = None,
    *,
    audit_path: str | None = None,
) -> None:
    """Append one audit entry (JSON line) to AUDIT.jsonl. Append-only, no deletion.

    Required fields per audit-protocol RULE.mdc §1 Audit Log Structure.
    Hash computed over entry (without log_hash) per §1 Immutable Logs.
    """
    path = audit_path or os.environ.get("AUDIT_LOG_PATH", DEFAULT_AUDIT_PATH)
    entry = {
        "timestamp": _timestamp_utc(),
        "event_type": event_type,
        "actor_id": "api",
        "actor_type": "system",
        "resource": resource,
        "action": action,
        "result": result,
        "correlation_id": correlation_id,
        "tenant_id": None,
        "metadata": dict(metadata) if metadata else {},
    }
    line_bytes = json.dumps(entry, ensure_ascii=False).encode("utf-8")
    entry["log_hash"] = hashlib.sha256(line_bytes).hexdigest()
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)


def log_audit_step(
    correlation_id: str,
    step_name: str,
    result: str,
    *,
    step_index: int | None = None,
    input_summary: dict[str, Any] | None = None,
    output_summary: dict[str, Any] | None = None,
    error_detail: dict[str, Any] | None = None,
    duration_ms: float | None = None,
    audit_path: str | None = None,
) -> None:
    """Log one execution step for full trace (LLM-as-Judge).

    Records what was called, with what inputs, what was returned or what error
    occurred and where, plus optional duration. All written to the same AUDIT.jsonl
    so a session can be reconstructed by correlation_id.

    Args:
        correlation_id: Request/session UUID.
        step_name: Logical step (e.g. fetch_repo_files, process_repo_files, summarize_repo).
        result: "success" or "failure".
        step_index: Optional 1-based step order in the flow.
        input_summary: Sanitized input summary (no secrets); e.g. {"github_url": "...", "file_count": 0}.
        output_summary: On success, summary of return (e.g. {"file_count": 42}); None on failure.
        error_detail: On failure, e.g. {"message": "...", "where": "module.function", "traceback": "..."}.
        duration_ms: Optional elapsed time in milliseconds.
        audit_path: Override path (default from AUDIT_LOG_PATH or DEFAULT_AUDIT_PATH).
    """
    meta: dict[str, Any] = {}
    if step_index is not None:
        meta["step_index"] = step_index
    if input_summary is not None:
        meta["input_summary"] = dict(input_summary)
    if output_summary is not None:
        meta["output_summary"] = dict(output_summary)
    if error_detail is not None:
        meta["error_detail"] = dict(error_detail)
    if duration_ms is not None:
        meta["duration_ms"] = round(duration_ms, 2)
    log_audit(
        event_type="execution_step",
        resource=step_name,
        action="call",
        result=result,
        correlation_id=correlation_id,
        metadata=meta,
        audit_path=audit_path,
    )


def error_detail_from_exception(exc: BaseException, where: str) -> dict[str, Any]:
    """Build error_detail dict for log_audit_step from an exception.

    Args:
        exc: The exception that was raised.
        where: Identifier of where it happened (e.g. "summary_api.github_client.fetch_repo_files").

    Returns:
        Dict with message, where, and traceback (string).
    """
    return {
        "message": str(exc),
        "where": where,
        "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__ or ())).strip(),
    }


def get_session_context_for_judge(
    correlation_id: str,
    *,
    audit_path: str | None = None,
) -> dict[str, Any]:
    """Load all audit entries for one request to feed LLM-as-Judge.

    Returns a Session Context with execution logs (steps in order), final outcome,
    and optional performance info, matching the input expected by the LLM Judge
    rule (Execution Logs, Audit Comparison, Final Output).

    Args:
        correlation_id: The request UUID to filter by.
        audit_path: Override path (default from AUDIT_LOG_PATH or DEFAULT_AUDIT_PATH).

    Returns:
        Dict with keys: correlation_id, execution_logs (list of step entries),
        api_request (final api_request entry if any), session_summary.
    """
    path = audit_path or os.environ.get("AUDIT_LOG_PATH", DEFAULT_AUDIT_PATH)
    execution_logs: list[dict[str, Any]] = []
    api_request: dict[str, Any] | None = None

    if not os.path.isfile(path):
        return {
            "correlation_id": correlation_id,
            "execution_logs": [],
            "api_request": None,
            "session_summary": "No audit file or no entries for this correlation_id.",
        }

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("correlation_id") != correlation_id:
                continue
            if entry.get("event_type") == "execution_step":
                execution_logs.append(entry)
            elif entry.get("event_type") == "api_request":
                api_request = entry

    steps_ordered = sorted(execution_logs, key=lambda e: (e.get("metadata", {}).get("step_index", 999), e.get("timestamp", "")))
    session_summary = (
        f"Steps: {len(steps_ordered)}. "
        f"Final result: {api_request.get('result', 'unknown') if api_request else 'no api_request'}."
    )
    return {
        "correlation_id": correlation_id,
        "execution_logs": steps_ordered,
        "api_request": api_request,
        "session_summary": session_summary,
    }
