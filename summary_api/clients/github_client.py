"""GitHub API client: fetch repository file list and contents for public repos."""

from __future__ import annotations

import asyncio
import base64
import binascii
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
# Default concurrency for parallel blob fetches (batch download)
DEFAULT_BLOB_FETCH_CONCURRENCY = 25
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


@dataclass
class TreeEntry:
    """A single file path from the repo tree (no content). Used for structure-only fetch."""

    path: str
    size: int | None = None
    sha: str | None = None


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


async def fetch_repo_tree(
    github_url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    github_token: str | None = None,
) -> List[TreeEntry]:
    """Fetch full repository file tree (paths only, no content) using Git Tree API.

    Uses GET /repos/{owner}/{repo} for default branch, then commits for tree sha,
    then GET .../git/trees/{tree_sha}?recursive=1. Caller should filter result
    with should_skip_path to match eligible files.

    Args:
        github_url: Full URL of the repo, e.g. https://github.com/owner/repo
        timeout: Request timeout in seconds.
        github_token: Optional GitHub token for higher rate limit.

    Returns:
        List of TreeEntry (path, optional size, optional sha) for each blob (file).
        Directories and skipped paths are not filtered here; filter in caller.

    Raises:
        GitHubClientError: Invalid URL, repo not found/private, or API error.
    """
    owner, repo = _parse_github_url(github_url)
    headers: dict[str, str] = {}
    if github_token and github_token.strip():
        headers["Authorization"] = f"Bearer {github_token.strip()}"

    async with httpx.AsyncClient(timeout=timeout, headers=headers or None) as client:
        try:
            repo_resp = await client.get(f"{GITHUB_API_BASE}/repos/{owner}/{repo}")
            repo_resp.raise_for_status()
            repo_data = repo_resp.json()
            default_branch = repo_data.get("default_branch") or "main"

            commits_resp = await client.get(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/commits",
                params={"sha": default_branch, "per_page": 1},
            )
            commits_resp.raise_for_status()
            commits_data = commits_resp.json()
            if not commits_data or not isinstance(commits_data, list):
                raise GitHubClientError(
                    "Could not get latest commit for repository",
                    is_transient=False,
                )
            commit = commits_data[0]
            tree_sha = None
            if isinstance(commit, dict) and "commit" in commit:
                tree_sha = commit["commit"].get("tree", {}).get("sha")
            if not tree_sha:
                raise GitHubClientError(
                    "Could not get tree SHA from commit",
                    is_transient=False,
                )

            tree_resp = await client.get(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/trees/{tree_sha}",
                params={"recursive": "1"},
            )
            tree_resp.raise_for_status()
            tree_data = tree_resp.json()
            tree_list = tree_data.get("tree") or []
            if not isinstance(tree_list, list):
                raise GitHubClientError(
                    "Invalid tree response from GitHub",
                    is_transient=False,
                )

            entries: List[TreeEntry] = []
            for item in tree_list:
                if item.get("type") != "blob":
                    continue
                path = item.get("path") or ""
                if not path:
                    continue
                entries.append(
                    TreeEntry(
                        path=path,
                        size=item.get("size") if isinstance(item.get("size"), int) else None,
                        sha=item.get("sha") if isinstance(item.get("sha"), str) else None,
                    )
                )
            return entries
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


async def _fetch_single_blob(
    client: httpx.AsyncClient,
    owner: str,
    repo: str,
    entry: TreeEntry,
) -> RepoFile | None:
    """Fetch one blob by sha; decode base64 to UTF-8. Return None if binary. Raises GitHubClientError on API/network errors."""
    if not entry.sha:
        return None
    try:
        resp = await client.get(
            f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/blobs/{entry.sha}",
            headers={"Accept": "application/vnd.github+json"},
        )
        resp.raise_for_status()
        data = resp.json()
        raw = data.get("content") if isinstance(data, dict) else None
        encoding = data.get("encoding") if isinstance(data, dict) else None
        if not raw or encoding != "base64":
            return None
        # GitHub may return base64 with newlines; strip whitespace before decode.
        raw_clean = (raw if isinstance(raw, str) else "").replace(" ", "").replace("\n", "").replace("\r", "").replace("\t", "")
        try:
            decoded = base64.b64decode(raw_clean, validate=True)
        except (binascii.Error, ValueError):
            return None
        try:
            text = decoded.decode("utf-8")
        except UnicodeDecodeError:
            return None
        return RepoFile(path=entry.path, content=text)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise GitHubClientError(
                "Blob not found or repository inaccessible", is_transient=False
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


@circuit(failure_threshold=5, recovery_timeout=60, expected_exception=GitHubClientError)
@retry(
    retry=retry_if_exception(_is_github_transient),
    stop=stop_after_attempt(RETRY_ATTEMPTS),
    wait=wait_random_exponential(multiplier=1, min=RETRY_MIN_WAIT, max=RETRY_MAX_WAIT),
    reraise=True,
)
async def fetch_blob_contents_for_entries(
    github_url: str,
    entries: List[TreeEntry],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    github_token: str | None = None,
    max_concurrency: int = DEFAULT_BLOB_FETCH_CONCURRENCY,
) -> List[RepoFile]:
    """Fetch blob contents for given tree entries in parallel (Git Blob API).

    Uses asyncio.Semaphore to limit concurrency. Decodes base64 to UTF-8;
    entries that decode as binary are skipped (not included in result).

    Args:
        github_url: Full URL of the repo.
        entries: List of TreeEntry (path, sha); entries without sha are skipped.
        timeout: Request timeout in seconds.
        github_token: Optional GitHub token for higher rate limit.
        max_concurrency: Max concurrent blob requests.

    Returns:
        List of RepoFile (path, content) for entries that decoded as UTF-8 text.

    Raises:
        GitHubClientError: Invalid URL, repo not found, or API error after retries.
    """
    if not entries:
        return []
    owner, repo = _parse_github_url(github_url)
    headers: dict[str, str] = {}
    if github_token and github_token.strip():
        headers["Authorization"] = f"Bearer {github_token.strip()}"

    semaphore = asyncio.Semaphore(max_concurrency)
    files: List[RepoFile] = []
    entries_with_sha = [e for e in entries if e.sha]

    async def fetch_one(
        client: httpx.AsyncClient, entry: TreeEntry
    ) -> RepoFile | None:
        async with semaphore:
            return await _fetch_single_blob(client, owner, repo, entry)

    async with httpx.AsyncClient(timeout=timeout, headers=headers or None) as client:
        results = await asyncio.gather(
            *[fetch_one(client, e) for e in entries_with_sha],
            return_exceptions=True,
        )
    for r in results:
        if isinstance(r, RepoFile):
            files.append(r)
        elif isinstance(r, Exception):
            if isinstance(r, GitHubClientError):
                raise r
            raise GitHubClientError(
                f"Blob fetch error: {r!s}", is_transient=True
            ) from r
    return files


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
