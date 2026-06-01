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
    hourly = analysis.hourly_scores
    correction_panel = (
        '<div id="correction-status" class="correction-status"></div>'
        if interactive
        else ""
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
            "hourly_labels_json": safe_json([f"{h:02d}:00" for h, _ in hourly]),
            "hourly_values_json": safe_json([score for _, score in hourly]),
            "timeline_html": timeline_html,
            "death_loops_html": render_death_loops(analysis.death_loops),
            "suggestions_html": render_suggestions(suggestions),
            "correction_panel": correction_panel,
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

function showStatus(message, isError = false) {
  if (!statusBox) return;
  statusBox.textContent = message;
  statusBox.className = isError ? "correction-status error" : "correction-status";
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
