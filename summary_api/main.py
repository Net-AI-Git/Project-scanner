"""FastAPI application: POST /summarize — full flow: GitHub → repo_processor → LLM → response."""

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

try:
    from .config import get_settings
    from .github_client import GitHubClientError, fetch_repo_files
    from .llm_client import LLMClientError, summarize_repo
    from .repo_processor import process_repo_files
    from .schemas import SummarizeRequest, SummarizeResponse
except ImportError:
    from config import get_settings
    from github_client import GitHubClientError, fetch_repo_files
    from llm_client import LLMClientError, summarize_repo
    from repo_processor import process_repo_files
    from schemas import SummarizeRequest, SummarizeResponse

app = FastAPI(title="Summary API", description="Summarize public GitHub repositories")


@app.exception_handler(RequestValidationError)
def validation_exception_handler(_request, exc: RequestValidationError) -> JSONResponse:
    """Return spec error body for validation errors (missing/invalid github_url)."""
    errors = exc.errors() or []
    if errors and len(errors) > 0:
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
        content={"status": "error", "message": msg},
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
    """Choose LLM provider and API key: Google if GOOGLE_API_KEY set, else Nebius."""
    google_key = (settings.GOOGLE_API_KEY.get_secret_value() or "").strip()
    if google_key:
        return "google", google_key
    nebius_key = (settings.NEBIUS_API_KEY.get_secret_value() or "").strip()
    if nebius_key:
        return "nebius", nebius_key
    return "nebius", ""


@app.get("/")
def root() -> dict[str, str]:
    """Root route: point to the summarize endpoint and API docs."""
    return {
        "message": "Summary API. Use POST /summarize with {\"github_url\": \"https://github.com/owner/repo\"}",
        "docs": "/docs",
    }


@app.post("/summarize", response_model=SummarizeResponse)
def summarize(request: SummarizeRequest) -> SummarizeResponse | JSONResponse:
    """Full flow: fetch repo → process context → LLM summarize → return JSON per spec."""
    settings = get_settings()
    github_token = settings.GITHUB_TOKEN.get_secret_value() or None

    try:
        files = fetch_repo_files(request.github_url, github_token=github_token)
    except GitHubClientError as e:
        status, message = _github_error_to_status_and_message(e)
        return JSONResponse(
            status_code=status,
            content={"status": "error", "message": message},
        )

    if not files:
        return JSONResponse(
            status_code=404,
            content={
                "status": "error",
                "message": "Repository is empty or has no readable files",
            },
        )

    context = process_repo_files(files)
    provider, api_key = _get_llm_provider_and_key(settings)

    try:
        result = summarize_repo(context, api_key=api_key, provider=provider)
    except LLMClientError as e:
        status, message = _llm_error_to_status_and_message(e)
        return JSONResponse(
            status_code=status,
            content={"status": "error", "message": message},
        )

    return SummarizeResponse(
        summary=result.get("summary", "") or "",
        technologies=result.get("technologies") or [],
        structure=result.get("structure", "") or "",
    )
