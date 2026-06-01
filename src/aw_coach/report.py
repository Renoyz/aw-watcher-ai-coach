"""Report generation - Markdown daily reports and CLI status output."""

from __future__ import annotations

import html as html_lib
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from aw_coach.analyzer import AnalysisResult


def _bar(value: float, max_value: float, width: int = 20) -> str:
    if max_value <= 0:
        return ""
    filled = int(value / max_value * width)
    return "█" * filled + "░" * (width - filled)


def _energy_emoji(score: int) -> str:
    if score >= 70:
        return "🟢"
    elif score >= 50:
        return "🟡"
    return "🔴"


class ReportGenerator:
    def __init__(self, config: Optional[object] = None):
        self.config = config

    def generate_daily(
        self, report_date: date, analysis: AnalysisResult, use_ai: bool = False
    ) -> str:
        sections = [
            self._header(report_date),
            self._overview_table(analysis),
            self._breakdown_section(analysis),
            self._energy_curve(analysis),
            self._suggestions_section(analysis, use_ai=use_ai),
        ]
        return "\n\n".join(sections)

    def generate_status(self, analysis: AnalysisResult) -> str:
        lines = []
        lines.append("🧠 AI Coach - 实时状态")
        lines.append("━" * 36)

        if analysis.activity_breakdown:
            lines.append(f"  专注得分: {analysis.focus_score}/100")
            lines.append("")
            lines.append("  今日累计")
            lines.append("  " + "─" * 30)

            max_hours = (
                max(analysis.activity_breakdown.values())
                if analysis.activity_breakdown
                else 1
            )
            for atype, hours in sorted(
                analysis.activity_breakdown.items(), key=lambda x: -x[1]
            ):
                bar = _bar(hours, max_hours, 12)
                h = int(hours)
                m = int((hours - h) * 60)
                lines.append(f"  {atype:<14} {bar}  {h}h {m:02d}m")

            lines.append("")
            lines.append(
                f"  有效工作: {analysis.effective_hours:.1f}h  |  "
                f"专注度: {analysis.focus_score}/100"
            )
            lines.append(
                f"  任务切换: {analysis.switch_count} 次   |  "
                f"深度工作: {analysis.deep_work_hours:.1f}h"
            )
        else:
            lines.append("  暂无数据")

        return "\n".join(lines)

    def _header(self, report_date: date) -> str:
        return f"# 工作效率日报 - {report_date.isoformat()}"

    def _overview_table(self, analysis: AnalysisResult) -> str:
        productivity_line = (
            f"| 生产力得分 | {analysis.productivity_score}/100 |\n"
            if analysis.productivity_score > 0
            else ""
        )
        return f"""## 今日概览

| 指标 | 数值 |
|------|------|
| 有效工作时长 | {analysis.effective_hours:.1f}h |
| 深度工作时长 | {analysis.deep_work_hours:.2f}h |
| 专注得分 | {analysis.focus_score}/100 |
{productivity_line}| 任务切换 | {analysis.switch_count} 次 |"""

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

        lines = ["## 精力曲线", ""]
        for hour, score in analysis.hourly_scores:
            emoji = _energy_emoji(score)
            lines.append(f"  {hour:02d}:00  {emoji} {score}")

        return "\n".join(lines)

    def _suggestions_section(self, analysis: AnalysisResult, use_ai: bool = False) -> str:
        suggestions = self._generate_suggestions(analysis, is_weekly=False, use_ai=use_ai)
        if not suggestions:
            return "## 建议\n\n今日表现不错，继续保持！"

        lines = ["## 建议", ""]
        for i, s in enumerate(suggestions, 1):
            lines.append(f"{i}. {s}")

        return "\n".join(lines)

    def _generate_suggestions(
        self,
        analysis: AnalysisResult,
        is_weekly: bool = False,
        use_ai: bool = False,
    ) -> List[str]:
        """Generate suggestions via AI (if hybrid/openai backend) or rules."""
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
                pass  # fallback to rules
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

        # Aggregate breakdown across days
        from collections import defaultdict
        merged_breakdown: Dict[str, float] = defaultdict(float)
        for a in daily_results:
            for k, v in a.activity_breakdown.items():
                merged_breakdown[k] += v

        # Best/worst day
        best_day = max(range(len(daily_results)), key=lambda i: daily_results[i].focus_score)
        worst_day = min(range(len(daily_results)), key=lambda i: daily_results[i].focus_score)

        breakdown_lines = []
        max_hours = max(merged_breakdown.values()) if merged_breakdown else 1
        for atype, hours in sorted(merged_breakdown.items(), key=lambda x: -x[1]):
            bar = _bar(hours, max_hours, 20)
            breakdown_lines.append(f"  {atype:<14} {bar} {hours:.1f}h")

        # Aggregate an AnalysisResult for weekly AI suggestions
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
                return "\n".join(f"- {s}" for s in suggestions)
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

        return "\n".join(f"- {s}" for s in suggestions)


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


def _build_hourly_timeline(slices, rules):
    """Aggregate slices into hourly timeline."""
    if not slices or not rules:
        return []
    from collections import defaultdict

    hourly = defaultdict(
        lambda: {"duration": 0, "apps": set(), "titles": set(), "activities": defaultdict(float)}
    )
    for s, r in zip(slices, rules):
        if getattr(s, "is_afk", False):
            continue
        if getattr(r, "skip_analysis", False):
            continue
        for start, duration in _split_slice_by_hour(s):
            hour = start.hour
            hourly[hour]["duration"] += duration
            hourly[hour]["apps"].add(getattr(s, "primary_app", "unknown"))
            title = getattr(s, "primary_title", "")
            if title:
                hourly[hour]["titles"].add(title)
            hourly[hour]["activities"][r.activity_type] += duration

    result = []
    for hour in sorted(hourly.keys()):
        info = hourly[hour]
        acts = info["activities"]
        top_activity = max(acts.items(), key=lambda x: x[1])[0] if acts else "unknown"
        result.append(
            {
                "hour": hour,
                "duration": info["duration"] / 3600,
                "apps": list(info["apps"])[:3],
                "titles": list(info["titles"])[:2],
                "activity": top_activity,
            }
        )
    return result


def _split_slice_by_hour(s):
    """Yield (segment_start, duration_seconds) split at hour boundaries."""
    start = getattr(s, "start", None)
    end = getattr(s, "end", None)
    if start is None or end is None or end <= start:
        return []

    segments = []
    cursor = start
    while cursor < end:
        next_hour = cursor.replace(minute=0, second=0, microsecond=0) + timedelta(
            hours=1
        )
        segment_end = min(end, next_hour)
        duration = (segment_end - cursor).total_seconds()
        if duration > 0:
            segments.append((cursor, duration))
        cursor = segment_end
    return segments


def _activity_color(activity: str) -> str:
    colors = {
        "programming": "#2563eb",
        "writing": "#8b5cf6",
        "design": "#ec4899",
        "research": "#f59e0b",
        "meeting": "#10b981",
        "admin": "#6b7280",
        "social": "#ef4444",
        "entertainment": "#6366f1",
    }
    return colors.get(activity, "#9ca3af")


def _safe_json(value) -> str:
    """Serialize JSON for embedding inside a <script> block."""
    return (
        json.dumps(value, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def generate_html_dashboard(
    config: object,
    target: date,
    analysis: AnalysisResult,
    slices=None,
    rules=None,
) -> Path:
    """Generate a self-contained HTML dashboard with Chart.js and timeline."""
    reporter = ReportGenerator(config)
    suggestions = reporter._generate_suggestions(analysis, is_weekly=False)
    breakdown = analysis.activity_breakdown
    hourly = analysis.hourly_scores

    labels_json = _safe_json(list(breakdown.keys()))
    values_json = _safe_json([round(v, 2) for v in breakdown.values()])
    hourly_labels_json = _safe_json([f"{h:02d}:00" for h, _ in hourly])
    hourly_values_json = _safe_json([s for _, s in hourly])

    suggestions_html = "".join(
        f"<li>{html_lib.escape(str(s), quote=True)}</li>" for s in suggestions
    )

    # Build timeline
    timeline_data = _build_hourly_timeline(slices, rules)
    timeline_html = ""
    if timeline_data:
        items = []
        for item in timeline_data:
            apps_str = html_lib.escape(", ".join(str(app) for app in item["apps"]), quote=True)
            titles_str = html_lib.escape(
                "; ".join(str(title) for title in item["titles"][:2]), quote=True
            )
            titles_html = f'<div class="timeline-titles">{titles_str}</div>' if titles_str else ""
            activity = html_lib.escape(str(item["activity"]), quote=True)
            color = _activity_color(str(item["activity"]))
            items.append(
                f"""<div class="timeline-item" style="border-left-color:{color}">
  <div class="timeline-time">{item["hour"]:02d}:00</div>
  <div class="timeline-content">
    <div class="timeline-activity">{activity}</div>
    <div class="timeline-apps">{apps_str}</div>
    {titles_html}
    <div class="timeline-duration">{item["duration"]:.1f}h</div>
  </div>
</div>"""
            )
        timeline_html = "\\n".join(items)
    else:
        timeline_html = '<p class="empty">暂无详细时间段数据</p>'

    # Death loops section
    death_loops_html = ""
    if analysis.death_loops:
        loops = []
        for loop in analysis.death_loops[:5]:
            apps = loop.get("apps", [])
            apps_str = html_lib.escape(" ↔ ".join(str(a) for a in apps), quote=True)
            count = html_lib.escape(
                str(loop.get("alternations", loop.get("count", 0))), quote=True
            )
            loops.append(
                f'<div class="loop-item">⚠️ {apps_str} （{count} 次切换）</div>'
            )
        death_loops_html = "\n".join(loops)
    else:
        death_loops_html = '<p class="empty">今日未检测到切换循环</p>'

    html = f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI Coach - {target.isoformat()}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
:root {{
  --bg: #f8f9fa;
  --card-bg: #ffffff;
  --text: #1f2937;
  --text-secondary: #6b7280;
  --border: #e5e7eb;
  --primary: #2563eb;
  --danger: #ef4444;
  --success: #10b981;
  --warning: #f59e0b;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --bg: #111827;
    --card-bg: #1f2937;
    --text: #f9fafb;
    --text-secondary: #9ca3af;
    --border: #374151;
  }}
}}
* {{ box-sizing: border-box; }}
body {{
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  max-width: 960px;
  margin: 0 auto;
  padding: 1.5em 1em;
  background: var(--bg);
  color: var(--text);
  line-height: 1.6;
}}
h1 {{ font-size: 1.5rem; margin-bottom: 0.2em; }}
.date {{ color: var(--text-secondary); font-size: 0.9rem; margin-bottom: 1.5em; }}

/* Cards */
.cards {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 0.8em;
  margin: 1.5em 0;
}}
.card {{
  background: var(--card-bg);
  border-radius: 12px;
  padding: 1em;
  box-shadow: 0 1px 3px rgba(0,0,0,0.08);
  text-align: center;
  transition: transform 0.15s;
}}
.card:hover {{ transform: translateY(-2px); }}
.card .value {{
  font-size: 1.8em;
  font-weight: 700;
  color: var(--primary);
}}
.card .label {{ color: var(--text-secondary); font-size: 0.85em; margin-top: 0.2em; }}
.card.danger .value {{ color: var(--danger); }}
.card.success .value {{ color: var(--success); }}

/* Charts */
.charts {{
  display: grid;
  grid-template-columns: 1fr;
  gap: 1.5em;
  margin: 1.5em 0;
}}
@media (min-width: 700px) {{
  .charts {{ grid-template-columns: 1fr 1fr; }}
}}
.chart-box {{
  background: var(--card-bg);
  border-radius: 12px;
  padding: 1em;
  box-shadow: 0 1px 3px rgba(0,0,0,0.08);
}}

/* Timeline */
.section {{ margin: 2em 0; }}
.section h2 {{ font-size: 1.1rem; margin-bottom: 0.8em; }}
.timeline {{
  display: flex;
  flex-direction: column;
  gap: 0.6em;
}}
.timeline-item {{
  display: flex;
  align-items: flex-start;
  background: var(--card-bg);
  border-radius: 10px;
  padding: 0.8em 1em;
  box-shadow: 0 1px 3px rgba(0,0,0,0.05);
  border-left: 4px solid var(--primary);
  transition: transform 0.1s;
}}
.timeline-item:hover {{ transform: translateX(4px); }}
.timeline-time {{
  font-weight: 700;
  color: var(--primary);
  min-width: 50px;
  font-size: 0.9em;
  flex-shrink: 0;
}}
.timeline-content {{ flex: 1; margin-left: 10px; }}
.timeline-activity {{
  font-weight: 600;
  font-size: 0.95em;
}}
.timeline-apps {{
  color: var(--text-secondary);
  font-size: 0.8em;
  margin-top: 2px;
}}
.timeline-titles {{
  color: var(--text-secondary);
  font-size: 0.78em;
  margin-top: 2px;
  font-style: italic;
}}
.timeline-duration {{
  color: var(--text-secondary);
  font-size: 0.75em;
  margin-top: 4px;
}}

/* Suggestions */
.suggestions {{
  background: var(--card-bg);
  border-radius: 12px;
  padding: 1em 1.2em;
  box-shadow: 0 1px 3px rgba(0,0,0,0.08);
}}
.suggestions ul {{ margin: 0; padding-left: 1.2em; }}
.suggestions li {{ margin-bottom: 0.4em; }}

/* Loops */
.loops {{
  background: var(--card-bg);
  border-radius: 12px;
  padding: 1em 1.2em;
  box-shadow: 0 1px 3px rgba(0,0,0,0.08);
}}
.loop-item {{
  padding: 0.4em 0;
  color: var(--danger);
  font-size: 0.9em;
}}
.empty {{ color: var(--text-secondary); font-size: 0.9em; }}
</style>
</head>
<body>
<h1>🧠 AI Coach Dashboard</h1>
<div class="date">{target.isoformat()}</div>

<div class="cards">
  <div class="card">
    <div class="value">{analysis.effective_hours:.1f}h</div>
    <div class="label">有效工作</div>
  </div>
  <div class="card">
    <div class="value">{analysis.deep_work_hours:.1f}h</div>
    <div class="label">深度工作</div>
  </div>
  <div class="card">
    <div class="value">{analysis.focus_score}</div>
    <div class="label">专注得分</div>
  </div>
  <div class="card">
    <div class="value">{analysis.productivity_score}</div>
    <div class="label">生产力得分</div>
  </div>
  <div class="card danger">
    <div class="value">{analysis.switch_count}</div>
    <div class="label">任务切换</div>
  </div>
  <div class="card danger">
    <div class="value">{len(analysis.death_loops)}</div>
    <div class="label">切换循环</div>
  </div>
</div>

<div class="charts">
  <div class="chart-box"><canvas id="pieChart"></canvas></div>
  <div class="chart-box"><canvas id="lineChart"></canvas></div>
</div>

<div class="section">
  <h2>📅 时间段总结</h2>
  <div class="timeline">
    {timeline_html}
  </div>
</div>

<div class="section">
  <h2>🔄 切换循环</h2>
  <div class="loops">
    {death_loops_html}
  </div>
</div>

<div class="section">
  <h2>💡 建议</h2>
  <div class="suggestions">
    <ul>{suggestions_html if suggestions_html else "<li>今日表现不错，继续保持！</li>"}</ul>
  </div>
</div>

<script>
new Chart(document.getElementById('pieChart'), {{
  type: 'doughnut',
  data: {{
    labels: {labels_json},
    datasets: [{{
      data: {values_json},
      backgroundColor: [
        '#2563eb',
        '#f59e0b',
        '#10b981',
        '#8b5cf6',
        '#ef4444',
        '#6b7280'
      ],
      borderWidth: 0
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{
      title: {{ display: true, text: '时间分布 (小时)' }},
      legend: {{ position: 'bottom' }}
    }}
  }}
}});
new Chart(document.getElementById('lineChart'), {{
  type: 'line',
  data: {{
    labels: {hourly_labels_json},
    datasets: [{{
      label: '专注度',
      data: {hourly_values_json},
      borderColor: '#2563eb',
      backgroundColor: 'rgba(37,99,235,0.1)',
      tension: 0.3,
      fill: true
    }}]
  }},
  options: {{
    responsive: true,
    scales: {{ y: {{ min: 0, max: 100 }} }},
    plugins: {{ title: {{ display: true, text: '精力曲线' }} }}
  }}
}});
</script>
</body>
</html>"""

    web_dir = config.reports_dir / "web"
    web_dir.mkdir(parents=True, exist_ok=True)
    html_path = web_dir / "index.html"
    html_path.write_text(html, encoding="utf-8")
    return html_path
