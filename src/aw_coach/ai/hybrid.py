"""Hybrid backend - rules first, LLM for uncertain, cost-gated, queue-backed."""

from __future__ import annotations

import logging
from typing import List, Optional

from aw_coach.ai.base import AIBackend, ClassificationResult
from aw_coach.ai.cost import CostController
from aw_coach.collector import ActivitySlice
from aw_coach.rules.engine import RuleEngine
from aw_coach.storage import Storage

logger = logging.getLogger(__name__)


class HybridBackend:
    def __init__(
        self,
        rule_engine: RuleEngine,
        llm_backend: AIBackend,
        cost_controller: CostController,
        threshold: float = 0.85,
        low_confidence_threshold: float = 0.5,
        storage: Optional[Storage] = None,
    ):
        self.rules = rule_engine
        self.llm = llm_backend
        self.cost = cost_controller
        self.threshold = threshold
        self.low_confidence_threshold = low_confidence_threshold
        self.storage = storage

    def batch_classify(self, slices: List[ActivitySlice]) -> List[ClassificationResult]:
        """Classify slices: rules first, uncertain ones go to LLM immediately.
        If storage is provided, uncertain slices are also logged to batch_queue.
        Very low-confidence slices skip the queue and fall back to rule-based.
        """
        results: List[Optional[ClassificationResult]] = []
        uncertain_indices: List[int] = []
        uncertain_slices: List[ActivitySlice] = []
        queue_ids: List[int] = []

        # 1. Rule engine pass
        for i, s in enumerate(slices):
            rule_result = self.rules.classify(s.primary_app, s.primary_title, s.web_url)
            if rule_result.confidence >= self.threshold:
                results.append(ClassificationResult(
                    activity_type=rule_result.activity_type,
                    confidence=rule_result.confidence,
                    method=rule_result.method,
                    weight=rule_result.weight,
                    skip_analysis=rule_result.skip_analysis,
                ))
            elif rule_result.confidence < self.low_confidence_threshold:
                # Skip queue for very low confidence: use rule fallback directly
                results.append(ClassificationResult(
                    activity_type=rule_result.activity_type,
                    confidence=rule_result.confidence,
                    method="rule_low_conf",
                    weight=rule_result.weight,
                    skip_analysis=rule_result.skip_analysis,
                ))
            else:
                results.append(None)
                uncertain_indices.append(i)
                uncertain_slices.append(s)

        # 2. Log uncertain to batch_queue (for analytics/replay)
        if uncertain_slices and self.storage:
            for s in uncertain_slices:
                rule_r = self.rules.classify(s.primary_app, s.primary_title, s.web_url)
                qid = self.storage.enqueue_batch_item(
                    slice_start=s.start.isoformat(),
                    slice_end=s.end.isoformat(),
                    app=s.primary_app,
                    title=s.primary_title,
                    url=s.web_url,
                    rule_confidence=rule_r.confidence,
                )
                queue_ids.append(qid)

        # 3. LLM pass for uncertain (if budget allows)
        if uncertain_slices:
            est_cost = self.llm.estimate_cost("batch_classify", len(uncertain_slices))
            if self.cost.can_use_llm(est_cost):
                try:
                    llm_results = self.llm.batch_classify(uncertain_slices)
                    for idx, llm_r in zip(uncertain_indices, llm_results):
                        results[idx] = llm_r
                    usage = getattr(self.llm, "last_usage", None) or {}
                    input_tokens = usage.get("prompt_tokens", 0)
                    output_tokens = usage.get("completion_tokens", 0)
                    if input_tokens <= 0 and output_tokens <= 0:
                        input_tokens = 200 + len(uncertain_slices) * 50
                        output_tokens = len(uncertain_slices) * 30
                    self.cost.track_call(
                        getattr(self.llm, "model", "unknown"),
                        input_tokens,
                        output_tokens,
                        "batch_classify",
                    )
                    # Mark only the queue items from this batch as processed
                    if self.storage and queue_ids:
                        self.storage.mark_batch_processed(queue_ids)
                except Exception as e:
                    logger.warning(f"LLM batch_classify failed: {e}")

        # 4. Fill remaining None with unknown
        for i, r in enumerate(results):
            if r is None:
                results[i] = ClassificationResult("unknown", 0.0, "budget_limited")

        return results  # type: ignore[return-value]
