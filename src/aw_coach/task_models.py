"""Task perception data models."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


def stable_session_uid(task_id: str, started_at: datetime) -> str:
    """Return a deterministic id for the same task session start."""
    raw = f"{task_id}|{started_at.isoformat()}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


@dataclass
class TaskEvidence:
    source: str
    value: str
    confidence: float


@dataclass
class WorkTask:
    task_id: str
    label: str
    project: Optional[str] = None
    intent: str = "unknown"
    confidence: float = 0.0
    evidence: List[TaskEvidence] = field(default_factory=list)


@dataclass
class TaskSession:
    task_id: str
    label: str
    project: Optional[str]
    intent: str
    started_at: datetime
    session_uid: Optional[str] = None
    ended_at: Optional[datetime] = None
    accumulated_sec: float = 0.0
    modes: List[str] = field(default_factory=list)
    blockers: List[str] = field(default_factory=list)
    outcome: str = "in_progress"
    confidence: float = 0.0
    evidence: List[TaskEvidence] = field(default_factory=list)
    source: Dict[str, Any] = field(default_factory=dict)
    version: int = 1

    def __post_init__(self) -> None:
        if not self.session_uid:
            self.session_uid = stable_session_uid(self.task_id, self.started_at)

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["started_at"] = self.started_at.isoformat()
        d["ended_at"] = self.ended_at.isoformat() if self.ended_at else None
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskSession":
        started_at = _parse_datetime(data.get("started_at"))
        if started_at is None:
            raise ValueError("TaskSession requires started_at")

        modes = _string_list(data.get("modes"))
        blockers = _string_list(data.get("blockers"))
        evidence = []
        for item in data.get("evidence") or []:
            if not isinstance(item, dict):
                continue
            evidence.append(
                TaskEvidence(
                    source=str(item.get("source") or ""),
                    value=str(item.get("value") or ""),
                    confidence=float(item.get("confidence") or 0.0),
                )
            )
        source = data.get("source") if isinstance(data.get("source"), dict) else {}

        return cls(
            session_uid=data.get("session_uid"),
            task_id=str(data.get("task_id") or "unknown:unknown"),
            label=str(data.get("label") or data.get("task_id") or "unknown"),
            project=data.get("project"),
            intent=str(data.get("intent") or "unknown"),
            started_at=started_at,
            ended_at=_parse_datetime(data.get("ended_at")),
            accumulated_sec=float(data.get("accumulated_sec") or 0.0),
            modes=modes,
            blockers=blockers,
            outcome=str(data.get("outcome") or "in_progress"),
            confidence=float(data.get("confidence") or 0.0),
            evidence=evidence,
            source=source,
            version=int(data.get("version") or 1),
        )


def _parse_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]
