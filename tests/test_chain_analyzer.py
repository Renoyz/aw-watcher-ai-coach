"""Tests for chain_analyzer module."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aw_coach.chain_analyzer import ChainAnalysisResult, ChainAnalyzer, llm_analyze_chain
from aw_coach.enriched_state import SemanticWorkState


def _make_records(modes: list[str]) -> list[SemanticWorkState]:
    """Create a list of SemanticWorkState with given modes."""
    now = datetime.now(timezone.utc).astimezone()
    records = []
    for i, mode in enumerate(modes):
        # Spread timestamps 1 minute apart
        ts = now - timedelta(minutes=len(modes) - i)
        records.append(
            SemanticWorkState(
                updated_at=ts,
                current_app="Code",
                current_title="test",
                likely_mode=mode,
                risk_level="normal",
                active_block_minutes=5,
            )
        )
    return records


class TestChainAnalyzer:
    def test_insufficient_data(self):
        analyzer = ChainAnalyzer()
        result = analyzer.analyze([])
        assert result.pattern == "insufficient_data"
        assert result.depth_score == 0.0

    def test_deep_coding(self):
        records = _make_records(["coding", "coding", "coding", "coding", "coding"])
        analyzer = ChainAnalyzer()
        result = analyzer.analyze(records)
        assert result.pattern == "deep_coding"
        assert result.depth_score >= 0.5
        assert result.fragmentation_score < 0.3
        assert "深度编码" in (result.insight or "")

    def test_debug_cycle(self):
        records = _make_records([
            "coding", "researching", "coding", "terminal", "coding", "browsing", "coding"
        ])
        analyzer = ChainAnalyzer()
        result = analyzer.analyze(records)
        assert result.pattern == "debug_cycle"
        assert "Debug 循环" in (result.insight or "")

    def test_research_loop(self):
        records = _make_records([
            "coding", "researching", "coding", "browsing", "coding", "researching"
        ])
        analyzer = ChainAnalyzer()
        result = analyzer.analyze(records)
        assert result.pattern == "research_loop"
        assert "研究模式" in (result.insight or "")

    def test_context_switching(self):
        records = _make_records([
            "coding", "browsing", "chatting", "meeting", "coding", "terminal"
        ])
        analyzer = ChainAnalyzer()
        result = analyzer.analyze(records)
        assert result.pattern == "context_switching"
        assert result.fragmentation_score >= 0.4
        assert "切换" in (result.insight or "")

    def test_meeting_block(self):
        records = _make_records(["meeting", "meeting", "meeting", "meeting"])
        analyzer = ChainAnalyzer()
        result = analyzer.analyze(records)
        assert result.pattern == "meeting_block"

    def test_idle(self):
        records = _make_records(["unknown", "unknown", "idle", "unknown"])
        analyzer = ChainAnalyzer()
        result = analyzer.analyze(records)
        assert result.pattern == "idle"

    def test_depth_score_with_long_block(self):
        # 10 consecutive coding = high depth
        records = _make_records(["coding"] * 12)
        analyzer = ChainAnalyzer()
        result = analyzer.analyze(records)
        assert result.depth_score >= 0.7

    def test_fragmentation_high_with_switches(self):
        records = _make_records(["coding", "browsing", "coding", "chatting", "coding"])
        # Add fragmented risk
        for r in records[1::2]:
            object.__setattr__(r, "risk_level", "fragmented")
        analyzer = ChainAnalyzer()
        result = analyzer.analyze(records)
        assert result.fragmentation_score >= 0.4

    def test_window_filtering(self):
        now = datetime.now(timezone.utc).astimezone()
        old = now - timedelta(hours=2)
        records = [
            SemanticWorkState(
                updated_at=old,
                current_app="x",
                current_title="y",
                likely_mode="coding",
            ),
            SemanticWorkState(
                updated_at=now,
                current_app="x",
                current_title="y",
                likely_mode="coding",
            ),
            SemanticWorkState(
                updated_at=now,
                current_app="x",
                current_title="y",
                likely_mode="coding",
            ),
            SemanticWorkState(
                updated_at=now,
                current_app="x",
                current_title="y",
                likely_mode="coding",
            ),
        ]
        analyzer = ChainAnalyzer()
        result = analyzer.analyze(records, window_min=30)
        # Should still work because it falls back to last 3 records
        assert result.pattern == "deep_coding"


class TestLLMPlaceholder:
    def test_llm_fallback(self):
        records = _make_records(["coding", "coding", "coding"])
        result = llm_analyze_chain(records)
        assert isinstance(result, ChainAnalysisResult)
        assert result.confidence == 0.0
        assert "[LLM not enabled]" in (result.insight or "")


class TestChainAnalysisResult:
    def test_dataclass(self):
        result = ChainAnalysisResult(
            pattern="deep_coding",
            depth_score=0.8,
            fragmentation_score=0.1,
            insight="test",
            confidence=1.0,
        )
        assert result.pattern == "deep_coding"
        assert result.confidence == 1.0
