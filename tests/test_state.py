"""Tests for CoachAgentStateMachine."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aw_coach.state import AgentState, CoachAgentStateMachine, StateMachineSnapshot


class TestStateTransitions:
    def test_initial_state_is_observing(self):
        sm = CoachAgentStateMachine()
        assert sm.state is AgentState.OBSERVING

    def test_transition_to_detected(self):
        sm = CoachAgentStateMachine()
        sm.transition_to(AgentState.DETECTED, signal_type="stuck")
        assert sm.state is AgentState.DETECTED
        assert sm.last_signal_type == "stuck"

    def test_auto_advance_feedback_to_cooldown(self):
        sm = CoachAgentStateMachine()
        now = datetime.now(timezone.utc).astimezone()
        sm.transition_to(AgentState.AWAITING_FEEDBACK, now=now)
        assert sm.state is AgentState.AWAITING_FEEDBACK
        # Immediately after timeout
        later = now + timedelta(seconds=sm.FEEDBACK_TIMEOUT_SEC + 1)
        sm.auto_advance(later)
        assert sm.state is AgentState.COOLDOWN

    def test_auto_advance_cooldown_to_observing(self):
        sm = CoachAgentStateMachine()
        now = datetime.now(timezone.utc).astimezone()
        sm.transition_to(AgentState.COOLDOWN, now=now)
        later = now + timedelta(seconds=sm.COOLDOWN_SEC + 1)
        sm.auto_advance(later)
        assert sm.state is AgentState.OBSERVING

    def test_may_notify_blocked_during_feedback(self):
        sm = CoachAgentStateMachine()
        sm.transition_to(AgentState.AWAITING_FEEDBACK)
        assert sm.may_notify("stuck") is False

    def test_may_notify_blocked_during_acting(self):
        sm = CoachAgentStateMachine()
        sm.transition_to(AgentState.ACTING)
        assert sm.may_notify("stuck") is False

    def test_may_notify_allowed_when_observing(self):
        sm = CoachAgentStateMachine()
        assert sm.may_notify("stuck") is True

    def test_may_notify_blocked_same_type_during_cooldown(self):
        sm = CoachAgentStateMachine()
        now = datetime.now(timezone.utc).astimezone()
        sm.transition_to(AgentState.COOLDOWN, signal_type="stuck", now=now)
        assert sm.may_notify("stuck") is False

    def test_may_notify_allowed_different_type_during_cooldown(self):
        sm = CoachAgentStateMachine()
        now = datetime.now(timezone.utc).astimezone()
        sm.transition_to(AgentState.COOLDOWN, signal_type="stuck", now=now)
        assert sm.may_notify("ai_loop") is True

    def test_may_inbox_blocked_during_feedback(self):
        sm = CoachAgentStateMachine()
        sm.transition_to(AgentState.AWAITING_FEEDBACK)
        assert sm.may_inbox("stuck") is False

    def test_may_inbox_allowed_when_observing(self):
        sm = CoachAgentStateMachine()
        assert sm.may_inbox("stuck") is True

    def test_daily_budget_reset(self):
        sm = CoachAgentStateMachine()
        sm.notifications_today = 3
        sm.notify_date = (datetime.now(timezone.utc).astimezone() - timedelta(days=1)).date()
        sm.reset_daily_budget_if_needed()
        assert sm.notifications_today == 0

    def test_record_notification_increments(self):
        sm = CoachAgentStateMachine()
        sm.record_notification()
        assert sm.notifications_today == 1


class TestSerialization:
    def test_round_trip_snapshot(self):
        sm = CoachAgentStateMachine()
        sm.transition_to(AgentState.DETECTED, signal_type="stuck", action="notify")
        sm.record_notification()
        snap = sm.to_snapshot()
        restored = CoachAgentStateMachine.from_snapshot(snap)
        assert restored.state is AgentState.DETECTED
        assert restored.last_signal_type == "stuck"
        assert restored.last_action == "notify"
        assert restored.notifications_today == 1

    def test_round_trip_json(self):
        sm = CoachAgentStateMachine()
        sm.transition_to(AgentState.COOLDOWN, signal_type="ai_loop")
        raw = sm.to_json()
        restored = CoachAgentStateMachine.from_json(raw)
        assert restored.state is AgentState.COOLDOWN
        assert restored.last_signal_type == "ai_loop"

    def test_snapshot_fields(self):
        sm = CoachAgentStateMachine()
        snap = sm.to_snapshot()
        assert snap.state == "OBSERVING"
        assert isinstance(snap.entered_at, str)
