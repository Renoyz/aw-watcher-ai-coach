"""HTML dashboard rendering from package templates."""

from __future__ import annotations

import html as html_lib
from datetime import date
from importlib import resources
from pathlib import Path

from aw_coach.analyzer import AnalysisResult
from aw_coach.reports.markdown import ReportGenerator
from aw_coach.web.helpers import (
    build_hourly_timeline,
    build_slice_timeline,
    render_death_loops,
    render_hourly_timeline,
    render_slice_timeline,
    render_suggestions,
    safe_json,
)


def render_template(name: str, context: dict) -> str:
    template = (
        resources.files("aw_coach")
        .joinpath("web", "templates", name)
        .read_text(encoding="utf-8")
    )
    for key, value in context.items():
        template = template.replace("{{ " + key + " }}", str(value))
    return template


def dashboard_html(
    config: object,
    target: date,
    analysis: AnalysisResult,
    slices=None,
    rules=None,
    interactive: bool = False,
) -> str:
    suggestions = ReportGenerator(config)._generate_suggestions(analysis, is_weekly=False)
    timeline_items = (
        build_slice_timeline(slices, rules)
        if interactive
        else build_hourly_timeline(slices, rules)
    )
    timeline_html = (
        render_slice_timeline(timeline_items, interactive=True)
        if interactive
        else render_hourly_timeline(timeline_items)
    )

    breakdown = analysis.activity_breakdown
    correction_panel = (
        '<div id="correction-status" class="correction-status"></div>'
        if interactive
        else ""
    )
    ai_summary_hint = (
        "基于今日活动、切换循环与近期纠错生成。"
        if interactive
        else "静态页面不调用 LLM；运行 aw-coach serve 后可生成。"
    )
    ai_summary_placeholder = (
        "点击按钮后会生成本次工作总结。"
        if interactive
        else "本页为静态只读面板。"
    )
    interactive_script = _interactive_script() if interactive else ""

    return render_template(
        "dashboard.html",
        {
            "target_date": target.isoformat(),
            "effective_hours": f"{analysis.effective_hours:.1f}",
            "deep_work_hours": f"{analysis.deep_work_hours:.1f}",
            "focus_score": analysis.focus_score,
            "productivity_score": analysis.productivity_score,
            "switch_count": analysis.switch_count,
            "death_loop_count": len(analysis.death_loops),
            "labels_json": safe_json(list(breakdown.keys())),
            "values_json": safe_json([round(v, 2) for v in breakdown.values()]),
            "timeline_html": timeline_html,
            "death_loops_html": render_death_loops(analysis.death_loops),
            "suggestions_html": render_suggestions(suggestions),
            "correction_panel": correction_panel,
            "ai_summary_hint": ai_summary_hint,
            "ai_summary_disabled": "" if interactive else "disabled",
            "ai_summary_placeholder": ai_summary_placeholder,
            "interactive_script": interactive_script,
        },
    )


def generate_html_dashboard(
    config: object,
    target: date,
    analysis: AnalysisResult,
    slices=None,
    rules=None,
) -> Path:
    html = dashboard_html(config, target, analysis, slices=slices, rules=rules)
    web_dir = config.reports_dir / "web"
    web_dir.mkdir(parents=True, exist_ok=True)
    html_path = web_dir / "index.html"
    html_path.write_text(html, encoding="utf-8")
    return html_path


def generate_report_page(config: object, target: date, markdown: str) -> Path:
    html = render_template(
        "report.html",
        {
            "target_date": target.isoformat(),
            "report_markdown": html_lib.escape(markdown, quote=True),
        },
    )
    report_dir = config.reports_dir / "web" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    html_path = report_dir / f"{target.isoformat()}.html"
    html_path.write_text(html, encoding="utf-8")
    return html_path


def _interactive_script() -> str:
    return """
const validTypes = [
  "programming", "writing", "meeting", "research",
  "design", "entertainment", "admin", "social"
];
const statusBox = document.getElementById("correction-status");
const summaryButton = document.getElementById("ai-summary-button");
const summaryStatus = document.getElementById("ai-summary-status");
const summaryOutput = document.getElementById("ai-summary-output");

function showStatus(message, isError = false) {
  if (!statusBox) return;
  statusBox.textContent = message;
  statusBox.className = isError ? "correction-status error" : "correction-status";
}

function showSummaryStatus(message, isError = false) {
  if (!summaryStatus) return;
  summaryStatus.textContent = message;
  summaryStatus.className = isError ? "summary-status error" : "summary-status";
}

function renderSummary(payload) {
  if (!summaryOutput) return;
  summaryOutput.replaceChildren();
  if (payload.summary) {
    summaryOutput.textContent = payload.summary;
    return;
  }
  if (Array.isArray(payload.suggestions) && payload.suggestions.length > 0) {
    const list = document.createElement("ul");
    payload.suggestions.forEach((item) => {
      const li = document.createElement("li");
      li.textContent = item;
      list.appendChild(li);
    });
    summaryOutput.appendChild(list);
    return;
  }
  summaryOutput.textContent = "暂无总结。";
}

if (summaryButton) {
  summaryButton.addEventListener("click", async () => {
    summaryButton.disabled = true;
    showSummaryStatus("生成中...");
    try {
      const response = await fetch("/api/summary", {method: "POST"});
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        showSummaryStatus(payload.error || "生成失败", true);
        return;
      }
      renderSummary(payload);
      showSummaryStatus("已生成");
    } catch (error) {
      showSummaryStatus(error.message || "生成失败", true);
    } finally {
      summaryButton.disabled = false;
    }
  });
}

document.querySelectorAll(".timeline-clickable").forEach((item) => {
  item.addEventListener("click", async () => {
    const current = item.dataset.activity || "unknown";
    const next = window.prompt(`Correct classification (${validTypes.join(", ")})`, current);
    if (!next) return;
    if (!validTypes.includes(next)) {
      showStatus(`Invalid type: ${next}`, true);
      return;
    }
    const response = await fetch("/api/corrections", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({slice_id: item.dataset.sliceId, corrected_type: next})
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      showStatus(payload.error || "Correction failed", true);
      return;
    }
    item.dataset.activity = next;
    const activity = item.querySelector(".timeline-activity");
    if (activity) activity.firstChild.textContent = next + " ";
    showStatus(`Saved correction: ${payload.app} -> ${next}`);
  });
});
"""
