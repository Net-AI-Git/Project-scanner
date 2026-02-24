"""Repository processor: filter files, prioritize content, and build a single context string for the LLM."""

from __future__ import annotations

import re
from typing import List, Sequence

from .github_client import RepoFile

# Default context size: ~60k chars leaves room for prompt + response in typical 8k–32k context windows.
DEFAULT_MAX_CONTEXT_CHARS = 60_000

# Directory names we skip (case-insensitive).
SKIP_DIRS = frozenset({
    "node_modules", "__pycache__", ".git", "venv", ".venv", "env", ".env",
    "dist", "build", ".eggs", ".tox", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "vendor", "pods", ".idea", ".vscode", "coverage", "htmlcov", ".nx", ".turbo",
})

# File name patterns to skip: lock files, minified, source maps, large binaries.
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
)

# High-priority files (included first): README, LICENSE, config at root or anywhere.
PRIORITY_README = re.compile(r"^(readme|read_me|contributing|changelog)(\.[a-z0-9]+)?$", re.I)
PRIORITY_LICENSE = re.compile(r"^license(\.[a-z0-9]+)?$", re.I)
PRIORITY_CONFIG_NAMES = frozenset({
    "package.json", "pyproject.toml", "requirements.txt", "requirements-dev.txt",
    "setup.py", "setup.cfg", "Cargo.toml", "go.mod", "go.sum", "Makefile",
    "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
    "tsconfig.json", "webpack.config.js", "CMakeLists.txt", "Makefile",
})


def _path_segments(path: str) -> List[str]:
    """Return path segments (dirs + final file name)."""
    return [p for p in path.replace("\\", "/").split("/") if p]


def should_skip_path(path: str) -> bool:
    """Return True if this path should be skipped (binary dirs, lock files, etc.)."""
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
        content = f.content or ""
        if should_skip_path(path):
            continue
        # Cap single-file size to leave room for other files
        if len(content) > max_chars // 3:
            content = content[: max_chars // 3] + "\n\n[... truncated for context limit ...]"
        filtered.append(RepoFile(path=path, content=content))

    if not filtered:
        return "Repository has no included text files (all skipped or empty)."

    paths = [f.path for f in filtered]
    tree_section = "## Repository structure\n\n```\n" + _build_directory_tree(paths) + "\n```"
    parts: List[str] = [tree_section, "\n\n## Key files\n"]
    used = len(tree_section) + len("\n\n## Key files\n")

    # Sort by priority, then by path
    ordered = sorted(filtered, key=lambda f: (_file_priority(f.path), f.path))
    omission_msg = "\n\n(Additional files omitted due to context limit.)"

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
