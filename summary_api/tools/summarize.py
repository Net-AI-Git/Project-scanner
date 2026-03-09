"""Summarize-repository tool for agent use.

Implements: .cursor/rules/agents/agentic-logic-and-tools (Tool Definition, deep documentation).
"""

from __future__ import annotations

from langchain_core.tools import tool

from summary_api.core.config import get_settings
from summary_api.models.schemas import SummarizeResponse
from summary_api.services.summarizer import PydanticAISummarizer


def get_summarize_repo_context_tool():
    """Build the summarize_repo_context tool with config from Settings (DI).

    Returns a LangChain @tool that summarizes repository context string and returns
    structured output (summary, technologies, structure). Use when an agent has already
    obtained repo context (e.g. from fetch+process) and needs a summary.

    Returns:
        LangChain tool callable with (context: str) -> dict.
    """
    summarizer = PydanticAISummarizer()

    @tool
    async def summarize_repo_context(context: str) -> dict:
        """Summarize a repository context string into a short summary, technology list, and structure description.

        Use this tool when you have the full text context of a codebase (e.g. directory tree + key file contents)
        and need a structured summary for the user or for downstream steps.

        Parameters
        ----------
        context : str
            The repository context string to summarize. Typically produced by:
            (1) Fetching file list and contents from a repo (e.g. GitHub),
            (2) Filtering and prioritizing files (e.g. skip node_modules, lock files),
            (3) Concatenating into a single string with a directory tree and key file sections.
            Must be non-empty. Length is typically bounded (e.g. 60_000 chars) to fit LLM context.

        Returns
        -------
        dict
            A dictionary with three keys:
            - "summary" (str): Short human-readable summary of the repository (purpose, main components).
            - "technologies" (list[str]): List of technologies, frameworks, or languages detected.
            - "structure" (str): Description of the repository layout and important paths.

        Raises
        ------
        (implementation-specific)
            May raise on missing API key, rate limit, timeout, or invalid LLM response.
            Caller should handle exceptions and surface user-friendly messages.

        Input example
        -------------
        context = "## Repository structure\\n\\n\\\\`\\\\`\\\\`\\nsrc/\\n  main.py\\n  utils.py\\n\\\\`\\\\`\\\\`\\n\\n## Key files\\n\\n### README.md\\n\\nMy project..."

        Output example
        --------------
        {
            "summary": "A Python CLI that processes files and outputs results.",
            "technologies": ["Python", "Click", "pytest"],
            "structure": "Entry point in src/main.py; utilities in src/utils.py; tests in tests/."
        }
        """
        settings = get_settings()
        api_key = (settings.NEBIUS_API_KEY.get_secret_value() or "").strip()
        result: SummarizeResponse = await summarizer.summarize(
            context,
            api_key=api_key,
            base_url=settings.NEBIUS_BASE_URL,
            model=settings.NEBIUS_MODEL,
            max_tokens=settings.NEBIUS_MAX_TOKENS,
        )
        return result.model_dump()

    return summarize_repo_context
