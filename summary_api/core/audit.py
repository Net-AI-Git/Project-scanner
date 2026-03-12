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
    audit_path: str,
) -> None:
    """Append one audit entry (JSON line) to AUDIT.jsonl. Append-only, no deletion.

    Why: Audit protocol requires immutable, append-only logs with required fields and hash.
    What: Builds entry with required fields, computes SHA-256 over payload, appends one JSON line.

    Args:
        event_type: Event type (e.g. api_request, execution_step).
        resource: Resource or step name.
        action: Action (e.g. POST, call).
        result: success or failure.
        correlation_id: Request UUID.
        metadata: Optional extra key-value data (sanitized, no secrets).
        audit_path: Path to audit log file. Caller must pass from Settings.AUDIT_LOG_PATH (get_settings().AUDIT_LOG_PATH).

    Returns:
        None.

    Raises:
        OSError: On failure to open or write to the audit file (e.g. permission, disk full).
    """
    path = audit_path
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


def _build_audit_step_meta(
    *,
    step_index: int | None = None,
    input_summary: dict[str, Any] | None = None,
    output_summary: dict[str, Any] | None = None,
    error_detail: dict[str, Any] | None = None,
    duration_ms: float | None = None,
    start_timestamp: str | None = None,
    end_timestamp: str | None = None,
) -> dict[str, Any]:
    """Build metadata dict for an execution_step audit entry."""
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
    if start_timestamp is not None:
        meta["start_timestamp"] = start_timestamp
    if end_timestamp is not None:
        meta["end_timestamp"] = end_timestamp
    return meta


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
    start_timestamp: str | None = None,
    end_timestamp: str | None = None,
    audit_path: str,
) -> None:
    """Log one execution step for full trace (LLM-as-Judge).

    Why: Judge needs full execution trace per correlation_id to evaluate API behavior.
    What: Builds metadata from step args, calls log_audit with event_type execution_step.

    Args:
        correlation_id: Request/session UUID.
        step_name: Logical step (e.g. fetch_repo_files, process_repo_files, scan_workers).
        result: "success" or "failure".
        step_index: Optional 1-based step order in the flow.
        input_summary: Sanitized input summary (no secrets); e.g. {"github_url": "...", "file_count": 0}.
        output_summary: On success, summary of return (e.g. {"file_count": 42}); None on failure.
        error_detail: On failure, e.g. {"message": "...", "where": "module.function", "traceback": "..."}.
        duration_ms: Optional elapsed time in milliseconds.
        start_timestamp: Optional ISO 8601 UTC start time (R2 observability).
        end_timestamp: Optional ISO 8601 UTC end time (R2 observability).
        audit_path: Path to audit log file. Caller must pass from Settings.AUDIT_LOG_PATH (get_settings().AUDIT_LOG_PATH).

    Returns:
        None.

    Raises:
        OSError: On failure to write to audit file (propagated from log_audit).
    """
    meta = _build_audit_step_meta(
        step_index=step_index,
        input_summary=input_summary,
        output_summary=output_summary,
        error_detail=error_detail,
        duration_ms=duration_ms,
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
    )
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

    Why: Structured error logging requires message, location, and traceback for Splunk/Judge.
    What: Formats exception message and traceback; returns dict with message, where, traceback.

    Args:
        exc: The exception that was raised.
        where: Identifier of where it happened (e.g. "summary_api.clients.github_client.fetch_repo_files").

    Returns:
        Dict with keys message (str), where (str), traceback (str).

    Raises:
        None.
    """
    return {
        "message": str(exc),
        "where": where,
        "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__ or ())).strip(),
    }


def _read_audit_entries_by_correlation(
    path: str, correlation_id: str
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Read audit file and return (execution_logs, api_request) for the given correlation_id."""
    execution_logs: list[dict[str, Any]] = []
    api_request: dict[str, Any] | None = None
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
    return execution_logs, api_request


def _build_session_summary(
    steps_ordered: list[dict[str, Any]], api_request: dict[str, Any] | None
) -> str:
    """Build a short session summary string for Judge context."""
    result = api_request.get("result", "unknown") if api_request else "no api_request"
    return f"Steps: {len(steps_ordered)}. Final result: {result}."


def get_session_context_for_judge(
    correlation_id: str,
    *,
    audit_path: str,
) -> dict[str, Any]:
    """Load all audit entries for one request to feed LLM-as-Judge.

    Why: Judge tool needs full session context (steps + api_request) from AUDIT.jsonl.
    What: Reads audit file, filters by correlation_id, sorts steps, builds session_summary.

    For external / CLI use only (e.g. Judge tool or scripts). Not called from the Summary API.
    Caller must pass audit_path from Settings.AUDIT_LOG_PATH (get_settings().AUDIT_LOG_PATH).

    Args:
        correlation_id: The request UUID to filter by.
        audit_path: Path to audit log file (from Settings.AUDIT_LOG_PATH).

    Returns:
        Dict with keys: correlation_id, execution_logs (list of step entries),
        api_request (final api_request entry if any), session_summary.

    Raises:
        OSError: On failure to open or read the audit file.
    """
    path = audit_path
    if not os.path.isfile(path):
        return {
            "correlation_id": correlation_id,
            "execution_logs": [],
            "api_request": None,
            "session_summary": "No audit file or no entries for this correlation_id.",
        }
    execution_logs, api_request = _read_audit_entries_by_correlation(path, correlation_id)
    steps_ordered = sorted(
        execution_logs,
        key=lambda e: (e.get("metadata", {}).get("step_index", 999), e.get("timestamp", "")),
    )
    session_summary = _build_session_summary(steps_ordered, api_request)
    return {
        "correlation_id": correlation_id,
        "execution_logs": steps_ordered,
        "api_request": api_request,
        "session_summary": session_summary,
    }
