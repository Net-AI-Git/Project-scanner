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


# --- Batch summary: one batch of file context → short summary (4-node graph Summarizer) ---

BATCH_SUMMARY_SYSTEM_PROMPT = """You are a technical writer. Given the contents of a batch of repository files, produce one short explanatory summary in JSON format with a single key "summary". Describe what these files contain and their role in the project. Be concise (2-4 sentences). No markdown code fences.

Format: {"summary": "Your concise summary here."}"""

BATCH_SUMMARY_USER_PROMPT_TEMPLATE = """Summarize this batch of repository files.

<batch>{batch_label}</batch>

<context>
{context}
</context>"""


def build_batch_summary_messages(batch_label: str, context: str) -> list[dict[str, str]]:
    """Build system + user messages for batch summarization (Summarizer node).

    Args:
        batch_label: Short label for the batch (e.g. paths or "batch 1").
        context: Prepared context string for the batch.

    Returns:
        List of dicts with role and content for chat completion.
    """
    return [
        {"role": "system", "content": BATCH_SUMMARY_SYSTEM_PROMPT},
        {"role": "user", "content": BATCH_SUMMARY_USER_PROMPT_TEMPLATE.format(batch_label=batch_label, context=context)},
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


# --- Plan batches: given repo structure, choose files and split into batches by priority ---

PLAN_BATCHES_SYSTEM_PROMPT = """You are a planner for a repository summarization workflow. You will receive:
1) A directory tree of a repository (filtered: no node_modules, lock files, binaries, etc.).
2) The full list of file paths in that tree.

Your task: Choose which files are needed to understand what this repository does (purpose, main functionality, technologies). Then split those files into batches ordered by importance. Each batch will be sent to a summarizer one after the other.

Rules:
- Use ONLY paths from the provided list. Do not invent paths.
- Order batches by priority: first batch = most important (e.g. README, root config, main entry points); later batches = supporting or secondary (tests, docs, other modules).
- Keep batches small enough for a single LLM context (e.g. up to ~30–50 files per batch, or fewer if files are large).
- Reply with valid JSON only. No markdown code fences, no explanation outside the JSON.

Format:
{"batches": [["path1", "path2", ...], ["path3", ...], ...]}

Example: {"batches": [["README.md", "package.json", "src/index.js"], ["src/utils.js", "src/api.js"], ["tests/unit.js"]]}"""

PLAN_BATCHES_USER_PROMPT_TEMPLATE = """Repository directory structure:

<structure>
{structure_text}
</structure>

Full list of eligible file paths (use only these paths in your reply):

<paths>
{paths_list}
</paths>

Produce JSON with key "batches": a list of batches, each batch a list of paths from the above list, ordered by priority (first batch = highest priority)."""


def build_plan_batches_messages(structure_text: str, paths: list[str]) -> list[dict[str, str]]:
    """Build system + user messages for LLM to plan batches from repo structure.

    Args:
        structure_text: ASCII directory tree (e.g. from _build_directory_tree).
        paths: Full list of eligible file paths; LLM must use only these in batches.

    Returns:
        List of dicts with role and content for chat completion.
    """
    paths_list = "\n".join(paths) if paths else "(no paths)"
    return [
        {"role": "system", "content": PLAN_BATCHES_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": PLAN_BATCHES_USER_PROMPT_TEMPLATE.format(
                structure_text=structure_text,
                paths_list=paths_list,
            ),
        },
    ]


# --- Decider: does the latest batch change our understanding of what the project DOES? ---

DECIDER_SYSTEM_PROMPT = """You are a judge for a repository summarization workflow. You will see:
1) Previous batch summaries (so far) — they already tell us something about the project.
2) The latest batch summary just produced.

Your task: Does the latest batch summary CHANGE our understanding of WHAT THIS PROJECT DOES?
By "what the project does" we mean: its purpose, main functionality, what problem it solves, or who it is for.

Answer with exactly one word: "continue" or "done".
- "done" = the latest summary does NOT change what the project does. For example: it only adds meta/process info (documentation, CI/CD, GitHub config, license, contributing guidelines, tests, certificates) and the core "what this project does" was already clear from previous summaries. We have enough to synthesize.
- "continue" = the latest summary DOES change or materially add to our understanding of what the project does (e.g. new core features, main components, or purpose we did not know before).

Reply with only the word, no explanation."""

DECIDER_USER_PROMPT_TEMPLATE = """Previous batch summaries:

<previous>
{previous_summaries}
</previous>

Latest batch summary:

<latest>
{current_summary}
</latest>

Does the latest change our understanding of what this project DOES? Answer: continue or done?"""


def build_decider_messages(previous_summaries: list[str], current_summary: str) -> list[dict[str, str]]:
    """Build system + user messages for Decider (continue or done based on content).

    Args:
        previous_summaries: Concatenated or list of prior batch summary texts.
        current_summary: The latest batch summary text.

    Returns:
        List of dicts with role and content for chat completion.
    """
    previous_blob = "\n\n".join(previous_summaries) if previous_summaries else "(none yet)"
    return [
        {"role": "system", "content": DECIDER_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": DECIDER_USER_PROMPT_TEMPLATE.format(
                previous_summaries=previous_blob,
                current_summary=current_summary or "",
            ),
        },
    ]
