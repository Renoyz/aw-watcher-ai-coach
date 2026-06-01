"""AI Backend abstract base class and shared types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional

from aw_coach.collector import ActivitySlice


@dataclass
class ClassificationResult:
    activity_type: str
    confidence: float
    method: str
    weight: Optional[float] = None
    skip_analysis: bool = False


class AIBackend(ABC):
    @abstractmethod
    def batch_classify(self, slices: List[ActivitySlice]) -> List[ClassificationResult]:
        pass

    @abstractmethod
    def estimate_cost(self, operation: str, count: int) -> float:
        pass
