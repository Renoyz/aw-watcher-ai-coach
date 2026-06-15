"""Detector: searching / reading docs without producing code."""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

from aw_coach.detectors.base import AgentSignal, Detector

if TYPE_CHECKING:
    from aw_coach.enriched_state import SemanticWorkState


class SearchLoopDetector(Detector):
    """Fire when the user is looping between search/docs and IDE without output."""

    RESEARCH_MODES = {"researching", "browsing"}
    CODING_MODES = {"coding", "writing", "editing"}
    MIN_OSCILLATIONS = 2  # research -> code -> research counts as 1
    MIN_HISTORY = 4

    def detect(
        self,
        state: "SemanticWorkState",
        history: List["SemanticWorkState"],
    ) -> Optional[AgentSignal]:
        if len(history) < self.MIN_HISTORY:
            return None

        recent = history[-self.MIN_HISTORY :]
        modes = [r.likely_mode for r in recent]

        # Count research -> code transitions
        oscillations = 0
        for i in range(len(modes) - 1):
            a, b = modes[i], modes[i + 1]
            if a in self.RESEARCH_MODES and b in self.CODING_MODES:
                oscillations += 1
            elif a in self.CODING_MODES and b in self.RESEARCH_MODES:
                oscillations += 1

        if oscillations >= self.MIN_OSCILLATIONS:
            # If the current mode is still research after many cycles,
            # they may be stuck reading without coding.
            if state.likely_mode in self.RESEARCH_MODES:
                return AgentSignal(
                    signal_type="search_loop",
                    severity=0.6,
                    confidence=0.8,
                    evidence=(
                        f"Research↔code oscillation: {oscillations} transitions "
                        "in recent history"
                    ),
                    suggested_action="inbox",
                )

        return None
