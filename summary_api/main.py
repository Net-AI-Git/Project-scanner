"""FastAPI application: POST /summarize — full flow: GitHub → repo_processor → LLM → response."""

import logging
import time
import uuid

from fastapi import FastAPI, Response

# Ensure LLM payload logs (what we send before each call) are visible when server runs
logging.getLogger("summary_api.llm_client").setLevel(logging.INFO)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

try:
    from .audit import error_detail_from_exception, log_audit, log_audit_step
    from .config import get_env_file_path, get_settings
    from .github_client import GitHubClientError, fetch_repo_files
    from .llm_client import LLMClientError, summarize_repo
    from .repo_processor import process_repo_files
    from .schemas import ErrorResponse, SummarizeRequest, SummarizeResponse
except ImportError:
    from audit import error_detail_from_exception, log_audit, log_audit_step
    from config import get_env_file_path, get_settings
    from github_client import GitHubClientError, fetch_repo_files
    from llm_client import LLMClientError, summarize_repo
    from repo_processor import process_repo_files
    from schemas import ErrorResponse, SummarizeRequest, SummarizeResponse

app = FastAPI(title="Summary API", description="Summarize public GitHub repositories")


@app.on_event("startup")
def _log_llm_config() -> None:
    """Log whether LLM API key is loaded (no secrets). Helps verify .env is read."""
    settings = get_settings()
    env_path = get_env_file_path()
    nebius_set = bool((settings.NEBIUS_API_KEY.get_secret_value() or "").strip())
    logging.getLogger(__name__).info(
        "Config: env_file=%s, NEBIUS_API_KEY=%s",
        env_path,
        "set" if nebius_set else "not set",
    )


@app.exception_handler(RequestValidationError)
def validation_exception_handler(_request, exc: RequestValidationError) -> JSONResponse:
    """Return spec error body for validation errors (missing/invalid github_url)."""
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
    """Map GitHubClientError to HTTP status code and user-facing message."""
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
    """Map LLMClientError to HTTP status code and user-facing message."""
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


def _get_llm_provider_and_key(settings) -> tuple[str, str]:
    """Return Nebius as provider and NEBIUS_API_KEY (or empty string if not set)."""
    nebius_key = (settings.NEBIUS_API_KEY.get_secret_value() or "").strip()
    return "nebius", nebius_key


@app.get("/")
def root() -> dict[str, str]:
    """Root route: point to the summarize endpoint and API docs."""
    return {
        "message": "Summary API. Use POST /summarize with {\"github_url\": \"https://github.com/owner/repo\"}",
        "docs": "/docs",
    }


def _audit(request_github_url: str, correlation_id: str, result: str, status_code: int, message: str | None = None) -> None:
    """Write one audit entry; swallow errors so response is never broken."""
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


def _with_correlation_header(content: dict, status: int, correlation_id: str) -> JSONResponse:
    """Build JSONResponse with X-Correlation-ID for LLM-as-Judge trace lookup."""
    return JSONResponse(status_code=status, content=content, headers={"X-Correlation-ID": correlation_id})


@app.post("/summarize", response_model=SummarizeResponse)
def summarize(request: SummarizeRequest, response: Response) -> SummarizeResponse | JSONResponse:
    """Full flow: fetch repo → process context → LLM summarize → return JSON per spec."""
    correlation_id = str(uuid.uuid4())
    settings = get_settings()
    github_token = settings.GITHUB_TOKEN.get_secret_value() or None

    # Step 1: fetch_repo_files
    t0 = time.perf_counter()
    try:
        files = fetch_repo_files(request.github_url, github_token=github_token)
        duration_ms = (time.perf_counter() - t0) * 1000
        if not files:
            log_audit_step(
                correlation_id,
                "fetch_repo_files",
                "failure",
                step_index=1,
                input_summary={"github_url": request.github_url, "has_token": bool(github_token)},
                output_summary={"file_count": 0},
                error_detail={"message": "Repository is empty or has no readable files", "where": "summary_api.main.summarize"},
                duration_ms=duration_ms,
            )
            _audit(request.github_url, correlation_id, "failure", 404, "Repository is empty or has no readable files")
            return _with_correlation_header(
                ErrorResponse(status="error", message="Repository is empty or has no readable files").model_dump(),
                404,
                correlation_id,
            )
        log_audit_step(
            correlation_id,
            "fetch_repo_files",
            "success",
            step_index=1,
            input_summary={"github_url": request.github_url, "has_token": bool(github_token)},
            output_summary={"file_count": len(files)},
            duration_ms=duration_ms,
        )
    except GitHubClientError as e:
        duration_ms = (time.perf_counter() - t0) * 1000
        log_audit_step(
            correlation_id,
            "fetch_repo_files",
            "failure",
            step_index=1,
            input_summary={"github_url": request.github_url, "has_token": bool(github_token)},
            error_detail=error_detail_from_exception(e, "summary_api.github_client.fetch_repo_files"),
            duration_ms=duration_ms,
        )
        status, message = _github_error_to_status_and_message(e)
        _audit(request.github_url, correlation_id, "failure", status, message)
        return _with_correlation_header(ErrorResponse(status="error", message=message).model_dump(), status, correlation_id)

    # Step 2: process_repo_files
    t1 = time.perf_counter()
    try:
        context = process_repo_files(files)
        duration_ms = (time.perf_counter() - t1) * 1000
        log_audit_step(
            correlation_id,
            "process_repo_files",
            "success",
            step_index=2,
            input_summary={"file_count": len(files)},
            output_summary={"context_length": len(context)},
            duration_ms=duration_ms,
        )
    except Exception as e:
        duration_ms = (time.perf_counter() - t1) * 1000
        log_audit_step(
            correlation_id,
            "process_repo_files",
            "failure",
            step_index=2,
            input_summary={"file_count": len(files)},
            error_detail=error_detail_from_exception(e, "summary_api.repo_processor.process_repo_files"),
            duration_ms=duration_ms,
        )
        _audit(request.github_url, correlation_id, "failure", 500, str(e))
        return _with_correlation_header(ErrorResponse(status="error", message=str(e)).model_dump(), 500, correlation_id)

    # Step 3: summarize_repo (LLM) — base_url and model from Settings so .env overrides apply
    provider, api_key = _get_llm_provider_and_key(settings)
    t2 = time.perf_counter()
    try:
        result = summarize_repo(
            context,
            api_key=api_key,
            base_url=settings.NEBIUS_BASE_URL,
            model=settings.NEBIUS_MODEL,
            max_tokens=settings.NEBIUS_MAX_TOKENS,
        )
        duration_ms = (time.perf_counter() - t2) * 1000
        log_audit_step(
            correlation_id,
            "summarize_repo",
            "success",
            step_index=3,
            input_summary={"context_length": len(context), "provider": provider},
            output_summary={
                "summary_length": len(result.get("summary", "") or ""),
                "technologies_count": len(result.get("technologies") or []),
                "structure_length": len(result.get("structure", "") or ""),
            },
            duration_ms=duration_ms,
        )
    except LLMClientError as e:
        duration_ms = (time.perf_counter() - t2) * 1000
        log_audit_step(
            correlation_id,
            "summarize_repo",
            "failure",
            step_index=3,
            input_summary={"context_length": len(context), "provider": provider},
            error_detail=error_detail_from_exception(e, "summary_api.llm_client.summarize_repo"),
            duration_ms=duration_ms,
        )
        status, message = _llm_error_to_status_and_message(e)
        _audit(request.github_url, correlation_id, "failure", status, message)
        return _with_correlation_header(ErrorResponse(status="error", message=message).model_dump(), status, correlation_id)

    _audit(request.github_url, correlation_id, "success", 200)
    response.headers["X-Correlation-ID"] = correlation_id
    summary_str = result.get("summary", "") or ""
    structure_str = result.get("structure", "") or ""
    # Log lengths so you can tell: if summary_len is small, LLM truncated; if large, client/UI is truncating display
    logging.getLogger(__name__).info(
        "Response lengths: summary=%d chars, structure=%d chars",
        len(summary_str),
        len(structure_str),
    )
    return SummarizeResponse(
        summary=summary_str,
        technologies=result.get("technologies") or [],
        structure=structure_str,
    )
