"""Tests for git_context module.

These tests create temporary git repositories so they can run without
relying on the user's real filesystem.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from aw_coach.git_context import (
    GitContext,
    _candidate_paths_from_project_name,
    _find_git_root,
    get_git_context_for_project,
    get_git_context_from_path,
)


def _git_init(path: Path, branch: str = "main") -> None:
    """Initialize a real git repo at *path* and set its HEAD to *branch*."""
    subprocess.run(["git", "init", str(path)], capture_output=True, check=True)
    head = path / ".git" / "HEAD"
    head.write_text(f"ref: refs/heads/{branch}\n")


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch):
    """Override $HOME to a temp directory for safe path searching."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


class TestFindGitRoot:
    def test_finds_git_root(self, tmp_path: Path):
        repo = tmp_path / "myrepo"
        repo.mkdir()
        (repo / ".git").mkdir()
        subdir = repo / "src" / "nested"
        subdir.mkdir(parents=True)
        assert _find_git_root(subdir) == repo

    def test_no_git(self, tmp_path: Path):
        plain = tmp_path / "plain"
        plain.mkdir()
        assert _find_git_root(plain) is None

    def test_file_input(self, tmp_path: Path):
        repo = tmp_path / "myrepo"
        repo.mkdir()
        (repo / ".git").mkdir()
        file_path = repo / "main.py"
        file_path.write_text("x")
        assert _find_git_root(file_path) == repo


class TestGetGitContextFromPath:
    def test_full_context(self, tmp_path: Path):
        repo = tmp_path / "x_system"
        repo.mkdir()
        _git_init(repo, branch="feature/ros-executor")

        ctx = get_git_context_from_path(repo)
        assert ctx is not None
        assert ctx.repo_name == "x_system"
        assert ctx.branch == "feature/ros-executor"
        assert ctx.is_dirty is False

    def test_dirty_repo(self, tmp_path: Path):
        repo = tmp_path / "dirty_repo"
        repo.mkdir()
        _git_init(repo, branch="main")

        # Add an untracked file
        (repo / "new_file.txt").write_text("hello")

        ctx = get_git_context_from_path(repo)
        assert ctx is not None
        assert ctx.is_dirty is True

    def test_not_a_repo(self, tmp_path: Path):
        plain = tmp_path / "plain"
        plain.mkdir()
        assert get_git_context_from_path(plain) is None

    def test_nonexistent_path(self, tmp_path: Path):
        assert get_git_context_from_path(tmp_path / "does_not_exist") is None

    def test_from_file_path(self, tmp_path: Path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _git_init(repo, branch="main")

        file_path = repo / "src" / "main.py"
        file_path.parent.mkdir(parents=True)
        file_path.write_text("pass")

        ctx = get_git_context_from_path(file_path)
        assert ctx is not None
        assert ctx.repo_name == "repo"
        assert ctx.branch == "main"


class TestGetGitContextForProject:
    def test_find_under_projects(self, fake_home: Path):
        repo = fake_home / "projects" / "x_system"
        repo.mkdir(parents=True)
        _git_init(repo, branch="main")

        ctx = get_git_context_for_project("x_system")
        assert ctx is not None
        assert ctx.repo_name == "x_system"
        assert ctx.branch == "main"

    def test_find_under_workspace(self, fake_home: Path):
        repo = fake_home / "workspace" / "aw-coach"
        repo.mkdir(parents=True)
        _git_init(repo, branch="dev")

        ctx = get_git_context_for_project("aw-coach")
        assert ctx is not None
        assert ctx.repo_name == "aw-coach"
        assert ctx.branch == "dev"

    def test_find_under_home(self, fake_home: Path):
        repo = fake_home / "myproject"
        repo.mkdir()
        _git_init(repo, branch="main")

        ctx = get_git_context_for_project("myproject")
        assert ctx is not None
        assert ctx.repo_name == "myproject"

    def test_not_found(self, fake_home: Path):
        assert get_git_context_for_project("nonexistent_project") is None

    def test_extra_bases(self, fake_home: Path):
        repo = fake_home / "custom" / "special"
        repo.mkdir(parents=True)
        _git_init(repo, branch="main")

        ctx = get_git_context_for_project("special", extra_bases=["custom"])
        assert ctx is not None
        assert ctx.repo_name == "special"


class TestCandidatePaths:
    def test_candidates_generated(self, fake_home: Path):
        candidates = _candidate_paths_from_project_name("x_system")
        strs = [str(c) for c in candidates]
        assert any("projects/x_system" in s for s in strs)
        assert any("workspace/x_system" in s for s in strs)
        assert any(str(fake_home / "x_system") in s for s in strs)


class TestGitContextDataclass:
    def test_frozen(self):
        ctx = GitContext(repo_name="x", branch="main", is_dirty=False)
        with pytest.raises(AttributeError):
            ctx.branch = "other"
