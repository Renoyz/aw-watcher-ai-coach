"""Tests for Phase 4: weight scoring, death loop detection, AI agent detection."""

from datetime import datetime, timedelta

import pytest

from aw_coach.analyzer import PatternAnalyzer
from aw_coach.collector import ActivitySlice
from aw_coach.config import AnalysisConfig
from aw_coach.rules.engine import RuleEngine, RuleResult


def _slice(start_hour, start_min, duration_min, app="vscode", is_afk=False):
    start = datetime(2026, 5, 30, start_hour, start_min)
    end = start + timedelta(minutes=duration_min)
    return ActivitySlice(
        start=start, end=end, duration=duration_min * 60,
        is_afk=is_afk, primary_app=app, primary_title=f"{app} window",
    )


def _rule(activity_type, confidence=0.9, weight=None):
    return RuleResult(
        activity_type=activity_type, confidence=confidence,
        method="rule_app_exact", rule_name=None, weight=weight,
    )


@pytest.fixture
def analyzer():
    return PatternAnalyzer(AnalysisConfig())


class TestWeightScoring:
    """Tests for weight-based productivity scoring."""

    def test_rule_result_has_weight(self):
        """RuleResult should carry a weight field."""
        r = RuleResult("programming", 0.9, "rule_app_exact", weight=1.0)
        assert r.weight == 1.0

    def test_default_weight_is_none(self):
        """Weight defaults to None when not specified."""
        r = RuleResult("unknown", 0.0, "rule_miss")
        assert r.weight is None

    def test_engine_returns_weight(self):
        """RuleEngine should return weight from YAML rules."""
        engine = RuleEngine.with_builtin_rules()
        r = engine.classify("Code", "main.py", None)
        # programming apps should have weight >= 0.8
        assert r.weight is not None
        assert r.weight >= 0.8

    def test_distraction_has_negative_weight(self):
        """Entertainment/social should have negative or low weight."""
        engine = RuleEngine.with_builtin_rules()
        r = engine.classify("chrome", "YouTube - video", "https://youtube.com")
        assert r.weight is not None
        assert r.weight <= 0.0

    def test_productivity_score_calculation(self, analyzer):
        """Productivity score uses weights instead of binary classification."""
        slices = [
            _slice(9, 0, 60),   # programming (weight 1.0)
            _slice(10, 0, 30),  # entertainment (weight -0.5)
            _slice(10, 30, 30), # admin (weight 0.3)
        ]
        rules = [
            _rule("programming", weight=1.0),
            _rule("entertainment", weight=-0.5),
            _rule("admin", weight=0.3),
        ]
        result = analyzer.analyze(slices, rules)
        assert hasattr(result, "productivity_score")
        assert 0 <= result.productivity_score <= 100


class TestDeathLoop:
    """Tests for A↔B repetitive switching pattern detection."""

    def test_detects_simple_loop(self, analyzer):
        """A↔B↔A↔B pattern (>=3 alternations) is detected."""
        slices = [
            _slice(9, 0, 2, app="vscode"),
            _slice(9, 2, 2, app="chrome"),
            _slice(9, 4, 2, app="vscode"),
            _slice(9, 6, 2, app="chrome"),
            _slice(9, 8, 2, app="vscode"),
            _slice(9, 10, 2, app="chrome"),
        ]
        rules = [
            _rule("programming"), _rule("research"),
            _rule("programming"), _rule("research"),
            _rule("programming"), _rule("research"),
        ]
        result = analyzer.analyze(slices, rules)
        assert hasattr(result, "death_loops")
        assert len(result.death_loops) >= 1
        loop = result.death_loops[0]
        assert "vscode" in loop["apps"] and "chrome" in loop["apps"]

    def test_no_loop_with_varied_apps(self, analyzer):
        """A→B→C→D is not a death loop."""
        slices = [
            _slice(9, 0, 5, app="vscode"),
            _slice(9, 5, 5, app="chrome"),
            _slice(9, 10, 5, app="slack"),
            _slice(9, 15, 5, app="terminal"),
        ]
        rules = [_rule("programming"), _rule("research"), _rule("social"), _rule("programming")]
        result = analyzer.analyze(slices, rules)
        assert result.death_loops == []

    def test_loop_needs_minimum_3_alternations(self, analyzer):
        """A↔B only twice is not a death loop."""
        slices = [
            _slice(9, 0, 5, app="vscode"),
            _slice(9, 5, 5, app="chrome"),
            _slice(9, 10, 5, app="vscode"),
            _slice(9, 15, 5, app="chrome"),
        ]
        rules = [_rule("programming"), _rule("research"), _rule("programming"), _rule("research")]
        result = analyzer.analyze(slices, rules)
        assert result.death_loops == []


class TestAIAgentDetection:
    """Tests for AI coding agent detection."""

    def test_cursor_detected_as_ai_assisted(self):
        """Cursor with AI markers in title should be ai_assisted."""
        engine = RuleEngine.with_builtin_rules()
        r = engine.classify("Cursor", "✳ Generating - main.rs", None)
        assert r.activity_type == "programming"
        # AI-assisted flag or tag
        assert r.weight is not None and r.weight >= 0.9

    def test_claude_code_session(self):
        """Terminal with Claude indicator should not penalize switches."""
        engine = RuleEngine.with_builtin_rules()
        r = engine.classify("Gnome-terminal", "claude - project", None)
        # Should still be programming, not penalized
        assert r.activity_type == "programming"
