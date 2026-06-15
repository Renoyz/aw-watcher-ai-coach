"""Task perception data models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Dict, List, Optional


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
    ended_at: Optional[datetime] = None
    accumulated_sec: float = 0.0
    modes: List[str] = field(default_factory=list)
    blockers: List[str] = field(default_factory=list)
    outcome: str = "in_progress"
    confidence: float = 0.0

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["started_at"] = self.started_at.isoformat()
        d["ended_at"] = self.ended_at.isoformat() if self.ended_at else None
        return d
