"""Prompt templates for folder and project summarization. Kept out of LLM logic per configuration rules."""

from __future__ import annotations

# --- Legacy full-repo summary (used by summarize_repo) ---

REPO_SYSTEM_PROMPT = """You are a technical writer. Given repository file contents and structure, produce a short summary in the exact JSON format below. Use only the keys "summary", "technologies", and "structure". No other keys or markdown code fences.

Format:
{"summary": "1-3 sentences describing what the project does.", "technologies": ["Python", "FastAPI", ...], "structure": "Brief description of directory layout and key folders."}"""

REPO_USER_PROMPT_TEMPLATE = """Summarize this repository based on the following context.

{context}
"""


def build_repo_messages(context: str) -> list[dict[str, str]]:
    """Build system + user messages for full-repo summarization (legacy single-phase)."""
    return [
        {"role": "system", "content": REPO_SYSTEM_PROMPT},
        {"role": "user", "content": REPO_USER_PROMPT_TEMPLATE.format(context=context)},
    ]


# --- Folder summary: one folder's context → short explanatory summary ---

FOLDER_SYSTEM_PROMPT = """You are a technical writer. Given the contents of a single folder (or root) of a repository, produce one short explanatory summary in JSON format with a single key "summary". The summary should describe what this folder contains and its role in the project. Be concise (2-4 sentences). No markdown code fences.

Format: {"summary": "Your concise summary here."}"""

FOLDER_USER_PROMPT_TEMPLATE = """Summarize this folder of the repository.

<folder>{folder_name}</folder>

<context>
{context}
</context>"""


def build_folder_summary_messages(folder_name: str, context: str) -> list[dict[str, str]]:
    """Build system + user messages for folder summarization.

    Args:
        folder_name: Top-level folder name, e.g. "(root)" or "src".
        context: Prepared context string for that folder.

    Returns:
        List of dicts with role and content for chat completion.
    """
    return [
        {"role": "system", "content": FOLDER_SYSTEM_PROMPT},
        {"role": "user", "content": FOLDER_USER_PROMPT_TEMPLATE.format(folder_name=folder_name, context=context)},
    ]


# --- Project from folders: list of folder summaries → final summary, technologies, structure ---

PROJECT_SYSTEM_PROMPT = """You are a technical writer. Given per-folder summaries of a repository, produce a single project summary in the exact JSON format below. Use only the keys "summary", "technologies", and "structure". No other keys or markdown code fences.

Format: {"summary": "1-3 sentences describing what the project does.", "technologies": ["Python", "FastAPI", ...], "structure": "Brief description of directory layout and key folders."}"""

PROJECT_USER_PROMPT_TEMPLATE = """Synthesize a project summary from the following folder summaries.

<folder_summaries>
{folder_summaries}
</folder_summaries>"""


def build_project_from_folders_messages(folder_summaries: list[dict[str, str]]) -> list[dict[str, str]]:
    """Build system + user messages for project synthesis from folder summaries.

    Args:
        folder_summaries: List of {"folder": str, "summary": str}.

    Returns:
        List of dicts with role and content for chat completion.
    """
    lines = []
    for item in folder_summaries:
        folder = item.get("folder", "")
        summary = item.get("summary", "")
        lines.append(f"[{folder}]\n{summary}")
    blob = "\n\n".join(lines)
    return [
        {"role": "system", "content": PROJECT_SYSTEM_PROMPT},
        {"role": "user", "content": PROJECT_USER_PROMPT_TEMPLATE.format(folder_summaries=blob)},
    ]
