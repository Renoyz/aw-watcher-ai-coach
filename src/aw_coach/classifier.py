"""Unified slice classification service."""

from __future__ import annotations

import logging
from typing import Callable, List, Optional

from aw_coach.collector import ActivitySlice
from aw_coach.config import Config
from aw_coach.rules.engine import RuleEngine, RuleResult

logger = logging.getLogger(__name__)


class RuleOnlyClassifier:
    """Adapter that gives RuleEngine the same interface as HybridBackend."""

    def __init__(self, rule_engine: RuleEngine):
        self.rule_engine = rule_engine

    def batch_classify(self, slices: List[ActivitySlice]) -> List[RuleResult]:
        return [
            self.rule_engine.classify(s.primary_app, s.primary_title, s.web_url)
            for s in slices
        ]


def create_classifier(
    config: Config,
    on_hybrid_fallback: Optional[Callable[[Exception], None]] = None,
):
    """Create the configured classifier, falling back to rules if hybrid cannot start."""
    rule_engine = RuleEngine.with_all_rules()

    if config.ai.backend == "hybrid":
        try:
            from aw_coach.ai.cost import CostController
            from aw_coach.ai.hybrid import HybridBackend
            from aw_coach.ai.openai_backend import OpenAIBackend
            from aw_coach.storage import Storage

            storage = Storage(config.db_path)
            llm = OpenAIBackend(
                api_key=config.ai.openai.api_key,
                model=config.ai.openai.model,
                base_url=config.ai.openai.base_url,
            )
            cost = CostController(config.cost, storage)
            return HybridBackend(rule_engine, llm, cost, storage=storage)
        except Exception as e:
            if on_hybrid_fallback is not None:
                on_hybrid_fallback(e)
            else:
                logger.warning(
                    "Failed to initialize hybrid backend; falling back to rule_only.",
                    exc_info=True,
                )

    return RuleOnlyClassifier(rule_engine)


def classify_slices(config: Config, slices: List[ActivitySlice]) -> List[RuleResult]:
    """Classify slices with the configured backend."""
    return create_classifier(config).batch_classify(slices)
