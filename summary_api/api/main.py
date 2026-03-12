"""FastAPI application: POST /scan (security scan)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logging.getLogger("summary_api.clients.llm_client").setLevel(logging.INFO)

from summary_api.core.audit import log_audit
from summary_api.core.config import get_env_file_path, get_settings
from summary_api.models.schemas import ErrorResponse, ScanRequest
from summary_api.workflows import get_scan_graph

def _apply_langsmith_env(settings) -> None:
    """Apply LangSmith/LangChain env vars from Settings so the SDK sends traces when enabled."""
    os.environ["LANGCHAIN_TRACING_V2"] = (
        "true" if str(settings.LANGCHAIN_TRACING_V2).strip().lower() in ("true", "1") else "false"
    )
    os.environ["LANGCHAIN_PROJECT"] = (settings.LANGCHAIN_PROJECT or "summary-api").strip() or "summary-api"
    key = (settings.LANGCHAIN_API_KEY.get_secret_value() or "").strip()
    if key:
        os.environ["LANGCHAIN_API_KEY"] = key
    endpoint = (settings.LANGSMITH_ENDPOINT or "").strip()
    if endpoint:
        os.environ["LANGSMITH_ENDPOINT"] = endpoint


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Startup: configure logging, shared HTTP client (connection pool), LangSmith env, and log LLM config. Shutdown: close HTTP client."""
    _configure_structured_logging()
    settings = get_settings()
    _apply_langsmith_env(settings)
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


app = FastAPI(title="Scan API", description="Security scan for public GitHub repositories", lifespan=_lifespan)
logger = logging.getLogger(__name__)


def _configure_structured_logging() -> None:
    """Configure JSON structured logging when LOG_FORMAT=json for observability (from Settings)."""
    if not hasattr(_configure_structured_logging, "_done"):
        _configure_structured_logging._done = False
    if _configure_structured_logging._done:
        return
    if get_settings().LOG_FORMAT.strip().lower() == "json":
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
    """Root route: point to scan endpoint and API docs."""
    return {
        "message": "Scan API. POST /scan with {\"github_url\": \"https://github.com/owner/repo\"}",
        "docs": "/docs",
    }


@app.post("/scan")
async def scan(
    payload: ScanRequest, request: Request
) -> JSONResponse:
    """Security scan: fetch → process → planner → orchestrator → workers → md_writer → synthesizer.
    Returns only report_path to the saved Markdown report.
    """
    correlation_id = str(uuid.uuid4())
    settings = get_settings()
    nebius_key = (settings.NEBIUS_API_KEY.get_secret_value() or "").strip()
    md_queue: asyncio.Queue = asyncio.Queue()
    initial_state: dict = {
        "correlation_id": correlation_id,
        "github_url": payload.github_url,
        "github_token": settings.GITHUB_TOKEN.get_secret_value() or None,
        "github_api_base": settings.GITHUB_API_BASE,
        "audit_path": settings.AUDIT_LOG_PATH,
        "dlq_path": settings.DLQ_PATH,
        "max_context_chars": settings.MAX_CONTEXT_CHARS,
        "context_limit_tokens": settings.CONTEXT_LIMIT_TOKENS,
        "scan_goal": "Scan repository for security vulnerabilities",
        "scan_reports_dir": settings.SCAN_REPORTS_DIR,
        "nebius_api_key": nebius_key,
        "nebius_base_url": settings.NEBIUS_BASE_URL,
        "nebius_model": settings.NEBIUS_MODEL,
        "nebius_max_tokens": settings.NEBIUS_MAX_TOKENS,
        "http_client": getattr(request.app.state, "http_client", None),
        "errors": [],
        "md_queue": md_queue,
    }
    graph = get_scan_graph()
    run_config = {"metadata": {"correlation_id": correlation_id}}
    final_state = await graph.ainvoke(initial_state, run_config)
    err_resp = final_state.get("error_response")
    if err_resp:
        status_code = err_resp.get("status_code", 502)
        try:
            log_audit(
                event_type="api_request",
                resource="/scan",
                action="POST",
                result="failure",
                correlation_id=correlation_id,
                metadata={"github_url": payload.github_url, "status_code": status_code},
                audit_path=settings.AUDIT_LOG_PATH,
            )
        except Exception:
            pass
        return JSONResponse(
            status_code=status_code,
            content=err_resp.get("content", {}),
            headers={"X-Correlation-ID": err_resp.get("correlation_id", correlation_id)},
        )
    result = final_state.get("result") or {}
    report_path = result.get("report_path", "")
    if not report_path:
        try:
            log_audit(
                event_type="api_request",
                resource="/scan",
                action="POST",
                result="failure",
                correlation_id=correlation_id,
                metadata={"github_url": payload.github_url, "status_code": 502},
                audit_path=settings.AUDIT_LOG_PATH,
            )
        except Exception:
            pass
        return JSONResponse(
            status_code=502,
            content=ErrorResponse(status="error", message="No report_path from scan").model_dump(),
            headers={"X-Correlation-ID": correlation_id},
        )
    try:
        log_audit(
            event_type="api_request",
            resource="/scan",
            action="POST",
            result="success",
            correlation_id=correlation_id,
            metadata={"github_url": payload.github_url, "status_code": 200, "report_path": report_path},
            audit_path=settings.AUDIT_LOG_PATH,
        )
    except Exception:
        pass
    return JSONResponse(
        status_code=200,
        content={"report_path": report_path},
        headers={"X-Correlation-ID": correlation_id},
    )
