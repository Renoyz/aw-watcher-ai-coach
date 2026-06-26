"""Tests for daily background insights."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from aw_coach.analyzer import AnalysisResult
from aw_coach.daily_insights import generate_daily_insights, render_daily_insights
from aw_coach.storage import Storage
from aw_coach.task_models import TaskEvidence, TaskSession


def _analysis(**kwargs):
    defaults = dict(
        total_hours=2.0,
        effective_hours=1.5,
        deep_work_hours=0.5,
        focus_score=60,
        switch_count=5,
        activity_breakdown={"programming": 0.5, "research": 1.0},
        hourly_scores=[],
    )
    defaults.update(kwargs)
    return AnalysisResult(**defaults)


def _save_session(storage, **kwargs):
    defaults = dict(
        task_id="aw-coach:main",
        label="main.py",
        project="aw-coach",
        intent="implement",
        started_at=datetime(2026, 6, 23, 9, 0),
        ended_at=datetime(2026, 6, 23, 9, 30),
        accumulated_sec=1800,
        outcome="progressed",
        confidence=0.8,
    )
    defaults.update(kwargs)
    storage.upsert_task_session(TaskSession(**defaults))


def test_fragmented_main_task_insight(tmp_path):
    storage = Storage(tmp_path / "coach.db")
    start = datetime(2026, 6, 23, 9, 0)
    for idx in range(4):
        _save_session(
            storage,
            started_at=start + timedelta(minutes=idx * 20),
            ended_at=start + timedelta(minutes=idx * 20 + 15),
            accumulated_sec=900,
        )

    insights = generate_daily_insights(date(2026, 6, 23), storage)

    assert any(item.kind == "fragmented_main_task" for item in insights)


def test_recovery_cost_insight(tmp_path):
    storage = Storage(tmp_path / "coach.db")
    _save_session(
        storage,
        task_id="meeting:sync",
        label="daily sync",
        intent="meeting",
        started_at=datetime(2026, 6, 23, 10, 0),
        ended_at=datetime(2026, 6, 23, 10, 30),
        accumulated_sec=1800,
    )
    _save_session(
        storage,
        task_id="aw-coach:main",
        label="main.py",
        intent="implement",
        started_at=datetime(2026, 6, 23, 11, 0),
        ended_at=datetime(2026, 6, 23, 11, 30),
        accumulated_sec=1800,
    )

    insights = generate_daily_insights(date(2026, 6, 23), storage)

    assert any(item.kind == "recovery_cost" for item in insights)


def test_pseudo_progress_insight(tmp_path):
    storage = Storage(tmp_path / "coach.db")
    _save_session(
        storage,
        task_id="research:memory",
        label="memory research",
        intent="research",
        started_at=datetime(2026, 6, 23, 9, 0),
        ended_at=datetime(2026, 6, 23, 10, 10),
        accumulated_sec=4200,
    )
    _save_session(
        storage,
        task_id="aw-coach:main",
        label="main.py",
        intent="implement",
        started_at=datetime(2026, 6, 23, 10, 15),
        ended_at=datetime(2026, 6, 23, 10, 35),
        accumulated_sec=1200,
    )

    insights = generate_daily_insights(date(2026, 6, 23), storage, _analysis())

    assert any(item.kind == "pseudo_progress" for item in insights)


def test_productive_closure_insight(tmp_path):
    storage = Storage(tmp_path / "coach.db")
    storage.upsert_task_session(
        TaskSession(
            task_id="aw-coach:ledger",
            label="task ledger",
            project="aw-coach",
            intent="implement",
            started_at=datetime(2026, 6, 23, 9, 0),
            ended_at=datetime(2026, 6, 23, 10, 0),
            accumulated_sec=3600,
            evidence=[TaskEvidence("git", "feature/task-ledger", 0.8)],
            source={"git_branch": "feature/task-ledger"},
        )
    )

    insights = generate_daily_insights(date(2026, 6, 23), storage)

    assert any(item.kind == "productive_closure" for item in insights)


def test_ignored_prompt_insight(tmp_path):
    storage = Storage(tmp_path / "coach.db")
    for idx in range(3):
        storage._conn.execute(
            "INSERT INTO delivery_log (timestamp, kind, channel, status) "
            "VALUES (?, ?, ?, ?)",
            (f"2026-06-23T1{idx}:00:00", "task_confirm", "inbox", "sent"),
        )
    storage._conn.commit()
    _save_session(storage)

    insights = generate_daily_insights(date(2026, 6, 23), storage)

    assert any(item.kind == "ignored_prompt" for item in insights)


def test_render_daily_insights_omits_empty_section():
    assert render_daily_insights([]) == ""
