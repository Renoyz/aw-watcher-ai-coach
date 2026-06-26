"""Tests for NotificationGate and quiet hours."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aw_coach.config import PolicyConfig
from aw_coach.notification_gate import NotificationGate, is_quiet_hours


class TestQuietHours:
    def test_inside_same_day_window(self):
        policy = PolicyConfig(quiet_hours_start="22:00", quiet_hours_end="23:00")
        now = datetime(2026, 6, 11, 22, 30)
        assert is_quiet_hours(now, policy) is True

    def test_outside_same_day_window(self):
        policy = PolicyConfig(quiet_hours_start="22:00", quiet_hours_end="23:00")
        now = datetime(2026, 6, 11, 12, 0)
        assert is_quiet_hours(now, policy) is False

    def test_cross_midnight_evening(self):
        policy = PolicyConfig(quiet_hours_start="22:00", quiet_hours_end="08:00")
        now = datetime(2026, 6, 11, 23, 0)
        assert is_quiet_hours(now, policy) is True

    def test_cross_midnight_morning(self):
        policy = PolicyConfig(quiet_hours_start="22:00", quiet_hours_end="08:00")
        now = datetime(2026, 6, 11, 7, 0)
        assert is_quiet_hours(now, policy) is True

    def test_disabled(self):
        policy = PolicyConfig(quiet_hours_enabled=False)
        now = datetime(2026, 6, 11, 23, 0)
        assert is_quiet_hours(now, policy) is False


class TestNotificationGate:
    def test_blocks_in_quiet_hours(self):
        gate = NotificationGate(
            policy=PolicyConfig(quiet_hours_start="22:00", quiet_hours_end="08:00")
        )
        now = datetime(2026, 6, 11, 23, 0)
        allowed, reason = gate.allow_notify("summary", now=now)
        assert allowed is False
        assert reason == "quiet_hours"

    def test_daily_budget(self):
        gate = NotificationGate(max_per_day=2)
        now = datetime(2026, 6, 11, 10, 0)
        gate.notifications_today = 2
        gate.last_notify_date = now.date()
        allowed, reason = gate.allow_notify("summary", now=now)
        assert allowed is False
        assert reason == "daily_budget"

    def test_cooldown(self):
        gate = NotificationGate(cooldown_seconds=600)
        now = datetime(2026, 6, 11, 10, 0)
        gate.record_notify("summary", now=now)
        allowed, reason = gate.allow_notify("summary", now=now + timedelta(seconds=60))
        assert allowed is False
        assert reason == "cooldown"
        allowed, _ = gate.allow_notify("summary", now=now + timedelta(seconds=700))
        assert allowed is True

    def test_mixed_naive_and_aware_datetimes(self):
        """record with aware datetime, then check with naive one (and reverse)."""
        gate = NotificationGate(cooldown_seconds=600)
        aware = datetime(2026, 6, 11, 10, 0, tzinfo=timezone.utc).astimezone()
        naive = aware.replace(tzinfo=None) + timedelta(seconds=60)
        gate.record_notify("summary", now=aware)
        allowed, reason = gate.allow_notify("summary", now=naive)
        assert allowed is False
        assert reason == "cooldown"

        gate2 = NotificationGate(cooldown_seconds=600)
        gate2.record_notify("summary", now=aware.replace(tzinfo=None))
        allowed, reason = gate2.allow_notify(
            "summary", now=aware + timedelta(seconds=60)
        )
        assert allowed is False
        assert reason == "cooldown"

    def test_seconds_since_and_record_event(self):
        gate = NotificationGate()
        now = datetime(2026, 6, 11, 10, 0)
        assert gate.seconds_since("task_confirm:x", now) is None
        gate.record_event("task_confirm:x", now)
        assert gate.seconds_since("task_confirm:x", now + timedelta(seconds=30)) == 30
        # record_event must not consume notification budget
        assert gate.notifications_today == 0

    def test_record_notify_can_skip_budget(self):
        gate = NotificationGate(max_per_day=1)
        now = datetime(2026, 6, 11, 10, 0)
        gate.record_notify("summary", now=now, consume_budget=False)
        assert gate.notifications_today == 0
        allowed, reason = gate.allow_notify("daily_report", now=now)
        assert allowed is True
        assert reason == "ok"
