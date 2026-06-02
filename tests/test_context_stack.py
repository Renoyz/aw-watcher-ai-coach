"""Tests for context_stack module."""

from __future__ import annotations

from datetime import datetime, timezone

from aw_coach.context_stack import ContextFrame, ContextStack
from aw_coach.enriched_state import SemanticWorkState


class TestContextFrame:
    def test_from_state(self):
        now = datetime.now(timezone.utc).astimezone()
        state = SemanticWorkState(
            updated_at=now,
            current_app="Code",
            current_title="main.py",
            semantic_project="x_system",
            likely_mode="coding",
            risk_level="normal",
        )
        frame = ContextFrame.from_state(state)
        assert frame.project == "x_system"
        assert frame.mode == "coding"
        assert frame.app == "Code"
        assert frame.title == "main.py"
        assert frame.accumulated_sec == 0.0
        assert frame.is_active is True

    def test_to_dict(self):
        now = datetime.now(timezone.utc).astimezone()
        frame = ContextFrame(
            project="x", mode="coding", app="Code", title="t",
            entered_at=now, accumulated_sec=120.0
        )
        d = frame.to_dict()
        assert d["project"] == "x"
        assert d["mode"] == "coding"
        assert isinstance(d["entered_at"], str)


class TestContextStack:
    def _make_state(self, mode, app="Code", title="t", project=None, minutes_ago=0):
        now = datetime.now(timezone.utc).astimezone()
        return SemanticWorkState(
            updated_at=now,
            current_app=app,
            current_title=title,
            semantic_project=project,
            likely_mode=mode,
            risk_level="normal",
        )

    def test_empty_stack_push(self):
        stack = ContextStack()
        assert stack.primary is None

        state = self._make_state("coding", project="x_system")
        stack.update(state)
        assert stack.primary is not None
        assert stack.primary.mode == "coding"
        assert stack.primary.project == "x_system"

    def test_stay_in_primary_accumulates(self):
        from datetime import timedelta
        stack = ContextStack()
        now = datetime.now(timezone.utc).astimezone()

        state1 = self._make_state("coding", project="x_system")
        stack.update(state1, now)

        state2 = self._make_state("coding", project="x_system")
        stack.update(state2, now + timedelta(minutes=1))

        assert stack.primary.accumulated_sec >= 60

    def test_shallow_interrupt_preserves_primary(self):
        from datetime import timedelta
        stack = ContextStack()
        now = datetime.now(timezone.utc).astimezone()

        # Enter primary context
        stack.update(self._make_state("coding", project="x_system"), now)

        # Brief interrupt (1 min browsing)
        stack.update(
            self._make_state("browsing", app="Chrome"),
            now + timedelta(minutes=1),
        )

        assert stack.primary.mode == "coding"
        assert stack.primary.project == "x_system"

    def test_return_to_primary_resets_interrupt(self):
        from datetime import timedelta
        stack = ContextStack()
        now = datetime.now(timezone.utc).astimezone()

        stack.update(self._make_state("coding", project="x_system"), now)
        stack.update(self._make_state("browsing", app="Chrome"), now + timedelta(minutes=1))
        stack.update(self._make_state("coding", project="x_system"), now + timedelta(minutes=2))

        assert stack.primary.mode == "coding"
        assert stack._interrupt_mode is None
        assert stack.primary.accumulated_sec >= 120

    def test_real_switch_after_timeout(self):
        from datetime import timedelta
        stack = ContextStack()
        now = datetime.now(timezone.utc).astimezone()

        stack.update(self._make_state("coding", project="x_system"), now)

        # Stay in researching for 6 minutes (> SWITCH_THRESHOLD_SEC)
        stack.update(
            self._make_state("researching", app="Chrome"),
            now + timedelta(minutes=6),
        )

        assert stack.primary.mode == "researching"

    def test_real_switch_to_deep_mode_immediate(self):
        from datetime import timedelta
        stack = ContextStack()
        now = datetime.now(timezone.utc).astimezone()

        stack.update(self._make_state("coding", project="x_system"), now)

        # Even 1 minute of debugging triggers switch (deep mode)
        stack.update(
            self._make_state("debugging", app="Code"),
            now + timedelta(minutes=1),
        )

        assert stack.primary.mode == "debugging"

    def test_stack_bounded(self):
        from datetime import timedelta
        stack = ContextStack()
        now = datetime.now(timezone.utc).astimezone()

        for i in range(10):
            stack.update(
                self._make_state(f"mode{i}", project=f"proj{i}"),
                now + timedelta(minutes=i * 6),
            )

        assert len(stack.frames) <= 5

    def test_to_dict(self):
        stack = ContextStack()
        now = datetime.now(timezone.utc).astimezone()
        stack.update(self._make_state("coding", project="x_system"), now)

        d = stack.to_dict()
        assert d["primary_mode"] == "coding"
        assert d["primary_project"] == "x_system"
        assert d["depth"] == 1
        assert len(d["frames"]) == 1

    def test_get_active_block_minutes(self):
        from datetime import timedelta
        stack = ContextStack()
        now = datetime.now(timezone.utc).astimezone()

        stack.update(self._make_state("coding", project="x_system"), now)
        stack.update(self._make_state("coding", project="x_system"), now + timedelta(minutes=5))

        mins = stack.get_active_block_minutes()
        assert mins >= 5

    def test_interruption_summary(self):
        from datetime import timedelta
        stack = ContextStack()
        now = datetime.now(timezone.utc).astimezone()

        stack.update(self._make_state("coding", project="x_system"), now)
        stack.update(self._make_state("browsing", app="Chrome"), now + timedelta(minutes=1))

        summary = stack.get_interruption_summary()
        assert summary is not None
        assert "临时切换" in summary
        assert "browsing" in summary
