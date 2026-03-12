"""Abstract base classes for agent component boundaries.

Implements: .cursor/rules/agents/agent-component-interfaces.
Use at boundary points where implementations may be swapped (fetcher, processor).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

import httpx

from summary_api.clients.github_client import RepoFile
from summary_api.models.schemas import SectionFindings


class RepoFetcher(ABC):
    """Contract for fetching repository file list and contents.

    Implementations may use GitHub API, local filesystem, or other backends.
    Input: repo URL and optional auth/config. Output: list of RepoFile.
    Exceptions: Caller must handle implementation-specific errors (e.g. GitHubClientError).
    Side effects: Network I/O when client is used.
    """

    @abstractmethod
    async def fetch(
        self,
        repo_url: str,
        *,
        api_base: str,
        token: str | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = 30.0,
        max_files: int = 500,
    ) -> list[RepoFile]:
        """Fetch files with content from a repository.

        Args:
            repo_url: Full URL of the repo (e.g. https://github.com/owner/repo).
            api_base: API base URL (e.g. GitHub API base).
            token: Optional auth token for higher rate limits.
            client: Optional shared async HTTP client for connection pooling.
            timeout: Request timeout in seconds.
            max_files: Maximum number of files to fetch.

        Returns:
            List of RepoFile (path, content). Paths relative to repo root.

        Raises:
            Implementation-specific exception (e.g. GitHubClientError) on invalid URL,
            not found, rate limit, timeout, or network error.
        """
        ...


class ContextBuilder(ABC):
    """Contract for building a single context string from repo files.

    Implementations filter, prioritize, and concatenate file contents for LLM context.
    Input: sequence of RepoFile and max character limit. Output: single string.
    Exceptions: May raise on invalid input; document in implementation.
    Side effects: None; pure transformation.
    """

    @abstractmethod
    def build_context(
        self,
        files: Sequence[RepoFile],
        max_chars: int = 60_000,
    ) -> str:
        """Build a single context string from repo files for the LLM.

        Args:
            files: List of RepoFile (path, content) from fetch step.
            max_chars: Maximum total context length.

        Returns:
            Single string: directory tree and key file contents, truncated to max_chars.
        """
        ...


class VulnerabilityScanner(ABC):
    """Contract for scanning one section (one file) for security vulnerabilities via LLM.

    Implementations use PydanticAI or other agents; return SectionFindings for one file.
    Input: file path and content (or context string). Output: SectionFindings.
    Exceptions: Implementation-specific (e.g. LLMClientError).
    Side effects: Network I/O to LLM API.
    """

    @abstractmethod
    async def scan(
        self,
        file_path: str,
        content: str,
        *,
        api_key: str,
        base_url: str,
        model: str,
        max_tokens: int = 4096,
        timeout: float = 120.0,
    ) -> SectionFindings:
        """Scan file content for security vulnerabilities and return structured findings.

        Args:
            file_path: Relative path of the file (for findings).
            content: Full file content to analyze.
            api_key: LLM API key.
            base_url: LLM API base URL.
            model: Model identifier.
            max_tokens: Max tokens to generate.
            timeout: Request timeout in seconds.

        Returns:
            SectionFindings (file_path + list of Finding for this file).

        Raises:
            Implementation-specific exception on auth failure, rate limit, timeout, or invalid response.
        """
        ...


class ReportSynthesizer(ABC):
    """Contract for merging worker outputs into a final VulnerabilityReport.

    Input: list of SectionFindings. Output: VulnerabilityReport (report_path + aggregated findings).
    Side effects: None; pure aggregation (MD file is written by md_writer node).
    """

    @abstractmethod
    def synthesize(
        self,
        worker_results: list[SectionFindings],
        report_path: str,
    ) -> dict:
        """Merge per-section findings into a single report structure.

        Args:
            worker_results: List of SectionFindings from all workers.
            report_path: Path to the saved MD file (set by md_writer).

        Returns:
            Dict suitable for state.result: report_path and findings (list of Finding).
        """
        ...
