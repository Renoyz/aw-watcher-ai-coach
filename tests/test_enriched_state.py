"""Tests for enriched_state module."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from aw_coach.context_parser import WindowContext
from aw_coach.enriched_state import (
    EnrichedStateAssembler,
    SemanticWorkState,
    _assess_risk,
    _infer_likely_mode,
    assemble_from_slice,
)
from aw_coach.git_context import GitContext

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _git_init(path: Path, branch: str = "main") -> None:
    subprocess.run(["git", "init", str(path)], capture_output=True, check=True)
    head = path / ".git" / "HEAD"
    head.write_text(f"ref: refs/heads/{branch}\n")


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# _infer_likely_mode
# ---------------------------------------------------------------------------

class TestInferLikelyMode:
    def test_ide_coding_default(self):
        ctx = WindowContext(app="Code", raw_title="main.py")
        assert _infer_likely_mode("Code", ctx, "programming") == "coding"

    def test_ide_debug_from_action(self):
        ctx = WindowContext(app="Code", raw_title="debug test.py", action_hint="debug")
        assert _infer_likely_mode("Code", ctx, "programming") == "debugging"

    def test_terminal_test_action(self):
        ctx = WindowContext(app="Terminal", raw_title="pytest -v", action_hint="test")
        assert _infer_likely_mode("Terminal", ctx, "programming") == "testing"

    def test_browser_research_github(self):
        ctx = WindowContext(app="Chrome", raw_title="Issues", site="github.com")
        assert _infer_likely_mode("Chrome", ctx, "research") == "researching"

    def test_browser_collaborating_notion(self):
        ctx = WindowContext(app="Chrome", raw_title="Notes", site="notion.so")
        assert _infer_likely_mode("Chrome", ctx, "research") == "collaborating"

    def test_browser_chatting(self):
        ctx = WindowContext(app="Chrome", raw_title="Slack", site="slack.com")
        assert _infer_likely_mode("Chrome", ctx, "social") == "chatting"

    def test_meeting_from_action(self):
        ctx = WindowContext(app="Zoom", raw_title="Meeting", action_hint="meeting")
        assert _infer_likely_mode("Zoom", ctx, "meeting") == "meeting"

    def test_unknown_app(self):
        ctx = WindowContext(app="SomeApp", raw_title="Window")
        assert _infer_likely_mode("SomeApp", ctx, None) == "unknown"

    def test_rule_fallback_writing(self):
        ctx = WindowContext(app="UnknownApp", raw_title="Document")
        assert _infer_likely_mode("UnknownApp", ctx, "writing") == "writing"

    def test_ide_research_override(self):
        ctx = WindowContext(app="Code", raw_title="docs")
        assert _infer_likely_mode("Code", ctx, "research") == "researching"


# ---------------------------------------------------------------------------
# _assess_risk
# ---------------------------------------------------------------------------

class TestAssessRisk:
    def test_debugging_long_stuck(self):
        assert _assess_risk("debugging", 30, None) == "stuck"

    def test_debugging_short_normal(self):
        assert _assess_risk("debugging", 10, None) == "normal"

    def test_coding_long_clean_repo_stuck(self):
        git = GitContext(repo_name="x", branch="main", is_dirty=False)
        assert _assess_risk("coding", 30, git) == "stuck"

    def test_coding_long_dirty_repo_normal(self):
        git = GitContext(repo_name="x", branch="main", is_dirty=True)
        assert _assess_risk("coding", 30, git) == "normal"

    def test_coding_short_normal(self):
        assert _assess_risk("coding", 10, None) == "normal"

    def test_unknown_long_stuck(self):
        assert _assess_risk("unknown", 15, None) == "stuck"

    def test_unknown_short_normal(self):
        assert _assess_risk("unknown", 5, None) == "normal"

    def test_browsing_long_distracted(self):
        assert _assess_risk("browsing", 30, None) == "distracted"

    def test_chatting_long_distracted(self):
        assert _assess_risk("chatting", 30, None) == "distracted"

    def test_no_git_ctx_coding_long_normal(self):
        # No git context -> can't tell if dirty, assume normal
        assert _assess_risk("coding", 30, None) == "normal"


# ---------------------------------------------------------------------------
# EnrichedStateAssembler
# ---------------------------------------------------------------------------

class TestAssembler:
    def test_basic_assembly(self):
        assembler = EnrichedStateAssembler()
        state = assembler.assemble(
            app="Code",
            title="main.py - aw-coach",
            url=None,
            active_block_minutes=25,
            rule_activity="programming",
        )
        assert isinstance(state, SemanticWorkState)
        assert state.current_app == "Code"
        assert state.semantic_project == "aw-coach"
        assert state.semantic_filename == "main.py"
        assert state.semantic_language == "python"
        assert state.likely_mode == "coding"
        # risk_level keeps old heuristic semantics
        assert state.risk_level == "normal"
        # FocusBlockDetector writes to detected_signal instead
        assert state.detected_signal == "focused"

    def test_browser_with_url(self):
        assembler = EnrichedStateAssembler()
        state = assembler.assemble(
            app="Chrome",
            title="python - Google Search",
            url="https://www.google.com/search?q=python",
            active_block_minutes=5,
            rule_activity="research",
        )
        assert state.semantic_site == "google.com"
        assert state.likely_mode == "browsing"

    def test_debug_mode(self):
        assembler = EnrichedStateAssembler()
        state = assembler.assemble(
            app="Code",
            title="debug main.py",
            active_block_minutes=35,
            rule_activity="programming",
        )
        assert state.semantic_action == "debug"
        assert state.likely_mode == "debugging"
        assert state.risk_level == "stuck"

    def test_fragmented_override(self):
        assembler = EnrichedStateAssembler()
        state = assembler.assemble(
            app="Code",
            title="main.py",
            active_block_minutes=5,
            rule_activity="programming",
            switches_last_5min=5,
        )
        assert state.risk_level == "fragmented"

    def test_with_git_context(self, fake_home: Path):
        repo = fake_home / "projects" / "aw-coach"
        repo.mkdir(parents=True)
        _git_init(repo, branch="feature/parser")

        assembler = EnrichedStateAssembler()
        state = assembler.assemble(
            app="Code",
            title="main.py - aw-coach",
            active_block_minutes=10,
            rule_activity="programming",
        )
        assert state.git_repo == "aw-coach"
        assert state.git_branch == "feature/parser"
        assert state.git_is_dirty is False

    def test_to_dict_serializable(self):
        assembler = EnrichedStateAssembler()
        state = assembler.assemble(
            app="Code",
            title="test.py",
            active_block_minutes=5,
        )
        d = state.to_dict()
        assert isinstance(d["updated_at"], str)
        assert d["current_app"] == "Code"
        assert d["likely_mode"] == "coding"

    def test_to_display_dict(self):
        assembler = EnrichedStateAssembler()
        state = assembler.assemble(
            app="Code",
            title="main.py - aw-coach",
            active_block_minutes=25,
        )
        display = state.to_display_dict()
        assert display["项目"] == "aw-coach"
        assert display["文件"] == "main.py"
        assert display["语言"] == "python"
        assert "专注块" in display


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------

class TestAssembleFromSlice:
    def test_one_shot(self):
        state = assemble_from_slice(
            app="Terminal",
            title="pytest -v",
            active_block_minutes=10,
            rule_activity="programming",
        )
        assert state.likely_mode == "testing"
        assert state.semantic_action == "test"


# ---------------------------------------------------------------------------
# SemanticWorkState dataclass
# ---------------------------------------------------------------------------

class TestSemanticWorkState:
    def test_empty_state(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).astimezone()
        state = SemanticWorkState(updated_at=now, current_app="x", current_title="y")
        assert state.semantic_project is None
        assert state.likely_mode == "unknown"

    def test_display_dict_truncates_long_title(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).astimezone()
        long_title = "a" * 100
        state = SemanticWorkState(
            updated_at=now,
            current_app="x",
            current_title=long_title,
        )
        display = state.to_display_dict()
        assert display["窗口标题"].endswith("...")
        assert len(display["窗口标题"]) <= 55

    def test_visual_fields_serialize(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).astimezone()
        state = SemanticWorkState(
            updated_at=now,
            current_app="x",
            current_title="y",
            screen_ocr_text="pytest failed",
            screen_diff_ratio=0.05,
            screen_content_type="scrolling",
            terminal_command="pytest -v",
        )
        d = state.to_dict()
        assert d["screen_ocr_text"] == "pytest failed"
        assert d["screen_diff_ratio"] == 0.05
        assert d["screen_content_type"] == "scrolling"
        assert d["terminal_command"] == "pytest -v"

    def test_display_dict_includes_visual(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).astimezone()
        state = SemanticWorkState(
            updated_at=now,
            current_app="x",
            current_title="y",
            screen_ocr_text="error in main.py",
            screen_content_type="static",
            terminal_command="python main.py",
        )
        display = state.to_display_dict()
        assert "终端命令" in display
        assert "OCR预览" in display
        assert "屏幕类型" in display


# ---------------------------------------------------------------------------
# OCR Refinement
# ---------------------------------------------------------------------------

class TestOCRRefinement:
    def test_ocr_error_to_debugging(self):
        assembler = EnrichedStateAssembler()
        state = assembler.assemble(
            app="Code",
            title="main.py",
            active_block_minutes=10,
            screen_ocr_text="Traceback (most recent call last):\nValueError: invalid",
        )
        assert state.likely_mode == "debugging"

    def test_ocr_stackoverflow_to_researching(self):
        assembler = EnrichedStateAssembler()
        state = assembler.assemble(
            app="Chrome",
            title="Python list comprehension - Stack Overflow",
            url="https://stackoverflow.com/questions/123",
            active_block_minutes=10,
            screen_ocr_text="Stack Overflow\nPython list comprehension",
        )
        assert state.likely_mode == "researching"

    def test_ocr_static_debug_screen_to_stuck(self):
        assembler = EnrichedStateAssembler()
        state = assembler.assemble(
            app="Code",
            title="main.py",
            active_block_minutes=35,
            screen_ocr_text="Exception: connection refused\n  File main.py, line 42",
            screen_content_type="static",
        )
        assert state.likely_mode == "debugging"
        assert state.risk_level == "stuck"

    def test_ocr_no_change_when_confident(self):
        assembler = EnrichedStateAssembler()
        state = assembler.assemble(
            app="Zoom",
            title="Team Standup",
            active_block_minutes=15,
            rule_activity="meeting",
            screen_ocr_text="some random text error",
        )
        # Zoom meeting should not become debugging just because OCR saw "error"
        assert state.likely_mode == "meeting"

    def test_ocr_code_heuristic(self):
        assembler = EnrichedStateAssembler()
        state = assembler.assemble(
            app="UnknownApp",
            title="Window",
            active_block_minutes=5,
            screen_ocr_text="def foo():\n    import os\n    class Bar:\n        pass",
        )
        assert state.likely_mode == "coding"
