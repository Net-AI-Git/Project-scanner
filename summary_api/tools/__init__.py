"""LangChain-style tools for agent use.

Implements: .cursor/rules/agents/agentic-logic-and-tools (Tool Definition, deep documentation).
"""

from summary_api.tools.summarize import get_summarize_repo_context_tool

__all__ = ["get_summarize_repo_context_tool"]
