"""Risk/behaviour detectors for aw-coach."""

from __future__ import annotations

from aw_coach.detectors.ai_coding_loop_detector import AICodingLoopDetector
from aw_coach.detectors.base import AgentSignal, Detector
from aw_coach.detectors.focus_block_detector import FocusBlockDetector
from aw_coach.detectors.search_loop_detector import SearchLoopDetector
from aw_coach.detectors.stuck_debug_detector import StuckDebugDetector
from aw_coach.detectors.unknown_detector import UnknownDetector

__all__ = [
    "AgentSignal",
    "Detector",
    "UnknownDetector",
    "StuckDebugDetector",
    "SearchLoopDetector",
    "AICodingLoopDetector",
    "FocusBlockDetector",
    "CompositeDetector",
]


class CompositeDetector:
    """Run multiple detectors in priority order and return the first hit."""

    def __init__(self) -> None:
        self._detectors: list[Detector] = [
            FocusBlockDetector(),      # Protect focus first
            UnknownDetector(),         # Unknown mode stuck
            StuckDebugDetector(),      # Debug/test loops
            SearchLoopDetector(),      # Research loops
            AICodingLoopDetector(),    # AI assistant loops
        ]

    def detect(
        self,
        state,
        history,
    ):
        for det in self._detectors:
            result = det.detect(state, history)
            if result is not None:
                return result
        return None
