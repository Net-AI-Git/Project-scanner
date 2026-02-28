"""Repository processor: filter files, prioritize content, and build a single context string for the LLM."""

from __future__ import annotations

import re
from typing import Any, List, Sequence, Tuple

from summary_api.clients.github_client import RepoFile

# Root-level files (no path segment) go under this key.
ROOT_FOLDER_KEY = "(root)"

# Default context size: ~60k chars leaves room for prompt + response in typical 8k–32k context windows.
DEFAULT_MAX_CONTEXT_CHARS = 60_000

# Directory names we skip (case-insensitive).
SKIP_DIRS = frozenset({
    "node_modules", "__pycache__", ".git", "venv", ".venv", "env", ".env",
    "dist", "build", ".eggs", ".tox", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "vendor", "pods", ".idea", ".vscode", "coverage", "htmlcov", ".nx", ".turbo",
    "target", "bower_components", ".cache", "cache",
})

# File name patterns to skip: lock files, minified, source maps, binary files (per task: binary/lock/node_modules).
SKIP_FILE_PATTERNS = (
    re.compile(r"\.(min\.(js|css))$", re.I),
    re.compile(r"\.(map)$", re.I),
    re.compile(r"package-lock\.json$", re.I),
    re.compile(r"yarn\.lock$", re.I),
    re.compile(r"poetry\.lock$", re.I),
    re.compile(r"pipfile\.lock$", re.I),
    re.compile(r"Cargo\.lock$", re.I),
    re.compile(r"composer\.lock$", re.I),
    re.compile(r"\.lock$", re.I),
    # Binary / non-text: images, fonts, executables, compiled (task: binary files must be skipped).
    re.compile(r"\.(png|jpe?g|gif|webp|ico|bmp)$", re.I),
    re.compile(r"\.(woff2?|ttf|eot|otf)$", re.I),
    re.compile(r"\.(pdf|pyc|so|dll|exe|class|jar)$", re.I),
    # Extra lock/deps: pnpm, bun, Ruby, Go checksums.
    re.compile(r"pnpm-lock\.yaml$", re.I),
    re.compile(r"bun\.lock(b)?$", re.I),
    re.compile(r"Gemfile\.lock$", re.I),
    re.compile(r"go\.sum$", re.I),
    # Build artifacts: Rust, TypeScript, WebAssembly.
    re.compile(r"\.(rlib|rmeta|tsbuildinfo|wasm)$", re.I),
    # Data / DB (not source).
    re.compile(r"\.(sqlite|db)$", re.I),
    # Logs and temp.
    re.compile(r"\.(log|tmp|temp)$", re.I),
)

# High-priority files (included first): README, LICENSE, config at root or anywhere.
PRIORITY_README = re.compile(r"^(readme|read_me|contributing|changelog)(\.[a-z0-9]+)?$", re.I)
PRIORITY_LICENSE = re.compile(r"^license(\.[a-z0-9]+)?$", re.I)
PRIORITY_CONFIG_NAMES = frozenset({
    "package.json", "pyproject.toml", "requirements.txt", "requirements-dev.txt",
    "setup.py", "setup.cfg", "Cargo.toml", "go.mod", "go.sum", "Makefile",
    "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
    "tsconfig.json", "webpack.config.js", "CMakeLists.txt",
})


def _path_segments(path: str) -> List[str]:
    """Return path segments (dirs + final file name)."""
    return [p for p in path.replace("\\", "/").split("/") if p]


def _top_level_folder(path: str) -> str:
    """Return top-level folder for path: first segment or ROOT_FOLDER_KEY for root-level files.

    Why: Per-folder summarization groups files by one folder level only.
    What: One segment (e.g. src/foo/bar.py -> src); README.md -> (root).
    """
    segments = _path_segments(path)
    if len(segments) <= 1:
        return ROOT_FOLDER_KEY
    return segments[0]


def should_skip_path(path: str) -> bool:
    """Return True if this path should be skipped (binary dirs, lock files, binary files, etc.).

    Aligns with task: binary files, lock files, node_modules/ and similar must not be sent to the LLM.
    Applied early after fetch so skipped paths never enter the graph or selection pool.
    """
    segments = _path_segments(path)
    for seg in segments[:-1]:  # directories only
        seg_lower = seg.lower()
        if seg_lower in SKIP_DIRS:
            return True
        if seg_lower.endswith(".egg-info") or seg_lower == ".eggs":
            return True
    base = segments[-1] if segments else ""
    for pat in SKIP_FILE_PATTERNS:
        if pat.search(base):
            return True
    return False


def group_files_by_top_level_folder(files: Sequence[RepoFile]) -> dict[str, List[RepoFile]]:
    """Group files by top-level folder; skip paths are excluded from any group.

    Why: Per-folder summarization needs one list of files per folder.
    What: _top_level_folder for key; only include files where should_skip_path is False.

    Returns:
        Dict mapping folder name (or ROOT_FOLDER_KEY) to list of RepoFile.
    """
    out: dict[str, List[RepoFile]] = {}
    for f in files:
        path = f.path or ""
        if should_skip_path(path):
            continue
        key = _top_level_folder(path)
        if key not in out:
            out[key] = []
        out[key].append(f)
    return out


def _file_priority(path: str) -> int:
    """Lower number = higher priority (included first when truncating)."""
    segments = _path_segments(path)
    base = (segments[-1] or "").lower()
    dir_depth = len(segments) - 1
    # README / LICENSE at any depth: 0
    if PRIORITY_README.match(base) or PRIORITY_LICENSE.match(base):
        return 0
    # Root-level config: 1
    if dir_depth == 0 and base in {s.lower() for s in PRIORITY_CONFIG_NAMES}:
        return 1
    # Config files anywhere: 2
    if base in {s.lower() for s in PRIORITY_CONFIG_NAMES}:
        return 2
    # Root-level source/docs: 3
    if dir_depth <= 1:
        return 3
    # Deeper files: 4+
    return 4 + min(dir_depth, 5)


def _folder_sort_key(folder_name: str) -> tuple[int, str]:
    """Order folders: (root) first, then alphabetical."""
    if folder_name == ROOT_FOLDER_KEY:
        return (0, "")
    return (1, folder_name)


def _subfolder_key(path: str) -> str:
    """Second path segment for splitting large top-level folders; root files stay in (root)."""
    segments = _path_segments(path)
    if len(segments) <= 2:
        return path
    return "/".join(segments[:2])


def partition_into_batches(
    paths: List[str],
    settings: Any,
    path_to_size: dict[str, int] | None = None,
) -> List[List[str]]:
    """Partition paths into batches by semantic structure (top-level folder, then subfolder if needed).

    Groups by top-level folder (reuse _top_level_folder). Orders batches: (root) first, then
    other folders. If a folder exceeds max_chars or max_files, splits by subfolder. Uses
    _file_priority for ordering paths within each group.

    Args:
        paths: Eligible file paths (already filtered with should_skip_path).
        settings: Settings with SUMMARY_MAX_CONTEXT_CHARS_PER_BATCH, SUMMARY_MAX_FILES_PER_BATCH,
            SUMMARY_MAX_CHARS_COUNT_PER_FILE (optional, for effective size cap).
        path_to_size: Optional map path -> byte/size for respecting context budget when splitting.

    Returns:
        Ordered list of batches; each batch is a list of paths.
    """
    if not paths:
        return []
    max_chars = getattr(settings, "SUMMARY_MAX_CONTEXT_CHARS_PER_BATCH", 50_000)
    max_files = getattr(settings, "SUMMARY_MAX_FILES_PER_BATCH", 50)
    max_chars_per_file = getattr(settings, "SUMMARY_MAX_CHARS_COUNT_PER_FILE", 25_000)

    def effective_size(p: str) -> int:
        if path_to_size and p in path_to_size:
            sz = path_to_size[p]
            return min(sz, max_chars_per_file) if max_chars_per_file > 0 else sz
        return 0

    groups: dict[str, List[str]] = {}
    for p in paths:
        folder = _top_level_folder(p)
        if folder not in groups:
            groups[folder] = []
        groups[folder].append(p)

    for folder in groups:
        groups[folder].sort(key=lambda x: (_file_priority(x), x))

    batches: List[List[str]] = []
    for folder_name in sorted(groups.keys(), key=_folder_sort_key):
        group_paths = groups[folder_name]
        if not group_paths:
            continue
        total_size = sum(effective_size(p) for p in group_paths) if path_to_size else 0
        if (
            len(group_paths) <= max_files
            and (not path_to_size or total_size <= max_chars)
        ):
            batches.append(group_paths)
            continue
        subgroups: dict[str, List[str]] = {}
        for p in group_paths:
            key = _subfolder_key(p)
            if key not in subgroups:
                subgroups[key] = []
            subgroups[key].append(p)
        for subkey in sorted(subgroups.keys()):
            subpaths = subgroups[subkey]
            subpaths.sort(key=lambda x: (_file_priority(x), x))
            current: List[str] = []
            current_size = 0
            for p in subpaths:
                sz = effective_size(p) if path_to_size else max_chars // max(len(subpaths), 1)
                if current and (len(current) >= max_files or (path_to_size and current_size + sz > max_chars)):
                    batches.append(current)
                    current = []
                    current_size = 0
                current.append(p)
                if path_to_size:
                    current_size += sz
            if current:
                batches.append(current)
    return batches


def _build_directory_tree(paths: List[str], max_entries: int = 200) -> str:
    """Build a simple ASCII tree of paths for structure context."""
    if not paths:
        return "(no files)"
    seen: set[str] = set()
    lines: List[str] = []
    for p in sorted(paths)[:max_entries]:
        parts = p.replace("\\", "/").split("/")
        prefix = ""
        for i, part in enumerate(parts[:-1]):
            key = "/".join(parts[: i + 1])
            if key not in seen:
                seen.add(key)
                lines.append(f"{prefix}{part}/")
            prefix = prefix + "  "
        file_part = parts[-1]
        lines.append(f"{prefix}{file_part}")
    if len(paths) > max_entries:
        lines.append(f"... and {len(paths) - max_entries} more files")
    return "\n".join(lines)


def _build_context_for_files(
    files: List[RepoFile],
    max_chars: int,
) -> str:
    """Build one context string from already-filtered files (tree + key files, priority, truncation).

    Why: Shared by process_repo_files and per-folder context building.
    What: Caps single-file size, builds tree + key files section, enforces max_chars.
    """
    if not files:
        return "Repository has no included text files (all skipped or empty)."
    capped: List[RepoFile] = []
    single_cap = max(max_chars // 3, 1)
    for f in files:
        content = (f.content or "").strip()
        if len(content) > single_cap:
            content = content[:single_cap] + "\n\n[... truncated for context limit ...]"
        capped.append(RepoFile(path=f.path or "", content=content))
    paths = [f.path for f in capped]
    tree_section = "## Repository structure\n\n```\n" + _build_directory_tree(paths) + "\n```"
    parts: List[str] = [tree_section, "\n\n## Key files\n"]
    used = len(tree_section) + len("\n\n## Key files\n")
    omission_msg = "\n\n(Additional files omitted due to context limit.)"
    ordered = sorted(capped, key=lambda x: (_file_priority(x.path), x.path))
    for f in ordered:
        if used + len(omission_msg) >= max_chars:
            parts.append(omission_msg)
            break
        header = f"\n### {f.path}\n\n"
        body = f.content.strip()
        if not body:
            continue
        remaining = max_chars - used - len(header) - len(omission_msg)
        if remaining <= 0:
            parts.append(omission_msg)
            break
        if len(body) > remaining:
            body = body[:remaining] + "\n\n[... truncated ...]"
        parts.append(header + body)
        used += len(header) + len(body)
    return "".join(parts)


def process_repo_files(
    files: Sequence[RepoFile],
    max_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
) -> str:
    """Filter, prioritize, and merge repo files into a single context string for the LLM.

    - Skips: binary dirs (node_modules, __pycache__, .git, venv, ...), lock files, minified files.
    - Prioritizes: README, LICENSE, config files (package.json, pyproject.toml, ...), then source.
    - Enforces max_chars by truncating low-priority file contents and then dropping files.

    Returns:
        Single string: directory tree + key file contents, suitable for LLM context.
    """
    filtered: List[RepoFile] = []
    for f in files:
        path = f.path or ""
        if should_skip_path(path):
            continue
        filtered.append(RepoFile(path=path, content=f.content or ""))
    return _build_context_for_files(filtered, max_chars)


def process_repo_files_by_folder(
    files: Sequence[RepoFile],
    max_chars_per_folder: int | None = None,
) -> List[Tuple[str, str]]:
    """Group files by top-level folder, build context per folder; return (folder_name, context) list.

    Why: Two-phase summarization needs one context per folder for parallel LLM calls.
    What: group_files_by_top_level_folder then _build_context_for_files per group; sorted by folder.

    Args:
        files: All repo files (filtering applied inside grouping).
        max_chars_per_folder: Cap per folder. If None or 0, use DEFAULT_MAX_CONTEXT_CHARS / num_folders.

    Returns:
        List of (folder_name, context) ordered by folder name, e.g. [("(root)", "..."), ("src", "...")].
    """
    groups = group_files_by_top_level_folder(files)
    if not groups:
        return [(ROOT_FOLDER_KEY, "Repository has no included text files (all skipped or empty).")]
    n = len(groups)
    cap = max_chars_per_folder or max(1, DEFAULT_MAX_CONTEXT_CHARS // n)
    result: List[Tuple[str, str]] = []
    for folder_name in sorted(groups.keys()):
        group_files = groups[folder_name]
        ctx = _build_context_for_files(group_files, cap)
        result.append((folder_name, ctx))
    return result
