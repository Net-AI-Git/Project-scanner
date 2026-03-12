"""Tests for agent component interfaces and contract compliance.

Implements: .cursor/rules/agents/agent-component-interfaces (Interface Testing).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from summary_api.contracts import ContextBuilder, RepoFetcher
from summary_api.clients.github_client import RepoFile
from summary_api.clients.github_fetcher_impl import GitHubRepoFetcher
from summary_api.services.repo_processor import RepoContextBuilder
from summary_api.workflows.nodes import make_fetch_node, make_process_node
from summary_api.workflows.state import ScanState


# --- Interface compliance: concrete implementations implement ABCs ---


def test_github_repo_fetcher_implements_repo_fetcher() -> None:
    """GitHubRepoFetcher must implement RepoFetcher contract."""
    assert issubclass(GitHubRepoFetcher, RepoFetcher)
    fetcher = GitHubRepoFetcher()
    assert isinstance(fetcher, RepoFetcher)


def test_repo_context_builder_implements_context_builder() -> None:
    """RepoContextBuilder must implement ContextBuilder contract."""
    assert issubclass(RepoContextBuilder, ContextBuilder)
    processor = RepoContextBuilder()
    assert isinstance(processor, ContextBuilder)


# --- Node factories use injected dependencies (mocks) ---


def test_fetch_node_calls_injected_fetcher() -> None:
    """fetch_node must call the injected RepoFetcher.fetch with state params."""
    mock_fetcher = MagicMock(spec=RepoFetcher)
    mock_fetcher.fetch = AsyncMock(return_value=[RepoFile(path="README.md", content="Hello")])
    fetch_node = make_fetch_node(mock_fetcher)
    state: ScanState = {
        "correlation_id": "test-123",
        "github_url": "https://github.com/owner/repo",
        "github_api_base": "https://api.github.com",
        "audit_path": "",
        "dlq_path": "",
        "errors": [],
    }
    with patch("summary_api.workflows.nodes.log_audit_step"):
        result = asyncio.run(fetch_node(state))
    mock_fetcher.fetch.assert_called_once()
    call_kw = mock_fetcher.fetch.call_args[1]
    assert call_kw["api_base"] == "https://api.github.com"
    assert result.get("files") == [RepoFile(path="README.md", content="Hello")]
    assert result.get("errors") == []
    assert result.get("ERROR_COUNT") == 0


def test_process_node_calls_injected_processor() -> None:
    """process_node must call the injected ContextBuilder.build_context."""
    mock_processor = MagicMock(spec=ContextBuilder)
    mock_processor.build_context.return_value = "## Repository structure\n\n## Key files\n\n### README\n\nHi"
    process_node = make_process_node(mock_processor)
    state: ScanState = {
        "correlation_id": "test-123",
        "audit_path": "",
        "files": [RepoFile(path="README.md", content="Hi")],
        "max_context_chars": 60_000,
    }
    with patch("summary_api.workflows.nodes.log_audit_step"):
        result = process_node(state)
    mock_processor.build_context.assert_called_once_with(
        [RepoFile(path="README.md", content="Hi")],
        max_chars=60_000,
    )
    assert result.get("context") == "## Repository structure\n\n## Key files\n\n### README\n\nHi"
