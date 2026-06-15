"""Extract task signals from window title, URL, git, and user config."""

from __future__ import annotations

import re
import socket
from typing import List, Optional, Tuple
from urllib.parse import urlparse

from aw_coach.config import TasksConfig
from aw_coach.context_parser import TitleParser
from aw_coach.git_context import GitContext
from aw_coach.task_models import TaskEvidence, WorkTask

_SSH_CONFIDENCE = 0.7

_MODE_TO_INTENT = {
    "coding": "implement",
    "debugging": "debug",
    "testing": "test",
    "researching": "research",
    "reviewing": "review",
    "writing": "document",
    "building": "build",
    "deploying": "deploy",
    "meeting": "meeting",
    "chatting": "communicate",
    "ai_coding": "implement",
    "editing": "implement",
    "committing": "commit",
    "browsing": "browse",
    "terminal": "operate",
}


def _normalize_project(project: Optional[str], config: TasksConfig) -> Optional[str]:
    if not project:
        return None
    return config.aliases.get(project, project)


def _parse_issue_from_url(url: str) -> Optional[Tuple[str, str, float]]:
    if not url:
        return None
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower().replace("www.", "")
    path = parsed.path.strip("/")
    segments = path.split("/") if path else []

    if "github.com" in host and len(segments) >= 3 and segments[2] in ("issues", "pull"):
        repo = segments[1]
        num = segments[3].split("-")[0]
        if num.isdigit():
            kind = "issue" if segments[2] == "issues" else "pr"
            return f"github:{repo}#{num}", f"GitHub {kind} #{num} ({repo})", 0.92

    if "gitlab.com" in host and len(segments) >= 3 and segments[2] in ("issues", "merge_requests"):
        repo = segments[1]
        num = segments[3]
        return f"gitlab:{repo}#{num}", f"GitLab {segments[2]} {num}", 0.9

    if "linear.app" in host:
        issue = segments[-1] if segments else None
        if issue and re.match(r"^[A-Z]+-\d+$", issue):
            return f"linear:{issue}", f"Linear {issue}", 0.9

    if "atlassian.net" in host and "browse" in path.lower():
        issue = segments[-1] if segments else None
        if issue and re.match(r"^[A-Z]+-\d+$", issue):
            return f"jira:{issue}", f"Jira {issue}", 0.88

    return None


def _ssh_task_from_title(
    title: str, config: TasksConfig
) -> Optional[Tuple[str, str, Optional[str], float]]:
    """Derive task from a *remote* SSH terminal prompt user@host: cwd."""
    parsed = TitleParser._parse_terminal_prompt(title)
    if parsed is None:
        return None
    _user, host, cwd = parsed
    dir_name = TitleParser._cwd_to_dir(cwd)
    if not dir_name:
        return None
    # Local prompts are handled by regular project/file signals
    local_host = socket.gethostname().lower()
    host_lower = host.lower()
    if host_lower == local_host or local_host.startswith(host_lower):
        return None
    host_label = config.aliases.get(host, host)
    tid = f"ssh:{host}:{dir_name}"
    label = f"{dir_name}@{host_label}"
    return tid, label, f"{dir_name}@{host}", _SSH_CONFIDENCE


def _branch_task_id(
    project: str, branch: Optional[str], config: TasksConfig
) -> Optional[Tuple[str, str, float]]:
    if not branch or branch in ("main", "master", "develop", "dev"):
        return None
    for pattern in config.branch_patterns:
        if pattern.endswith("*"):
            prefix = pattern[:-1]
            if branch.startswith(prefix):
                return f"{project}:{branch}", f"{project} ({branch})", 0.82
        elif branch == pattern:
            return f"{project}:{branch}", f"{project} ({branch})", 0.82
    if "/" in branch or branch.startswith("issue-"):
        return f"{project}:{branch}", f"{project} ({branch})", 0.75
    return None


class TaskSignalExtractor:
    def __init__(self, config: TasksConfig) -> None:
        self.config = config
        self._parser = TitleParser()

    def extract(
        self,
        app: str,
        title: str,
        url: Optional[str],
        likely_mode: str,
        activity_type: str,
        git_ctx: Optional[GitContext] = None,
        filename: Optional[str] = None,
        project: Optional[str] = None,
    ) -> WorkTask:
        evidence: List[TaskEvidence] = []
        candidates: List[Tuple[str, str, Optional[str], str, float]] = []

        if self.config.user_task_id and self.config.user_task_label:
            candidates.append((
                self.config.user_task_id,
                self.config.user_task_label,
                _normalize_project(project, self.config),
                "user_declared",
                0.98,
            ))
            evidence.append(TaskEvidence("user", self.config.user_task_label, 0.98))

        issue = _parse_issue_from_url(url or "")
        if issue:
            tid, label, conf = issue
            candidates.append((tid, label, None, "issue", conf))
            evidence.append(TaskEvidence("url", url or "", conf))

        project = _normalize_project(project, self.config)

        # Webmail-like browser titles (user@mail.com: subject) are not SSH prompts
        ssh_hit = None
        if not TitleParser._is_browser(app):
            ssh_hit = _ssh_task_from_title(title, self.config)
        if ssh_hit:
            tid, label, proj, conf = ssh_hit
            candidates.append((tid, label, proj, "ssh_remote", conf))
            evidence.append(TaskEvidence("terminal", title, conf))

        if git_ctx and project:
            branch_hit = _branch_task_id(project, git_ctx.branch, self.config)
            if branch_hit:
                tid, label, conf = branch_hit
                candidates.append((tid, label, project, "git_branch", conf))
                evidence.append(TaskEvidence("git", git_ctx.branch or "", conf))

        if project and filename:
            tid = f"{project}:{filename}"
            candidates.append((tid, f"{filename} ({project})", project, "file", 0.65))
            evidence.append(TaskEvidence("title", filename, 0.65))

        intent = _MODE_TO_INTENT.get(likely_mode, "unknown")
        if project:
            tid = f"{project}:{intent}"
            candidates.append((tid, f"{project} — {intent}", project, intent, 0.45))

        tid = f"unknown:{activity_type or 'unknown'}"
        candidates.append((tid, activity_type or "unknown", None, "unknown", 0.2))

        best = max(candidates, key=lambda c: c[4])
        return WorkTask(
            task_id=best[0],
            label=best[1],
            project=best[2],
            intent=best[3],
            confidence=best[4],
            evidence=evidence,
        )
