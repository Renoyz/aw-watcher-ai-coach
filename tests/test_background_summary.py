"""Tests for background summary silent logic."""

from __future__ import annotations

from aw_coach.analyzer import AnalysisResult
from aw_coach.background_summary import build_background_summary_prompt, should_silent_summary
from aw_coach.config import Config


def _analysis(**kwargs) -> AnalysisResult:
    defaults = dict(
        total_hours=1.0,
        effective_hours=0.2,
        deep_work_hours=0.1,
        focus_score=50,
        productivity_score=50,
        switch_count=3,
        activity_breakdown={"programming": 0.2},
        hourly_scores=[],
        death_loops=[],
    )
    defaults.update(kwargs)
    return AnalysisResult(**defaults)


class TestBackgroundSummary:
    def test_silent_when_low_activity(self):
        config = Config()
        assert should_silent_summary(_analysis(effective_hours=0.1), config) is True

    def test_not_silent_with_death_loops(self):
        config = Config()
        assert should_silent_summary(
            _analysis(effective_hours=0.1, death_loops=[{"apps": ["a", "b"]}]),
            config,
        ) is False

    def test_not_silent_with_signal_override(self):
        config = Config()
        assert should_silent_summary(
            _analysis(effective_hours=0.1),
            config,
            active_signals=["stuck"],
        ) is False

    def test_prompt_includes_task_sessions(self):
        from aw_coach.task_models import TaskSession

        session = TaskSession(
            task_id="aw-coach:main",
            label="main.py",
            project="aw-coach",
            intent="implement",
            started_at=__import__("datetime").datetime.now(),
            accumulated_sec=3600,
        )
        prompt = build_background_summary_prompt(
            _analysis(effective_hours=2.0),
            task_sessions=[session],
        )
        assert "main.py" in prompt
        assert "任务会话" in prompt
