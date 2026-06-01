"""Tests for PatternAnalyzer - focus score, deep work, switches."""

from datetime import datetime, timedelta

import pytest

from aw_coach.analyzer import PatternAnalyzer
from aw_coach.collector import ActivitySlice
from aw_coach.config import AnalysisConfig
from aw_coach.rules.engine import RuleResult


def _slice(start_hour, start_min, duration_min, app="vscode", is_afk=False):
    """Helper to create a slice at a given time with duration in minutes."""
    start = datetime(2026, 5, 30, start_hour, start_min)
    end = start + timedelta(minutes=duration_min)
    return ActivitySlice(
        start=start, end=end, duration=duration_min * 60,
        is_afk=is_afk, primary_app=app, primary_title=f"{app} window",
    )


def _rule(activity_type, confidence=0.9):
    return RuleResult(activity_type=activity_type, confidence=confidence, method="rule_app_exact")


@pytest.fixture
def analyzer():
    return PatternAnalyzer(AnalysisConfig())


class TestEffectiveHours:
    def test_all_active_work(self, analyzer):
        slices = [_slice(9, 0, 60), _slice(10, 0, 60)]
        rules = [_rule("programming"), _rule("programming")]
        result = analyzer.analyze(slices, rules)
        assert result.effective_hours == pytest.approx(2.0, abs=0.01)

    def test_afk_excluded(self, analyzer):
        slices = [_slice(9, 0, 60), _slice(10, 0, 60, is_afk=True)]
        rules = [_rule("programming"), _rule("programming")]
        result = analyzer.analyze(slices, rules)
        assert result.effective_hours == pytest.approx(1.0, abs=0.01)

    def test_entertainment_excluded(self, analyzer):
        slices = [_slice(9, 0, 60), _slice(10, 0, 60, app="chrome")]
        rules = [_rule("programming"), _rule("entertainment")]
        result = analyzer.analyze(slices, rules)
        assert result.effective_hours == pytest.approx(1.0, abs=0.01)


class TestDeepWork:
    def test_25min_continuous_counts(self, analyzer):
        """25+ minutes of same type counts as deep work."""
        slices = [_slice(9, 0, 30)]
        rules = [_rule("programming")]
        result = analyzer.analyze(slices, rules)
        assert result.deep_work_hours >= 30 / 60

    def test_short_session_not_deep(self, analyzer):
        """Less than 25 minutes does not count as deep work."""
        slices = [_slice(9, 0, 20)]
        rules = [_rule("programming")]
        result = analyzer.analyze(slices, rules)
        assert result.deep_work_hours == 0.0

    def test_two_consecutive_same_type(self, analyzer):
        """Two consecutive slices of same type combine for deep work."""
        slices = [_slice(9, 0, 15), _slice(9, 15, 15)]
        rules = [_rule("programming"), _rule("programming")]
        result = analyzer.analyze(slices, rules)
        assert result.deep_work_hours >= 30 / 60

    def test_entertainment_not_deep(self, analyzer):
        """Entertainment never counts as deep work."""
        slices = [_slice(9, 0, 60)]
        rules = [_rule("entertainment")]
        result = analyzer.analyze(slices, rules)
        assert result.deep_work_hours == 0.0

    def test_short_afk_does_not_break_streak(self, analyzer):
        """AFK <= 2 minutes does not interrupt deep work streak."""
        slices = [
            _slice(9, 0, 15),                              # programming
            _slice(9, 15, 1, is_afk=True),                 # 1min AFK (short)
            _slice(9, 16, 15),                             # programming
        ]
        rules = [_rule("programming"), _rule("programming"), _rule("programming")]
        result = analyzer.analyze(slices, rules)
        # 15 + 15 = 30 min streak, short AFK didn't break it
        assert result.deep_work_hours >= 30 / 60

    def test_long_afk_breaks_streak(self, analyzer):
        """AFK > 2 minutes breaks deep work streak."""
        slices = [
            _slice(9, 0, 20),                              # programming 20min
            _slice(9, 20, 5, is_afk=True),                 # 5min AFK (long)
            _slice(9, 25, 20),                             # programming 20min
        ]
        rules = [_rule("programming"), _rule("programming"), _rule("programming")]
        result = analyzer.analyze(slices, rules)
        # Neither 20min segment meets threshold alone
        assert result.deep_work_hours == 0.0


class TestSwitchCount:
    def test_no_switches(self, analyzer):
        slices = [_slice(9, 0, 30), _slice(9, 30, 30)]
        rules = [_rule("programming"), _rule("programming")]
        result = analyzer.analyze(slices, rules)
        assert result.switch_count == 0

    def test_one_switch(self, analyzer):
        slices = [_slice(9, 0, 30), _slice(9, 30, 30)]
        rules = [_rule("programming"), _rule("meeting")]
        result = analyzer.analyze(slices, rules)
        assert result.switch_count == 1

    def test_multiple_switches(self, analyzer):
        slices = [_slice(9, 0, 15), _slice(9, 15, 15), _slice(9, 30, 15), _slice(9, 45, 15)]
        rules = [_rule("programming"), _rule("meeting"), _rule("programming"), _rule("social")]
        result = analyzer.analyze(slices, rules)
        assert result.switch_count == 3

    def test_brief_flicker_not_counted(self, analyzer):
        """A brief (<30s) type change should not count as a real switch."""
        # programming 5min, then 10s of 'research', then back to programming 5min
        slices = [
            _slice(9, 0, 5),       # programming 5min
            ActivitySlice(
                start=datetime(2026, 5, 30, 9, 5),
                end=datetime(2026, 5, 30, 9, 5, 10),
                duration=10, is_afk=False,
                primary_app="chrome", primary_title="quick glance",
            ),
            _slice(9, 6, 5),       # programming 5min
        ]
        rules = [_rule("programming"), _rule("research"), _rule("programming")]
        result = analyzer.analyze(slices, rules)
        # The 10s research flicker should not count as 2 switches
        assert result.switch_count <= 1


class TestFocusScore:
    def test_perfect_focus(self, analyzer):
        """Long deep work, no switches, no distractions → high score."""
        slices = [_slice(9, 0, 120)]
        rules = [_rule("programming")]
        result = analyzer.analyze(slices, rules)
        assert result.focus_score >= 80

    def test_many_switches_lower_score(self, analyzer):
        """Frequent switches lower the score."""
        slices = [_slice(9, 0 + i * 5, 5) for i in range(12)]
        rules = [_rule("programming") if i % 2 == 0 else _rule("meeting") for i in range(12)]
        result = analyzer.analyze(slices, rules)
        assert result.focus_score < 60

    def test_all_entertainment_low_score(self, analyzer):
        """All entertainment → low score."""
        slices = [_slice(9, 0, 60)]
        rules = [_rule("entertainment")]
        result = analyzer.analyze(slices, rules)
        assert result.focus_score < 30

    def test_score_clamped_0_100(self, analyzer):
        """Score is always between 0 and 100."""
        slices = [_slice(9, 0, 5)]
        rules = [_rule("programming")]
        result = analyzer.analyze(slices, rules)
        assert 0 <= result.focus_score <= 100


class TestActivityBreakdown:
    def test_single_type(self, analyzer):
        slices = [_slice(9, 0, 60)]
        rules = [_rule("programming")]
        result = analyzer.analyze(slices, rules)
        assert "programming" in result.activity_breakdown
        assert result.activity_breakdown["programming"] == pytest.approx(1.0, abs=0.01)

    def test_multiple_types(self, analyzer):
        slices = [_slice(9, 0, 30), _slice(9, 30, 30)]
        rules = [_rule("programming"), _rule("meeting")]
        result = analyzer.analyze(slices, rules)
        assert result.activity_breakdown["programming"] == pytest.approx(0.5, abs=0.01)
        assert result.activity_breakdown["meeting"] == pytest.approx(0.5, abs=0.01)

    def test_configured_distraction_apps_fallback(self):
        analyzer = PatternAnalyzer(AnalysisConfig(distraction_apps=["videoapp"]))
        slices = [_slice(9, 0, 60, app="videoapp-player")]
        rules = [_rule("unknown", confidence=0.0)]
        result = analyzer.analyze(slices, rules)
        assert result.activity_breakdown["entertainment"] == pytest.approx(1.0, abs=0.01)
        assert result.effective_hours == pytest.approx(0.0, abs=0.01)

    def test_optional_work_schedule_filter(self):
        analyzer = PatternAnalyzer(
            AnalysisConfig(
                work_hours_start="09:00",
                work_hours_end="18:00",
                work_days=[5],
                restrict_to_work_schedule=True,
            )
        )
        friday = datetime(2026, 5, 29, 8, 30)
        slices = [
            ActivitySlice(friday, friday + timedelta(hours=1), 3600, False, "vscode", ""),
            ActivitySlice(
                friday + timedelta(hours=1),
                friday + timedelta(hours=2),
                3600,
                False,
                "vscode",
                "",
            ),
        ]
        rules = [_rule("programming"), _rule("programming")]
        result = analyzer.analyze(slices, rules)
        assert result.total_hours == pytest.approx(1.5, abs=0.01)


class TestHourlyScores:
    def test_has_scores_for_active_hours(self, analyzer):
        slices = [_slice(9, 0, 60), _slice(10, 0, 60)]
        rules = [_rule("programming"), _rule("programming")]
        result = analyzer.analyze(slices, rules)
        hours = [h for h, _ in result.hourly_scores]
        assert 9 in hours
        assert 10 in hours

    def test_cross_hour_slice_is_split(self, analyzer):
        slices = [_slice(9, 45, 30)]
        rules = [_rule("programming")]
        result = analyzer.analyze(slices, rules)
        hours = [h for h, _ in result.hourly_scores]
        assert hours == [9, 10]

    def test_empty_slices(self, analyzer):
        result = analyzer.analyze([], [])
        assert result.total_hours == 0.0
        assert result.focus_score == 0
        assert result.hourly_scores == []
