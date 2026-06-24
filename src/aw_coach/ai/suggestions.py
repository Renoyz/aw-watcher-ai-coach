"""AI-powered suggestion generation using DeepSeek / OpenAI-compatible API."""

from __future__ import annotations

import json
import logging
from typing import Any, List, Optional

from aw_coach.analyzer import AnalysisResult
from aw_coach.config import Config

logger = logging.getLogger(__name__)


def generate_ai_suggestions(
    analysis: AnalysisResult,
    config: Config,
    corrections: Optional[List[dict]] = None,
    is_weekly: bool = False,
    cost_controller=None,
) -> List[str]:
    """Generate personalized work suggestions via LLM.

    Falls back to rule-based suggestions if LLM is unavailable or fails.
    """
    if config.ai.backend == "rule_only":
        return _fallback_suggestions(analysis, is_weekly)

    try:
        from aw_coach.ai.openai_backend import OpenAIBackend

        backend = OpenAIBackend(
            api_key=config.ai.openai.api_key,
            model=config.ai.openai.model,
            base_url=config.ai.openai.base_url,
        )
    except Exception as e:
        logger.warning(f"Failed to initialize AI backend for suggestions: {e}")
        return _fallback_suggestions(analysis, is_weekly)

    prompt = _build_prompt(analysis, corrections, is_weekly)

    # Budget check before spending real tokens
    if cost_controller is not None:
        est_cost = backend.estimate_cost("suggestions", 1)
        if not cost_controller.can_use_llm(est_cost):
            logger.warning("AI suggestion skipped: monthly budget exhausted")
            return _fallback_suggestions(analysis, is_weekly)

    try:
        response = backend.chat_completion(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
            max_tokens=600,
        )
        raw = response.choices[0].message.content
        suggestions = _parse_response(raw)
        if suggestions:
            logger.info(f"AI generated {len(suggestions)} suggestions")
            _track_suggestion_cost(cost_controller, backend.model, response, prompt, raw)
            return suggestions
    except Exception as e:
        logger.warning(f"AI suggestion generation failed: {e}")

    return _fallback_suggestions(analysis, is_weekly)


def _track_suggestion_cost(cost_controller, model: str, response, prompt: str, raw: str) -> None:
    if cost_controller is None:
        return

    usage = getattr(response, "usage", None)
    if isinstance(usage, dict):
        input_tokens = int(usage.get("prompt_tokens") or 0)
        output_tokens = int(usage.get("completion_tokens") or 0)
    elif usage is not None:
        input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    else:
        # Some OpenAI-compatible APIs omit usage; keep cost accounting conservative.
        input_tokens = max(1, len(prompt) // 4)
        output_tokens = max(1, len(raw) // 4)

    if input_tokens <= 0 and output_tokens <= 0:
        input_tokens = max(1, len(prompt) // 4)
        output_tokens = max(1, len(raw) // 4)

    try:
        cost_controller.track_call(model, input_tokens, output_tokens, "suggestions")
    except Exception:
        logger.debug("Failed to track AI suggestion cost", exc_info=True)


def _build_prompt(
    analysis: AnalysisResult,
    corrections: Optional[List[dict]] = None,
    is_weekly: bool = False,
) -> str:
    """Build prompt for LLM suggestion generation."""
    # Format hourly scores
    hourly_text = ", ".join(
        f"{h:02d}:00={score}" for h, score in analysis.hourly_scores
    )

    # Format activity breakdown
    breakdown_text = ", ".join(
        f"{k}={v:.1f}h"
        for k, v in sorted(
            analysis.activity_breakdown.items(), key=lambda x: -x[1]
        )
    )

    # Corrections context
    corrections_text = ""
    if corrections:
        corrections_text = "\n用户近期纠正记录:\n" + "\n".join(
            f"- {c['app']}: {c['original_type']} → {c['corrected_type']}"
            for c in corrections[:10]
        )

    # Format death loops
    death_loops_text = ""
    if analysis.death_loops:
        loops = []
        for loop in analysis.death_loops[:3]:
            apps = loop.get("apps", [])
            count = loop.get("alternations", 0)
            loops.append(f"{'↔'.join(apps)} (交替{count}次)")
        death_loops_text = "\n- 检测到的切换循环: " + "; ".join(loops)

    scope = "本周" if is_weekly else "今日"
    return f"""你是一位资深软件工程师的工作效率导师。
请根据以下结构化数据，给出 3-5 条具体、可执行的建议。

重要提示：以下时间段是固定的吃饭/休息时间，分析时请勿将其视为低效或问题：
- 12:30-14:00 午餐+午休
- 18:00-19:00 晚餐

{scope}工作数据：
- 总工作时长: {analysis.total_hours:.1f}h
- 有效工作时长: {analysis.effective_hours:.1f}h
- 深度工作时长: {analysis.deep_work_hours:.1f}h
- 专注得分: {analysis.focus_score}/100
- 生产力得分: {analysis.productivity_score}/100 (加权计算，1.0=高效编程，-0.5=娱乐)
- 任务切换次数: {analysis.switch_count}{death_loops_text}
- 活动分布: {breakdown_text or '无数据'}
- 小时级专注度: {hourly_text or '无数据'}{corrections_text}

要求：
1. 分析具体时间段的工作模式（上午 vs 下午的效率差异）
2. 必须排除 12:30-14:00 和 18:00-19:00 的休息时段，不将其视为低效
3. 如有切换循环，指出具体是哪两个应用反复切换并给出减少循环的建议
4. 建议要具体、可执行，避免空泛口号
5. 如果数据表现优秀，给予正面鼓励
6. 用中文回答，每条建议 30-60 字
7. 直接返回 JSON 数组格式: ["建议1", "建议2", ...]"""


def _json_candidates(text: str) -> List[str]:
    """Return likely JSON payloads from a sometimes-chatty LLM response."""
    candidates = [text]

    if "```" in text:
        parts = text.split("```")
        for part in parts:
            clean = part.strip()
            if clean:
                candidates.append(clean)

    for open_char, close_char in (("[", "]"), ("{", "}")):
        start = text.find(open_char)
        end = text.rfind(close_char)
        if start != -1 and end > start:
            candidates.append(text[start : end + 1])

    # Preserve order while removing duplicates.
    unique = []
    seen = set()
    for candidate in candidates:
        candidate = candidate.strip()
        if candidate and candidate not in seen:
            unique.append(candidate)
            seen.add(candidate)
    return unique


def _coerce_suggestions(data: Any) -> List[str]:
    """Flatten common LLM response shapes into suggestion strings."""
    if data is None:
        return []

    if isinstance(data, str):
        text = data.strip()
        if not text:
            return []
        if text.startswith(("[", "{")):
            try:
                return _coerce_suggestions(json.loads(text))
            except (json.JSONDecodeError, TypeError):
                pass
        return [text]

    if isinstance(data, list):
        suggestions: List[str] = []
        for item in data:
            suggestions.extend(_coerce_suggestions(item))
        return suggestions

    if isinstance(data, dict):
        for key in ("suggestions", "建议", "result", "results"):
            if key in data:
                return _coerce_suggestions(data[key])
        return []

    text = str(data).strip()
    return [text] if text else []


def _clean_suggestions(suggestions: List[str]) -> List[str]:
    cleaned: List[str] = []
    for suggestion in suggestions:
        text = suggestion.strip()
        if not text:
            continue
        if text.startswith(("- ", "* ", "• ")):
            text = text[2:].strip()
        elif len(text) > 2 and text[0].isdigit() and text[1:3] in (". ", "、", ") "):
            text = text[3:].strip()
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned[:5]


def _parse_response(raw: str) -> List[str]:
    """Parse LLM response into list of suggestion strings."""
    text = raw.strip()

    for candidate in _json_candidates(text):
        try:
            suggestions = _coerce_suggestions(json.loads(candidate))
        except (json.JSONDecodeError, TypeError):
            continue
        cleaned = _clean_suggestions(suggestions)
        if cleaned:
            return cleaned

    # Fallback: line-by-line extraction
    suggestions = []
    for line in raw.split("\n"):
        line = line.strip()
        if not line or len(line) < 10:
            continue
        # Remove markdown bullets and numbering
        if line.startswith(("- ", "* ", "• ")):
            line = line[2:]
        elif len(line) > 2 and line[0].isdigit() and line[1:3] in (". ", "、", ") "):
            line = line[3:].strip()
        if line.startswith(("[", "{")):
            nested = _clean_suggestions(_coerce_suggestions(line))
            if nested:
                suggestions.extend(nested)
                continue
        if len(line) > 10:
            suggestions.append(line)

    return _clean_suggestions(suggestions)


def _fallback_suggestions(analysis: AnalysisResult, is_weekly: bool) -> List[str]:
    """Fallback to rule-based suggestions when LLM is unavailable."""
    from aw_coach.report import generate_rule_suggestions

    if is_weekly:
        suggestions = []
        if analysis.deep_work_hours < 1.0:
            suggestions.append("深度工作时长不足，尝试划出无打扰时间段。")
        if analysis.switch_count > 20:
            suggestions.append("任务切换较频繁，建议使用番茄工作法。")
        if not suggestions:
            suggestions.append("本周表现不错，继续保持！")
        return suggestions
    return generate_rule_suggestions(analysis)
