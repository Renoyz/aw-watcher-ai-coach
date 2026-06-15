"""Background LLM summaries for daemon (Hermes-style, cost-gated)."""

from __future__ import annotations

import logging
from typing import List, Optional

from aw_coach.analyzer import AnalysisResult
from aw_coach.config import Config
from aw_coach.task_models import TaskSession

logger = logging.getLogger(__name__)


def should_silent_summary(
    analysis: AnalysisResult,
    config: Config,
    active_signals: Optional[List[str]] = None,
) -> bool:
    """Return True when summary should be suppressed."""
    active_signals = active_signals or []
    threshold = config.report.silent_if_effective_hours_below
    if analysis.effective_hours >= threshold:
        return False
    for sig in config.report.always_notify_signals:
        if sig in active_signals:
            return False
    if analysis.death_loops:
        return False
    return True


def build_background_summary_prompt(
    analysis: AnalysisResult,
    semantic_state: Optional[dict] = None,
    corrections: Optional[List[dict]] = None,
    task_sessions: Optional[List[TaskSession]] = None,
) -> str:
    breakdown_text = ", ".join(
        f"{activity}={hours:.1f}h"
        for activity, hours in sorted(
            analysis.activity_breakdown.items(), key=lambda item: -item[1]
        )
    )
    hourly_text = ", ".join(
        f"{hour:02d}:00={score}" for hour, score in analysis.hourly_scores
    )

    mode_line = ""
    insight_line = ""
    if semantic_state:
        state = semantic_state.get("state", {})
        chain = semantic_state.get("chain", {})
        if state.get("likely_mode"):
            mode_line = f"- 当前工作模式: {state.get('likely_mode')}"
        if state.get("semantic_project"):
            mode_line += f" | 项目: {state.get('semantic_project')}"
        if chain.get("insight"):
            insight_line = f"- 行为链洞察: {chain.get('insight')}"

    task_lines = ""
    if task_sessions:
        parts = []
        for session in sorted(task_sessions, key=lambda s: -s.accumulated_sec)[:6]:
            hours = session.accumulated_sec / 3600
            blockers = ",".join(session.blockers) if session.blockers else "无"
            parts.append(
                f"  - {session.label}: {hours:.1f}h ({session.intent})"
                f"{'; 卡点: ' + blockers if blockers != '无' else ''}"
            )
        if parts:
            task_lines = "今日任务会话:\n" + "\n".join(parts)

    corrections_text = "无"
    if corrections:
        corrections_text = "\n".join(
            f"- {item['app']}: {item['original_type']} -> {item['corrected_type']}"
            for item in corrections[:8]
        )

    return f"""你是一个本地工作效率仪表盘的 AI 教练。
请根据数据生成一段中文工作总结，要求客观、具体、可执行。

今日数据：
- 总工作时长: {analysis.total_hours:.1f}h
- 有效工作时长: {analysis.effective_hours:.1f}h
- 深度工作时长: {analysis.deep_work_hours:.1f}h
- 专注得分: {analysis.focus_score}/100
- 生产力得分: {analysis.productivity_score}/100
- 任务切换次数: {analysis.switch_count}
- 活动分布: {breakdown_text or '无数据'}
- 小时级专注度: {hourly_text or '无数据'}
{mode_line}
{insight_line}
{task_lines}

近期用户纠错：
{corrections_text}

注意：
1. 12:30-14:00 与 18:00-19:00 是固定休息时间，不要视为低效问题。
2. 先总结主模式，再指出一个最值得改进的点，最后给出下一步动作。
3. 控制在 120-180 字，不要输出 JSON，不要使用 Markdown 标题。"""


def generate_background_summary(
    analysis: AnalysisResult,
    config: Config,
    cost_controller=None,
    semantic_state: Optional[dict] = None,
    corrections: Optional[List[dict]] = None,
    task_sessions: Optional[List[TaskSession]] = None,
    active_signals: Optional[List[str]] = None,
) -> Optional[str]:
    """Generate narrative summary or None (silent / failure / budget)."""
    if not config.report.background_ai_summary:
        return None
    if config.ai.backend == "rule_only":
        return None
    if not config.ai.openai.api_key:
        return None
    if should_silent_summary(analysis, config, active_signals):
        return None

    try:
        from aw_coach.ai.openai_backend import OpenAIBackend

        backend = OpenAIBackend(
            api_key=config.ai.openai.api_key,
            model=config.ai.openai.model,
            base_url=config.ai.openai.base_url,
        )
        prompt = build_background_summary_prompt(
            analysis, semantic_state, corrections, task_sessions
        )

        if cost_controller is not None:
            estimated = backend.estimate_cost("summary", 1)
            if not cost_controller.can_use_llm(estimated):
                logger.warning("Background summary skipped: monthly budget exhausted")
                return None

        response = backend.chat_completion(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=500,
        )
        raw = response.choices[0].message.content or ""
        summary = raw.strip()
        if not summary:
            return None

        if cost_controller is not None:
            _track_cost(cost_controller, backend.model, response, prompt, summary)
        return summary
    except Exception as e:
        logger.warning(f"Background summary generation failed: {e}")
        return None


def _track_cost(cost_controller, model: str, response, prompt: str, raw: str) -> None:
    usage = getattr(response, "usage", None)
    if isinstance(usage, dict):
        input_tokens = int(usage.get("prompt_tokens") or 0)
        output_tokens = int(usage.get("completion_tokens") or 0)
    elif usage is not None:
        input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    else:
        input_tokens = max(1, len(prompt) // 4)
        output_tokens = max(1, len(raw) // 4)
    cost_controller.track_call(model, input_tokens, output_tokens, "summary")
