"""Track task sessions over time with merge rules."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from aw_coach.enriched_state import SemanticWorkState
from aw_coach.task_models import TaskSession, WorkTask

MERGE_GAP_SEC = 300
ORPHAN_SEC = 120


class TaskSessionTracker:
    def __init__(self) -> None:
        self._current: Optional[TaskSession] = None
        self._completed: List[TaskSession] = []
        self._last_update: Optional[datetime] = None

    @property
    def current_session(self) -> Optional[TaskSession]:
        return self._current

    @property
    def completed_sessions(self) -> List[TaskSession]:
        return list(self._completed)

    def update(self, task: WorkTask, state: SemanticWorkState, now: datetime) -> TaskSession:
        elapsed = 60.0
        if self._last_update is not None:
            elapsed = min((now - self._last_update).total_seconds(), 300.0)
        self._last_update = now

        if self._current is None or self._current.task_id != task.task_id:
            if self._current is not None:
                self._finalize_current(now)
            self._current = TaskSession(
                task_id=task.task_id,
                label=task.label,
                project=task.project,
                intent=task.intent,
                started_at=now,
                confidence=task.confidence,
            )

        self._current.accumulated_sec += elapsed
        if state.likely_mode and state.likely_mode not in self._current.modes:
            self._current.modes.append(state.likely_mode)
        if state.detected_signal and state.detected_signal not in self._current.blockers:
            self._current.blockers.append(state.detected_signal)
        if state.risk_level == "stuck" and "stuck" not in self._current.blockers:
            self._current.blockers.append("stuck")
        self._current.confidence = max(self._current.confidence, task.confidence)
        return self._current

    def _finalize_current(self, now: datetime) -> None:
        if self._current is None:
            return
        self._current.ended_at = now
        if self._current.blockers:
            self._current.outcome = "blocked"
        elif self._current.accumulated_sec >= 600:
            self._current.outcome = "progressed"
        elif self._current.accumulated_sec < ORPHAN_SEC:
            self._current.outcome = "abandoned"
        else:
            self._current.outcome = "abandoned"
        self._completed.append(self._current)
        self._merge_completed()
        self._current = None

    def _merge_completed(self) -> None:
        if len(self._completed) < 2:
            return
        merged: List[TaskSession] = []
        for session in self._completed:
            if not merged:
                merged.append(session)
                continue
            prev = merged[-1]
            gap = 0.0
            if prev.ended_at and session.started_at:
                gap = (session.started_at - prev.ended_at).total_seconds()
            if (
                prev.task_id == session.task_id
                and gap >= 0
                and gap < MERGE_GAP_SEC
            ):
                prev.accumulated_sec += session.accumulated_sec
                prev.ended_at = session.ended_at
                for mode in session.modes:
                    if mode not in prev.modes:
                        prev.modes.append(mode)
                for blocker in session.blockers:
                    if blocker not in prev.blockers:
                        prev.blockers.append(blocker)
                if session.outcome == "blocked":
                    prev.outcome = "blocked"
            else:
                merged.append(session)
        self._completed = [
            s for s in merged
            if s.accumulated_sec >= ORPHAN_SEC or s.outcome == "blocked"
        ]

    def flush(self, now: datetime) -> None:
        if self._current is not None:
            self._finalize_current(now)

    def drain_completed(self) -> List[TaskSession]:
        """Return completed sessions and clear them (persist-once semantics)."""
        drained = self._completed
        self._completed = []
        return drained

    def to_dict(self) -> Dict[str, Any]:
        return {
            "current": self._session_to_dict(self._current),
            "completed": [
                self._session_to_dict(session) for session in self._completed
            ],
            "last_update": self._last_update.isoformat() if self._last_update else None,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> "TaskSessionTracker":
        data = json.loads(raw)
        if not isinstance(data, dict):
            return cls()
        tracker = cls()
        tracker._current = cls._session_from_dict(data.get("current"))
        completed = data.get("completed") or []
        if isinstance(completed, list):
            tracker._completed = [
                session
                for session in (cls._session_from_dict(item) for item in completed)
                if session is not None
            ]
        tracker._last_update = cls._parse_datetime(data.get("last_update"))
        return tracker

    @staticmethod
    def _session_to_dict(session: Optional[TaskSession]) -> Optional[Dict[str, Any]]:
        if session is None:
            return None
        return session.to_dict()

    @classmethod
    def _session_from_dict(cls, data: Any) -> Optional[TaskSession]:
        if not isinstance(data, dict):
            return None
        started_at = cls._parse_datetime(data.get("started_at"))
        if started_at is None:
            return None

        modes = data.get("modes") or []
        blockers = data.get("blockers") or []
        if not isinstance(modes, list):
            modes = []
        if not isinstance(blockers, list):
            blockers = []

        return TaskSession(
            task_id=str(data.get("task_id") or "unknown:unknown"),
            label=str(data.get("label") or data.get("task_id") or "unknown"),
            project=data.get("project"),
            intent=str(data.get("intent") or "unknown"),
            started_at=started_at,
            ended_at=cls._parse_datetime(data.get("ended_at")),
            accumulated_sec=float(data.get("accumulated_sec") or 0.0),
            modes=[str(mode) for mode in modes],
            blockers=[str(blocker) for blocker in blockers],
            outcome=str(data.get("outcome") or "in_progress"),
            confidence=float(data.get("confidence") or 0.0),
        )

    @staticmethod
    def _parse_datetime(value: Any) -> Optional[datetime]:
        if not value:
            return None
        if isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            return None
