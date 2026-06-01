"""Rule-based report suggestions."""

from __future__ import annotations

from typing import List

from aw_coach.analyzer import AnalysisResult


def generate_rule_suggestions(analysis: AnalysisResult) -> List[str]:
    suggestions = []

    if analysis.switch_count > 20:
        suggestions.append("今日任务切换较频繁，建议使用番茄工作法减少中断。")

    if analysis.deep_work_hours < 1.0 and analysis.total_hours > 2.0:
        suggestions.append("深度工作时长不足 1 小时，尝试划出一段无打扰时间。")

    entertainment = analysis.activity_breakdown.get("entertainment", 0)
    if entertainment > 2.0:
        suggestions.append(f"今日娱乐时间 {entertainment:.1f}h，注意平衡。")

    if analysis.hourly_scores:
        best_hour = max(analysis.hourly_scores, key=lambda x: x[1])
        suggestions.append(f"你在 {best_hour[0]}:00 左右效率最高，建议安排重要任务。")

    social = analysis.activity_breakdown.get("social", 0)
    if social > 1.5:
        suggestions.append(f"社交应用使用 {social:.1f}h，考虑集中处理消息。")

    return suggestions[:5]
