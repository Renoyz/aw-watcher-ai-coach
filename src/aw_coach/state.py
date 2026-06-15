"""CoachAgentStateMachine: manage the agent's own operational state.

Prevents notification spam by tracking whether the agent is:
- observing (normal)
- detected (problem spotted, evaluating)
- acting (sending a notification)
- awaiting_feedback (waiting for user response)
- cooldown (recently interacted, suppressing duplicates)
- quiet_hours (silent period)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum, auto
from typing import Dict, Optional


class AgentState(Enum):
    OBSERVING = auto()
    DETECTED = auto()
    DECIDING = auto()
    ACTING = auto()
    AWAITING_FEEDBACK = auto()
    COOLDOWN = auto()
    QUIET_HOURS = auto()


@dataclass
class StateMachineSnapshot:
    """Serializable snapshot of the state machine."""

    state: str
    entered_at: str  # ISO datetime
    last_signal_type: Optional[str] = None
    last_action: Optional[str] = None
    notifications_today: int = 0
    notify_date: Optional[str] = None  # ISO date
    cooldown_until: Optional[str] = None  # ISO datetime
    feedback_timeout_until: Optional[str] = None  # ISO datetime


class CoachAgentStateMachine:
    """Finite state machine for the AI Coach agent itself.

    Usage:
        sm = CoachAgentStateMachine()
        sm.transition_to(AgentState.DETECTED, signal_type="stuck")
        if sm.may_notify():
            sm.transition_to(AgentState.ACTING)
            send_notification(...)
            sm.transition_to(AgentState.AWAITING_FEEDBACK)
    """

    FEEDBACK_TIMEOUT_SEC = 300  # 5 minutes
    COOLDOWN_SEC = 900          # 15 minutes

    def __init__(self) -> None:
        self.state = AgentState.OBSERVING
        self.entered_at: datetime = datetime.now(timezone.utc).astimezone()
        self.last_signal_type: Optional[str] = None
        self.last_action: Optional[str] = None
        self.notifications_today: int = 0
        self.notify_date: Optional[date] = None  # type: ignore[name-defined]
        self.cooldown_until: Optional[datetime] = None
        self.feedback_timeout_until: Optional[datetime] = None

    # ------------------------------------------------------------------ #
    # Transition helpers
    # ------------------------------------------------------------------ #

    def transition_to(
        self,
        new_state: AgentState,
        signal_type: Optional[str] = None,
        action: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> None:
        if now is None:
            now = datetime.now(timezone.utc).astimezone()

        self.state = new_state
        self.entered_at = now

        if signal_type is not None:
            self.last_signal_type = signal_type
        if action is not None:
            self.last_action = action

        if new_state is AgentState.AWAITING_FEEDBACK:
            self.feedback_timeout_until = now + timedelta(seconds=self.FEEDBACK_TIMEOUT_SEC)
        elif new_state is AgentState.COOLDOWN:
            self.cooldown_until = now + timedelta(seconds=self.COOLDOWN_SEC)
            self.feedback_timeout_until = None

    def auto_advance(self, now: Optional[datetime] = None) -> AgentState:
        """Check timeouts and advance state if needed.  Returns the (possibly new) state."""
        if now is None:
            now = datetime.now(timezone.utc).astimezone()

        if self.state is AgentState.AWAITING_FEEDBACK:
            if self.feedback_timeout_until and now >= self.feedback_timeout_until:
                self.transition_to(AgentState.COOLDOWN, now=now)

        if self.state is AgentState.COOLDOWN:
            if self.cooldown_until and now >= self.cooldown_until:
                self.transition_to(AgentState.OBSERVING, now=now)

        return self.state

    # ------------------------------------------------------------------ #
    # Guards
    # ------------------------------------------------------------------ #

    def may_notify(self, signal_type: Optional[str] = None) -> bool:
        """Return True if the agent is allowed to send a notification *right now*."""
        self.auto_advance()

        if self.state in (AgentState.AWAITING_FEEDBACK, AgentState.ACTING):
            return False

        if self.state is AgentState.COOLDOWN:
            # During cooldown, suppress notifications for the *same* signal type.
            if signal_type is not None and signal_type == self.last_signal_type:
                return False
            # Different signal type is allowed after cooldown, so auto-advance first.
            return True

        if self.state is AgentState.QUIET_HOURS:
            return False

        return True

    def may_log(self) -> bool:
        """Logging is always allowed."""
        return True

    def may_inbox(self, signal_type: Optional[str] = None) -> bool:
        """Inbox queuing is more permissive than notify, but still blocked during acting/feedback."""
        self.auto_advance()
        if self.state in (AgentState.AWAITING_FEEDBACK, AgentState.ACTING):
            return False
        return True

    # ------------------------------------------------------------------ #
    # Daily budget
    # ------------------------------------------------------------------ #

    def reset_daily_budget_if_needed(self, now: Optional[datetime] = None) -> None:
        if now is None:
            now = datetime.now(timezone.utc).astimezone()
        today = now.date()
        if self.notify_date is None or self.notify_date != today:
            self.notifications_today = 0
            self.notify_date = today

    def record_notification(self, now: Optional[datetime] = None) -> None:
        self.reset_daily_budget_if_needed(now)
        self.notifications_today += 1

    # ------------------------------------------------------------------ #
    # Serialization
    # ------------------------------------------------------------------ #

    def to_snapshot(self) -> StateMachineSnapshot:
        return StateMachineSnapshot(
            state=self.state.name,
            entered_at=self.entered_at.isoformat(),
            last_signal_type=self.last_signal_type,
            last_action=self.last_action,
            notifications_today=self.notifications_today,
            notify_date=self.notify_date.isoformat() if self.notify_date else None,
            cooldown_until=self.cooldown_until.isoformat() if self.cooldown_until else None,
            feedback_timeout_until=self.feedback_timeout_until.isoformat()
            if self.feedback_timeout_until
            else None,
        )

    @classmethod
    def from_snapshot(cls, snap: StateMachineSnapshot) -> "CoachAgentStateMachine":
        sm = cls()
        sm.state = AgentState[snap.state]
        sm.entered_at = datetime.fromisoformat(snap.entered_at)
        sm.last_signal_type = snap.last_signal_type
        sm.last_action = snap.last_action
        sm.notifications_today = snap.notifications_today
        sm.notify_date = (
            datetime.fromisoformat(snap.notify_date).date() if snap.notify_date else None
        )
        sm.cooldown_until = (
            datetime.fromisoformat(snap.cooldown_until) if snap.cooldown_until else None
        )
        sm.feedback_timeout_until = (
            datetime.fromisoformat(snap.feedback_timeout_until)
            if snap.feedback_timeout_until
            else None
        )
        return sm

    def to_json(self) -> str:
        import json

        snap = self.to_snapshot()
        return json.dumps(
            {
                "state": snap.state,
                "entered_at": snap.entered_at,
                "last_signal_type": snap.last_signal_type,
                "last_action": snap.last_action,
                "notifications_today": snap.notifications_today,
                "notify_date": snap.notify_date,
                "cooldown_until": snap.cooldown_until,
                "feedback_timeout_until": snap.feedback_timeout_until,
            },
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, raw: str) -> "CoachAgentStateMachine":
        import json

        data = json.loads(raw)
        snap = StateMachineSnapshot(**data)
        return cls.from_snapshot(snap)
