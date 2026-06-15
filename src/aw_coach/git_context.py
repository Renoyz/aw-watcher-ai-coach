"""Git repository context extraction — find repo name, branch, dirty state from path."""

from __future__ import annotations

import logging
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_GIT_CACHE: Dict[str, Tuple[float, Optional[Path], Optional["GitContext"]]] = {}
_GIT_CACHE_TTL_SEC = 90.0


def clear_git_context_cache() -> None:
    """Clear project git lookup cache (useful in tests)."""
    _GIT_CACHE.clear()


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
    project_roots: Optional[list[str]] = None,
) -> Optional[GitContext]:
    """
    Heuristic: given a *project* name (from window title), try to find its git repo.

    Searches under ``~/projects/``, ``~/workspace/``, ``~/``, etc.
    Returns the first match that is a valid git repository.
    """
    cache_key = (
        f"{Path.home()}|{project}|{','.join(extra_bases or [])}|"
        f"{','.join(project_roots or [])}"
    )
    now = time.monotonic()
    cached = _GIT_CACHE.get(cache_key)
    if cached is not None and (now - cached[0]) < _GIT_CACHE_TTL_SEC:
        cached_path, cached_ctx = cached[1], cached[2]
        if cached_path is not None and (cached_path / ".git").exists():
            return cached_ctx

    candidates = _candidate_paths_from_project_name(project)
    if project_roots:
        home = Path.home()
        for root in project_roots:
            expanded = Path(root).expanduser()
            if not expanded.is_absolute():
                expanded = home / expanded
            candidates.insert(0, expanded / project)
            candidates.insert(0, expanded)

    if extra_bases:
        home = Path.home()
        for base in extra_bases:
            candidates.append(home / base / project)

    result: Optional[GitContext] = None
    matched_path: Optional[Path] = None
    for path in candidates:
        if (path / ".git").exists():
            matched_path = path
            result = get_git_context_from_path(path)
            break

    _GIT_CACHE[cache_key] = (now, matched_path, result)
    return result
