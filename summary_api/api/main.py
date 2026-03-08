"""FastAPI application: POST /summarize — full flow: GitHub → repo_processor → LLM → response."""

from __future__ import annotations

import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response

logging.getLogger("summary_api.clients.llm_client").setLevel(logging.INFO)

try:
    from circuitbreaker import CircuitBreakerError  # type: ignore[import-untyped]
except ImportError:
    CircuitBreakerError = Exception  # noqa: A001

from summary_api.clients.github_client import GitHubClientError, RepoFile, fetch_repo_files
from summary_api.clients.llm_client import LLMClientError, summarize_repo
from summary_api.core.audit import error_detail_from_exception, log_audit, log_audit_step
from summary_api.core.config import get_env_file_path, get_settings
from summary_api.infrastructure.dlq import write_to_dlq
from summary_api.models.schemas import ErrorResponse, SummarizeRequest, SummarizeResponse
from summary_api.services.repo_processor import process_repo_files

@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Startup: configure logging, shared HTTP client (connection pool), and log LLM config. Shutdown: close HTTP client."""
    _configure_structured_logging()
    settings = get_settings()
    env_path = get_env_file_path()
    nebius_set = bool((settings.NEBIUS_API_KEY.get_secret_value() or "").strip())
    logger.info(
        "Config: env_file=%s, NEBIUS_API_KEY=%s",
        env_path,
        "set" if nebius_set else "not set",
    )
    app.state.http_client = httpx.AsyncClient(
        limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
    )
    try:
        yield
    finally:
        await app.state.http_client.aclose()


app = FastAPI(title="Summary API", description="Summarize public GitHub repositories", lifespan=_lifespan)
logger = logging.getLogger(__name__)


def _configure_structured_logging() -> None:
    """Configure JSON structured logging when LOG_FORMAT=json for observability."""
    if not hasattr(_configure_structured_logging, "_done"):
        _configure_structured_logging._done = False
    if _configure_structured_logging._done:
        return
    import os
    if os.environ.get("LOG_FORMAT") == "json":
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

    Raises:
        None.
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

    Raises:
        None.
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


def _llm_error_to_status_and_message(exc: LLMClientError) -> tuple[int, str]:
    """Map LLMClientError to HTTP status code and user-facing message.

    Why: Callers need a single (status, message) to return to the client.
    What: 401 auth, 429 rate limit, 502 timeout/server/network.

    Args:
        exc: The LLM client exception.

    Returns:
        (status_code, message) for the error response.

    Raises:
        None.
    """
    msg = exc.message or str(exc)
    if "authentication" in msg.lower() or "API key" in msg or "401" in msg:
        return 401, msg
    if "rate limit" in msg.lower() or "429" in msg:
        return 429, msg
    if "timed out" in msg.lower() or "network" in msg.lower():
        return 502, msg
    if "server error" in msg.lower() or "500" in msg:
        return 502, msg
    return 502, msg


def _get_llm_provider_and_key(settings: object) -> tuple[str, str]:
    """Return Nebius as provider and NEBIUS_API_KEY (or empty string if not set).

    Why: Centralizes secret access so callers never log the key.
    What: Reads SecretStr from settings; returns provider name and raw key for the client.

    Args:
        settings: Application Settings instance from get_settings().

    Returns:
        (provider_name, api_key_string).

    Raises:
        None.
    """
    raw = getattr(settings, "NEBIUS_API_KEY", None)
    if hasattr(raw, "get_secret_value"):
        nebius_key = (raw.get_secret_value() or "").strip()
    else:
        nebius_key = (raw or "").strip() if isinstance(raw, str) else ""
    return "nebius", nebius_key


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

    Raises:
        None. All exceptions from log_audit are caught and swallowed.
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

    Raises:
        None.
    """
    return JSONResponse(
        status_code=status,
        content=content,
        headers={"X-Correlation-ID": correlation_id},
    )


def _error_detail_with_classification(exc: BaseException, where: str) -> dict:
    """Build error_detail dict with error_classification (transient/permanent) for audit.

    Why: Error-handling rule requires classifying errors for retry vs no-retry and audit.
    What: Uses error_detail_from_exception then adds error_classification from exc.is_transient.

    Args:
        exc: The exception that was raised (may have is_transient attribute).
        where: Identifier of where it happened (e.g. module.function).

    Returns:
        Dict with message, where, traceback, and error_classification (transient or permanent).

    Raises:
        None.
    """
    detail = error_detail_from_exception(exc, where)
    detail["error_classification"] = (
        "transient" if getattr(exc, "is_transient", False) else "permanent"
    )
    return detail


def _fetch_step_empty_response(
    correlation_id: str,
    req_summary: dict,
    duration_ms: float,
    *,
    start_timestamp: str | None = None,
    end_timestamp: str | None = None,
) -> tuple[None, JSONResponse]:
    """Build (None, error_response) for empty repo; log audit step."""
    log_audit_step(
        correlation_id, "fetch_repo_files", "failure",
        step_index=1, input_summary=req_summary,
        output_summary={"file_count": 0},
        error_detail={
            "message": "Repository is empty or has no readable files",
            "where": "summary_api.main._run_fetch_step",
            "error_classification": "permanent",
        },
        duration_ms=duration_ms,
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
    )
    err_resp = _with_correlation_header(
        ErrorResponse(status="error", message="Repository is empty or has no readable files").model_dump(),
        404, correlation_id,
    )
    return None, err_resp


def _fetch_step_github_error(
    correlation_id: str,
    req_summary: dict,
    e: GitHubClientError,
    duration_ms: float,
    *,
    start_timestamp: str | None = None,
    end_timestamp: str | None = None,
) -> tuple[None, JSONResponse]:
    """Log, DLQ, and return error response for GitHubClientError in fetch step."""
    err_detail = _error_detail_with_classification(e, "summary_api.clients.github_client.fetch_repo_files")
    log_audit_step(
        correlation_id, "fetch_repo_files", "failure",
        step_index=1, input_summary=req_summary,
        error_detail=err_detail,
        duration_ms=duration_ms,
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
    )
    write_to_dlq(correlation_id, "fetch_repo_files", request_summary=req_summary, error_detail=err_detail)
    status, message = _github_error_to_status_and_message(e)
    return None, _with_correlation_header(
        ErrorResponse(status="error", message=message).model_dump(), status, correlation_id
    )


def _fetch_step_circuit_error(
    correlation_id: str,
    req_summary: dict,
    e: CircuitBreakerError,
    duration_ms: float,
    *,
    start_timestamp: str | None = None,
    end_timestamp: str | None = None,
) -> tuple[None, JSONResponse]:
    """Log, DLQ, and return 503 for CircuitBreakerError in fetch step."""
    log_audit_step(
        correlation_id, "fetch_repo_files", "failure",
        step_index=1, input_summary=req_summary,
        error_detail={
            "message": "Service temporarily unavailable (circuit open)",
            "where": "summary_api.clients.github_client.fetch_repo_files",
            "error_classification": "transient",
        },
        duration_ms=duration_ms,
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
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


async def _run_fetch_step(
    correlation_id: str,
    github_url: str,
    github_token: str | None,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> tuple[list[RepoFile] | None, JSONResponse | None]:
    """Run fetch_repo_files step; return (files, None) on success or (None, error_response) on failure.

    Why: Keeps summarize() under 20 lines by extracting step logic.
    What: Awaits async fetch, measures duration, logs audit step; on empty repo or exception returns error.

    Args:
        correlation_id: Request UUID.
        github_url: Repo URL from request.
        github_token: Optional GitHub token from settings.
        http_client: Optional shared HTTP client for connection pooling (R4).

    Returns:
        (files, None) on success; (None, JSONResponse) on failure (caller should return the response).
    """
    start_time = datetime.now(timezone.utc).isoformat()
    t0 = time.perf_counter()
    req_summary = {"github_url": github_url, "has_token": bool(github_token)}
    try:
        files = await fetch_repo_files(
            github_url, github_token=github_token, client=http_client
        )
        duration_ms = (time.perf_counter() - t0) * 1000
        end_time = datetime.now(timezone.utc).isoformat()
        if not files:
            return _fetch_step_empty_response(
                correlation_id, req_summary, duration_ms,
                start_timestamp=start_time, end_timestamp=end_time,
            )
        log_audit_step(
            correlation_id, "fetch_repo_files", "success",
            step_index=1, input_summary=req_summary,
            output_summary={"file_count": len(files)}, duration_ms=duration_ms,
            start_timestamp=start_time, end_timestamp=end_time,
        )
        return files, None
    except GitHubClientError as e:
        duration_ms = (time.perf_counter() - t0) * 1000
        end_time = datetime.now(timezone.utc).isoformat()
        return _fetch_step_github_error(
            correlation_id, req_summary, e, duration_ms,
            start_timestamp=start_time, end_timestamp=end_time,
        )
    except CircuitBreakerError as e:
        duration_ms = (time.perf_counter() - t0) * 1000
        end_time = datetime.now(timezone.utc).isoformat()
        return _fetch_step_circuit_error(
            correlation_id, req_summary, e, duration_ms,
            start_timestamp=start_time, end_timestamp=end_time,
        )


def _process_step_failure(
    correlation_id: str,
    file_count: int,
    e: Exception,
    duration_ms: float,
    *,
    start_timestamp: str | None = None,
    end_timestamp: str | None = None,
) -> tuple[None, JSONResponse]:
    """Log audit failure and return (None, error_response) for process step."""
    err_detail = {**error_detail_from_exception(e, "summary_api.services.repo_processor.process_repo_files"), "error_classification": "permanent"}
    log_audit_step(
        correlation_id, "process_repo_files", "failure",
        step_index=2, input_summary={"file_count": file_count},
        error_detail=err_detail,
        duration_ms=duration_ms,
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
    )
    return None, _with_correlation_header(
        ErrorResponse(status="error", message=str(e)).model_dump(), 500, correlation_id
    )


def _run_process_step(
    correlation_id: str,
    files: list[RepoFile],
) -> tuple[str | None, JSONResponse | None]:
    """Run process_repo_files step; return (context, None) on success or (None, error_response) on failure.

    Why: Keeps summarize() under 20 lines; process is sync and fast.
    What: Calls process_repo_files, measures duration, logs audit step.

    Args:
        correlation_id: Request UUID.
        files: List of repo files from fetch step.

    Returns:
        (context, None) on success; (None, JSONResponse) on failure.
    """
    start_time = datetime.now(timezone.utc).isoformat()
    t0 = time.perf_counter()
    try:
        context = process_repo_files(files)
        duration_ms = (time.perf_counter() - t0) * 1000
        end_time = datetime.now(timezone.utc).isoformat()
        log_audit_step(
            correlation_id, "process_repo_files", "success",
            step_index=2, input_summary={"file_count": len(files)},
            output_summary={"context_length": len(context)}, duration_ms=duration_ms,
            start_timestamp=start_time, end_timestamp=end_time,
        )
        return context, None
    except Exception as e:
        duration_ms = (time.perf_counter() - t0) * 1000
        end_time = datetime.now(timezone.utc).isoformat()
        return _process_step_failure(
            correlation_id, len(files), e, duration_ms,
            start_timestamp=start_time, end_timestamp=end_time,
        )


def _llm_step_success(
    correlation_id: str,
    input_summary: dict,
    result: dict,
    duration_ms: float,
    *,
    start_timestamp: str | None = None,
    end_timestamp: str | None = None,
) -> tuple[dict, None]:
    """Log success and return (result, None) for LLM step."""
    log_audit_step(
        correlation_id, "summarize_repo", "success",
        step_index=3, input_summary=input_summary,
        output_summary={
            "summary_length": len(result.get("summary", "") or ""),
            "technologies_count": len(result.get("technologies") or []),
            "structure_length": len(result.get("structure", "") or ""),
        },
        duration_ms=duration_ms,
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
    )
    return result, None


def _llm_step_llm_error(
    correlation_id: str,
    input_summary: dict,
    context_len: int,
    provider: str,
    e: LLMClientError,
    duration_ms: float,
    *,
    start_timestamp: str | None = None,
    end_timestamp: str | None = None,
) -> tuple[None, JSONResponse]:
    """Log, DLQ, and return error response for LLMClientError in LLM step."""
    err_detail = _error_detail_with_classification(e, "summary_api.clients.llm_client.summarize_repo")
    log_audit_step(
        correlation_id, "summarize_repo", "failure",
        step_index=3, input_summary=input_summary,
        error_detail=err_detail,
        duration_ms=duration_ms,
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
    )
    write_to_dlq(
        correlation_id, "summarize_repo",
        request_summary={"context_length": context_len, "provider": provider},
        error_detail=err_detail,
    )
    status, message = _llm_error_to_status_and_message(e)
    return None, _with_correlation_header(
        ErrorResponse(status="error", message=message).model_dump(), status, correlation_id
    )


def _llm_step_circuit_error(
    correlation_id: str,
    input_summary: dict,
    e: CircuitBreakerError,
    duration_ms: float,
    *,
    start_timestamp: str | None = None,
    end_timestamp: str | None = None,
) -> tuple[None, JSONResponse]:
    """Log, DLQ, and return 503 for CircuitBreakerError in LLM step."""
    log_audit_step(
        correlation_id, "summarize_repo", "failure",
        step_index=3, input_summary=input_summary,
        error_detail={
            "message": "Service temporarily unavailable (circuit open)",
            "where": "summary_api.clients.llm_client.summarize_repo",
            "error_classification": "transient",
        },
        duration_ms=duration_ms,
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
    )
    write_to_dlq(
        correlation_id, "summarize_repo",
        request_summary=input_summary,
        error_detail={"message": str(e), "error_classification": "transient"},
    )
    return None, _with_correlation_header(
        ErrorResponse(status="error", message="Service temporarily unavailable. Try again later.").model_dump(),
        503, correlation_id,
    )


async def _run_llm_step(
    correlation_id: str,
    context: str,
    settings: object,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> tuple[dict | None, JSONResponse | None]:
    """Run summarize_repo (LLM) step; return (result, None) on success or (None, error_response) on failure.

    Why: Keeps summarize() under 20 lines by extracting LLM call and audit.
    What: Gets provider/key from settings, awaits async summarize_repo, logs audit step; on exception writes DLQ and returns error.

    Args:
        correlation_id: Request UUID.
        context: Prepared context from process step.
        settings: Application Settings.
        http_client: Optional shared HTTP client for connection pooling (R4).

    Returns:
        (result_dict, None) on success; (None, JSONResponse) on failure.
    """
    start_time = datetime.now(timezone.utc).isoformat()
    provider, api_key = _get_llm_provider_and_key(settings)
    t0 = time.perf_counter()
    input_summary = {"context_length": len(context), "provider": provider}
    try:
        result = await summarize_repo(
            context,
            api_key=api_key,
            base_url=getattr(settings, "NEBIUS_BASE_URL", None),
            model=getattr(settings, "NEBIUS_MODEL", None),
            max_tokens=getattr(settings, "NEBIUS_MAX_TOKENS", 4096),
            client=http_client,
        )
        duration_ms = (time.perf_counter() - t0) * 1000
        end_time = datetime.now(timezone.utc).isoformat()
        return _llm_step_success(
            correlation_id, input_summary, result, duration_ms,
            start_timestamp=start_time, end_timestamp=end_time,
        )
    except LLMClientError as e:
        duration_ms = (time.perf_counter() - t0) * 1000
        end_time = datetime.now(timezone.utc).isoformat()
        return _llm_step_llm_error(
            correlation_id, input_summary, len(context), provider, e, duration_ms,
            start_timestamp=start_time, end_timestamp=end_time,
        )
    except CircuitBreakerError as e:
        duration_ms = (time.perf_counter() - t0) * 1000
        end_time = datetime.now(timezone.utc).isoformat()
        return _llm_step_circuit_error(
            correlation_id, input_summary, e, duration_ms,
            start_timestamp=start_time, end_timestamp=end_time,
        )


def _build_success_response(result: dict, correlation_id: str) -> Response:
    """Build 200 Response with SummarizeResponse body and X-Correlation-ID header.

    Why: Centralizes success response construction so summarize() stays under 20 lines.
    What: Logs lengths, builds SummarizeResponse, JSON-encodes, returns Response with header.
    """
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


# Health endpoints (R1/R2: liveness and readiness for K8s and monitoring)
HEALTH_READY_CACHE_SEC = 5.0
_ready_cache: tuple[float, bool] = (0.0, True)


def _check_ready() -> bool:
    """True if app is ready to serve (e.g. settings loaded). Used with ~5s cache per R2."""
    try:
        get_settings()
        return True
    except Exception:
        return False


@app.get("/health/live", summary="Liveness probe")
def health_live() -> dict[str, str]:
    """Liveness: process is alive. No external checks. For Kubernetes liveness probe."""
    return {"status": "ok"}


@app.get("/health/ready", summary="Readiness probe", response_model=None)
def health_ready() -> dict | JSONResponse:
    """Readiness: app ready to accept traffic. Cached ~5s per monitoring-and-observability rule."""
    global _ready_cache
    now = time.monotonic()
    cached_at, cached_ok = _ready_cache
    if now - cached_at < HEALTH_READY_CACHE_SEC:
        if cached_ok:
            return {"status": "ok"}
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "message": "Service not ready"},
        )
    ok = _check_ready()
    _ready_cache = (now, ok)
    if ok:
        return {"status": "ok"}
    return JSONResponse(
        status_code=503,
        content={"status": "not_ready", "message": "Service not ready"},
    )


@app.get("/")
def root() -> dict[str, str]:
    """Root route: point to the summarize endpoint and API docs."""
    return {
        "message": "Summary API. Use POST /summarize with {\"github_url\": \"https://github.com/owner/repo\"}",
        "docs": "/docs",
    }


@app.post("/summarize", response_model=SummarizeResponse)
async def summarize(
    payload: SummarizeRequest, response: Response, request: Request
) -> SummarizeResponse | JSONResponse:
    """Full flow: fetch repo → process context → LLM summarize → return JSON per spec.

    Why: Single entrypoint for the summarize API; delegates to step helpers for clarity and rule compliance (max 20 lines).
    What: Runs fetch, process, LLM steps in order; on any failure returns error response; on success audits and returns SummarizeResponse.
    """
    correlation_id = str(uuid.uuid4())
    settings = get_settings()
    github_token = settings.GITHUB_TOKEN.get_secret_value() or None
    http_client = getattr(request.app.state, "http_client", None)

    files, err = await _run_fetch_step(
        correlation_id, payload.github_url, github_token, http_client=http_client
    )
    if err is not None:
        _audit(payload.github_url, correlation_id, "failure", err.status_code, None)
        return err

    context, err = _run_process_step(correlation_id, files)
    if err is not None:
        _audit(payload.github_url, correlation_id, "failure", err.status_code, None)
        return err

    result, err = await _run_llm_step(
        correlation_id, context, settings, http_client=http_client
    )
    if err is not None:
        _audit(payload.github_url, correlation_id, "failure", err.status_code, None)
        return err

    _audit(payload.github_url, correlation_id, "success", 200)
    return _build_success_response(result, correlation_id)
