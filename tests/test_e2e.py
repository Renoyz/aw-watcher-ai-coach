"""End-to-end test: hybrid backend → analyzer → report generation."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock

from aw_coach.ai.base import AIBackend, ClassificationResult
from aw_coach.ai.cost import CostController
from aw_coach.ai.hybrid import HybridBackend
from aw_coach.analyzer import PatternAnalyzer
from aw_coach.collector import ActivitySlice
from aw_coach.config import AnalysisConfig, CostConfig
from aw_coach.report import ReportGenerator
from aw_coach.rules.engine import RuleEngine
from aw_coach.storage import Storage


def _make_slices():
    """Simulate a realistic 2-hour work session."""
    t = datetime(2026, 5, 30, 9, 0)
    slices = []
    # 45min programming in Cursor
    slices.append(ActivitySlice(
        start=t, end=t + timedelta(minutes=45), duration=2700,
        is_afk=False, primary_app="Cursor", primary_title="main.rs - project",
    ))
    # 10min unknown app (will need LLM)
    t1 = t + timedelta(minutes=45)
    slices.append(ActivitySlice(
        start=t1, end=t1 + timedelta(minutes=10), duration=600,
        is_afk=False, primary_app="mystery-tool", primary_title="Dashboard",
    ))
    # 30min in browser reading docs (GitHub - rule will catch)
    t2 = t1 + timedelta(minutes=10)
    slices.append(ActivitySlice(
        start=t2, end=t2 + timedelta(minutes=30), duration=1800,
        is_afk=False, primary_app="firefox",
        primary_title="Pull Request - GitHub", web_url="https://github.com/org/pr",
    ))
    # 5min AFK
    t3 = t2 + timedelta(minutes=30)
    slices.append(ActivitySlice(
        start=t3, end=t3 + timedelta(minutes=5), duration=300,
        is_afk=True, primary_app="", primary_title="",
    ))
    # 30min more Cursor
    t4 = t3 + timedelta(minutes=5)
    slices.append(ActivitySlice(
        start=t4, end=t4 + timedelta(minutes=30), duration=1800,
        is_afk=False, primary_app="Cursor", primary_title="test.rs - project",
    ))
    return slices


class TestEndToEndHybrid:
    def test_full_pipeline(self, tmp_path):
        """
        End-to-end: slices → hybrid classify → analyze → report.
        LLM is called only for the 'mystery-tool' slice.
        """
        storage = Storage(tmp_path / "e2e.db")
        rule_engine = RuleEngine.with_builtin_rules()
        mock_llm = MagicMock(spec=AIBackend)
        mock_llm.estimate_cost.return_value = 0.01
        mock_llm.batch_classify.return_value = [
            ClassificationResult("admin", 0.75, "llm_batch"),
            ClassificationResult("programming", 0.85, "llm_batch"),
        ]
        cost = CostController(CostConfig(monthly_budget_usd=5.0), storage)
        hybrid = HybridBackend(rule_engine, mock_llm, cost, threshold=0.85)

        slices = _make_slices()

        # 1. Classify
        results = hybrid.batch_classify(slices)
        assert len(results) == 5
        assert results[0].activity_type == "programming"   # Cursor → rule (0.90)
        assert results[1].activity_type == "admin"          # mystery → LLM
        assert results[2].activity_type == "programming"    # GitHub → LLM (rule=0.80 < threshold)
        assert results[4].activity_type == "programming"    # Cursor → rule

        # LLM called with 2 uncertain slices (mystery-tool + firefox/github)
        mock_llm.batch_classify.assert_called_once()
        call_args = mock_llm.batch_classify.call_args[0][0]
        assert len(call_args) == 2

        # 2. Analyze
        analyzer = PatternAnalyzer(AnalysisConfig())
        analysis = analyzer.analyze(slices, results)
        assert analysis.effective_hours > 1.5
        assert analysis.deep_work_hours > 0  # 45min Cursor streak
        assert analysis.focus_score > 50

        # 3. Report
        from datetime import date
        reporter = ReportGenerator()
        report = reporter.generate_daily(date(2026, 5, 30), analysis)
        assert "programming" in report
        assert "2026-05-30" in report

    def test_budget_exhausted_graceful(self, tmp_path):
        """When budget is exhausted, pipeline still works with rule-only."""
        storage = Storage(tmp_path / "e2e_budget.db")
        storage.record_cost("gpt-4o-mini", 10000, 5000, 5.0, "previous")

        rule_engine = RuleEngine.with_builtin_rules()
        mock_llm = MagicMock(spec=AIBackend)
        mock_llm.estimate_cost.return_value = 0.01
        cost = CostController(CostConfig(monthly_budget_usd=5.0), storage)
        hybrid = HybridBackend(rule_engine, mock_llm, cost, threshold=0.85)

        slices = _make_slices()
        results = hybrid.batch_classify(slices)

        # mystery-tool stays unknown since LLM not called
        assert results[1].activity_type == "unknown"
        mock_llm.batch_classify.assert_not_called()

        # Analysis still produces valid output
        analyzer = PatternAnalyzer(AnalysisConfig())
        analysis = analyzer.analyze(slices, results)
        assert analysis.total_hours > 0
