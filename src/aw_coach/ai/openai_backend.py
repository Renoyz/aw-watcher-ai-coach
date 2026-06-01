"""OpenAI backend - batch classification via GPT-4o-mini."""

from __future__ import annotations

import json
import logging
from typing import List, Optional

from aw_coach.ai.base import AIBackend, ClassificationResult
from aw_coach.ai.cost import PRICING
from aw_coach.collector import ActivitySlice

logger = logging.getLogger(__name__)


class OpenAIBackend(AIBackend):
    def __init__(self, api_key: str, model: str = "gpt-4o-mini", base_url: Optional[str] = None):
        import openai
        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = openai.OpenAI(**kwargs)
        self.model = model
        self.last_usage: Optional[dict] = None

    def batch_classify(self, slices: List[ActivitySlice]) -> List[ClassificationResult]:
        prompt = self._build_batch_prompt(slices)
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.1,
        )
        self.last_usage = _extract_usage(response)
        raw = response.choices[0].message.content
        return self._parse_response(raw, len(slices))

    def estimate_cost(self, operation: str, count: int) -> float:
        pricing = PRICING.get(self.model, PRICING["gpt-4o-mini"])
        if operation == "batch_classify":
            input_tokens = 200 + count * 50
            output_tokens = count * 30
        else:
            input_tokens = 500
            output_tokens = 300
        return (
            input_tokens / 1000 * pricing["input"]
            + output_tokens / 1000 * pricing["output"]
        )

    def _build_batch_prompt(self, slices: List[ActivitySlice]) -> str:
        lines = []
        for i, s in enumerate(slices, 1):
            start = s.start.strftime("%H:%M")
            end = s.end.strftime("%H:%M")
            app = self._sanitize(s.primary_app, 100)
            title = self._sanitize(s.primary_title, 200)
            url_part = f', url="{self._sanitize(s.web_url or "", 200)}"' if s.web_url else ""
            lines.append(f'{i}. [{start}-{end}] app="{app}", title="{title}"{url_part}')

        slice_list = "\n".join(lines)
        return f"""You are a work efficiency assistant. Classify each activity slice below.

Types: programming, writing, meeting, research, design, entertainment, admin, social, unknown

Slices:
{slice_list}

Return JSON: {{"classifications": [{{"activity_type": "...", "confidence": 0.0-1.0}}]}}"""

    @staticmethod
    def _sanitize(text: str, max_len: int) -> str:
        cleaned = text.replace("{", "").replace("}", "").replace("\\", "")
        cleaned = "".join(c for c in cleaned if c.isprintable())
        return cleaned[:max_len]

    def _parse_response(self, raw: str, expected_count: int) -> List[ClassificationResult]:
        try:
            data = json.loads(raw)
            items = data.get("classifications", data.get("results", []))
        except (json.JSONDecodeError, TypeError):
            logger.warning("LLM returned invalid JSON, falling back to unknown")
            return [ClassificationResult("unknown", 0.0, "llm_parse_error")] * expected_count

        results = []
        for i in range(expected_count):
            if i < len(items):
                item = items[i]
                results.append(ClassificationResult(
                    activity_type=item.get("activity_type", "unknown"),
                    confidence=item.get("confidence", 0.5),
                    method="llm_batch",
                ))
            else:
                results.append(ClassificationResult("unknown", 0.0, "llm_missing"))

        return results


def _extract_usage(response) -> Optional[dict]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    if isinstance(usage, dict):
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
    else:
        prompt_tokens = getattr(usage, "prompt_tokens", 0)
        completion_tokens = getattr(usage, "completion_tokens", 0)
    return {
        "prompt_tokens": int(prompt_tokens or 0),
        "completion_tokens": int(completion_tokens or 0),
    }
