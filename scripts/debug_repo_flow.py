#!/usr/bin/env python3
"""
Debug scan flow (fetch + process) — fixed REPO.
Runs fetch and context build for https://github.com/Net-AI-Git/Project-scanner,
logging what is fetched, filtered, and the context sent to downstream (e.g. scan).

Run from project root with venv activated:
  python scripts/debug_repo_flow.py
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

logger = logging.getLogger("debug_repo_flow")
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setLevel(logging.INFO)
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)

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
    from summary_api.core.config import get_settings
    from summary_api.clients.github_client import _parse_github_url, DEFAULT_MAX_FILES
    from summary_api.services.repo_processor import DEFAULT_MAX_CONTEXT_CHARS

    logger.info("\n" + "=" * 70)
    logger.info("Step 0: Parameters and settings")
    logger.info("=" * 70)
    logger.info("  Fixed REPO: %s", FIXED_REPO_URL)
    try:
        owner, repo = _parse_github_url(FIXED_REPO_URL)
        logger.info("  owner/repo: %r / %r", owner, repo)
    except Exception as e:
        logger.info("  URL parse error: %s", e)
        raise
    logger.info("  max_files (GitHub): %s", DEFAULT_MAX_FILES)
    logger.info("  max_context_chars: %s", DEFAULT_MAX_CONTEXT_CHARS)
    settings = get_settings()
    has_github = bool((settings.GITHUB_TOKEN.get_secret_value() or "").strip())
    has_nebius = bool((settings.NEBIUS_API_KEY.get_secret_value() or "").strip())
    logger.info("  GITHUB_TOKEN: %s", "set (5000 req/h)" if has_github else "not set — 60/h limit")
    logger.info("  NEBIUS_API_KEY: %s", "set" if has_nebius else "not set")
    logger.info("")


# ---------------------------------------------------------------------------
# Step 1: Fetch files from GitHub
# ---------------------------------------------------------------------------
def step1_fetch(github_url: str, github_token: str | None, github_api_base: str):
    from summary_api.clients.github_client import fetch_repo_files, GitHubClientError

    logger.info("\n" + "=" * 70)
    logger.info("Step 1: Fetch files from GitHub")
    logger.info("=" * 70)
    logger.info("  GitHub Contents API: lists directory contents; only files (not dirs as items).")
    logger.info("  Each file: GET to download_url, UTF-8 content. Binary files are skipped.")
    logger.info("  Stops after max_files.")
    logger.info("")
    try:
        files = asyncio.run(
            fetch_repo_files(
                github_url,
                github_api_base=github_api_base,
                github_token=github_token,
            )
        )
    except GitHubClientError as e:
        logger.info("  Error: %s", e.message)
        raise
    logger.info("  Files fetched: %s", len(files))
    paths = [f.path for f in files]
    for i, p in enumerate(sorted(paths)):
        logger.info("    [%s] %s", i + 1, p)
    lengths = [len(f.content or "") for f in files]
    if lengths:
        logger.info("  Content metrics (chars): min=%s, max=%s, total=%s", min(lengths), max(lengths), sum(lengths))
    logger.info("")
    return files


# ---------------------------------------------------------------------------
# Step 2: Filter — what is skipped and why
# ---------------------------------------------------------------------------
def _path_segments(path: str) -> list[str]:
    return [p for p in path.replace("\\", "/").split("/") if p]


def _skip_reason(path: str) -> str | None:
    """Return skip reason if path is skipped, else None."""
    from summary_api.services.repo_processor import SKIP_DIRS, SKIP_FILE_PATTERNS

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
    from summary_api.services.repo_processor import SKIP_DIRS, SKIP_FILE_PATTERNS, should_skip_path

    logger.info("\n" + "=" * 70)
    logger.info("Step 2: Filter files (what is skipped)")
    logger.info("=" * 70)
    logger.info("  Directories skipped (SKIP_DIRS):")
    for d in sorted(SKIP_DIRS):
        logger.info("    - %s", d)
    logger.info("  File patterns skipped (SKIP_FILE_PATTERNS): lock, .min.js/.min.css, .map, etc.")
    logger.info("")
    skipped = []
    kept = []
    for f in files:
        path = f.path or ""
        reason = _skip_reason(path)
        if reason:
            skipped.append((path, reason))
        else:
            kept.append(f)
    logger.info("  Files skipped (path + reason):")
    for path, reason in skipped:
        logger.info("    - %r  => %s", path, reason)
    logger.info("  Summary: %s skipped, %s kept.", len(skipped), len(kept))
    logger.info("")
    return kept


# ---------------------------------------------------------------------------
# Step 3: Priority order
# ---------------------------------------------------------------------------
def step3_priorities(files: list):
    from summary_api.services.repo_processor import _file_priority

    logger.info("\n" + "=" * 70)
    logger.info("Step 3: Priority order — which files are sent and in what order")
    logger.info("=" * 70)
    logger.info("  Priority: lower number = sent first. Sort key: (priority, path).")
    logger.info("  0 = README/LICENSE/CONTRIBUTING/CHANGELOG (any depth)")
    logger.info("  1 = config file at root (package.json, requirements.txt, Dockerfile, ...)")
    logger.info("  2 = config file anywhere")
    logger.info("  3 = files at depth 0–1")
    logger.info("  4+ = deeper files")
    logger.info("")
    with_priority = [(f.path, _file_priority(f.path)) for f in files]
    ordered = sorted(with_priority, key=lambda x: (x[1], x[0]))
    logger.info("  (path, priority) — send order:")
    for path, prio in ordered:
        logger.info("    [%s] %s", prio, path)
    logger.info("")
    return ordered


# ---------------------------------------------------------------------------
# Step 4: Build context
# ---------------------------------------------------------------------------
def step4_context(files: list):
    from summary_api.services.repo_processor import (
        DEFAULT_MAX_CONTEXT_CHARS,
        process_repo_files,
    )

    logger.info("\n" + "=" * 70)
    logger.info("Step 4: Build context (what is sent to the LLM)")
    logger.info("=" * 70)
    logger.info("  max_context_chars = %s", DEFAULT_MAX_CONTEXT_CHARS)
    logger.info("  Single-file truncation: up to max_chars//3 chars; rest replaced with '[... truncated for context limit ...]'")
    logger.info("  Directory tree: up to 200 entries (_build_directory_tree)")
    logger.info("  When space runs out: '(Additional files omitted due to context limit.)' is appended")
    logger.info("")
    context = process_repo_files(files, max_chars=DEFAULT_MAX_CONTEXT_CHARS)
    logger.info("  Final context length: %s chars", len(context))
    has_omitted = "(Additional files omitted due to context limit.)" in context
    logger.info("  Files omitted due to context limit: %s", "yes" if has_omitted else "no")
    preview_len = 1200
    preview = context[:preview_len]
    if len(context) > preview_len:
        preview += "\n\n[... preview truncated ...]"
    logger.info("  Preview (first %s chars):", min(preview_len, len(context)))
    logger.info("-" * 40)
    logger.info("%s", preview)
    logger.info("-" * 40)
    logger.info("")
    return context


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="Debug scan flow: fetch, filter, and build context for a fixed REPO")
    parser.parse_args()

    from summary_api.core.config import get_settings

    settings = get_settings()
    github_token = (settings.GITHUB_TOKEN.get_secret_value() or "").strip() or None

    logger.info("*** Debug flow (fetch + process) — fixed REPO: %s ***", FIXED_REPO_URL)

    step0_params()
    files = step1_fetch(FIXED_REPO_URL, github_token, settings.GITHUB_API_BASE)
    if not files:
        logger.info("No files — exiting.")
        return 1
    kept = step2_filter(files)
    if not kept:
        logger.info("No files after filter — exiting.")
        return 1
    step3_priorities(kept)
    step4_context(kept)
    return 0


if __name__ == "__main__":
    sys.exit(main())
