"""Repo summary graph: Selector → Batch Fetcher → Summarizer → Decider → Synthesizer with conditional loop."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.memory import MemorySaver

from .config import Settings, get_settings
from .nodes import (
    batch_fetcher_node,
    decider_node,
    selector_node,
    summarizer_node,
    synthesizer_node,
)
from .models.state import SummaryGraphState

logger = logging.getLogger(__name__)


def _route_after_decider(state: SummaryGraphState) -> str:
    """Return next node name from Decider decision (continue → selector, done → synthesizer)."""
    decision = state.get("decision") or "done"
    return "selector" if decision == "continue" else "synthesizer"


def build_graph(settings: Settings | None = None) -> Any:
    """Build and compile the summary graph. Nodes close over settings for DI.

    Returns:
        Compiled graph (invoke with initial state). Saves visualization to images/ if present.
    """
    settings = settings or get_settings()

    def selector(state: SummaryGraphState) -> dict[str, Any]:
        return selector_node(state, settings)

    async def batch_fetcher(state: SummaryGraphState) -> dict[str, Any]:
        return await batch_fetcher_node(state, settings)

    async def summarizer(state: SummaryGraphState) -> dict[str, Any]:
        return await summarizer_node(state, settings)

    async def decider(state: SummaryGraphState) -> dict[str, Any]:
        return await decider_node(state, settings)

    async def synthesizer(state: SummaryGraphState) -> dict[str, Any]:
        return await synthesizer_node(state, settings)

    workflow: StateGraph[SummaryGraphState] = StateGraph(SummaryGraphState)
    workflow.add_node("selector", selector)
    workflow.add_node("batch_fetcher", batch_fetcher)
    workflow.add_node("summarizer", summarizer)
    workflow.add_node("decider", decider)
    workflow.add_node("synthesizer", synthesizer)

    workflow.add_edge(START, "selector")
    workflow.add_edge("selector", "batch_fetcher")
    workflow.add_edge("batch_fetcher", "summarizer")
    workflow.add_edge("summarizer", "decider")
    workflow.add_conditional_edges("decider", _route_after_decider, {"selector": "selector", "synthesizer": "synthesizer"})
    workflow.add_edge("synthesizer", END)

    memory = MemorySaver()
    compiled = workflow.compile(checkpointer=memory)

    try:
        images_dir = Path(__file__).resolve().parent.parent / "images"
        images_dir.mkdir(parents=True, exist_ok=True)
        png_path = images_dir / "summary_workflow.png"
        compiled.get_graph().draw_mermaid_png(png_path)
        logger.info("Workflow visualization saved to %s", png_path)
    except Exception as e:
        logger.debug("Could not save workflow image: %s", e)

    return compiled


async def run_summary_graph(
    correlation_id: str,
    repo_github_url: str,
    repo_tree_entries: list[Any],
    planned_batches: list[list[str]],
    settings: Settings | None = None,
) -> SummaryGraphState:
    """Initialize state, invoke graph until END; return final state.

    repo_github_url: Repo URL for batch blob fetch.
    repo_tree_entries: Filtered TreeEntry list (path, sha, size) from fetch_repo_tree.
    planned_batches: Precomputed list of path batches (from LLM plan_batches_from_structure).
    If None or empty, graph will have no batches and will go to synthesizer with empty chunks.
    """
    settings = settings or get_settings()
    graph = build_graph(settings)

    batches = planned_batches if planned_batches is not None else []

    initial: SummaryGraphState = {
        "repo_github_url": repo_github_url,
        "repo_tree_entries": repo_tree_entries,
        "planned_batches": batches,
        "current_batch_index": 0,
        "current_batch_paths": [],
        "current_batch_files": [],
        "already_summarized_paths": [],
        "summarized_chunks": [],
        "decision": "continue",
        "final_summary": None,
        "iteration_count": 0,
        "errors": [],
        "correlation_id": correlation_id,
    }

    config = {"configurable": {"thread_id": correlation_id}}
    final_state = await graph.ainvoke(initial, config=config)
    return final_state
