"""Markdown reports and CLI status output."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Dict, List, Optional

from aw_coach.analyzer import AnalysisResult
from aw_coach.reports.suggestions import generate_rule_suggestions


def _bar(value: float, max_value: float, width: int = 20) -> str:
    if max_value <= 0:
        return ""
    filled = int(value / max_value * width)
    return "█" * filled + "░" * (width - filled)


def _energy_emoji(score: int, median: Optional[float] = None) -> str:
    if median is not None:
        if score >= median + 10:
            return "🟢"
        if score <= median - 10:
            return "🔴"
        return "🟡"
    if score >= 70:
        return "🟢"
    if score >= 50:
        return "🟡"
    return "🔴"


def _energy_trend(score: int, prev: Optional[int]) -> str:
    if prev is None:
        return ""
    if score > prev:
        return " ↑"
    if score < prev:
        return " ↓"
    return " →"


class ReportGenerator:
    def __init__(self, config: Optional[object] = None):
        self.config = config

    def generate_daily(
        self,
        report_date: date,
        analysis: AnalysisResult,
        use_ai: bool = False,
        project_breakdown: Optional[Dict[str, float]] = None,
        inbox_items: Optional[List[dict]] = None,
    ) -> str:
        sections = [
            self._header(report_date),
            self._overview_table(analysis),
        ]
        if project_breakdown is None and analysis.task_breakdown:
            project_breakdown = analysis.task_breakdown
        if project_breakdown:
            sections.append(self._project_breakdown_section(project_breakdown, analysis))
        sections.extend([
            self._breakdown_section(analysis),
            self._energy_curve(analysis),
            self._suggestions_section(analysis, use_ai=use_ai),
        ])
        inbox_section = self._inbox_section(inbox_items)
        if inbox_section:
            sections.append(inbox_section)
        return "\n\n".join(sections)

    def generate_status(self, analysis: AnalysisResult) -> str:
        lines = ["🧠 AI Coach - 实时状态", "━" * 36]

        if analysis.activity_breakdown:
            lines.append(f"  专注得分: {analysis.focus_score}/100")
            lines.append("")
            lines.append("  今日累计")
            lines.append("  " + "─" * 30)

            max_hours = max(analysis.activity_breakdown.values())
            for atype, hours in sorted(
                analysis.activity_breakdown.items(), key=lambda x: -x[1]
            ):
                bar = _bar(hours, max_hours, 12)
                h = int(hours)
                m = int((hours - h) * 60)
                lines.append(f"  {atype:<14} {bar}  {h}h {m:02d}m")

            if analysis.task_breakdown:
                lines.append("")
                lines.append("  任务分布")
                lines.append("  " + "─" * 30)
                max_task_hours = max(analysis.task_breakdown.values())
                for task, hours in sorted(
                    analysis.task_breakdown.items(), key=lambda x: -x[1]
                )[:5]:
                    bar = _bar(hours, max_task_hours, 12)
                    h = int(hours)
                    m = int((hours - h) * 60)
                    lines.append(f"  {task[:14]:<14} {bar}  {h}h {m:02d}m")

            lines.append("")
            lines.append(
                f"  有效工作: {analysis.effective_hours:.1f}h  |  "
                f"专注度: {analysis.focus_score}/100"
            )
            lines.append(
                f"  语义切换: {analysis.task_switch_count} 次   |  "
                f"深度工作: {analysis.deep_work_hours:.1f}h"
            )
            if analysis.switch_count != analysis.task_switch_count:
                lines.append(f"  活动切换: {analysis.switch_count} 次")
        else:
            lines.append("  暂无数据")

        return "\n".join(lines)

    def _header(self, report_date: date) -> str:
        return f"# 工作效率日报 - {report_date.isoformat()}"

    def _ai_collaboration_ratio(self, analysis: AnalysisResult) -> Optional[str]:
        breakdown = analysis.activity_breakdown
        ai_h = breakdown.get("ai_assisted", 0.0)
        prog_h = breakdown.get("programming", 0.0)
        total = ai_h + prog_h
        if total <= 0:
            return None
        pct = ai_h / total * 100
        return f"{pct:.0f}% ({ai_h:.1f}h / {total:.1f}h)"

    def _overview_table(self, analysis: AnalysisResult) -> str:
        productivity_line = (
            f"| 生产力得分 | {analysis.productivity_score}/100 |\n"
            if analysis.productivity_score > 0
            else ""
        )
        ai_ratio = self._ai_collaboration_ratio(analysis)
        ai_line = f"| AI 协作占比 | {ai_ratio} |\n" if ai_ratio else ""
        primary_switches = (
            analysis.task_switch_count
            if analysis.task_switch_count
            else analysis.switch_count
        )
        switch_line = f"| 任务切换 | {primary_switches} 次 |\n"
        activity_switch_line = (
            f"| 活动类型切换 | {analysis.switch_count} 次 |\n"
            if analysis.switch_count != analysis.task_switch_count
            else ""
        )
        return f"""## 今日概览

| 指标 | 数值 |
|------|------|
| 有效工作时长 | {analysis.effective_hours:.1f}h |
| 深度工作时长 | {analysis.deep_work_hours:.2f}h |
| 专注得分 | {analysis.focus_score}/100 |
{productivity_line}{ai_line}{switch_line}{activity_switch_line}"""

    def _project_breakdown_section(
        self, project_breakdown: Dict[str, float], analysis: Optional[AnalysisResult] = None
    ) -> str:
        if not project_breakdown:
            return ""
        lines = ["## 任务/项目分布", ""]
        max_hours = max(project_breakdown.values())
        for project, hours in sorted(project_breakdown.items(), key=lambda x: -x[1]):
            bar = _bar(hours, max_hours, 20)
            deep = 0.0
            if analysis is not None:
                deep = analysis.task_deep_work_breakdown.get(project, 0.0)
            suffix = f" | 深度 {deep:.1f}h" if deep > 0 else ""
            lines.append(f"  {project:<20} {bar} {hours:.1f}h{suffix}")
        return "\n".join(lines)

    def _breakdown_section(self, analysis: AnalysisResult) -> str:
        if not analysis.activity_breakdown:
            return "## 时间分布\n\n暂无数据"

        lines = ["## 时间分布", ""]
        max_hours = max(analysis.activity_breakdown.values())
        for atype, hours in sorted(
            analysis.activity_breakdown.items(), key=lambda x: -x[1]
        ):
            bar = _bar(hours, max_hours, 20)
            pct = hours / analysis.total_hours * 100 if analysis.total_hours > 0 else 0
            lines.append(f"  {atype:<14} {bar} {hours:.1f}h ({pct:.0f}%)")

        return "\n".join(lines)

    def _energy_curve(self, analysis: AnalysisResult) -> str:
        if not analysis.hourly_scores:
            return ""

        scores = [s for _, s in analysis.hourly_scores]
        median = float(sorted(scores)[len(scores) // 2])

        lines = ["## 精力曲线", ""]
        prev_score: Optional[int] = None
        for hour, score in analysis.hourly_scores:
            emoji = _energy_emoji(score, median=median)
            trend = _energy_trend(score, prev_score)
            lines.append(f"  {hour:02d}:00  {emoji} {score}{trend}")
            prev_score = score

        return "\n".join(lines)

    def _inbox_section(self, inbox_items: Optional[List[dict]]) -> str:
        if not inbox_items:
            return ""
        lines = ["## 待处理建议 (Inbox)", ""]
        for item in inbox_items[:5]:
            signal = item.get("signal_type", "signal")
            evidence = item.get("evidence", "")
            if len(evidence) > 60:
                evidence = evidence[:57] + "..."
            lines.append(f"- [{signal}] {evidence}")
        lines.append("")
        lines.append("运行 `aw-coach inbox list` 查看并处理。")
        return "\n".join(lines)

    def _suggestions_section(self, analysis: AnalysisResult, use_ai: bool = False) -> str:
        suggestions = self._generate_suggestions(analysis, is_weekly=False, use_ai=use_ai)
        if not suggestions:
            return "## 建议\n\n今日表现不错，继续保持！"

        lines = ["## 建议", ""]
        for i, suggestion in enumerate(suggestions, 1):
            lines.append(f"{i}. {suggestion}")

        return "\n".join(lines)

    def _generate_suggestions(
        self,
        analysis: AnalysisResult,
        is_weekly: bool = False,
        use_ai: bool = False,
    ) -> List[str]:
        if use_ai and self.config and self.config.ai.backend not in ("rule_only",):
            try:
                from aw_coach.ai.cost import CostController
                from aw_coach.ai.suggestions import generate_ai_suggestions
                from aw_coach.storage import Storage

                storage = Storage(self.config.db_path)
                cost = CostController(self.config.cost, storage)
                corrections = storage.get_corrections_last_30_days()
                return generate_ai_suggestions(
                    analysis,
                    self.config,
                    corrections=corrections,
                    is_weekly=is_weekly,
                    cost_controller=cost,
                )
            except Exception:
                pass
        return generate_rule_suggestions(analysis)

    def generate_weekly(
        self, week_start: date, daily_results: List[AnalysisResult], use_ai: bool = False
    ) -> str:
        if not daily_results:
            return "# 周报\n\n暂无数据"

        total_effective = sum(a.effective_hours for a in daily_results)
        total_deep = sum(a.deep_work_hours for a in daily_results)
        avg_focus = int(sum(a.focus_score for a in daily_results) / len(daily_results))
        avg_productivity = int(
            sum(a.productivity_score for a in daily_results) / len(daily_results)
        )
        total_switches = sum(a.switch_count for a in daily_results)

        merged_breakdown: Dict[str, float] = defaultdict(float)
        for result in daily_results:
            for activity_type, hours in result.activity_breakdown.items():
                merged_breakdown[activity_type] += hours

        best_day = max(range(len(daily_results)), key=lambda i: daily_results[i].focus_score)
        worst_day = min(range(len(daily_results)), key=lambda i: daily_results[i].focus_score)

        breakdown_lines = []
        max_hours = max(merged_breakdown.values()) if merged_breakdown else 1
        for atype, hours in sorted(merged_breakdown.items(), key=lambda x: -x[1]):
            bar = _bar(hours, max_hours, 20)
            breakdown_lines.append(f"  {atype:<14} {bar} {hours:.1f}h")

        weekly_analysis = AnalysisResult(
            total_hours=sum(a.total_hours for a in daily_results),
            effective_hours=total_effective,
            deep_work_hours=total_deep,
            focus_score=avg_focus,
            switch_count=total_switches,
            activity_breakdown=dict(merged_breakdown),
            hourly_scores=[],
        )

        return f"""# 周报 - {week_start.isoformat()} 起

## 本周概览

| 指标 | 数值 |
|------|------|
| 有效工作总时长 | {total_effective:.1f}h |
| 深度工作总时长 | {total_deep:.1f}h |
| 平均专注得分 | {avg_focus}/100 |
| 平均生产力得分 | {avg_productivity}/100 |
| 总任务切换 | {total_switches} 次 |
| 工作天数 | {len(daily_results)} 天 |

## 时间分布（周累计）

{chr(10).join(breakdown_lines)}

## 最佳/最差工作日

- 最佳: Day {best_day + 1} (专注度 {daily_results[best_day].focus_score}/100)
- 最差: Day {worst_day + 1} (专注度 {daily_results[worst_day].focus_score}/100)

## 建议

{self._weekly_suggestions(daily_results, weekly_analysis, use_ai=use_ai)}
"""

    def _weekly_suggestions(
        self,
        daily_results: List[AnalysisResult],
        weekly_analysis: AnalysisResult,
        use_ai: bool = False,
    ) -> str:
        if use_ai and self.config and self.config.ai.backend not in ("rule_only",):
            try:
                from aw_coach.ai.cost import CostController
                from aw_coach.ai.suggestions import generate_ai_suggestions
                from aw_coach.storage import Storage

                storage = Storage(self.config.db_path)
                cost = CostController(self.config.cost, storage)
                corrections = storage.get_corrections_last_30_days()
                suggestions = generate_ai_suggestions(
                    weekly_analysis,
                    self.config,
                    corrections=corrections,
                    is_weekly=True,
                    cost_controller=cost,
                )
                return "\n".join(f"- {suggestion}" for suggestion in suggestions)
            except Exception:
                pass

        suggestions = []
        avg_deep = sum(a.deep_work_hours for a in daily_results) / len(daily_results)
        avg_switches = sum(a.switch_count for a in daily_results) / len(daily_results)

        if avg_deep < 1.0:
            suggestions.append("本周平均深度工作不足 1h/天，尝试每天预留一段无打扰时间。")
        if avg_switches > 50:
            suggestions.append(f"平均每天切换 {avg_switches:.0f} 次，考虑批量处理消息和邮件。")

        focus_scores = [a.focus_score for a in daily_results]
        if max(focus_scores) - min(focus_scores) > 30:
            suggestions.append("专注度波动较大，分析高效日的作息规律并复制到其他天。")

        if not suggestions:
            suggestions.append("本周表现稳定，继续保持当前节奏！")

        return "\n".join(f"- {suggestion}" for suggestion in suggestions)
