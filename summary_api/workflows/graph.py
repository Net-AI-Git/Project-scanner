"""LangGraph workflows: fetch → process → summarize; fetch → process → planner → orchestrator → workers → md_writer → synthesizer (scan).

Implements: .cursor/rules/agents/langgraph-architecture-and-nodes (Workflow Design).
Dependencies injected per agent-component-interfaces.
"""

from __future__ import annotations

from langgraph.graph import StateGraph
from langgraph.graph.state import CompiledStateGraph

from summary_api.clients.github_fetcher_impl import GitHubRepoFetcher
from summary_api.contracts import (
    ContextBuilder,
    RepoFetcher,
    ReportSynthesizer,
    Summarizer,
    VulnerabilityScanner,
)
from summary_api.services.repo_processor import RepoContextBuilder
from summary_api.services.summarizer import PydanticAISummarizer
from summary_api.services.vulnerability_scanner import PydanticAIVulnerabilityScanner
from summary_api.workflows.nodes import (
    make_fetch_node,
    make_process_node,
    make_summarize_node,
)
from summary_api.workflows.scan_nodes import (
    make_scan_md_writer_node,
    make_scan_orchestrator_node,
    make_scan_planner_node,
    make_scan_synthesizer_node,
    make_scan_workers_node,
)
from summary_api.workflows.state import ScanState, SummarizeState


def get_summarize_graph(
    *,
    fetcher: RepoFetcher | None = None,
    processor: ContextBuilder | None = None,
    summarizer: Summarizer | None = None,
) -> CompiledStateGraph[SummarizeState]:
    """Build and compile the summarize workflow graph.

    Args:
        fetcher: RepoFetcher implementation (default: GitHubRepoFetcher).
        processor: ContextBuilder implementation (default: RepoContextBuilder).
        summarizer: Summarizer implementation (default: PydanticAISummarizer).

    Returns:
        Compiled graph: fetch_node → process_node → summarize_node.
    """
    fetcher = fetcher or GitHubRepoFetcher()
    processor = processor or RepoContextBuilder()
    summarizer = summarizer or PydanticAISummarizer()
    workflow = StateGraph(SummarizeState)
    workflow.add_node("fetch_node", make_fetch_node(fetcher))
    workflow.add_node("process_node", make_process_node(processor))
    workflow.add_node("summarize_node", make_summarize_node(summarizer))
    workflow.set_entry_point("fetch_node")
    workflow.add_edge("fetch_node", "process_node")
    workflow.add_edge("process_node", "summarize_node")
    return workflow.compile()


def get_scan_graph(
    *,
    fetcher: RepoFetcher | None = None,
    processor: ContextBuilder | None = None,
    scanner: VulnerabilityScanner | None = None,
    synthesizer: ReportSynthesizer | None = None,
) -> CompiledStateGraph[ScanState]:
    """Build and compile the security scan workflow graph.

    Flow: fetch_node → process_node → planner_node → orchestrator_node
          → workers_node → md_writer_node → synthesizer_node.
    """
    fetcher = fetcher or GitHubRepoFetcher()
    processor = processor or RepoContextBuilder()
    scanner = scanner or PydanticAIVulnerabilityScanner()
    workflow = StateGraph(ScanState)
    workflow.add_node("fetch_node", make_fetch_node(fetcher))
    workflow.add_node("process_node", make_process_node(processor))
    workflow.add_node("planner_node", make_scan_planner_node())
    workflow.add_node("orchestrator_node", make_scan_orchestrator_node())
    workflow.add_node("workers_node", make_scan_workers_node(scanner))
    workflow.add_node("md_writer_node", make_scan_md_writer_node())
    workflow.add_node("synthesizer_node", make_scan_synthesizer_node(synthesizer))
    workflow.set_entry_point("fetch_node")
    workflow.add_edge("fetch_node", "process_node")
    workflow.add_edge("process_node", "planner_node")
    workflow.add_edge("planner_node", "orchestrator_node")
    workflow.add_edge("orchestrator_node", "workers_node")
    workflow.add_edge("workers_node", "md_writer_node")
    workflow.add_edge("md_writer_node", "synthesizer_node")
    return workflow.compile()
