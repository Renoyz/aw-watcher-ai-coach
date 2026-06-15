"""Base class for risk/behaviour detectors."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from aw_coach.enriched_state import SemanticWorkState


@dataclass
class AgentSignal:
    """Structured output from a detector."""

    signal_type: str       # e.g. "focused", "stuck", "ai_loop", "search_loop"
    severity: float = 0.5  # 0.0-1.0
    confidence: float = 1.0  # 0.0-1.0
    evidence: str = ""     # human-readable description
    suggested_action: str = "log_only"  # "log_only" | "inbox" | "notify_now"


class Detector(ABC):
    """Detect a specific risk pattern from a work state and its history."""

    @abstractmethod
    def detect(
        self,
        state: "SemanticWorkState",
        history: List["SemanticWorkState"],
    ) -> Optional[AgentSignal]:
        """Return an AgentSignal or None if this detector does not fire."""
