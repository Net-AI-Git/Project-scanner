"""FastAPI application: POST /summarize — fetch → 4-node graph (Selector → Summarizer → Decider → Synthesizer) → response."""

from __future__ import annotations

import json
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response

logging.getLogger("summary_api.llm_client").setLevel(logging.INFO)

try:
    from circuitbreaker import CircuitBreakerError  # type: ignore[import-untyped]
except ImportError:
    CircuitBreakerError = Exception  # noqa: A001

try:
    from .config import get_env_file_path, get_settings
    from .infrastructure.audit import error_detail_from_exception, log_audit, log_audit_step
    from .infrastructure.dlq import write_to_dlq
    from .clients.github_client import GitHubClientError, RepoFile, fetch_repo_files
    from .models.schemas import ErrorResponse, SummarizeRequest, SummarizeResponse
    from .services.repo_processor import should_skip_path
    from .workflow import run_summary_graph
except ImportError:
    from summary_api.config import get_env_file_path, get_settings
    from summary_api.infrastructure.audit import error_detail_from_exception, log_audit, log_audit_step
    from summary_api.infrastructure.dlq import write_to_dlq
    from summary_api.clients.github_client import GitHubClientError, RepoFile, fetch_repo_files
    from summary_api.models.schemas import ErrorResponse, SummarizeRequest, SummarizeResponse
    from summary_api.services.repo_processor import should_skip_path
    from summary_api.workflow import run_summary_graph

@asynccontextmanager
async def _lifespan(_app: FastAPI):  # noqa: ARG001
    """Startup: configure logging and log LLM config. Shutdown: none."""
    _configure_structured_logging()
    settings = get_settings()
    env_path = get_env_file_path()
    nebius_set = bool((settings.NEBIUS_API_KEY.get_secret_value() or "").strip())
    logger.info(
        "Config: env_file=%s, NEBIUS_API_KEY=%s",
        env_path,
        "set" if nebius_set else "not set",
    )
    yield


app = FastAPI(title="Summary API", description="Summarize public GitHub repositories", lifespan=_lifespan)
logger = logging.getLogger(__name__)


def _configure_structured_logging() -> None:
    """Configure JSON structured logging when Settings.LOG_FORMAT=json for observability."""
    if not hasattr(_configure_structured_logging, "_done"):
        _configure_structured_logging._done = False
    if _configure_structured_logging._done:
        return
    if get_settings().LOG_FORMAT == "json":
        for h in logging.root.handlers[:]:
            logging.root.removeHandler(h)
        handler = logging.StreamHandler()
        handler.setFormatter(_JsonFormatter())
        logging.root.addHandler(handler)
        logging.root.setLevel(logging.INFO)
    _configure_structured_logging._done = True


class _JsonFormatter(logging.Formatter):
    """Format log records as JSON with timestamp, level, message, and extra fields."""

    def format(self, record: logging.LogRecord) -> str:
        obj = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(record.created)),
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }
        if hasattr(record, "correlation_id"):
            obj["correlation_id"] = record.correlation_id
        if hasattr(record, "operation_name"):
            obj["operation_name"] = record.operation_name
        return json.dumps(obj, ensure_ascii=False)


@app.exception_handler(RequestValidationError)
def validation_exception_handler(_request: object, exc: RequestValidationError) -> JSONResponse:
    """Return spec error body for validation errors (missing/invalid github_url).

    Why: Ensures clients receive a consistent ErrorResponse shape per API spec.
    What: Maps Pydantic validation errors to a single user-facing message.

    Args:
        _request: The FastAPI request (unused).
        exc: The validation exception with error details.

    Returns:
        JSONResponse with status 400 and ErrorResponse body.
    """
    errors = exc.errors() or []
    if errors:
        first = errors[0]
        msg = first.get("msg", "Invalid request")
        loc = first.get("loc", ())
        if "body" in loc and "github_url" in loc:
            msg = "github_url is required and must be a non-empty string"
        elif "body" in loc:
            msg = "Invalid request: github_url is required and must be a non-empty string"
    else:
        msg = "Invalid request: github_url is required and must be a non-empty string"
    return JSONResponse(
        status_code=400,
        content=ErrorResponse(status="error", message=msg).model_dump(),
    )


def _github_error_to_status_and_message(exc: GitHubClientError) -> tuple[int, str]:
    """Map GitHubClientError to HTTP status code and user-facing message.

    Why: Callers need a single (status, message) to return to the client.
    What: Classifies by message content; 400 invalid URL, 404 not found, 503 rate limit, else 502.

    Args:
        exc: The GitHub client exception.

    Returns:
        (status_code, message) for the error response.
    """
    msg = exc.message or str(exc)
    if "Invalid GitHub URL" in msg:
        return 400, msg
    if "not found" in msg.lower() or "private" in msg.lower():
        return 404, msg
    if "timed out" in msg.lower() or "Network error" in msg:
        return 502, msg
    if "rate limit" in msg.lower() or "403" in msg:
        return 503, msg
    return 502, msg


def _audit(
    request_github_url: str,
    correlation_id: str,
    result: str,
    status_code: int,
    message: str | None = None,
) -> None:
    """Write one audit entry; swallow errors so response is never broken.

    Why: Audit trail must not affect API response.
    What: Calls log_audit with api_request event; ignores any write error.

    Args:
        request_github_url: The requested repo URL (sanitized).
        correlation_id: Request UUID.
        result: "success" or "failure".
        status_code: HTTP status returned to client.
        message: Optional error message for failures.

    Returns:
        None.
    """
    try:
        meta = {"github_url": request_github_url, "status_code": status_code}
        if message:
            meta["message"] = message
        log_audit(
            event_type="api_request",
            resource="/summarize",
            action="POST",
            result=result,
            correlation_id=correlation_id,
            metadata=meta,
        )
    except Exception:
        pass


def _with_correlation_header(
    content: dict, status: int, correlation_id: str
) -> JSONResponse:
    """Build JSONResponse with X-Correlation-ID for LLM-as-Judge trace lookup.

    Why: Clients and Judge scripts need the correlation ID in the response.
    What: Wraps JSONResponse with X-Correlation-ID header.

    Args:
        content: Response body dict.
        status: HTTP status code.
        correlation_id: Request UUID.

    Returns:
        JSONResponse with header set.
    """
    return JSONResponse(
        status_code=status,
        content=content,
        headers={"X-Correlation-ID": correlation_id},
    )


def _error_detail_with_classification(exc: BaseException, where: str) -> dict:
    """Build error_detail dict with error_classification (transient/permanent) for audit."""
    detail = error_detail_from_exception(exc, where)
    detail["error_classification"] = (
        "transient" if getattr(exc, "is_transient", False) else "permanent"
    )
    return detail


async def _run_fetch_step(
    correlation_id: str,
    github_url: str,
    github_token: str | None,
) -> tuple[list[RepoFile] | None, JSONResponse | None]:
    """Run fetch_repo_files step; return (files, None) on success or (None, error_response) on failure.

    Why: Keeps summarize() under 20 lines by extracting step logic.
    What: Awaits async fetch, measures duration, logs audit step; on empty repo or exception returns error.

    Args:
        correlation_id: Request UUID.
        github_url: Repo URL from request.
        github_token: Optional GitHub token from settings.

    Returns:
        (files, None) on success; (None, JSONResponse) on failure (caller should return the response).
    """
    t0 = time.perf_counter()
    req_summary = {"github_url": github_url, "has_token": bool(github_token)}
    try:
        files = await fetch_repo_files(github_url, github_token=github_token)
        fetched_count = len(files)
        # Task: do not send binary files, lock files, node_modules, etc. to the LLM — filter early.
        files = [f for f in files if not should_skip_path((f.path or "").strip())]
        eligible_after_filter = len(files)
        filtered_out = fetched_count - eligible_after_filter
        duration_ms = (time.perf_counter() - t0) * 1000
        if not files:
            log_audit_step(
                correlation_id, "fetch_repo_files", "failure",
                step_index=1, input_summary=req_summary,
                output_summary={"file_count": 0, "fetched_count": fetched_count, "filtered_out": filtered_out},
                error_detail={"message": "Repository is empty or has no readable files", "where": "summary_api.main._run_fetch_step", "error_classification": "permanent"},
                duration_ms=duration_ms,
            )
            return None, _with_correlation_header(
                ErrorResponse(status="error", message="Repository is empty or has no readable files").model_dump(),
                404, correlation_id,
            )
        log_audit_step(
            correlation_id, "fetch_repo_files", "success",
            step_index=1, input_summary=req_summary,
            output_summary={
                "file_count": eligible_after_filter,
                "fetched_count": fetched_count,
                "filtered_out": filtered_out,
            },
            duration_ms=duration_ms,
        )
        return files, None
    except GitHubClientError as e:
        duration_ms = (time.perf_counter() - t0) * 1000
        log_audit_step(
            correlation_id, "fetch_repo_files", "failure",
            step_index=1, input_summary=req_summary,
            error_detail=_error_detail_with_classification(e, "summary_api.github_client.fetch_repo_files"),
            duration_ms=duration_ms,
        )
        write_to_dlq(
            correlation_id, "fetch_repo_files",
            request_summary=req_summary,
            error_detail=_error_detail_with_classification(e, "summary_api.github_client.fetch_repo_files"),
        )
        status, message = _github_error_to_status_and_message(e)
        return None, _with_correlation_header(
            ErrorResponse(status="error", message=message).model_dump(), status, correlation_id
        )
    except CircuitBreakerError as e:
        duration_ms = (time.perf_counter() - t0) * 1000
        log_audit_step(
            correlation_id, "fetch_repo_files", "failure",
            step_index=1, input_summary=req_summary,
            error_detail={"message": "Service temporarily unavailable (circuit open)", "where": "summary_api.github_client.fetch_repo_files", "error_classification": "transient"},
            duration_ms=duration_ms,
        )
        write_to_dlq(
            correlation_id, "fetch_repo_files",
            request_summary=req_summary,
            error_detail={"message": str(e), "error_classification": "transient"},
        )
        return None, _with_correlation_header(
            ErrorResponse(status="error", message="Service temporarily unavailable. Try again later.").model_dump(),
            503, correlation_id,
        )


@app.get("/")
def root() -> dict[str, str]:
    """Root route: point to the summarize endpoint and API docs."""
    return {
        "message": "Summary API. Use POST /summarize with {\"github_url\": \"https://github.com/owner/repo\"}",
        "docs": "/docs",
    }


async def _run_summary_graph_step(
    correlation_id: str,
    files: list[RepoFile],
    settings: object,
) -> tuple[dict | None, JSONResponse | None]:
    """Run 4-node graph (Selector → Summarizer → Decider → Synthesizer); return (final_state, None) or (None, error_response)."""
    try:
        final_state = await run_summary_graph(files, correlation_id, settings)
    except Exception as e:
        detail = error_detail_from_exception(e, "summary_api.workflow.run_summary_graph")
        detail["error_classification"] = "transient" if getattr(e, "is_transient", False) else "permanent"
        log_audit_step(correlation_id, "summary_graph", "failure", error_detail=detail)
        write_to_dlq(correlation_id, "summary_graph", request_summary={"file_count": len(files)}, error_detail=detail)
        return None, _with_correlation_header(
            ErrorResponse(status="error", message=str(e)).model_dump(), 502, correlation_id
        )
    errors = final_state.get("errors") or []
    if errors:
        last_err = errors[-1].get("message", "Graph completed with errors")
        log_audit_step(correlation_id, "summary_graph", "failure", error_detail={"errors": errors})
        write_to_dlq(correlation_id, "summary_graph", request_summary={"file_count": len(files)}, error_detail={"errors": errors})
        return None, _with_correlation_header(
            ErrorResponse(status="error", message=last_err).model_dump(), 502, correlation_id
        )
    return final_state, None


@app.post("/summarize", response_model=SummarizeResponse)
async def summarize(
    request: SummarizeRequest, response: Response
) -> SummarizeResponse | JSONResponse:
    """Full flow: fetch → 4-node graph (Selector → Summarizer → Decider → Synthesizer) → return JSON per spec."""
    correlation_id = str(uuid.uuid4())
    settings = get_settings()
    github_token = settings.GITHUB_TOKEN.get_secret_value() or None

    files, err = await _run_fetch_step(correlation_id, request.github_url, github_token)
    if err is not None:
        _audit(request.github_url, correlation_id, "failure", err.status_code, None)
        return err

    final_state, err = await _run_summary_graph_step(correlation_id, files, settings)
    if err is not None:
        _audit(request.github_url, correlation_id, "failure", err.status_code, None)
        return err

    result = final_state.get("final_summary") or {}
    _audit(request.github_url, correlation_id, "success", 200)
    summary_str = result.get("summary", "") or ""
    structure_str = result.get("structure", "") or ""
    logger.info(
        "Response lengths: summary=%d chars, structure=%d chars",
        len(summary_str),
        len(structure_str),
        extra={"correlation_id": correlation_id, "operation_name": "summarize"},
    )
    body = SummarizeResponse(
        summary=summary_str,
        technologies=result.get("technologies") or [],
        structure=structure_str,
    )
    body_bytes = json.dumps(
        body.model_dump(),
        indent=2,
        ensure_ascii=False,
    ).encode("utf-8")
    return Response(
        content=body_bytes,
        media_type="application/json",
        status_code=200,
        headers={"X-Correlation-ID": correlation_id},
    )
