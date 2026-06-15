"""On-demand LLM summary generation for the interactive dashboard."""

from __future__ import annotations

from typing import List, Optional

from aw_coach.analyzer import AnalysisResult


def generate_ai_summary(
    analysis: AnalysisResult,
    config: object,
    corrections: Optional[List[dict]] = None,
    cost_controller=None,
) -> str:
    """Generate one concise work summary through the configured LLM backend."""
    if config.ai.backend == "rule_only":
        raise ValueError("当前 ai.backend=rule_only，无法调用 LLM。")
    if not config.ai.openai.api_key:
        raise ValueError("缺少 OpenAI/DeepSeek API key，无法调用 LLM。")

    from aw_coach.ai.openai_backend import OpenAIBackend

    backend = OpenAIBackend(
        api_key=config.ai.openai.api_key,
        model=config.ai.openai.model,
        base_url=config.ai.openai.base_url,
    )
    prompt = _build_summary_prompt(analysis, corrections)

    if cost_controller is not None:
        estimated_cost = backend.estimate_cost("summary", 1)
        if not cost_controller.can_use_llm(estimated_cost):
            raise ValueError("LLM 月度预算已用尽，无法生成总结。")

    response = backend.chat_completion(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=500,
    )
    raw = response.choices[0].message.content or ""
    summary = raw.strip()
    if not summary:
        raise ValueError("LLM 返回了空总结。")

    _track_summary_cost(cost_controller, backend.model, response, prompt, summary)
    return summary


def _build_summary_prompt(
    analysis: AnalysisResult,
    corrections: Optional[List[dict]] = None,
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

    death_loop_text = "无"
    if analysis.death_loops:
        loop_parts = []
        for loop in analysis.death_loops[:3]:
            apps = "↔".join(loop.get("apps", []))
            alternations = loop.get("alternations", 0)
            loop_parts.append(f"{apps} 交替 {alternations} 次")
        death_loop_text = "; ".join(loop_parts)

    corrections_text = "无"
    if corrections:
        corrections_text = "\n".join(
            f"- {item['app']}: {item['original_type']} -> {item['corrected_type']}"
            for item in corrections[:8]
        )

    return f"""你是一个本地工作效率仪表盘的 AI 教练。
请根据今日数据生成一段中文总结，要求客观、具体、可执行。

今日数据：
- 总工作时长: {analysis.total_hours:.1f}h
- 有效工作时长: {analysis.effective_hours:.1f}h
- 深度工作时长: {analysis.deep_work_hours:.1f}h
- 专注得分: {analysis.focus_score}/100
- 生产力得分: {analysis.productivity_score}/100
- 任务切换次数: {analysis.switch_count}
- 活动分布: {breakdown_text or '无数据'}
- 小时级专注度: {hourly_text or '无数据'}
- 切换循环: {death_loop_text}

近期用户纠错：
{corrections_text}

注意：
1. 12:30-14:00 与 18:00-19:00 是固定休息时间，不要视为低效问题。
2. 先总结今天的主模式，再指出一个最值得改进的点，最后给出下一步动作。
3. 控制在 120-180 字，不要输出 JSON，不要使用 Markdown 标题。"""


def _track_summary_cost(cost_controller, model: str, response, prompt: str, raw: str) -> None:
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
        input_tokens = max(1, len(prompt) // 4)
        output_tokens = max(1, len(raw) // 4)

    if input_tokens <= 0 and output_tokens <= 0:
        input_tokens = max(1, len(prompt) // 4)
        output_tokens = max(1, len(raw) // 4)

    cost_controller.track_call(model, input_tokens, output_tokens, "summary")
