"""Lightweight cron job runner for scheduled summaries."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional


@dataclass
class CronJobState:
    template: str
    schedule: str
    delivery: str
    last_run: Optional[datetime] = None


@dataclass
class CronRunner:
    jobs: List[CronJobState] = field(default_factory=list)
    _intervals: Dict[str, timedelta] = field(default_factory=dict)

    @classmethod
    def from_config(cls, cron_jobs: list) -> "CronRunner":
        runner = cls()
        for job in cron_jobs:
            state = CronJobState(
                template=getattr(job, "template", "work_progress"),
                schedule=getattr(job, "schedule", "every 4h"),
                delivery=getattr(job, "delivery", "inbox"),
            )
            interval = _parse_interval(state.schedule)
            if interval is not None:
                runner.jobs.append(state)
                runner._intervals[id(state)] = interval
        return runner

    def due_jobs(self, now: datetime) -> List[CronJobState]:
        due: List[CronJobState] = []
        for job in self.jobs:
            interval = self._intervals.get(id(job))
            if interval is None:
                continue
            if job.last_run is None or (now - job.last_run) >= interval:
                due.append(job)
        return due

    def mark_run(self, job: CronJobState, now: datetime) -> None:
        job.last_run = now


def _parse_interval(schedule: str) -> Optional[timedelta]:
    schedule = schedule.strip().lower()
    match = re.match(r"every\s+(\d+)\s*h", schedule)
    if match:
        return timedelta(hours=int(match.group(1)))
    match = re.match(r"every\s+(\d+)\s*m", schedule)
    if match:
        return timedelta(minutes=int(match.group(1)))
    return None
