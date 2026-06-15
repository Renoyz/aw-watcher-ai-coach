"""Detector: unknown / low-confidence mode persisting for too long."""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

from aw_coach.detectors.base import AgentSignal, Detector

if TYPE_CHECKING:
    from aw_coach.enriched_state import SemanticWorkState


class UnknownDetector(Detector):
    """Fire when the user has been in 'unknown' mode for a suspicious length."""

    THRESHOLD_MIN = 20  # minutes

    def detect(
        self,
        state: "SemanticWorkState",
        history: List["SemanticWorkState"],
    ) -> Optional[AgentSignal]:
        if state.likely_mode != "unknown":
            return None
        if state.active_block_minutes >= self.THRESHOLD_MIN:
            return AgentSignal(
                signal_type="stuck",
                severity=0.5,
                confidence=0.8,
                evidence=f"Unknown mode persisted for {state.active_block_minutes} min",
                suggested_action="inbox",
            )
        return None
