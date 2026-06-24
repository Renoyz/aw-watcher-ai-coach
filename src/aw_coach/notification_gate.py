"""Shared notification budget, cooldown, and quiet-hours gate."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Dict, Optional

from aw_coach.config import Config, PolicyConfig


def _parse_hhmm(value: str) -> tuple[int, int]:
    parts = value.strip().split(":")
    return int(parts[0]), int(parts[1])


def _to_naive_local(dt: datetime) -> datetime:
    """Normalize aware datetimes to naive local so comparisons never mix."""
    if dt.tzinfo is None:
        return dt
    return dt.astimezone().replace(tzinfo=None)


def is_quiet_hours(now: datetime, policy: PolicyConfig) -> bool:
    """Return True when *now* falls inside configured quiet hours."""
    if not policy.quiet_hours_enabled:
        return False

    start_h, start_m = _parse_hhmm(policy.quiet_hours_start)
    end_h, end_m = _parse_hhmm(policy.quiet_hours_end)
    now_minutes = now.hour * 60 + now.minute
    start_minutes = start_h * 60 + start_m
    end_minutes = end_h * 60 + end_m

    if start_minutes == end_minutes:
        return False
    if start_minutes < end_minutes:
        return start_minutes <= now_minutes < end_minutes
    # Cross-midnight, e.g. 22:00 -> 08:00
    return now_minutes >= start_minutes or now_minutes < end_minutes


@dataclass
class NotificationGate:
    """Single source of truth for notification budget and quiet hours."""

    max_per_day: int = 4
    cooldown_seconds: int = 600
    policy: PolicyConfig = field(default_factory=PolicyConfig)

    notifications_today: int = 0
    last_notify_date: Optional[date] = None
    last_notify_by_type: Dict[str, datetime] = field(default_factory=dict)

    @classmethod
    def from_config(cls, config: Config) -> "NotificationGate":
        return cls(
            max_per_day=config.report.daily_notification_budget,
            cooldown_seconds=config.report.notification_cooldown_seconds,
            policy=config.policy,
        )

    def reset_daily_if_needed(self, now: datetime) -> None:
        now = _to_naive_local(now)
        today = now.date()
        if self.last_notify_date is None or self.last_notify_date != today:
            self.notifications_today = 0
            self.last_notify_date = today
            self.last_notify_by_type = {}

    def quiet_hours_active(self, now: Optional[datetime] = None) -> bool:
        now = _to_naive_local(now or datetime.now(timezone.utc).astimezone())
        return is_quiet_hours(now, self.policy)

    def allow_notify(
        self,
        kind: str,
        *,
        now: Optional[datetime] = None,
        require_budget: bool = True,
        require_cooldown: bool = True,
    ) -> tuple[bool, str]:
        """Check whether a popup notification is allowed."""
        now = _to_naive_local(now or datetime.now(timezone.utc).astimezone())
        self.reset_daily_if_needed(now)

        if self.quiet_hours_active(now):
            return False, "quiet_hours"

        if require_budget and self.notifications_today >= self.max_per_day:
            return False, "daily_budget"

        if require_cooldown:
            last = self.last_notify_by_type.get(kind)
            if last is not None:
                elapsed = (now - last).total_seconds()
                if elapsed < self.cooldown_seconds:
                    return False, "cooldown"

        return True, "ok"

    def record_notify(
        self,
        kind: str,
        now: Optional[datetime] = None,
        *,
        consume_budget: bool = True,
    ) -> None:
        now = _to_naive_local(now or datetime.now(timezone.utc).astimezone())
        self.reset_daily_if_needed(now)
        if consume_budget:
            self.notifications_today += 1
        self.last_notify_by_type[kind] = now

    def record_event(self, kind: str, now: Optional[datetime] = None) -> None:
        """Record a non-notification event for cooldown tracking only."""
        now = _to_naive_local(now or datetime.now(timezone.utc).astimezone())
        self.last_notify_by_type[kind] = now

    def seconds_since(self, kind: str, now: datetime) -> Optional[float]:
        """Seconds elapsed since the last event of *kind*, or None."""
        now = _to_naive_local(now)
        last = self.last_notify_by_type.get(kind)
        if last is None:
            return None
        return (now - last).total_seconds()

    def blackboard_kwargs(self, now: Optional[datetime] = None) -> dict:
        """Kwargs for PolicyEngine.decide() from shared gate state."""
        now = _to_naive_local(now or datetime.now(timezone.utc).astimezone())
        self.reset_daily_if_needed(now)
        return {
            "quiet_hours": self.quiet_hours_active(now),
            "notifications_today": self.notifications_today,
            "last_notify_by_type": dict(self.last_notify_by_type),
            "now": now,
        }
