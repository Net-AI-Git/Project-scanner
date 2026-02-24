"""FastAPI application: POST /summarize endpoint (stub returns fixed success response)."""

from fastapi import FastAPI

try:
    from .schemas import SummarizeRequest, SummarizeResponse
except ImportError:
    from schemas import SummarizeRequest, SummarizeResponse

app = FastAPI(title="Summary API", description="Summarize public GitHub repositories")


@app.get("/")
def root() -> dict[str, str]:
    """Root route: point to the summarize endpoint and API docs."""
    return {
        "message": "Summary API. Use POST /summarize with {\"github_url\": \"https://github.com/owner/repo\"}",
        "docs": "/docs",
    }


@app.post("/summarize", response_model=SummarizeResponse)
def summarize(request: SummarizeRequest) -> SummarizeResponse:
    """Stub: accept github_url and return a fixed success response (no real processing yet)."""
    return SummarizeResponse(
        summary="(Stub) Repository summary will be generated here.",
        technologies=["(stub)"],
        structure="(Stub) Repository structure will be described here.",
    )
