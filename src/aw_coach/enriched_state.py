"""Enriched state assembler: combines TitleParser + GitContext into semantic work state.

This module is the "glue layer" of Phase 1. It takes raw aw data
(app, title, url) and produces a `SemanticWorkState` with
semantic understanding of *what* the user is doing.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Dict, Optional

from aw_coach.context_parser import TitleParser, WindowContext
from aw_coach.detectors import AgentSignal, CompositeDetector
from aw_coach.git_context import GitContext, get_git_context_for_project

# --- App category lists for mode inference ---

_IDE_APPS = frozenset({
    "code", "code - insiders", "vscodium", "cursor",
    "idea", "idea64", "pycharm", "webstorm",
    "goland", "clion", "rustrover", "android studio",
    "eclipse", "xcode", "visual studio",
    "sublime_text", "subl", "atom",
    "vim", "nvim", "neovim", "gvim", "emacs", "emacs-gtk",
    "rider", "datagrip", "rubymine", "phpstorm", "appcode", "fleet",
    "hbuilderx", "hbuilder",
})

_BROWSER_APPS = frozenset({
    "chrome", "chromium", "google-chrome", "firefox", "edge",
    "microsoft edge", "safari", "brave", "opera", "vivaldi",
    "arc", "zen", "orion",
})

_TERMINAL_APPS = frozenset({
    "terminal", "iterm2", "gnome-terminal", "konsole",
    "alacritty", "wezterm", "warp", "tabby", "xterm",
    "kitty", "windows terminal", "terminator", "tilix",
    "hyper", "st",
})

# These must match the *site names* returned by TitleParser._detect_site()
# (e.g. "github", "stackoverflow"), NOT full domain strings.
_RESEARCH_SITES = frozenset({
    "github", "gitlab", "stackoverflow", "stackexchange",
    "docs", "readthedocs", "wikipedia", "arxiv", "csdn",
    "juejin", "segmentfault",
})

_COLLAB_SITES = frozenset({
    "github", "gitlab", "notion", "figma",
    "miro", "linear", "jira", "confluence",
    "trello", "asana",
})

_STATE_ACTION_LABELS = {
    "debugging": "debug",
    "testing": "test",
}


def _is_ide(app: str) -> bool:
    app_lower = app.lower()
    return any(name in app_lower for name in _IDE_APPS)


def _is_browser(app: str) -> bool:
    app_lower = app.lower()
    return any(name in app_lower for name in _BROWSER_APPS)


def _is_terminal(app: str) -> bool:
    app_lower = app.lower()
    return any(name in app_lower for name in _TERMINAL_APPS)


def _infer_likely_mode(
    app: str,
    semantic: WindowContext,
    rule_activity: Optional[str],
) -> str:
    """Infer user's likely mode from semantic context + app category.

    Priority:
        1. action_hint from title (most specific signal)
        2. site category (for browser)
        3. app category (IDE/terminal/browser)
        4. rule_engine fallback
    """
    action = semantic.action_hint
    site = (semantic.site or "").lower()
    app_lower = app.lower()

    # 1. Action hint overrides everything
    if action:
        if action in ("debug", "debugging"):
            return "debugging"
        if action in ("test", "testing"):
            return "testing"
        if action == "deploy":
            return "deploying"
        if action == "write":
            return "writing"
        if action == "meeting":
            return "meeting"

    # 2. IDE -> coding by default
    if _is_ide(app_lower):
        if rule_activity == "research":
            return "researching"  # reading docs in IDE
        return "coding"

    # 3. Terminal -> action or generic terminal
    if _is_terminal(app_lower):
        if action:
            return action
        # Try to infer from foreground process inside the terminal
        from aw_coach.process_context import (
            get_terminal_foreground_command,
            infer_action_from_command,
        )

        fg = get_terminal_foreground_command()
        if fg:
            cmd_action = infer_action_from_command(fg[0], fg[1])
            if cmd_action:
                return cmd_action
        return "terminal"

    # 4. Browser -> site-specific
    if _is_browser(app_lower):
        if any(s in site for s in _RESEARCH_SITES):
            return "researching"
        if any(s in site for s in _COLLAB_SITES):
            return "collaborating"
        if rule_activity == "ai_assisted":
            return "ai_coding"
        if rule_activity == "social":
            return "chatting"
        if rule_activity == "entertainment":
            return "browsing"
        return "browsing"

    # 5. Rule engine fallback
    if rule_activity == "meeting":
        return "meeting"
    if rule_activity == "writing":
        return "writing"
    if rule_activity == "ai_assisted":
        return "ai_coding"
    if rule_activity == "social":
        return "chatting"
    if rule_activity == "entertainment":
        return "browsing"

    return "unknown"


def _state_action_label(action: Optional[str]) -> Optional[str]:
    if action is None:
        return None
    return _STATE_ACTION_LABELS.get(action, action)


def _assess_risk(
    likely_mode: str,
    active_block_minutes: int,
    git_ctx: Optional[GitContext],
) -> str:
    """Simple risk assessment based on mode + duration + git state.

    Rules:
        - debugging > 30min  -> stuck
        - coding > 30min + clean repo (no uncommitted changes)
          -> stuck (likely stuck in thought/design)
        - unknown > 15min     -> stuck
        - browsing > 30min    -> distracted
        - otherwise           -> normal
    """
    if likely_mode == "debugging" and active_block_minutes >= 30:
        return "stuck"

    if likely_mode == "coding" and active_block_minutes >= 30:
        if git_ctx is not None and not git_ctx.is_dirty:
            return "stuck"

    if likely_mode == "unknown" and active_block_minutes >= 15:
        return "stuck"

    if likely_mode in ("browsing", "chatting") and active_block_minutes >= 30:
        return "distracted"

    return "normal"


# ---------------------------------------------------------------------------
# OCR-driven refinement
# ---------------------------------------------------------------------------

_OCR_DEBUG_KEYWORDS = {
    "error", "failed", "exception", "traceback", "stack trace",
    "assertionerror", "runtimeerror", "valueerror", "typeerror",
    "syntaxerror", "nameerror", "module not found",
}

_OCR_TEST_FAIL_KEYWORDS = {
    "pytest", "unittest", "jest", "mocha", "test failed",
    "failures=", "errors=", "failed",
}

_OCR_RESEARCH_KEYWORDS = {
    "stack overflow", "stackoverflow", "github issues",
    "pull request", "merge request", "documentation",
}

_OCR_CODE_HEURISTICS = {"def ", "class ", "import ", "function(", "{", "}"}


def _refine_mode_with_ocr(mode: str, ocr_text: Optional[str]) -> str:
    """Refine likely_mode using OCR text from screenshot."""
    if not ocr_text:
        return mode

    text_lower = ocr_text.lower()

    # Debug signals override unknown/coding/terminal
    if mode in ("unknown", "coding", "terminal", "browsing"):
        if any(kw in text_lower for kw in _OCR_DEBUG_KEYWORDS):
            return "debugging"

    # Test failure signals → debugging (user is likely fixing tests)
    if mode in ("unknown", "coding", "terminal", "testing"):
        if any(kw in text_lower for kw in _OCR_TEST_FAIL_KEYWORDS):
            # Only override if failure keywords also present
            if "failed" in text_lower or "error" in text_lower or "failures=" in text_lower:
                return "debugging"

    # Research signals override unknown/browsing
    if mode in ("unknown", "browsing"):
        if any(kw in text_lower for kw in _OCR_RESEARCH_KEYWORDS):
            return "researching"

    # Code-like content on screen → coding
    if mode == "unknown":
        code_score = sum(1 for h in _OCR_CODE_HEURISTICS if h in ocr_text)
        if code_score >= 3:
            return "coding"

    return mode


def _refine_risk_with_ocr(
    risk: str,
    mode: str,
    ocr_text: Optional[str],
    content_type: Optional[str],
) -> tuple[str, Optional[str]]:
    """Refine risk_level and detect OCR-driven signals.

    Returns (risk_level, detected_signal).  detected_signal may be None.
    """
    if not ocr_text:
        return risk, None

    text_lower = ocr_text.lower()

    # Debugging + static screen + error text visible → likely stuck
    if mode == "debugging" and content_type == "static":
        if any(kw in text_lower for kw in _OCR_DEBUG_KEYWORDS):
            return "stuck", None

    # Researching + static + same search page → search_loop signal
    if mode == "researching" and content_type == "static":
        if "search" in text_lower or "results" in text_lower:
            return risk, "search_loop"

    return risk, None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@dataclass
class SemanticWorkState:
    """Semantic-enriched user work state snapshot.

    This is a lightweight, zero-LLM-cost state object that combines:
    - Window title semantic parsing (TitleParser)
    - Git repository context (GitContext)
    - Simple heuristic mode inference
    """

    # === Meta ===
    updated_at: datetime

    # === Raw aw data ===
    current_app: str
    current_title: str
    current_url: Optional[str] = None

    # === Semantic parsing (TitleParser) ===
    semantic_project: Optional[str] = None
    semantic_filename: Optional[str] = None
    semantic_language: Optional[str] = None
    semantic_site: Optional[str] = None
    semantic_action: Optional[str] = None

    # === Git context ===
    git_repo: Optional[str] = None
    git_branch: Optional[str] = None
    git_is_dirty: bool = False

    # === Inferred state ===
    activity_type: str = "unknown"
    likely_mode: str = "unknown"
    risk_level: str = "unknown"
    detected_signal: Optional[str] = None  # e.g. focused, search_loop, ai_loop
    agent_signal: Optional[AgentSignal] = None  # structured signal from detectors
    active_block_minutes: int = 0

    # === Task perception (optional) ===
    task_id: Optional[str] = None
    task_label: Optional[str] = None
    task_intent: Optional[str] = None
    task_confidence: float = 0.0

    # === Visual signal (from screenshot/OCR) ===
    screen_ocr_text: Optional[str] = None
    screen_diff_ratio: Optional[float] = None
    screen_content_type: Optional[str] = None

    # === Process context ===
    terminal_command: Optional[str] = None

    # === Time stats (populated by caller) ===
    switches_last_5min: int = 0

    def to_dict(self) -> Dict:
        """Serialize to JSON-friendly dict."""
        from aw_coach.detectors import AgentSignal

        d = asdict(self)
        # datetime -> ISO string
        if isinstance(d.get("updated_at"), datetime):
            d["updated_at"] = d["updated_at"].isoformat()
        # AgentSignal -> dict
        sig = d.get("agent_signal")
        if isinstance(sig, AgentSignal):
            d["agent_signal"] = {
                "signal_type": sig.signal_type,
                "severity": sig.severity,
                "confidence": sig.confidence,
                "evidence": sig.evidence,
                "suggested_action": sig.suggested_action,
            }
        return d

    def to_display_dict(self) -> Dict[str, str]:
        """Return human-friendly key-value pairs for CLI rendering."""
        title = (
            self.current_title[:50] + "..."
            if len(self.current_title) > 50
            else self.current_title
        )
        display = {
            "应用": self.current_app,
            "窗口标题": title,
            "项目": self.semantic_project or "-",
            "文件": self.semantic_filename or "-",
            "语言": self.semantic_language or "-",
            "网站": self.semantic_site or "-",
            "动作信号": self.semantic_action or "-",
            "Git仓库": self.git_repo or "-",
            "Git分支": self.git_branch or "-",
            "未提交更改": "是" if self.git_is_dirty else "否",
            "活动类型": self.activity_type,
            "工作模式": self.likely_mode,
            "风险等级": self.risk_level,
            "专注块": f"{self.active_block_minutes} 分钟",
        }
        if self.task_label:
            display["当前任务"] = self.task_label
        if self.task_id:
            display["任务ID"] = self.task_id
        if self.terminal_command:
            display["终端命令"] = self.terminal_command
        if self.screen_ocr_text:
            ocr_preview = (
                self.screen_ocr_text[:60] + "..."
                if len(self.screen_ocr_text) > 60
                else self.screen_ocr_text
            )
            display["OCR预览"] = ocr_preview
        if self.screen_content_type:
            display["屏幕类型"] = self.screen_content_type
        if self.detected_signal:
            display["检测信号"] = self.detected_signal
        if self.agent_signal:
            display["信号严重度"] = f"{self.agent_signal.severity:.1f}"
            display["建议动作"] = self.agent_signal.suggested_action
        return display


class EnrichedStateAssembler:
    """Assemble SemanticWorkState from raw aw data.

    Usage:
        assembler = EnrichedStateAssembler()
        state = assembler.assemble(
            app="Code",
            title="main.py - aw-coach",
            url=None,
            active_block_minutes=25,
            rule_activity="programming",
        )
    """

    def __init__(self) -> None:
        self._parser = TitleParser()
        self._detector = CompositeDetector()

    def assemble(
        self,
        app: str,
        title: str,
        url: Optional[str] = None,
        active_block_minutes: int = 0,
        rule_activity: Optional[str] = None,
        switches_last_5min: int = 0,
        screen_ocr_text: Optional[str] = None,
        screen_diff_ratio: Optional[float] = None,
        screen_content_type: Optional[str] = None,
        terminal_command: Optional[str] = None,
        history: Optional[list] = None,
    ) -> SemanticWorkState:
        """Build enriched state from a single current activity point.

        Args:
            app: Application name (e.g. "Code", "Chrome")
            title: Window title
            url: Optional URL (for browser events)
            active_block_minutes: How long the user has been in this activity type
            rule_activity: RuleEngine's classification (e.g. "programming")
            switches_last_5min: Number of context switches in last 5 minutes
            screen_ocr_text: OCR text from last screenshot (if any)
            screen_diff_ratio: Frame-to-frame diff ratio (if any)
            screen_content_type: Screen content classification (if any)
            terminal_command: Foreground command in terminal (if any)
            history: Recent SemanticWorkState list for detector context
        """
        now = datetime.now(timezone.utc).astimezone()

        # 1. Parse window title
        semantic = self._parser.parse(app, title, url)

        # 2. Look up git context if project detected
        git_ctx: Optional[GitContext] = None
        if semantic.project:
            try:
                git_ctx = get_git_context_for_project(semantic.project)
            except Exception:
                git_ctx = None  # Graceful degradation

        # 3. Infer likely mode
        likely_mode = _infer_likely_mode(app, semantic, rule_activity)

        # 4. Assess risk (heuristic baseline)
        risk = _assess_risk(likely_mode, active_block_minutes, git_ctx)

        # 5. Override risk if too many switches
        if switches_last_5min >= 5:
            risk = "fragmented"

        # 6. Refine with visual signal (OCR / screen diff)
        likely_mode = _refine_mode_with_ocr(likely_mode, screen_ocr_text)
        risk, ocr_signal = _refine_risk_with_ocr(
            risk, likely_mode, screen_ocr_text, screen_content_type
        )

        # 7. Build tentative state for detector evaluation
        tentative = SemanticWorkState(
            updated_at=now,
            current_app=app,
            current_title=title,
            current_url=url,
            semantic_project=semantic.project,
            semantic_filename=semantic.filename,
            semantic_language=semantic.language,
            semantic_site=semantic.site,
            semantic_action=_state_action_label(semantic.action_hint),
            git_repo=git_ctx.repo_name if git_ctx else None,
            git_branch=git_ctx.branch if git_ctx else None,
            git_is_dirty=git_ctx.is_dirty if git_ctx else False,
            activity_type=rule_activity or "unknown",
            likely_mode=likely_mode,
            risk_level=risk,
            active_block_minutes=active_block_minutes,
            switches_last_5min=switches_last_5min,
            screen_ocr_text=screen_ocr_text,
            screen_diff_ratio=screen_diff_ratio,
            screen_content_type=screen_content_type,
            terminal_command=terminal_command,
        )

        # 8. Run detectors; write signal without clobbering risk_level semantics
        detector_result = self._detector.detect(tentative, history or [])
        if detector_result is not None:
            tentative.agent_signal = detector_result
            tentative.detected_signal = detector_result.signal_type
        elif ocr_signal is not None:
            tentative.detected_signal = ocr_signal

        return tentative


def assemble_from_slice(
    app: str,
    title: str,
    url: Optional[str] = None,
    active_block_minutes: int = 0,
    rule_activity: Optional[str] = None,
    switches_last_5min: int = 0,
    screen_ocr_text: Optional[str] = None,
    screen_diff_ratio: Optional[float] = None,
    screen_content_type: Optional[str] = None,
    terminal_command: Optional[str] = None,
    history: Optional[list] = None,
) -> SemanticWorkState:
    """Convenience function: one-shot assembly without keeping assembler state."""
    assembler = EnrichedStateAssembler()
    return assembler.assemble(
        app=app,
        title=title,
        url=url,
        active_block_minutes=active_block_minutes,
        rule_activity=rule_activity,
        switches_last_5min=switches_last_5min,
        screen_ocr_text=screen_ocr_text,
        screen_diff_ratio=screen_diff_ratio,
        screen_content_type=screen_content_type,
        terminal_command=terminal_command,
        history=history,
    )
