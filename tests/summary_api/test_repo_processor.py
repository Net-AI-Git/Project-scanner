"""Tests for summary_api.repo_processor: filtering, prioritization, and context limit."""

import pytest

from summary_api.github_client import RepoFile
from summary_api.repo_processor import (
    DEFAULT_MAX_CONTEXT_CHARS,
    ROOT_FOLDER_KEY,
    group_files_by_top_level_folder,
    process_repo_files,
    process_repo_files_by_folder,
    should_skip_path,
)


# --- should_skip_path: what we skip (binary dirs, lock files, etc.) ---


@pytest.mark.parametrize(
    "path",
    [
        "node_modules/foo/bar.js",
        "Node_Modules/lib/x.js",
        "__pycache__/module.cpython-310.pyc",
        ".git/config",
        "venv/bin/python",
        ".venv/lib/site-packages/x.py",
        "dist/pkg.tar.gz",
        "build/lib/x.so",
        "src/.eggs/foo.egg-info/PKG-INFO",
        "package-lock.json",
        "yarn.lock",
        "poetry.lock",
        "Pipfile.lock",
        "Cargo.lock",
        "composer.lock",
        "static/app.min.js",
        "static/app.min.css",
        "bundle.js.map",
    ],
)
def test_should_skip_path_skipped(path: str) -> None:
    """Paths under skipped dirs or matching lock/min/map are skipped."""
    assert should_skip_path(path) is True


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "src/main.py",
        "package.json",
        "pyproject.toml",
        "requirements.txt",
        "src/foo/bar.py",
        "docs/readme.rst",
    ],
)
def test_should_skip_path_included(path: str) -> None:
    """Normal source and config paths are not skipped."""
    assert should_skip_path(path) is False


# --- process_repo_files: output is string, under limit ---


def test_process_repo_files_empty_returns_message() -> None:
    """Empty file list returns a clear message."""
    out = process_repo_files([])
    assert "no included" in out or "skipped" in out.lower()
    assert isinstance(out, str)


def test_process_repo_files_all_skipped_returns_message() -> None:
    """When all files are skipped, return message."""
    files = [
        RepoFile(path="node_modules/foo.js", content="x"),
        RepoFile(path="package-lock.json", content="{}"),
    ]
    out = process_repo_files(files)
    assert "no included" in out or "skipped" in out.lower()


def test_process_repo_files_mock_output_has_structure_and_key_files() -> None:
    """Output includes repository structure and key files sections."""
    files = [
        RepoFile(path="README.md", content="Hello world project."),
        RepoFile(path="src/main.py", content="def main(): pass"),
    ]
    out = process_repo_files(files)
    assert "## Repository structure" in out
    assert "## Key files" in out
    assert "README.md" in out
    assert "Hello world project" in out
    assert "src/main.py" in out
    assert "def main(): pass" in out


def test_process_repo_files_respects_max_chars() -> None:
    """Output length does not exceed max_chars (volume limit)."""
    big = "x" * 10_000
    files = [
        RepoFile(path="README.md", content="Short readme."),
        RepoFile(path="a.txt", content=big),
        RepoFile(path="b.txt", content=big),
    ]
    max_chars = 5_000
    out = process_repo_files(files, max_chars=max_chars)
    assert len(out) <= max_chars + 200  # small tolerance for truncation message
    assert "context limit" in out  # either "omitted due to context limit" or "[... truncated for context limit ...]"


def test_process_repo_files_default_limit_under_constant() -> None:
    """With default max_chars, output is under DEFAULT_MAX_CONTEXT_CHARS."""
    files = [
        RepoFile(path=f"file_{i}.py", content=f"# file {i}\ncode = 1")
        for i in range(100)
    ]
    out = process_repo_files(files)
    assert len(out) <= DEFAULT_MAX_CONTEXT_CHARS + 500


def test_process_repo_files_priority_readme_first() -> None:
    """README and config appear before deep source files in the output."""
    files = [
        RepoFile(path="deep/nested/file.py", content="nested"),
        RepoFile(path="README.md", content="Root readme."),
        RepoFile(path="pyproject.toml", content="[project]"),
    ]
    out = process_repo_files(files, max_chars=2000)
    readme_pos = out.find("README.md")
    pyproject_pos = out.find("pyproject.toml")
    deep_pos = out.find("deep/nested/file.py")
    assert readme_pos < deep_pos
    assert pyproject_pos < deep_pos


# --- group_files_by_top_level_folder and process_repo_files_by_folder ---


def test_group_files_by_top_level_folder_root_and_src() -> None:
    """Root-level files go under (root); src/ files under src."""
    files = [
        RepoFile(path="README.md", content="Root"),
        RepoFile(path="src/main.py", content="def main(): pass"),
        RepoFile(path="src/foo/bar.py", content="bar"),
    ]
    groups = group_files_by_top_level_folder(files)
    assert ROOT_FOLDER_KEY in groups
    assert "src" in groups
    assert len(groups[ROOT_FOLDER_KEY]) == 1
    assert groups[ROOT_FOLDER_KEY][0].path == "README.md"
    assert len(groups["src"]) == 2


def test_group_files_by_top_level_folder_skips_skipped_paths() -> None:
    """Skipped paths (e.g. node_modules) are not in any group."""
    files = [
        RepoFile(path="README.md", content="x"),
        RepoFile(path="node_modules/foo/bar.js", content="skip"),
    ]
    groups = group_files_by_top_level_folder(files)
    assert ROOT_FOLDER_KEY in groups
    assert len(groups[ROOT_FOLDER_KEY]) == 1
    assert groups[ROOT_FOLDER_KEY][0].path == "README.md"


def test_process_repo_files_by_folder_returns_list_of_tuples() -> None:
    """Output is list of (folder_name, context) sorted by folder name."""
    files = [
        RepoFile(path="README.md", content="Root readme."),
        RepoFile(path="src/main.py", content="def main(): pass"),
    ]
    result = process_repo_files_by_folder(files, max_chars_per_folder=2000)
    assert isinstance(result, list)
    assert len(result) == 2
    folder_names = [r[0] for r in result]
    assert ROOT_FOLDER_KEY in folder_names
    assert "src" in folder_names
    assert result[0][0] == ROOT_FOLDER_KEY  # (root) before src alphabetically
    assert "Repository structure" in result[0][1]
    assert "Key files" in result[0][1]
    assert "README.md" in result[0][1]
    assert "src/main.py" in result[1][1]


def test_process_repo_files_by_folder_respects_per_folder_cap() -> None:
    """Per-folder context length is capped."""
    big = "x" * 5000
    files = [
        RepoFile(path="README.md", content="short"),
        RepoFile(path="a/large.txt", content=big),
    ]
    result = process_repo_files_by_folder(files, max_chars_per_folder=1000)
    assert len(result) == 2
    for _name, ctx in result:
        assert len(ctx) <= 1000 + 200
