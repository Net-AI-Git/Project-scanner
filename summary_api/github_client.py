"""GitHub API client: fetch repository file list and contents for public repos."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List

import httpx

# Default timeout for GitHub API and content requests
DEFAULT_TIMEOUT = 30.0
# Max files to fetch to avoid excessive requests and rate limits
DEFAULT_MAX_FILES = 500

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
    """

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


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
        GitHubClientError: If URL is not a valid GitHub repo URL.
    """
    if not github_url or not isinstance(github_url, str):
        raise GitHubClientError("Invalid GitHub URL: URL is required")
    url = github_url.strip()
    # Match github.com/owner/repo with optional trailing slash or .git
    match = re.match(
        r"^https?://(?:www\.)?github\.com/([^/]+)/([^/]+?)(?:/.*)?(?:\.git)?/?$",
        url,
        re.IGNORECASE,
    )
    if not match:
        raise GitHubClientError("Invalid GitHub URL: must be https://github.com/owner/repo")
    owner, repo = match.group(1), match.group(2)
    if not owner or not repo or repo in ("", ".git"):
        raise GitHubClientError("Invalid GitHub URL: owner and repo are required")
    return owner, repo


def fetch_repo_files(
    github_url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    max_files: int = DEFAULT_MAX_FILES,
    github_token: str | None = None,
) -> List[RepoFile]:
    """Fetch list of files with content from a public GitHub repository.

    Uses GitHub Contents API: lists root, recurses into directories, and fetches
    raw content for each file. Stops after max_files to avoid rate limits and timeouts.

    Args:
        github_url: Full URL of the repo, e.g. https://github.com/psf/requests
        timeout: Request timeout in seconds.
        max_files: Maximum number of files to fetch (remaining are skipped).
        github_token: Optional GitHub token for higher rate limit (5000/h). From env GITHUB_TOKEN.

    Returns:
        List of RepoFile (path, content). Paths are relative to repo root.

    Raises:
        GitHubClientError: Invalid URL, repo not found/private, timeout, or network error.
    """
    owner, repo = _parse_github_url(github_url)
    headers: dict[str, str] = {}
    if github_token and github_token.strip():
        headers["Authorization"] = f"Bearer {github_token.strip()}"
    session = httpx.Client(timeout=timeout, headers=headers or None)
    files: List[RepoFile] = []

    try:
        _fetch_contents_recurse(
            session=session,
            owner=owner,
            repo=repo,
            path="",
            files=files,
            max_files=max_files,
        )
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise GitHubClientError("Repository not found or private") from e
        if e.response.status_code == 403:
            raise GitHubClientError(
                "GitHub API rate limit or access denied"
            ) from e
        raise GitHubClientError(
            f"GitHub API error: {e.response.status_code} {e.response.text[:200]}"
        ) from e
    except httpx.TimeoutException as e:
        raise GitHubClientError("Request to GitHub timed out") from e
    except httpx.RequestError as e:
        raise GitHubClientError(f"Network error: {e!s}") from e
    finally:
        session.close()

    return files


def _fetch_contents_recurse(
    session: httpx.Client,
    owner: str,
    repo: str,
    path: str,
    files: List[RepoFile],
    max_files: int,
) -> None:
    """List contents at path; for each file fetch content and append; for each dir recurse."""
    if len(files) >= max_files:
        return
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/contents/{path}" if path else f"{GITHUB_API_BASE}/repos/{owner}/{repo}/contents"
    resp = session.get(url)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        # Single file (path was a file)
        item = data
        if item.get("type") == "file":
            content = _get_file_content(session, item.get("download_url"))
            if content is not None:
                files.append(RepoFile(path=item.get("path", path), content=content))
        return
    for item in data:
        if len(files) >= max_files:
            return
        name = item.get("name") or ""
        item_path = item.get("path") or (f"{path}/{name}".lstrip("/") if path else name)
        if item.get("type") == "file":
            content = _get_file_content(session, item.get("download_url"))
            if content is not None:
                files.append(RepoFile(path=item_path, content=content))
        elif item.get("type") == "dir":
            _fetch_contents_recurse(
                session=session,
                owner=owner,
                repo=repo,
                path=item_path,
                files=files,
                max_files=max_files,
            )


def _get_file_content(session: httpx.Client, download_url: str | None) -> str | None:
    """Fetch raw file content from download_url. Returns None if binary or error."""
    if not download_url:
        return None
    try:
        resp = session.get(download_url)
        resp.raise_for_status()
        # Try to decode as UTF-8; skip binary/large files by checking content-type or size
        content_type = resp.headers.get("content-type", "")
        if "charset" in content_type or "text" in content_type or not content_type:
            try:
                return resp.text
            except Exception:
                return None
        # Binary or unknown: skip (return None so caller does not add to list)
        if "application/octet-stream" in content_type or "image/" in content_type:
            return None
        try:
            return resp.text
        except Exception:
            return None
    except Exception:
        return None
