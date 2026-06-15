"""Detector: debug / test / build cycle repeating without resolution."""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

from aw_coach.detectors.base import AgentSignal, Detector

if TYPE_CHECKING:
    from aw_coach.enriched_state import SemanticWorkState


class StuckDebugDetector(Detector):
    """Fire when the user is stuck in a debug/build/test loop."""

    # Modes that count as "problem-solving" steps
    WORK_MODES = {"coding", "debugging", "testing", "building", "terminal"}
    MIN_BLOCK_MIN = 25
    MIN_HISTORY = 3

    def detect(
        self,
        state: "SemanticWorkState",
        history: List["SemanticWorkState"],
    ) -> Optional[AgentSignal]:
        if len(history) < self.MIN_HISTORY:
            return None

        # Count how many of the recent records are in work modes
        recent = history[-self.MIN_HISTORY :]
        work_count = sum(1 for r in recent if r.likely_mode in self.WORK_MODES)
        if work_count < self.MIN_HISTORY:
            return None

        # If the current block is long and the mode keeps oscillating
        # between coding/debugging/testing, we are likely stuck.
        modes = [r.likely_mode for r in recent]
        unique_modes = len(set(modes))
        if unique_modes >= 2 and state.active_block_minutes >= self.MIN_BLOCK_MIN:
            return AgentSignal(
                signal_type="stuck",
                severity=0.7,
                confidence=0.85,
                evidence=(
                    f"Debug/build loop detected: {unique_modes} modes over "
                    f"{self.MIN_HISTORY} checks"
                ),
                suggested_action="notify_now",
            )

        # Also fire if current mode is debugging for a very long time
        if state.likely_mode == "debugging" and state.active_block_minutes >= 30:
            return AgentSignal(
                signal_type="stuck",
                severity=0.7,
                confidence=0.9,
                evidence=f"Debugging for {state.active_block_minutes} min without resolution",
                suggested_action="notify_now",
            )

        return None
