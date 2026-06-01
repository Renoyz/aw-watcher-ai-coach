"""Cost controller - budget tracking, auto-degrade, alerts."""

from __future__ import annotations

import logging
from typing import Set

from aw_coach.config import CostConfig
from aw_coach.storage import Storage

logger = logging.getLogger(__name__)

PRICING = {
    "gpt-4o": {"input": 0.0025, "output": 0.01},
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    "deepseek-chat": {"input": 0.00014, "output": 0.00028},
    "deepseek-v4-flash": {"input": 0.00007, "output": 0.00014},
    "deepseek-v4-pro": {"input": 0.0005, "output": 0.002},
}


class CostController:
    def __init__(self, config: CostConfig, storage: Storage):
        self.budget = config.monthly_budget_usd
        self.alert_thresholds = getattr(config, "alert_thresholds", [0.5, 0.8, 1.0])
        self.storage = storage
        self._alerts_sent: Set[float] = set()

    def this_month_total(self) -> float:
        return self.storage.get_monthly_cost()

    def can_use_llm(self, estimated_cost: float = 0.02) -> bool:
        current = self.this_month_total()
        self._check_thresholds(current)
        if current + estimated_cost > self.budget:
            logger.warning(
                f"Budget ${self.budget:.2f} reached (current: ${current:.2f}). "
                "Falling back to rule-only."
            )
            return False
        return True

    def track_call(
        self, model: str, input_tokens: int, output_tokens: int, operation: str
    ) -> float:
        pricing = PRICING.get(model, PRICING["gpt-4o-mini"])
        cost = (
            input_tokens / 1000 * pricing["input"]
            + output_tokens / 1000 * pricing["output"]
        )
        self.storage.record_cost(model, input_tokens, output_tokens, cost, operation)
        total = self.this_month_total()
        logger.info(f"LLM call: ${cost:.4f} | Monthly: ${total:.2f}/${self.budget:.2f}")
        self._check_thresholds(total)
        return cost

    def _check_thresholds(self, current: float) -> None:
        if self.budget <= 0:
            return
        ratio = current / self.budget
        for threshold in self.alert_thresholds:
            if ratio >= threshold and threshold not in self._alerts_sent:
                self._alerts_sent.add(threshold)
                pct = int(threshold * 100)
                logger.warning(
                    f"Cost alert: {pct}% of monthly budget used "
                    f"(${current:.2f}/${self.budget:.2f})"
                )
