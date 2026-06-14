"""Git repository context extraction — find repo name, branch, dirty state from path."""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GitContext:
    """Git repository information for a working directory."""

    repo_name: Optional[str] = None
    branch: Optional[str] = None
    is_dirty: bool = False


def _run_git(cwd: str | Path, *args: str, timeout: float = 3.0) -> Optional[str]:
    """Run a git command in *cwd* and return stripped stdout, or None on failure."""
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


def _find_git_root(start: Path) -> Optional[Path]:
    """Walk upwards from *start* looking for a .git directory."""
    for parent in [start] + list(start.parents):
        if (parent / ".git").exists():
            return parent
    return None


def get_git_context_from_path(path: str | Path) -> Optional[GitContext]:
    """Return GitContext for the git repo that contains *path* (if any)."""
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return None

    # If path itself is a file, start from its parent directory
    start = p if p.is_dir() else p.parent

    git_root = _find_git_root(start)
    if git_root is None:
        return None

    ctx = GitContext(repo_name=git_root.name)

    branch = _run_git(git_root, "branch", "--show-current")
    if branch:
        ctx = GitContext(repo_name=ctx.repo_name, branch=branch, is_dirty=ctx.is_dirty)

    dirty_output = _run_git(git_root, "status", "--porcelain")
    if dirty_output is not None:
        ctx = GitContext(repo_name=ctx.repo_name, branch=ctx.branch, is_dirty=bool(dirty_output))

    return ctx


# Common directories under $HOME where repositories typically live.
_SEARCH_BASES = ["projects", "workspace", "work", "src", "code", "dev"]


def _candidate_paths_from_project_name(project: str) -> list[Path]:
    """Generate likely filesystem paths for a project name."""
    home = Path.home()
    candidates: list[Path] = []

    # Directly under home
    candidates.append(home / project)

    # Under common sub-directories
    for base in _SEARCH_BASES:
        candidates.append(home / base / project)

    # Deeper nesting (e.g. ~/projects/company/project)
    for base in _SEARCH_BASES:
        candidates.append(home / base / project / project)

    return candidates


def get_git_context_for_project(
    project: str,
    extra_bases: Optional[list[str]] = None,
) -> Optional[GitContext]:
    """
    Heuristic: given a *project* name (from window title), try to find its git repo.

    Searches under ``~/projects/``, ``~/workspace/``, ``~/``, etc.
    Returns the first match that is a valid git repository.
    """
    candidates = _candidate_paths_from_project_name(project)

    if extra_bases:
        home = Path.home()
        for base in extra_bases:
            candidates.append(home / base / project)

    for path in candidates:
        if (path / ".git").exists():
            return get_git_context_from_path(path)

    return None
