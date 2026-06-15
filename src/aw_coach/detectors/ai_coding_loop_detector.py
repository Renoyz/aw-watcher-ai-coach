"""Detector: AI coding assistant loop (ChatGPT/Claude/Cursor-chat ↔ IDE).

Phase-1 fix: reduce false positives by:
1. Removing "cursor" from hard-coded AI_APPS (Cursor is primarily an IDE).
2. Adding semantic_site and title-keyword checks for browser-based AI tools.
3. Requiring cumulative AI time >= MIN_AI_MINUTES (prevents brief doc lookups).
4. Requiring AI sessions to have likely_mode in AI_MODES (not just "browsing").
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

from aw_coach.detectors.base import AgentSignal, Detector

if TYPE_CHECKING:
    from aw_coach.enriched_state import SemanticWorkState


class AICodingLoopDetector(Detector):
    """Fire when the user is bouncing between an AI assistant and their IDE."""

    # Desktop/native AI apps only.  Cursor is an IDE; we detect Cursor's AI
    # usage via title keywords (e.g. "Generating", "✳") instead.
    AI_APPS = {
        "claude", "claude code", "chatgpt", "copilot chat",
        "github copilot", "openai codex", "aider", "codex",
    }

    # Browser sites that indicate an AI session
    AI_SITES = {
        "chatgpt", "claude", "copilot", "openai", "codex",
    }

    # Title keywords that indicate active AI assistance inside an IDE
    AI_TITLE_KEYWORDS = {
        "generating", "✳", "claude code", "copilot", "ai-assisted",
    }

    # Modes that count as "AI assistance" (not generic browsing)
    AI_MODES = {"chatting", "ai_coding"}

    CODING_MODES = {"coding", "debugging", "testing", "terminal"}
    MIN_HISTORY = 4
    MIN_AI_MINUTES = 10  # cumulative AI time in recent history

    def detect(
        self,
        state: "SemanticWorkState",
        history: List["SemanticWorkState"],
    ) -> Optional[AgentSignal]:
        if len(history) < self.MIN_HISTORY:
            return None

        recent = history[-self.MIN_HISTORY :]

        # Count AI sessions using multi-signal check
        ai_sessions = [r for r in recent if self._is_ai_session(r)]
        ai_count = len(ai_sessions)
        code_count = sum(1 for r in recent if r.likely_mode in self.CODING_MODES)

        # Need presence of both AI and coding in recent history
        if ai_count < 1 or code_count < 2:
            return None

        # Cumulative AI time must be meaningful (exclude brief glimpses)
        ai_minutes = sum(
            getattr(r, "active_block_minutes", 0) for r in ai_sessions
        )
        if ai_minutes < self.MIN_AI_MINUTES:
            return None

        # Check for alternation between AI and non-AI contexts
        transitions = 0
        for i in range(len(recent) - 1):
            a_ai = self._is_ai_session(recent[i])
            b_ai = self._is_ai_session(recent[i + 1])
            if a_ai != b_ai:
                transitions += 1
        if transitions >= 2:
            return AgentSignal(
                signal_type="ai_loop",
                severity=0.6,
                confidence=0.75,
                evidence=(
                    f"AI↔code alternation: {transitions} transitions, "
                    f"{ai_minutes} min AI time"
                ),
                suggested_action="inbox",
            )

        return None

    def _is_ai_session(self, state: "SemanticWorkState") -> bool:
        """Multi-signal check: app name, semantic site, or title keywords."""
        # Signal 1: native/desktop AI app
        app_lower = state.current_app.lower()
        if any(name in app_lower for name in self.AI_APPS):
            return True

        # Signal 2: browser on known AI site with AI-specific mode
        site = (state.semantic_site or "").lower()
        if site and any(s in site for s in self.AI_SITES):
            if state.likely_mode in self.AI_MODES:
                return True

        # Signal 3: title contains AI activity keywords (e.g. Cursor generating)
        title_lower = state.current_title.lower()
        if any(kw in title_lower for kw in self.AI_TITLE_KEYWORDS):
            return True

        return False
