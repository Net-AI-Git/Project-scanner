#!/usr/bin/env python3
"""
Debug Summary API flow — fixed REPO.
Runs the same flow as POST /summarize on https://github.com/Net-AI-Git/Project-scanner,
printing each step: what is fetched, what is filtered, which files are sent to the LLM and in what order, and the metrics.

Run from project root with venv activated:
  python scripts/debug_repo_flow.py         — full flow including LLM call (requires NEBIUS_API_KEY)
  python scripts/debug_repo_flow.py --no-llm  — no LLM; stop after building context
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Allow running from project root or from scripts/
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# --- Fixed REPO ---
FIXED_REPO_URL = "https://github.com/Net-AI-Git/Project-scanner"

# ---------------------------------------------------------------------------
# Step 0: Parameters and settings
# ---------------------------------------------------------------------------
def step0_params():
    from summary_api.config import get_settings
    from summary_api.github_client import _parse_github_url, DEFAULT_MAX_FILES
    from summary_api.repo_processor import DEFAULT_MAX_CONTEXT_CHARS

    print("\n" + "=" * 70)
    print("Step 0: Parameters and settings")
    print("=" * 70)
    print(f"  Fixed REPO: {FIXED_REPO_URL}")
    try:
        owner, repo = _parse_github_url(FIXED_REPO_URL)
        print(f"  owner/repo: {owner!r} / {repo!r}")
    except Exception as e:
        print(f"  URL parse error: {e}")
        raise
    print(f"  max_files (GitHub): {DEFAULT_MAX_FILES}")
    print(f"  max_context_chars: {DEFAULT_MAX_CONTEXT_CHARS}")
    settings = get_settings()
    has_github = bool((settings.GITHUB_TOKEN.get_secret_value() or "").strip())
    has_nebius = bool((settings.NEBIUS_API_KEY.get_secret_value() or "").strip())
    print(f"  GITHUB_TOKEN: {'set (5000 req/h)' if has_github else 'not set — 60/h limit'}")
    print(f"  NEBIUS_API_KEY: {'set' if has_nebius else 'not set'}")
    print()


# ---------------------------------------------------------------------------
# Step 1: Fetch files from GitHub
# ---------------------------------------------------------------------------
def step1_fetch(github_url: str, github_token: str | None):
    from summary_api.github_client import fetch_repo_files, GitHubClientError

    print("\n" + "=" * 70)
    print("Step 1: Fetch files from GitHub")
    print("=" * 70)
    print("  GitHub Contents API: lists directory contents; only files (not dirs as items).")
    print("  Each file: GET to download_url, UTF-8 content. Binary files are skipped.")
    print("  Stops after max_files.")
    print()
    try:
        files = asyncio.run(fetch_repo_files(github_url, github_token=github_token))
    except GitHubClientError as e:
        print(f"  Error: {e.message}")
        raise
    print(f"  Files fetched: {len(files)}")
    paths = [f.path for f in files]
    for i, p in enumerate(sorted(paths)):
        print(f"    [{i+1}] {p}")
    lengths = [len(f.content or "") for f in files]
    if lengths:
        print(f"\n  Content metrics (chars): min={min(lengths)}, max={max(lengths)}, total={sum(lengths)}")
    print()
    return files


# ---------------------------------------------------------------------------
# Step 2: Filter — what is skipped and why
# ---------------------------------------------------------------------------
def _path_segments(path: str) -> list[str]:
    return [p for p in path.replace("\\", "/").split("/") if p]


def _skip_reason(path: str) -> str | None:
    """Return skip reason if path is skipped, else None."""
    from summary_api.repo_processor import SKIP_DIRS, SKIP_FILE_PATTERNS

    segments = _path_segments(path)
    for seg in segments[:-1]:
        seg_lower = seg.lower()
        if seg_lower in SKIP_DIRS:
            return f"dir in SKIP_DIRS: {seg!r}"
        if seg_lower.endswith(".egg-info") or seg_lower == ".eggs":
            return f"dir: {seg!r} (.egg-info/.eggs)"
    base = segments[-1] if segments else ""
    for pat in SKIP_FILE_PATTERNS:
        if pat.search(base):
            return f"file pattern: {pat.pattern!r} matches {base!r}"
    return None


def step2_filter(files: list):
    from summary_api.repo_processor import SKIP_DIRS, SKIP_FILE_PATTERNS, should_skip_path

    print("\n" + "=" * 70)
    print("Step 2: Filter files (what is skipped)")
    print("=" * 70)
    print("  Directories skipped (SKIP_DIRS):")
    for d in sorted(SKIP_DIRS):
        print(f"    - {d}")
    print("  File patterns skipped (SKIP_FILE_PATTERNS): lock, .min.js/.min.css, .map, etc.")
    print()
    skipped = []
    kept = []
    for f in files:
        path = f.path or ""
        reason = _skip_reason(path)
        if reason:
            skipped.append((path, reason))
        else:
            kept.append(f)
    print("  Files skipped (path + reason):")
    for path, reason in skipped:
        print(f"    - {path!r}  => {reason}")
    print(f"\n  Summary: {len(skipped)} skipped, {len(kept)} kept.")
    print()
    return kept


# ---------------------------------------------------------------------------
# Step 3: Priority order
# ---------------------------------------------------------------------------
def step3_priorities(files: list):
    from summary_api.repo_processor import _file_priority

    print("\n" + "=" * 70)
    print("Step 3: Priority order — which files are sent and in what order")
    print("=" * 70)
    print("  Priority: lower number = sent first. Sort key: (priority, path).")
    print("  0 = README/LICENSE/CONTRIBUTING/CHANGELOG (any depth)")
    print("  1 = config file at root (package.json, requirements.txt, Dockerfile, ...)")
    print("  2 = config file anywhere")
    print("  3 = files at depth 0–1")
    print("  4+ = deeper files")
    print()
    with_priority = [(f.path, _file_priority(f.path)) for f in files]
    ordered = sorted(with_priority, key=lambda x: (x[1], x[0]))
    print("  (path, priority) — send order:")
    for path, prio in ordered:
        print(f"    [{prio}] {path}")
    print()
    return ordered


# ---------------------------------------------------------------------------
# Step 4: Build context
# ---------------------------------------------------------------------------
def step4_context(files: list):
    from summary_api.repo_processor import (
        DEFAULT_MAX_CONTEXT_CHARS,
        process_repo_files,
    )

    print("\n" + "=" * 70)
    print("Step 4: Build context (what is sent to the LLM)")
    print("=" * 70)
    print(f"  max_context_chars = {DEFAULT_MAX_CONTEXT_CHARS}")
    print("  Single-file truncation: up to max_chars//3 chars; rest replaced with '[... truncated for context limit ...]'")
    print("  Directory tree: up to 200 entries (_build_directory_tree)")
    print("  When space runs out: '(Additional files omitted due to context limit.)' is appended")
    print()
    context = process_repo_files(files, max_chars=DEFAULT_MAX_CONTEXT_CHARS)
    print(f"  Final context length: {len(context)} chars")
    has_omitted = "(Additional files omitted due to context limit.)" in context
    print(f"  Files omitted due to context limit: {'yes' if has_omitted else 'no'}")
    preview_len = 1200
    preview = context[:preview_len]
    if len(context) > preview_len:
        preview += "\n\n[... preview truncated ...]"
    print(f"\n  Preview (first {min(preview_len, len(context))} chars):")
    print("-" * 40)
    print(preview)
    print("-" * 40)
    from summary_api.llm_client import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
    print("\n  LLM prompt structure:")
    print("    system: (role + JSON format)")
    print(f"    user: {USER_PROMPT_TEMPLATE.strip()[:80]}...")
    print(f"    Inside user: the context built above ({len(context)} chars).")
    print()
    return context


# ---------------------------------------------------------------------------
# Step 5: LLM call (optional)
# ---------------------------------------------------------------------------
def step5_llm(context: str):
    from summary_api.config import get_settings
    from summary_api.llm_client import summarize_repo, LLMClientError

    print("\n" + "=" * 70)
    print("Step 5: LLM call (Nebius Token Factory)")
    print("=" * 70)
    settings = get_settings()
    api_key = (settings.NEBIUS_API_KEY.get_secret_value() or "").strip()
    if not api_key:
        print("  Skipped: NEBIUS_API_KEY not set. Use .env or set the env var.")
        return
    print("  Sending context + system/user prompt; waiting for JSON (summary, technologies, structure).")
    print()
    try:
        result = asyncio.run(
            summarize_repo(
                context,
                api_key=api_key,
                base_url=settings.NEBIUS_BASE_URL,
                model=settings.NEBIUS_MODEL,
                max_tokens=settings.NEBIUS_MAX_TOKENS,
            )
        )
        print("  Success.")
        print(f"  summary: {result.get('summary', '')[:300]}...")
        print(f"  technologies: {result.get('technologies', [])}")
        print(f"  structure: {result.get('structure', '')[:300]}...")
    except LLMClientError as e:
        print(f"  Error: {e.message}")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="Debug Summary API flow on a fixed REPO")
    parser.add_argument("--no-llm", action="store_true", help="Do not call the LLM; stop after building context")
    args = parser.parse_args()

    from summary_api.config import get_settings

    settings = get_settings()
    github_token = (settings.GITHUB_TOKEN.get_secret_value() or "").strip() or None

    print("\n*** Debug Summary API flow — fixed REPO:", FIXED_REPO_URL, "***")

    step0_params()
    files = step1_fetch(FIXED_REPO_URL, github_token)
    if not files:
        print("No files — exiting.")
        return 1
    kept = step2_filter(files)
    if not kept:
        print("No files after filter — exiting.")
        return 1
    step3_priorities(kept)
    context = step4_context(kept)
    if not args.no_llm:
        step5_llm(context)
    else:
        print("\n(Skipping step 5 — --no-llm passed)\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
