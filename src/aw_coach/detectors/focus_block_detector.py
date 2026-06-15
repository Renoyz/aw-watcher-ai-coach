"""Detector: protect deep-focus blocks from being misclassified as stuck."""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

from aw_coach.detectors.base import AgentSignal, Detector

if TYPE_CHECKING:
    from aw_coach.enriched_state import SemanticWorkState


class FocusBlockDetector(Detector):
    """Recognize and protect long, uninterrupted deep-work blocks."""

    DEEP_MODES = {"coding", "writing", "researching", "debugging", "testing"}
    MIN_BLOCK_MIN = 20
    MAX_SWITCHES = 1

    def detect(
        self,
        state: "SemanticWorkState",
        history: List["SemanticWorkState"],
    ) -> Optional[AgentSignal]:
        if state.likely_mode not in self.DEEP_MODES:
            return None
        if state.active_block_minutes < self.MIN_BLOCK_MIN:
            return None
        if state.switches_last_5min > self.MAX_SWITCHES:
            return None
        if state.risk_level in {"stuck", "distracted", "fragmented"}:
            # If another detector already flagged risk, do not override
            return None
        return AgentSignal(
            signal_type="focused",
            severity=0.2,
            confidence=1.0,
            evidence=f"Deep focus block: {state.likely_mode} for {state.active_block_minutes} min",
            suggested_action="log_only",
        )
