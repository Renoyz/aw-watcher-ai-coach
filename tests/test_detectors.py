"""Tests for risk/behaviour detectors."""

from __future__ import annotations

from datetime import datetime, timezone

from aw_coach.detectors import (
    AICodingLoopDetector,
    CompositeDetector,
    FocusBlockDetector,
    SearchLoopDetector,
    StuckDebugDetector,
    UnknownDetector,
)
from aw_coach.enriched_state import SemanticWorkState


def _make_state(
    mode="unknown",
    risk="normal",
    block_min=0,
    app="Code",
    title="test",
    switches=0,
    ocr=None,
    semantic_site=None,
) -> SemanticWorkState:
    return SemanticWorkState(
        updated_at=datetime.now(timezone.utc).astimezone(),
        current_app=app,
        current_title=title,
        likely_mode=mode,
        risk_level=risk,
        active_block_minutes=block_min,
        switches_last_5min=switches,
        screen_ocr_text=ocr,
        semantic_site=semantic_site,
    )


class TestUnknownDetector:
    def test_unknown_short_no_fire(self):
        det = UnknownDetector()
        state = _make_state(mode="unknown", block_min=10)
        assert det.detect(state, []) is None

    def test_unknown_long_fires(self):
        det = UnknownDetector()
        state = _make_state(mode="unknown", block_min=25)
        result = det.detect(state, [])
        assert result is not None
        assert result.signal_type == "stuck"

    def test_coding_no_fire(self):
        det = UnknownDetector()
        state = _make_state(mode="coding", block_min=30)
        assert det.detect(state, []) is None


class TestStuckDebugDetector:
    def test_debugging_long_fires(self):
        det = StuckDebugDetector()
        state = _make_state(mode="debugging", block_min=35)
        history = [
            _make_state(mode="coding", block_min=5),
            _make_state(mode="debugging", block_min=10),
            _make_state(mode="debugging", block_min=20),
        ]
        result = det.detect(state, history)
        assert result is not None
        assert result.signal_type == "stuck"

    def test_no_history_no_fire(self):
        det = StuckDebugDetector()
        state = _make_state(mode="debugging", block_min=35)
        assert det.detect(state, []) is None

    def test_coding_short_no_fire(self):
        det = StuckDebugDetector()
        state = _make_state(mode="coding", block_min=10)
        history = [
            _make_state(mode="coding", block_min=5),
            _make_state(mode="coding", block_min=5),
        ]
        assert det.detect(state, history) is None


class TestSearchLoopDetector:
    def test_research_loop_fires(self):
        det = SearchLoopDetector()
        state = _make_state(mode="researching")
        history = [
            _make_state(mode="coding"),
            _make_state(mode="researching"),
            _make_state(mode="coding"),
            _make_state(mode="researching"),
        ]
        result = det.detect(state, history)
        assert result is not None
        assert result.signal_type == "search_loop"

    def test_no_loop_no_fire(self):
        det = SearchLoopDetector()
        state = _make_state(mode="coding")
        history = [
            _make_state(mode="coding"),
            _make_state(mode="coding"),
            _make_state(mode="coding"),
        ]
        assert det.detect(state, history) is None


class TestAICodingLoopDetector:
    def test_ai_loop_fires(self):
        det = AICodingLoopDetector()
        state = _make_state(mode="coding", app="Code")
        history = [
            _make_state(mode="coding", app="Code", block_min=15),
            _make_state(mode="chatting", app="Claude", block_min=15),
            _make_state(mode="coding", app="Code", block_min=15),
            _make_state(mode="chatting", app="Claude", block_min=15),
        ]
        result = det.detect(state, history)
        assert result is not None
        assert result.signal_type == "ai_loop"

    def test_no_ai_app_no_fire(self):
        det = AICodingLoopDetector()
        state = _make_state(mode="coding", app="Code")
        history = [
            _make_state(mode="coding", app="Code"),
            _make_state(mode="browsing", app="Chrome"),
            _make_state(mode="coding", app="Code"),
        ]
        assert det.detect(state, history) is None

    def test_cursor_normal_coding_no_fire(self):
        """Cursor IDE switching to browser docs should NOT be ai_loop."""
        det = AICodingLoopDetector()
        state = _make_state(mode="coding", app="Cursor")
        history = [
            _make_state(mode="coding", app="Cursor", block_min=30),
            _make_state(mode="browsing", app="firefox", title="Stack Overflow", block_min=10),
            _make_state(mode="coding", app="Cursor", block_min=30),
            _make_state(mode="browsing", app="firefox", title="docs.rs", block_min=10),
        ]
        assert det.detect(state, history) is None

    def test_browser_chatgpt_with_mode_fires(self):
        """Browser ChatGPT with chatting mode + sufficient time should fire."""
        det = AICodingLoopDetector()
        state = _make_state(mode="coding", app="Code")
        history = [
            _make_state(mode="coding", app="Code", block_min=15),
            _make_state(mode="chatting", app="firefox", title="ChatGPT", semantic_site="chatgpt", block_min=15),
            _make_state(mode="coding", app="Code", block_min=15),
            _make_state(mode="chatting", app="firefox", title="ChatGPT", semantic_site="chatgpt", block_min=15),
        ]
        result = det.detect(state, history)
        assert result is not None
        assert result.signal_type == "ai_loop"

    def test_browser_chatgpt_browsing_mode_no_fire(self):
        """Browser ChatGPT but mode=browsing (not chatting/ai_coding) should NOT fire."""
        det = AICodingLoopDetector()
        state = _make_state(mode="coding", app="Code")
        history = [
            _make_state(mode="coding", app="Code", block_min=15),
            _make_state(mode="browsing", app="firefox", title="ChatGPT", semantic_site="chatgpt", block_min=15),
            _make_state(mode="coding", app="Code", block_min=15),
            _make_state(mode="browsing", app="firefox", title="ChatGPT", semantic_site="chatgpt", block_min=15),
        ]
        assert det.detect(state, history) is None

    def test_brief_ai_glimpse_no_fire(self):
        """AI usage < MIN_AI_MINUTES should not trigger."""
        det = AICodingLoopDetector()
        state = _make_state(mode="coding", app="Code")
        history = [
            _make_state(mode="coding", app="Code", block_min=20),
            _make_state(mode="chatting", app="Claude", block_min=3),
            _make_state(mode="coding", app="Code", block_min=20),
            _make_state(mode="chatting", app="Claude", block_min=3),
        ]
        assert det.detect(state, history) is None

    def test_cursor_generating_title_fires(self):
        """Cursor with 'Generating' in title indicates active AI assistance."""
        det = AICodingLoopDetector()
        state = _make_state(mode="coding", app="Code")
        history = [
            _make_state(mode="coding", app="Code", block_min=15),
            _make_state(mode="coding", app="Cursor", title="✳ Generating - main.rs", block_min=15),
            _make_state(mode="coding", app="Code", block_min=15),
            _make_state(mode="coding", app="Cursor", title="✳ Generating - lib.rs", block_min=15),
        ]
        result = det.detect(state, history)
        assert result is not None
        assert result.signal_type == "ai_loop"


class TestFocusBlockDetector:
    def test_focused_long_coding(self):
        det = FocusBlockDetector()
        state = _make_state(mode="coding", block_min=25, switches=0, risk="normal")
        result = det.detect(state, [])
        assert result is not None
        assert result.signal_type == "focused"

    def test_no_focus_when_fragmented(self):
        det = FocusBlockDetector()
        state = _make_state(mode="coding", block_min=25, switches=3, risk="fragmented")
        assert det.detect(state, []) is None

    def test_no_focus_when_short(self):
        det = FocusBlockDetector()
        state = _make_state(mode="coding", block_min=10, switches=0, risk="normal")
        assert det.detect(state, []) is None

    def test_no_focus_when_browsing(self):
        det = FocusBlockDetector()
        state = _make_state(mode="browsing", block_min=30, switches=0, risk="normal")
        assert det.detect(state, []) is None


class TestCompositeDetector:
    def test_first_hit_wins(self):
        comp = CompositeDetector()
        # FocusBlockDetector fires first for long coding
        state = _make_state(mode="coding", block_min=25, switches=0, risk="normal")
        result = comp.detect(state, [])
        assert result is not None
        assert result.signal_type == "focused"

    def test_fallback_to_none(self):
        comp = CompositeDetector()
        state = _make_state(mode="browsing", block_min=5, switches=0, risk="normal")
        result = comp.detect(state, [])
        assert result is None
