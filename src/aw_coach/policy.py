"""Lightweight policy engine using a behaviour-tree pattern.

No third-party BT library is used; the whole module is ~150 lines.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum, auto
from typing import Dict, List, Optional, Set


class Status(Enum):
    SUCCESS = auto()
    FAILURE = auto()


# ---------------------------------------------------------------------------
# Action decisions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ActionDecision:
    action: str          # "log_only" | "notify_now" | "inbox"
    reason: str          # human-readable why this action was chosen
    evidence: str = ""   # detector evidence to show the user


# ---------------------------------------------------------------------------
# Blackboard: shared context passed to every node
# ---------------------------------------------------------------------------

@dataclass
class Blackboard:
    signal_type: Optional[str] = None
    severity: float = 0.0
    evidence: str = ""

    # Focus / interrupt state
    in_focus_block: bool = False
    focus_block_minutes: int = 0

    # Time & budget state
    now: datetime = None  # type: ignore[assignment]
    quiet_hours: bool = False
    notifications_today: int = 0
    max_notifications_per_day: int = 4
    last_notify_by_type: Dict[str, datetime] = None  # type: ignore[assignment]
    cooldown_seconds: int = 600  # 10 min default

    def __post_init__(self):
        if self.now is None:
            self.now = datetime.now(timezone.utc).astimezone()
        if self.last_notify_by_type is None:
            self.last_notify_by_type = {}


# ---------------------------------------------------------------------------
# Base node
# ---------------------------------------------------------------------------

class Node(ABC):
    """A single node in the policy tree."""

    @abstractmethod
    def tick(self, bb: Blackboard) -> Status:
        """Evaluate this node against the blackboard."""


# ---------------------------------------------------------------------------
# Composite nodes
# ---------------------------------------------------------------------------

class Selector(Node):
    """Evaluate children in order; return SUCCESS on first SUCCESS."""

    def __init__(self, *children: Node) -> None:
        self.children = list(children)

    def tick(self, bb: Blackboard) -> Status:
        for child in self.children:
            if child.tick(bb) is Status.SUCCESS:
                return Status.SUCCESS
        return Status.FAILURE


class Sequence(Node):
    """Evaluate children in order; return SUCCESS only if ALL succeed."""

    def __init__(self, *children: Node) -> None:
        self.children = list(children)

    def tick(self, bb: Blackboard) -> Status:
        for child in self.children:
            if child.tick(bb) is Status.FAILURE:
                return Status.FAILURE
        return Status.SUCCESS


# ---------------------------------------------------------------------------
# Decorators (guards / conditions)
# ---------------------------------------------------------------------------

class FocusGuard(Node):
    """FAIL if user is in a protected focus block."""

    def tick(self, bb: Blackboard) -> Status:
        if bb.in_focus_block and bb.focus_block_minutes >= 20:
            return Status.FAILURE
        return Status.SUCCESS


class QuietHours(Node):
    """FAIL if current time is in quiet hours."""

    def tick(self, bb: Blackboard) -> Status:
        if bb.quiet_hours:
            return Status.FAILURE
        return Status.SUCCESS


class DailyBudget(Node):
    """FAIL if daily notification budget is exhausted."""

    def __init__(self, max_per_day: int = 4) -> None:
        self.max_per_day = max_per_day

    def tick(self, bb: Blackboard) -> Status:
        if bb.notifications_today >= self.max_per_day:
            return Status.FAILURE
        return Status.SUCCESS


class Cooldown(Node):
    """FAIL if this signal type was recently notified."""

    def __init__(self, seconds: int = 600) -> None:
        self.seconds = seconds

    def tick(self, bb: Blackboard) -> Status:
        if bb.signal_type is None:
            return Status.SUCCESS
        last = bb.last_notify_by_type.get(bb.signal_type)
        if last is not None:
            elapsed = (bb.now - last).total_seconds()
            if elapsed < self.seconds:
                return Status.FAILURE
        return Status.SUCCESS


class SeverityCheck(Node):
    """FAIL if severity is below threshold."""

    def __init__(self, min_severity: float = 0.5) -> None:
        self.min_severity = min_severity

    def tick(self, bb: Blackboard) -> Status:
        if bb.severity < self.min_severity:
            return Status.FAILURE
        return Status.SUCCESS


# ---------------------------------------------------------------------------
# Action nodes (leafs that record the decision)
# ---------------------------------------------------------------------------

class _DecisionStore:
    """Shared mutable store for the action chosen by the tree."""

    def __init__(self) -> None:
        self.decision: Optional[ActionDecision] = None


class LogOnly(Node):
    """Always succeeds; sets decision to 'log_only'."""

    def __init__(self, store: _DecisionStore, reason: str = "default") -> None:
        self.store = store
        self.reason = reason

    def tick(self, bb: Blackboard) -> Status:
        self.store.decision = ActionDecision(
            action="log_only",
            reason=self.reason,
            evidence=bb.evidence,
        )
        return Status.SUCCESS


class NotifyNow(Node):
    """Succeeds; sets decision to 'notify_now'."""

    def __init__(self, store: _DecisionStore, reason: str = "severity high") -> None:
        self.store = store
        self.reason = reason

    def tick(self, bb: Blackboard) -> Status:
        self.store.decision = ActionDecision(
            action="notify_now",
            reason=self.reason,
            evidence=bb.evidence,
        )
        return Status.SUCCESS


class InboxQueue(Node):
    """Succeeds; sets decision to 'inbox'."""

    def __init__(self, store: _DecisionStore, reason: str = "deferrable") -> None:
        self.store = store
        self.reason = reason

    def tick(self, bb: Blackboard) -> Status:
        self.store.decision = ActionDecision(
            action="inbox",
            reason=self.reason,
            evidence=bb.evidence,
        )
        return Status.SUCCESS


# ---------------------------------------------------------------------------
# Policy engine: assembles the tree and runs it
# ---------------------------------------------------------------------------

class PolicyEngine:
    """Decide what to do with a detected signal.

    Usage:
        engine = PolicyEngine()
        decision = engine.decide(signal_type="stuck", severity=0.7, ...)
        # decision.action == "notify_now" | "inbox" | "log_only"
    """

    def __init__(
        self,
        max_notifications_per_day: int = 4,
        cooldown_seconds: int = 600,
    ) -> None:
        self._store = _DecisionStore()
        self.max_notifications_per_day = max_notifications_per_day
        self.cooldown_seconds = cooldown_seconds
        self._tree = self._build_tree(self._store)

    def _build_tree(self, store: _DecisionStore) -> Node:
        """Build the default policy tree."""
        return Selector(
            # Branch 1: Immediate notify (high severity, no suppression)
            Sequence(
                SeverityCheck(min_severity=0.8),
                FocusGuard(),
                QuietHours(),
                NotifyNow(store, reason="high severity, immediate"),
            ),
            # Branch 2: Inbox queue (medium severity, budget/cooldown aware)
            Sequence(
                SeverityCheck(min_severity=0.5),
                FocusGuard(),
                QuietHours(),
                DailyBudget(max_per_day=self.max_notifications_per_day),
                Cooldown(seconds=self.cooldown_seconds),
                InboxQueue(store, reason="medium severity, queued"),
            ),
            # Fallback: silently log
            LogOnly(store, reason="below threshold or suppressed"),
        )

    def decide(
        self,
        signal_type: Optional[str],
        severity: float,
        evidence: str = "",
        in_focus_block: bool = False,
        focus_block_minutes: int = 0,
        quiet_hours: bool = False,
        notifications_today: int = 0,
        last_notify_by_type: Optional[Dict[str, datetime]] = None,
        now: Optional[datetime] = None,
    ) -> ActionDecision:
        """Run the policy tree and return the chosen action."""
        bb = Blackboard(
            signal_type=signal_type,
            severity=severity,
            evidence=evidence,
            in_focus_block=in_focus_block,
            focus_block_minutes=focus_block_minutes,
            quiet_hours=quiet_hours,
            notifications_today=notifications_today,
            last_notify_by_type=last_notify_by_type or {},
            now=now or datetime.now(timezone.utc).astimezone(),
            cooldown_seconds=self.cooldown_seconds,
            max_notifications_per_day=self.max_notifications_per_day,
        )
        self._store.decision = None
        self._tree.tick(bb)
        if self._store.decision is None:
            return ActionDecision(action="log_only", reason="tree returned no decision")
        return self._store.decision
