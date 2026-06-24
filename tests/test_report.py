"""Tests for ReportGenerator - Markdown daily report."""

from datetime import date

import pytest

from aw_coach.analyzer import AnalysisResult
from aw_coach.report import ReportGenerator, generate_rule_suggestions


@pytest.fixture
def sample_analysis():
    return AnalysisResult(
        total_hours=6.5,
        effective_hours=5.2,
        deep_work_hours=2.25,
        focus_score=72,
        switch_count=23,
        activity_breakdown={
            "programming": 3.3,
            "meeting": 1.5,
            "research": 1.0,
            "admin": 0.4,
        },
        hourly_scores=[(9, 85), (10, 78), (11, 62), (14, 75), (15, 55), (16, 35)],
        task_switch_count=4,
        task_breakdown={"api": 3.0, "docs": 1.0},
        task_deep_work_breakdown={"api": 1.5},
    )


@pytest.fixture
def generator():
    return ReportGenerator()


class TestDailyReport:
    def test_contains_date(self, generator, sample_analysis):
        report = generator.generate_daily(date(2026, 5, 30), sample_analysis)
        assert "2026-05-30" in report

    def test_contains_overview_table(self, generator, sample_analysis):
        report = generator.generate_daily(date(2026, 5, 30), sample_analysis)
        assert "5.2" in report or "5.20" in report  # effective hours
        assert "2.25" in report or "2.2" in report  # deep work
        assert "72" in report  # focus score
        assert "23" in report  # switch count
        assert "任务切换" in report
        assert "4" in report

    def test_contains_activity_breakdown(self, generator, sample_analysis):
        report = generator.generate_daily(date(2026, 5, 30), sample_analysis)
        assert "programming" in report
        assert "meeting" in report
        assert "api" in report
        assert "深度 1.5h" in report

    def test_contains_energy_curve(self, generator, sample_analysis):
        report = generator.generate_daily(date(2026, 5, 30), sample_analysis)
        assert "09:00" in report or "9:" in report
        assert "85" in report  # hour 9 score

    def test_is_valid_markdown(self, generator, sample_analysis):
        report = generator.generate_daily(date(2026, 5, 30), sample_analysis)
        assert report.startswith("#")
        assert "##" in report


class TestStatusOutput:
    def test_contains_current_stats(self, generator, sample_analysis):
        output = generator.generate_status(sample_analysis)
        assert "72" in output  # focus score
        assert "programming" in output

    def test_contains_progress_bars(self, generator, sample_analysis):
        output = generator.generate_status(sample_analysis)
        assert "█" in output or "=" in output


class TestRuleSuggestions:
    def test_high_switches_suggestion(self):
        analysis = AnalysisResult(
            total_hours=6.0, effective_hours=5.0, deep_work_hours=2.0,
            focus_score=60, switch_count=25,
            activity_breakdown={"programming": 5.0},
            hourly_scores=[(9, 70)],
        )
        suggestions = generate_rule_suggestions(analysis)
        assert any("切换" in s or "switch" in s.lower() for s in suggestions)

    def test_low_deep_work_suggestion(self):
        analysis = AnalysisResult(
            total_hours=6.0, effective_hours=5.0, deep_work_hours=0.5,
            focus_score=60, switch_count=5,
            activity_breakdown={"programming": 5.0},
            hourly_scores=[(9, 70)],
        )
        suggestions = generate_rule_suggestions(analysis)
        assert any("深度" in s or "deep" in s.lower() for s in suggestions)

    def test_high_entertainment_suggestion(self):
        analysis = AnalysisResult(
            total_hours=6.0, effective_hours=3.0, deep_work_hours=1.0,
            focus_score=40, switch_count=5,
            activity_breakdown={"programming": 3.0, "entertainment": 3.0},
            hourly_scores=[(9, 70)],
        )
        suggestions = generate_rule_suggestions(analysis)
        assert any("娱乐" in s or "entertainment" in s.lower() for s in suggestions)

    def test_best_hour_suggestion(self):
        analysis = AnalysisResult(
            total_hours=6.0, effective_hours=5.0, deep_work_hours=2.0,
            focus_score=70, switch_count=5,
            activity_breakdown={"programming": 5.0},
            hourly_scores=[(9, 90), (10, 60), (14, 70)],
        )
        suggestions = generate_rule_suggestions(analysis)
        assert any("9" in s for s in suggestions)

    def test_max_5_suggestions(self):
        analysis = AnalysisResult(
            total_hours=6.0, effective_hours=3.0, deep_work_hours=0.3,
            focus_score=30, switch_count=30,
            activity_breakdown={"programming": 2.0, "entertainment": 3.0, "social": 1.0},
            hourly_scores=[(9, 90), (10, 20)],
        )
        suggestions = generate_rule_suggestions(analysis)
        assert len(suggestions) <= 5

    def test_empty_analysis_no_crash(self):
        analysis = AnalysisResult(
            total_hours=0, effective_hours=0, deep_work_hours=0,
            focus_score=0, switch_count=0,
            activity_breakdown={},
            hourly_scores=[],
        )
        suggestions = generate_rule_suggestions(analysis)
        assert isinstance(suggestions, list)
