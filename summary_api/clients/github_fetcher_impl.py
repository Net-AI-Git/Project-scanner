"""GitHub implementation of RepoFetcher contract.

Implements: .cursor/rules/agents/agent-component-interfaces.
Separate module to avoid circular import (contracts import RepoFile from github_client).
"""

from __future__ import annotations

import httpx

from summary_api.clients.github_client import (
    DEFAULT_MAX_FILES,
    DEFAULT_TIMEOUT,
    RepoFile,
    fetch_repo_files,
)
from summary_api.contracts import RepoFetcher


class GitHubRepoFetcher(RepoFetcher):
    """RepoFetcher implementation using GitHub Contents API.

    Delegates to fetch_repo_files with circuit breaker and retry.
    """

    async def fetch(
        self,
        repo_url: str,
        *,
        api_base: str,
        token: str | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_files: int = DEFAULT_MAX_FILES,
    ) -> list[RepoFile]:
        """Fetch files from GitHub; implements RepoFetcher contract."""
        return await fetch_repo_files(
            repo_url,
            github_api_base=api_base,
            github_token=token,
            client=client,
            timeout=timeout,
            max_files=max_files,
        )
