"""Tests for summary_api.github_client: URL parsing, fetch, and error handling (real API when GITHUB_TOKEN set)."""

import os

import pytest

from summary_api.github_client import (
    GitHubClientError,
    RepoFile,
    _parse_github_url,
    fetch_repo_files,
)


# --- _parse_github_url ---


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://github.com/psf/requests", ("psf", "requests")),
        ("https://github.com/owner/repo", ("owner", "repo")),
        ("https://github.com/owner/repo/", ("owner", "repo")),
        ("https://github.com/owner/repo.git", ("owner", "repo")),
        ("http://github.com/a/b", ("a", "b")),
        ("https://www.github.com/foo/bar", ("foo", "bar")),
    ],
)
def test_parse_github_url_valid(url: str, expected: tuple[str, str]) -> None:
    """Valid GitHub URLs return (owner, repo)."""
    assert _parse_github_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "",
        "https://gitlab.com/owner/repo",
        "https://github.com",
        "https://github.com/owner",
        "https://github.com/owner/",
        "not-a-url",
    ],
)
def test_parse_github_url_invalid_raises(url: str) -> None:
    """Invalid URLs raise GitHubClientError (uniform error type for main to translate)."""
    with pytest.raises(GitHubClientError) as exc_info:
        _parse_github_url(url)
    assert "Invalid GitHub URL" in exc_info.value.message or str(exc_info.value)


def test_parse_github_url_none_raises() -> None:
    """None or empty raises GitHubClientError."""
    with pytest.raises(GitHubClientError):
        _parse_github_url("")


# --- fetch_repo_files: invalid URL (no network) ---


def test_fetch_repo_files_invalid_url_raises() -> None:
    """fetch_repo_files with invalid URL raises GitHubClientError."""
    with pytest.raises(GitHubClientError) as exc_info:
        fetch_repo_files("https://gitlab.com/owner/repo")
    assert "Invalid GitHub URL" in (exc_info.value.message or str(exc_info.value))


# --- fetch_repo_files: real API (require GITHUB_TOKEN) ---

def _github_token() -> str | None:
    t = os.environ.get("GITHUB_TOKEN", "").strip()
    return t or None


# --- fetch_repo_files: 404 (repo not found / private) ---


@pytest.mark.skipif(not _github_token(), reason="Set GITHUB_TOKEN to run real GitHub API tests")
def test_fetch_repo_files_404_raises() -> None:
    """fetch_repo_files with nonexistent repo raises GitHubClientError (real API)."""
    with pytest.raises(GitHubClientError) as exc_info:
        fetch_repo_files(
            "https://github.com/this-org-does-not-exist-xyz/this-repo-either-xyz",
            github_token=_github_token(),
        )
    msg = (exc_info.value.message or str(exc_info.value)).lower()
    assert "not found" in msg or "private" in msg


# --- fetch_repo_files: success (integration) ---


@pytest.mark.skipif(not _github_token(), reason="Set GITHUB_TOKEN to run real GitHub API tests")
def test_fetch_repo_files_returns_list_of_files() -> None:
    """Fetching psf/requests returns list of RepoFile with path and content (real API)."""
    files = fetch_repo_files(
        "https://github.com/psf/requests",
        max_files=15,
        github_token=_github_token(),
    )
    assert isinstance(files, list)
    assert len(files) > 0
    assert len(files) <= 15
    for f in files:
        assert isinstance(f, RepoFile)
        assert isinstance(f.path, str)
        assert isinstance(f.content, str)
        assert len(f.path) > 0


@pytest.mark.skipif(not _github_token(), reason="Set GITHUB_TOKEN to run real GitHub API tests")
def test_fetch_repo_files_includes_readme_content() -> None:
    """At least one file has path containing README and non-empty content (real API)."""
    files = fetch_repo_files(
        "https://github.com/psf/requests",
        max_files=50,
        github_token=_github_token(),
    )
    readmes = [f for f in files if "README" in f.path.upper()]
    assert len(readmes) >= 1, "Expected at least one README file"
    assert any(len(f.content) > 0 for f in readmes), "README content should be non-empty"
