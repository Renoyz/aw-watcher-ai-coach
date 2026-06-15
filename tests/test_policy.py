"""Tests for the lightweight policy engine (behaviour-tree style)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aw_coach.policy import (
    Blackboard,
    Cooldown,
    DailyBudget,
    FocusGuard,
    LogOnly,
    NotifyNow,
    PolicyEngine,
    QuietHours,
    Selector,
    Sequence,
    SeverityCheck,
    Status,
)


class TestNodes:
    def test_log_only_always_success(self):
        from aw_coach.policy import _DecisionStore

        store = _DecisionStore()
        node = LogOnly(store)
        bb = Blackboard()
        assert node.tick(bb) is Status.SUCCESS
        assert store.decision.action == "log_only"

    def test_notify_now_success(self):
        from aw_coach.policy import _DecisionStore

        store = _DecisionStore()
        node = NotifyNow(store)
        bb = Blackboard(severity=0.9)
        assert node.tick(bb) is Status.SUCCESS
        assert store.decision.action == "notify_now"

    def test_focus_guard_blocks_focus(self):
        node = FocusGuard()
        bb = Blackboard(in_focus_block=True, focus_block_minutes=25)
        assert node.tick(bb) is Status.FAILURE

    def test_focus_guard_allows_non_focus(self):
        node = FocusGuard()
        bb = Blackboard(in_focus_block=False)
        assert node.tick(bb) is Status.SUCCESS

    def test_quiet_hours_blocks(self):
        node = QuietHours()
        bb = Blackboard(quiet_hours=True)
        assert node.tick(bb) is Status.FAILURE

    def test_daily_budget_blocks(self):
        node = DailyBudget(max_per_day=2)
        bb = Blackboard(notifications_today=3)
        assert node.tick(bb) is Status.FAILURE

    def test_daily_budget_allows(self):
        node = DailyBudget(max_per_day=4)
        bb = Blackboard(notifications_today=2)
        assert node.tick(bb) is Status.SUCCESS

    def test_cooldown_blocks_recent(self):
        node = Cooldown(seconds=600)
        now = datetime.now(timezone.utc).astimezone()
        bb = Blackboard(
            signal_type="stuck",
            now=now,
            last_notify_by_type={"stuck": now - timedelta(seconds=300)},
        )
        assert node.tick(bb) is Status.FAILURE

    def test_cooldown_allows_old(self):
        node = Cooldown(seconds=600)
        now = datetime.now(timezone.utc).astimezone()
        bb = Blackboard(
            signal_type="stuck",
            now=now,
            last_notify_by_type={"stuck": now - timedelta(seconds=900)},
        )
        assert node.tick(bb) is Status.SUCCESS

    def test_severity_check_blocks_low(self):
        node = SeverityCheck(min_severity=0.7)
        bb = Blackboard(severity=0.5)
        assert node.tick(bb) is Status.FAILURE

    def test_severity_check_allows_high(self):
        node = SeverityCheck(min_severity=0.7)
        bb = Blackboard(severity=0.8)
        assert node.tick(bb) is Status.SUCCESS


class TestComposites:
    def test_selector_first_success_wins(self):
        from aw_coach.policy import _DecisionStore

        store = _DecisionStore()
        tree = Selector(
            Sequence(FocusGuard(), NotifyNow(store)),
            LogOnly(store),
        )
        bb = Blackboard(in_focus_block=False, severity=0.9)
        assert tree.tick(bb) is Status.SUCCESS
        assert store.decision.action == "notify_now"

    def test_selector_fallback_to_log_only(self):
        from aw_coach.policy import _DecisionStore

        store = _DecisionStore()
        tree = Selector(
            Sequence(FocusGuard(), NotifyNow(store)),
            LogOnly(store),
        )
        bb = Blackboard(in_focus_block=True, focus_block_minutes=25, severity=0.9)
        assert tree.tick(bb) is Status.SUCCESS
        assert store.decision.action == "log_only"

    def test_sequence_all_must_pass(self):
        from aw_coach.policy import _DecisionStore

        store = _DecisionStore()
        tree = Sequence(
            SeverityCheck(min_severity=0.5),
            DailyBudget(max_per_day=4),
            NotifyNow(store),
        )
        bb = Blackboard(severity=0.6, notifications_today=2)
        assert tree.tick(bb) is Status.SUCCESS
        assert store.decision.action == "notify_now"

    def test_sequence_fails_early(self):
        from aw_coach.policy import _DecisionStore

        store = _DecisionStore()
        tree = Sequence(
            SeverityCheck(min_severity=0.5),
            DailyBudget(max_per_day=2),
            NotifyNow(store),
        )
        bb = Blackboard(severity=0.6, notifications_today=3)
        assert tree.tick(bb) is Status.FAILURE
        assert store.decision is None


class TestPolicyEngine:
    def test_high_severity_notify_now(self):
        engine = PolicyEngine()
        decision = engine.decide(
            signal_type="stuck",
            severity=0.85,
            evidence="Debug loop detected",
            in_focus_block=False,
        )
        assert decision.action == "notify_now"

    def test_medium_severity_inbox(self):
        engine = PolicyEngine()
        decision = engine.decide(
            signal_type="search_loop",
            severity=0.6,
            evidence="Research oscillation",
            in_focus_block=False,
        )
        assert decision.action == "inbox"

    def test_low_severity_log_only(self):
        engine = PolicyEngine()
        decision = engine.decide(
            signal_type="focused",
            severity=0.2,
            evidence="Deep focus block",
            in_focus_block=False,
        )
        assert decision.action == "log_only"

    def test_focus_guard_blocks_notify(self):
        engine = PolicyEngine()
        decision = engine.decide(
            signal_type="stuck",
            severity=0.85,
            evidence="Debug loop",
            in_focus_block=True,
            focus_block_minutes=25,
        )
        # Focus guard blocks notify_now, then inbox is also blocked by focus guard,
        # so fallback to log_only
        assert decision.action == "log_only"

    def test_daily_budget_blocks_inbox(self):
        engine = PolicyEngine(max_notifications_per_day=2)
        decision = engine.decide(
            signal_type="search_loop",
            severity=0.6,
            evidence="Research oscillation",
            in_focus_block=False,
            notifications_today=3,
        )
        assert decision.action == "log_only"

    def test_cooldown_blocks_repeated_type(self):
        now = datetime.now(timezone.utc).astimezone()
        engine = PolicyEngine(cooldown_seconds=600)
        decision = engine.decide(
            signal_type="search_loop",
            severity=0.6,
            evidence="Research oscillation",
            in_focus_block=False,
            notifications_today=1,
            last_notify_by_type={"search_loop": now - timedelta(seconds=300)},
            now=now,
        )
        assert decision.action == "log_only"

    def test_quiet_hours_suppresses_all(self):
        engine = PolicyEngine()
        decision = engine.decide(
            signal_type="stuck",
            severity=0.9,
            evidence="Critical issue",
            in_focus_block=False,
            quiet_hours=True,
        )
        assert decision.action == "log_only"
