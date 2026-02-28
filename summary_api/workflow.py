"""Repo summary 4-node graph: Selector → Summarizer → Decider → Synthesizer with conditional loop."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.memory import MemorySaver

from .config import Settings, get_settings
from .nodes import decider_node, selector_node, summarizer_node, synthesizer_node
from .models.state import SummaryGraphState

logger = logging.getLogger(__name__)


def _route_after_decider(state: SummaryGraphState) -> str:
    """Return next node name from Decider decision (continue → selector, done → synthesizer)."""
    decision = state.get("decision") or "done"
    return "selector" if decision == "continue" else "synthesizer"


def build_graph(settings: Settings | None = None) -> Any:
    """Build and compile the 4-node summary graph. Nodes close over settings for DI.

    Returns:
        Compiled graph (invoke with initial state). Saves visualization to images/ if present.
    """
    settings = settings or get_settings()

    def selector(state: SummaryGraphState) -> dict[str, Any]:
        return selector_node(state, settings)

    async def summarizer(state: SummaryGraphState) -> dict[str, Any]:
        return await summarizer_node(state, settings)

    def decider(state: SummaryGraphState) -> dict[str, Any]:
        return decider_node(state, settings)

    async def synthesizer(state: SummaryGraphState) -> dict[str, Any]:
        return await synthesizer_node(state, settings)

    workflow: StateGraph[SummaryGraphState] = StateGraph(SummaryGraphState)
    workflow.add_node("selector", selector)
    workflow.add_node("summarizer", summarizer)
    workflow.add_node("decider", decider)
    workflow.add_node("synthesizer", synthesizer)

    workflow.add_edge(START, "selector")
    workflow.add_edge("selector", "summarizer")
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
    all_repo_files: list[Any],
    correlation_id: str,
    settings: Settings | None = None,
) -> SummaryGraphState:
    """Initialize state, invoke graph until END; return final state."""
    settings = settings or get_settings()
    graph = build_graph(settings)

    initial: SummaryGraphState = {
        "all_repo_files": all_repo_files,
        "already_summarized_paths": [],
        "summarized_chunks": [],
        "current_batch_paths": [],
        "decision": "continue",
        "final_summary": None,
        "iteration_count": 0,
        "errors": [],
        "correlation_id": correlation_id,
    }

    config = {"configurable": {"thread_id": correlation_id}}
    final_state = await graph.ainvoke(initial, config=config)
    return final_state
