"""Fuse task signals with hysteresis to avoid session fragmentation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from aw_coach.task_models import WorkTask


@dataclass
class TaskFusionState:
    confirmed_task_id: Optional[str] = None
    confirmed_label: Optional[str] = None
    confirmed_project: Optional[str] = None
    confirmed_intent: str = "unknown"
    confirmed_confidence: float = 0.0
    pending_task_id: Optional[str] = None
    pending_count: int = 0


class TaskFusionEngine:
    HYSTERESIS_CYCLES = 2

    def __init__(self) -> None:
        self._state = TaskFusionState()

    @property
    def state(self) -> TaskFusionState:
        return self._state

    def restore_confirmed(
        self,
        *,
        task_id: str,
        label: str,
        project: Optional[str],
        intent: str,
        confidence: float,
    ) -> None:
        self._state.confirmed_task_id = task_id
        self._state.confirmed_label = label
        self._state.confirmed_project = project
        self._state.confirmed_intent = intent
        self._state.confirmed_confidence = confidence
        self._state.pending_task_id = None
        self._state.pending_count = 0

    def resolve(self, candidate: WorkTask) -> WorkTask:
        if self._state.confirmed_task_id is None:
            self._confirm(candidate)
            return candidate

        if candidate.task_id == self._state.confirmed_task_id:
            self._state.pending_task_id = None
            self._state.pending_count = 0
            if candidate.confidence > self._state.confirmed_confidence:
                self._confirm(candidate)
            else:
                return self._confirmed_task()
            return candidate

        # Same project, lower confidence jitter — keep confirmed task
        if (
            candidate.project
            and candidate.project == self._state.confirmed_project
            and candidate.confidence < self._state.confirmed_confidence
        ):
            return self._confirmed_task()

        if candidate.task_id == self._state.pending_task_id:
            self._state.pending_count += 1
        else:
            self._state.pending_task_id = candidate.task_id
            self._state.pending_count = 1

        if self._state.pending_count >= self.HYSTERESIS_CYCLES:
            self._confirm(candidate)
            self._state.pending_task_id = None
            self._state.pending_count = 0
            return candidate

        return self._confirmed_task()

    def _confirm(self, task: WorkTask) -> None:
        self._state.confirmed_task_id = task.task_id
        self._state.confirmed_label = task.label
        self._state.confirmed_project = task.project
        self._state.confirmed_intent = task.intent
        self._state.confirmed_confidence = task.confidence

    def _confirmed_task(self) -> WorkTask:
        return WorkTask(
            task_id=self._state.confirmed_task_id or "unknown:unknown",
            label=self._state.confirmed_label or "unknown",
            project=self._state.confirmed_project,
            intent=self._state.confirmed_intent,
            confidence=self._state.confirmed_confidence,
        )
