"""Tests for summary_api.repo_processor: filtering, prioritization, and context limit."""

import pytest

from summary_api.github_client import RepoFile
from summary_api.repo_processor import (
    DEFAULT_MAX_CONTEXT_CHARS,
    process_repo_files,
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
