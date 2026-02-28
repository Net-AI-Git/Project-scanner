"""Selection of the next batch of repo files for summarization (no duplicates)."""

from __future__ import annotations

from typing import Sequence

from summary_api.clients.github_client import RepoFile
from .repo_processor import should_skip_path

# Import priority helper for ordering; keep in sync with repo_processor.
from .repo_processor import _file_priority as file_priority_for_path  # noqa: PLC2701


def _normalize_already(already_summarized_paths: set[str] | Sequence[str]) -> set[str]:
    """Return a set of paths for duplicate check."""
    if isinstance(already_summarized_paths, set):
        return already_summarized_paths
    return set(already_summarized_paths)


def _path_to_file(repo_files: Sequence[RepoFile]) -> dict[str, RepoFile]:
    """Build path -> RepoFile for content size lookup."""
    return {f.path: f for f in repo_files if f.path}


def select_next_batch(
    repo_files: Sequence[RepoFile],
    already_summarized_paths: set[str] | Sequence[str],
    batch_size: int,
) -> list[str]:
    """Choose the next batch of paths to summarize, excluding already-summarized and skipped paths.

    Why: Selector node needs a deterministic, priority-ordered batch without re-reading files.
    What: Filter by should_skip_path, exclude already_summarized_paths, sort by priority then path, take batch_size.

    Args:
        repo_files: All repo files from fetch.
        already_summarized_paths: Paths already in summarized_chunks (no duplicate reads).
        batch_size: Max number of paths to return (from config SUMMARY_BATCH_SIZE).

    Returns:
        List of paths for the next batch, or empty if none left.
    """
    already = _normalize_already(already_summarized_paths)
    candidates: list[str] = []
    for f in repo_files:
        path = f.path or ""
        if not path or path in already or should_skip_path(path):
            continue
        candidates.append(path)
    candidates.sort(key=lambda p: (file_priority_for_path(p), p))
    return candidates[: batch_size] if batch_size > 0 else []


def _effective_file_size(size: int, max_chars_budget: int, max_chars_per_file: int) -> int:
    """Cap file size for selection so one huge file does not force a single-file batch.

    Why: When the next candidate is huge (e.g. AUTHORS.rst 80k), we would take 1 file per batch
    and pay one LLM call (~30s) per file. Capping counts lets us pack 2+ files per batch; Summarizer truncates.
    """
    if max_chars_per_file <= 0:
        return size
    return min(size, max_chars_per_file)


def select_next_batch_by_budget(
    repo_files: Sequence[RepoFile],
    already_summarized_paths: set[str] | Sequence[str],
    max_chars_budget: int,
    max_files_cap: int,
    max_chars_per_file: int = 0,
) -> list[str]:
    """Choose the next batch by character budget and file cap (context-safe, dynamic batch size).

    Why: Prevents context-window overflow; batch size varies by file sizes (few large vs many small).
    What: Priority order, then add paths until total (capped) content length reaches budget or max_files_cap.
    If max_chars_per_file > 0, each file counts at most that much so huge files do not force single-file batches.

    Args:
        repo_files: All repo files from fetch (used for content length).
        already_summarized_paths: Paths already in summarized_chunks.
        max_chars_budget: Stop adding when cumulative content length reaches this (e.g. 50_000).
        max_files_cap: Hard cap on number of paths per batch (e.g. 50).
        max_chars_per_file: If > 0, cap each file's contribution to this (avoids 1-file batches; default 0 = no cap).

    Returns:
        List of paths for the next batch, or empty if none left.
    """
    already = _normalize_already(already_summarized_paths)
    path_to_file = _path_to_file(repo_files)
    candidates: list[str] = []
    for path in path_to_file:
        if path in already or should_skip_path(path):
            continue
        candidates.append(path)
    candidates.sort(key=lambda p: (file_priority_for_path(p), p))

    chosen: list[str] = []
    total_chars = 0
    for path in candidates:
        if len(chosen) >= max_files_cap:
            break
        f = path_to_file.get(path)
        size = len((f.content or "").strip()) if f else 0
        effective = _effective_file_size(size, max_chars_budget, max_chars_per_file)
        if total_chars + effective > max_chars_budget and chosen:
            break
        chosen.append(path)
        total_chars += effective
    return chosen
