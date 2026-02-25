"""GitHub API client: fetch repository file list and contents for public repos."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List

import httpx
from circuitbreaker import circuit
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_random_exponential,
)

# Default timeout for GitHub API and content requests
DEFAULT_TIMEOUT = 30.0
# Max files to fetch to avoid excessive requests and rate limits
DEFAULT_MAX_FILES = 500
# Retry: 3 attempts, exponential backoff 1–60s with jitter
RETRY_ATTEMPTS = 3
RETRY_MIN_WAIT = 1
RETRY_MAX_WAIT = 60

# GitHub API base (no auth required for public repos)
GITHUB_API_BASE = "https://api.github.com"


@dataclass
class RepoFile:
    """A single file in the repository: path (relative to repo root) and decoded content."""

    path: str
    content: str


class GitHubClientError(Exception):
    """Raised for invalid URL, repo not found/private, timeout, or network errors.

    main.py can catch this and return an appropriate HTTP status and ErrorResponse.
    is_transient: True for errors that may succeed on retry (rate limit, timeout, 5xx).
    """

    def __init__(self, message: str, is_transient: bool = False) -> None:
        self.message = message
        self.is_transient = is_transient
        super().__init__(message)


def _is_github_transient(exc: BaseException) -> bool:
    """Return True if the exception is a transient GitHub error (retryable)."""
    return isinstance(exc, GitHubClientError) and getattr(exc, "is_transient", False)


def _parse_github_url(github_url: str) -> tuple[str, str]:
    """Extract owner and repo from a GitHub repository URL.

    Supports:
        https://github.com/owner/repo
        https://github.com/owner/repo/
        https://github.com/owner/repo.git
        http://github.com/owner/repo

    Returns:
        (owner, repo)

    Raises:
        GitHubClientError: If URL is not a valid GitHub repo URL (permanent).
    """
    if not github_url or not isinstance(github_url, str):
        raise GitHubClientError("Invalid GitHub URL: URL is required", is_transient=False)
    url = github_url.strip()
    match = re.match(
        r"^https?://(?:www\.)?github\.com/([^/]+)/([^/]+?)(?:/.*)?(?:\.git)?/?$",
        url,
        re.IGNORECASE,
    )
    if not match:
        raise GitHubClientError("Invalid GitHub URL: must be https://github.com/owner/repo", is_transient=False)
    owner, repo = match.group(1), match.group(2)
    if not owner or not repo or repo in ("", ".git"):
        raise GitHubClientError("Invalid GitHub URL: owner and repo are required", is_transient=False)
    return owner, repo


async def _get_file_content(
    client: httpx.AsyncClient, download_url: str | None
) -> str | None:
    """Fetch raw file content from download_url. Returns None if binary or error."""
    if not download_url:
        return None
    try:
        resp = await client.get(download_url)
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        if "charset" in content_type or "text" in content_type or not content_type:
            try:
                return resp.text
            except Exception:
                return None
        if "application/octet-stream" in content_type or "image/" in content_type:
            return None
        try:
            return resp.text
        except Exception:
            return None
    except Exception:
        return None


async def _fetch_contents_recurse(
    client: httpx.AsyncClient,
    owner: str,
    repo: str,
    path: str,
    files: List[RepoFile],
    max_files: int,
) -> None:
    """List contents at path; for each file fetch content and append; for each dir recurse."""
    if len(files) >= max_files:
        return
    url = (
        f"{GITHUB_API_BASE}/repos/{owner}/{repo}/contents/{path}"
        if path
        else f"{GITHUB_API_BASE}/repos/{owner}/{repo}/contents"
    )
    resp = await client.get(url)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        item = data
        if item.get("type") == "file":
            content = await _get_file_content(client, item.get("download_url"))
            if content is not None:
                files.append(RepoFile(path=item.get("path", path), content=content))
        return
    for item in data:
        if len(files) >= max_files:
            return
        name = item.get("name") or ""
        item_path = item.get("path") or (f"{path}/{name}".lstrip("/") if path else name)
        if item.get("type") == "file":
            content = await _get_file_content(client, item.get("download_url"))
            if content is not None:
                files.append(RepoFile(path=item_path, content=content))
        elif item.get("type") == "dir":
            await _fetch_contents_recurse(
                client=client,
                owner=owner,
                repo=repo,
                path=item_path,
                files=files,
                max_files=max_files,
            )


@circuit(failure_threshold=5, recovery_timeout=60, expected_exception=GitHubClientError)
@retry(
    retry=retry_if_exception(_is_github_transient),
    stop=stop_after_attempt(RETRY_ATTEMPTS),
    wait=wait_random_exponential(multiplier=1, min=RETRY_MIN_WAIT, max=RETRY_MAX_WAIT),
    reraise=True,
)
async def fetch_repo_files(
    github_url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    max_files: int = DEFAULT_MAX_FILES,
    github_token: str | None = None,
) -> List[RepoFile]:
    """Fetch list of files with content from a public GitHub repository (async).

    Uses GitHub Contents API with async httpx. Transient errors (rate limit, timeout,
    network) are retried with exponential backoff and jitter. Circuit breaker opens
    after 5 failures and blocks for 60s before half-open.

    Args:
        github_url: Full URL of the repo, e.g. https://github.com/owner/repo
        timeout: Request timeout in seconds.
        max_files: Maximum number of files to fetch.
        github_token: Optional GitHub token for higher rate limit (5000/h).

    Returns:
        List of RepoFile (path, content). Paths are relative to repo root.

    Raises:
        GitHubClientError: Invalid URL, repo not found/private, timeout, or network
            error after retries. is_transient True for retryable errors.
    """
    owner, repo = _parse_github_url(github_url)
    headers: dict[str, str] = {}
    if github_token and github_token.strip():
        headers["Authorization"] = f"Bearer {github_token.strip()}"
    files: List[RepoFile] = []
    async with httpx.AsyncClient(timeout=timeout, headers=headers or None) as client:
        try:
            await _fetch_contents_recurse(
                client=client,
                owner=owner,
                repo=repo,
                path="",
                files=files,
                max_files=max_files,
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise GitHubClientError(
                    "Repository not found or private", is_transient=False
                ) from e
            if e.response.status_code == 403:
                raise GitHubClientError(
                    "GitHub API rate limit or access denied", is_transient=True
                ) from e
            if e.response.status_code >= 500:
                raise GitHubClientError(
                    f"GitHub API error: {e.response.status_code} {e.response.text[:200]}",
                    is_transient=True,
                ) from e
            raise GitHubClientError(
                f"GitHub API error: {e.response.status_code} {e.response.text[:200]}",
                is_transient=False,
            ) from e
        except httpx.TimeoutException as e:
            raise GitHubClientError("Request to GitHub timed out", is_transient=True) from e
        except httpx.RequestError as e:
            raise GitHubClientError(f"Network error: {e!s}", is_transient=True) from e
    return files
