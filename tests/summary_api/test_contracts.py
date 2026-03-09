"""Tests for agent component interfaces and contract compliance.

Implements: .cursor/rules/agents/agent-component-interfaces (Interface Testing).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from summary_api.contracts import ContextBuilder, RepoFetcher, Summarizer
from summary_api.clients.github_client import RepoFile
from summary_api.clients.github_fetcher_impl import GitHubRepoFetcher
from summary_api.models.schemas import SummarizeResponse
from summary_api.services.repo_processor import RepoContextBuilder
from summary_api.services.summarizer import PydanticAISummarizer
from summary_api.workflows.graph import get_summarize_graph
from summary_api.workflows.nodes import make_fetch_node, make_process_node, make_summarize_node
from summary_api.workflows.state import SummarizeState


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


def test_pydantic_ai_summarizer_implements_summarizer() -> None:
    """PydanticAISummarizer must implement Summarizer contract."""
    assert issubclass(PydanticAISummarizer, Summarizer)
    summarizer = PydanticAISummarizer()
    assert isinstance(summarizer, Summarizer)


# --- Node factories use injected dependencies (mocks) ---


def test_fetch_node_calls_injected_fetcher() -> None:
    """fetch_node must call the injected RepoFetcher.fetch with state params."""
    mock_fetcher = MagicMock(spec=RepoFetcher)
    mock_fetcher.fetch = AsyncMock(return_value=[RepoFile(path="README.md", content="Hello")])
    fetch_node = make_fetch_node(mock_fetcher)
    state: SummarizeState = {
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
    state: SummarizeState = {
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


def test_summarize_node_calls_injected_summarizer() -> None:
    """summarize_node must call the injected Summarizer.summarize with state params."""
    mock_summarizer = MagicMock(spec=Summarizer)
    mock_summarizer.summarize = AsyncMock(
        return_value=SummarizeResponse(
            summary="A test repo",
            technologies=["Python"],
            structure="Flat.",
        )
    )
    summarize_node = make_summarize_node(mock_summarizer)
    state: SummarizeState = {
        "correlation_id": "test-123",
        "audit_path": "",
        "dlq_path": "",
        "context": "## Key files\n\nContent here.",
        "nebius_api_key": "sk-test",
        "nebius_base_url": "https://api.example.com",
        "nebius_model": "test-model",
        "nebius_max_tokens": 4096,
    }
    with patch("summary_api.workflows.nodes.log_audit_step"), patch(
        "summary_api.workflows.nodes.append_scratchpad"
    ):
        result = asyncio.run(summarize_node(state))
    mock_summarizer.summarize.assert_called_once()
    call_kw = mock_summarizer.summarize.call_args[1]
    assert call_kw["api_key"] == "sk-test"
    assert call_kw["base_url"] == "https://api.example.com"
    assert call_kw["model"] == "test-model"
    assert result.get("result") == {
        "summary": "A test repo",
        "technologies": ["Python"],
        "structure": "Flat.",
    }
    assert result.get("errors") == []
    assert result.get("ERROR_COUNT") == 0


def test_get_summarize_graph_accepts_optional_deps() -> None:
    """get_summarize_graph() must accept optional fetcher, processor, summarizer and compile."""
    graph = get_summarize_graph()
    assert graph is not None
    # With explicit defaults (None) we get the same
    graph2 = get_summarize_graph(fetcher=None, processor=None, summarizer=None)
    assert graph2 is not None
    # With custom mocks we get a graph that uses them
    mock_fetcher = MagicMock(spec=RepoFetcher)
    mock_processor = MagicMock(spec=ContextBuilder)
    mock_summarizer = MagicMock(spec=Summarizer)
    graph3 = get_summarize_graph(
        fetcher=mock_fetcher,
        processor=mock_processor,
        summarizer=mock_summarizer,
    )
    assert graph3 is not None
