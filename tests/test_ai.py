"""Tests for AI backend layer - CostController, OpenAI, Hybrid."""

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from aw_coach.ai.base import AIBackend, ClassificationResult
from aw_coach.ai.cost import CostController
from aw_coach.ai.hybrid import HybridBackend
from aw_coach.ai.openai_backend import OpenAIBackend
from aw_coach.collector import ActivitySlice
from aw_coach.config import CostConfig
from aw_coach.rules.engine import RuleEngine
from aw_coach.storage import Storage


@pytest.fixture
def storage(tmp_path):
    return Storage(tmp_path / "test.db")


@pytest.fixture
def cost_controller(storage):
    config = CostConfig(monthly_budget_usd=5.0, alert_thresholds=[0.5, 0.8, 1.0])
    return CostController(config, storage)


def _slice(app="chrome", title="page", url=None):
    t = datetime(2026, 5, 30, 9, 0)
    return ActivitySlice(
        start=t, end=t + timedelta(minutes=15),
        duration=900, is_afk=False,
        primary_app=app, primary_title=title, web_url=url,
    )


class TestCostController:
    def test_can_use_llm_within_budget(self, cost_controller):
        assert cost_controller.can_use_llm(0.05) is True

    def test_cannot_use_llm_over_budget(self, cost_controller, storage):
        storage.record_cost("gpt-4o-mini", 1000, 500, 4.98, "batch")
        assert cost_controller.can_use_llm(0.05) is False

    def test_track_call_records_cost(self, cost_controller, storage):
        cost_controller.track_call("gpt-4o-mini", 500, 100, "batch_classify")
        total = storage.get_monthly_cost()
        assert total > 0

    def test_this_month_total(self, cost_controller, storage):
        storage.record_cost("gpt-4o-mini", 500, 100, 0.05, "op1")
        storage.record_cost("gpt-4o-mini", 300, 80, 0.03, "op2")
        assert cost_controller.this_month_total() == pytest.approx(0.08)


class TestOpenAIBackend:
    def test_parse_valid_response(self):
        backend = OpenAIBackend.__new__(OpenAIBackend)
        backend.model = "gpt-4o-mini"
        raw = json.dumps({
            "classifications": [
                {"activity_type": "programming", "confidence": 0.85},
                {"activity_type": "entertainment", "confidence": 0.90},
            ]
        })
        results = backend._parse_response(raw, 2)
        assert len(results) == 2
        assert results[0].activity_type == "programming"
        assert results[1].activity_type == "entertainment"

    def test_parse_invalid_json_fallback(self):
        backend = OpenAIBackend.__new__(OpenAIBackend)
        backend.model = "gpt-4o-mini"
        results = backend._parse_response("not json at all", 3)
        assert len(results) == 3
        assert all(r.activity_type == "unknown" for r in results)

    def test_parse_missing_items_filled(self):
        backend = OpenAIBackend.__new__(OpenAIBackend)
        backend.model = "gpt-4o-mini"
        raw = json.dumps({"classifications": [
            {"activity_type": "research", "confidence": 0.7},
        ]})
        results = backend._parse_response(raw, 3)
        assert len(results) == 3
        assert results[0].activity_type == "research"
        assert results[1].activity_type == "unknown"
        assert results[2].activity_type == "unknown"

    def test_estimate_cost(self):
        backend = OpenAIBackend.__new__(OpenAIBackend)
        backend.model = "gpt-4o-mini"
        cost = backend.estimate_cost("batch_classify", 8)
        assert cost > 0
        assert cost < 0.1  # Should be very cheap for gpt-4o-mini


class TestHybridBackend:
    def test_all_confident_skips_llm(self, storage):
        """When all slices are confidently classified by rules, LLM is not called."""
        rule_engine = RuleEngine.with_builtin_rules()
        mock_llm = MagicMock(spec=AIBackend)
        config = CostConfig(monthly_budget_usd=5.0)
        cost = CostController(config, storage)

        hybrid = HybridBackend(rule_engine, mock_llm, cost, threshold=0.85)

        slices = [_slice("Code", "main.py"), _slice("Code", "test.py")]
        results = hybrid.batch_classify(slices)

        assert len(results) == 2
        assert all(r.activity_type == "programming" for r in results)
        mock_llm.batch_classify.assert_not_called()

    def test_uncertain_triggers_llm(self, storage):
        """Slices below threshold trigger LLM batch call."""
        rule_engine = RuleEngine.with_builtin_rules()
        mock_llm = MagicMock(spec=AIBackend)
        mock_llm.batch_classify.return_value = [
            ClassificationResult("entertainment", 0.85, "llm_batch"),
        ]
        mock_llm.estimate_cost.return_value = 0.02
        config = CostConfig(monthly_budget_usd=5.0)
        cost = CostController(config, storage)

        hybrid = HybridBackend(rule_engine, mock_llm, cost, threshold=0.85)

        # "random-app" won't match any rule
        slices = [_slice("Code", "main.py"), _slice("random-unknown-app", "window")]
        results = hybrid.batch_classify(slices)

        assert len(results) == 2
        assert results[0].activity_type == "programming"  # Rule hit
        assert results[1].activity_type == "entertainment"  # LLM result
        mock_llm.batch_classify.assert_called_once()

    def test_budget_exceeded_returns_unknown(self, storage):
        """When budget is exceeded, uncertain slices stay unknown."""
        rule_engine = RuleEngine.with_builtin_rules()
        mock_llm = MagicMock(spec=AIBackend)
        mock_llm.estimate_cost.return_value = 0.10

        # Exhaust budget
        storage.record_cost("gpt-4o-mini", 1000, 500, 5.0, "previous")
        config = CostConfig(monthly_budget_usd=5.0)
        cost = CostController(config, storage)

        hybrid = HybridBackend(rule_engine, mock_llm, cost, threshold=0.85)

        slices = [_slice("random-unknown-app", "window")]
        results = hybrid.batch_classify(slices)

        assert results[0].activity_type == "unknown"
        mock_llm.batch_classify.assert_not_called()
