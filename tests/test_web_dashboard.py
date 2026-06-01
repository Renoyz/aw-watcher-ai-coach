"""Tests for templated dashboard and interactive correction server."""

import json
import sqlite3
from datetime import date, datetime, timedelta
from importlib import resources
from types import SimpleNamespace
from unittest.mock import patch
from urllib import request

from aw_coach.analyzer import AnalysisResult
from aw_coach.collector import ActivitySlice, DataCollector
from aw_coach.config import AnalysisConfig
from aw_coach.rules.engine import RuleResult
from aw_coach.web.dashboard import dashboard_html, generate_html_dashboard
from aw_coach.web.server import InteractiveReportServer


def _analysis():
    return AnalysisResult(
        total_hours=1.0,
        effective_hours=0.8,
        deep_work_hours=0.5,
        focus_score=72,
        switch_count=3,
        activity_breakdown={"programming": 0.8, "research": 0.2},
        hourly_scores=[(9, 72)],
    )


def _slice(app="chrome", title="Some page"):
    start = datetime(2026, 5, 30, 9, 0)
    return ActivitySlice(
        start=start,
        end=start + timedelta(minutes=15),
        duration=900,
        is_afk=False,
        primary_app=app,
        primary_title=title,
        web_url="https://example.com",
    )


def _rule(activity_type="research", confidence=0.5):
    return RuleResult(activity_type, confidence, "rule_app_fuzzy")


def test_dashboard_templates_are_package_data():
    template_dir = resources.files("aw_coach").joinpath("web", "templates")

    assert template_dir.joinpath("dashboard.html").is_file()
    assert template_dir.joinpath("report.html").is_file()


def test_generate_html_dashboard_uses_template(tmp_path):
    cfg = SimpleNamespace(reports_dir=tmp_path)

    path = generate_html_dashboard(
        cfg,
        date(2026, 5, 30),
        _analysis(),
        slices=[_slice("Code", "main.py")],
        rules=[_rule("programming", 0.9)],
    )

    html = path.read_text(encoding="utf-8")
    assert path.name == "index.html"
    assert "AI Coach Dashboard" in html
    assert "main.py" in html
    assert "{{ target_date }}" not in html


def test_interactive_dashboard_contains_correction_script():
    cfg = SimpleNamespace(reports_dir=None)

    html = dashboard_html(
        cfg,
        date(2026, 5, 30),
        _analysis(),
        slices=[_slice()],
        rules=[_rule()],
        interactive=True,
    )

    assert "/api/corrections" in html
    assert "timeline-clickable" in html


def test_interactive_server_correction_api_records_to_storage(tmp_path):
    cfg = SimpleNamespace(
        db_path=tmp_path / "coach.db",
        reports_dir=tmp_path,
        analysis=AnalysisConfig(),
    )
    slices = [_slice()]

    with patch.object(DataCollector, "__init__", lambda self, **kw: None), \
         patch.object(DataCollector, "fetch_range", return_value=slices):
        server = InteractiveReportServer(cfg, date(2026, 5, 30), port=0)
        server.start()
        try:
            payload = json.dumps({
                "slice_id": "0",
                "corrected_type": "programming",
            }).encode("utf-8")
            req = request.Request(
                f"{server.url}api/corrections",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with request.urlopen(req, timeout=5) as response:
                body = json.loads(response.read().decode("utf-8"))
        finally:
            server.stop()

    assert body["ok"] is True
    conn = sqlite3.connect(cfg.db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM corrections").fetchone()
    assert row["app"] == "chrome"
    assert row["original_type"] == "research"
    assert row["corrected_type"] == "programming"
