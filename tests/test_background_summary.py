"""Tests for background summary silent logic."""

from __future__ import annotations

from concurrent.futures import Future
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Optional

from aw_coach.analyzer import AnalysisResult
from aw_coach.background_summary import build_background_summary_prompt, should_silent_summary
from aw_coach.config import (
    Config,
    ContextCaptureConfig,
    DeliveryConfig,
    PolicyConfig,
    ReportConfig,
    TasksConfig,
)
from aw_coach.notification_gate import NotificationGate
from aw_coach.scheduler import CoachScheduler
from aw_coach.storage import Storage
from aw_coach.task_models import WorkTask
from aw_coach.task_tracker import TaskSessionTracker


def _analysis(**kwargs) -> AnalysisResult:
    defaults = dict(
        total_hours=1.0,
        effective_hours=0.2,
        deep_work_hours=0.1,
        focus_score=50,
        productivity_score=50,
        switch_count=3,
        activity_breakdown={"programming": 0.2},
        hourly_scores=[],
        death_loops=[],
    )
    defaults.update(kwargs)
    return AnalysisResult(**defaults)


class TestBackgroundSummary:
    def test_silent_when_low_activity(self):
        config = Config()
        assert should_silent_summary(_analysis(effective_hours=0.1), config) is True

    def test_not_silent_with_death_loops(self):
        config = Config()
        assert should_silent_summary(
            _analysis(effective_hours=0.1, death_loops=[{"apps": ["a", "b"]}]),
            config,
        ) is False

    def test_not_silent_with_signal_override(self):
        config = Config()
        assert should_silent_summary(
            _analysis(effective_hours=0.1),
            config,
            active_signals=["stuck"],
        ) is False

    def test_prompt_includes_task_sessions(self):
        from aw_coach.task_models import TaskSession

        session = TaskSession(
            task_id="aw-coach:main",
            label="main.py",
            project="aw-coach",
            intent="implement",
            started_at=__import__("datetime").datetime.now(),
            accumulated_sec=3600,
        )
        prompt = build_background_summary_prompt(
            _analysis(effective_hours=2.0),
            task_sessions=[session],
        )
        assert "main.py" in prompt
        assert "任务会话" in prompt

    def test_fallback_summary_keeps_notification_preference(self):
        scheduler = CoachScheduler.__new__(CoachScheduler)
        future = Future()
        future.set_result(None)
        scheduler._summary_future = future
        scheduler._pending_summary_delivery = {
            "kind": "summary",
            "title": "AI Coach 摘要",
            "fallback_body": "fallback",
            "now": datetime(2026, 6, 17, 10, 0),
            "detail_url": "http://127.0.0.1:8899/",
            "silent": False,
            "prefer_notify": True,
        }
        delivered = []

        def fake_deliver(kind, title, body, *, now, detail_url=None, prefer_notify=True):
            delivered.append((kind, title, body, now, detail_url, prefer_notify))

        scheduler._deliver_summary = fake_deliver

        scheduler._poll_summary_future()

        assert delivered == [
            (
                "summary",
                "AI Coach 摘要",
                "fallback",
                datetime(2026, 6, 17, 10, 0),
                "http://127.0.0.1:8899/",
                True,
            )
        ]
        assert scheduler._summary_future is None
        assert scheduler._pending_summary_delivery is None

    def test_background_summary_timeout_delivers_fallback(self):
        scheduler = CoachScheduler.__new__(CoachScheduler)
        future = Future()
        scheduler._summary_future = future
        scheduler.config = SimpleNamespace(report=ReportConfig(llm_timeout_seconds=1))
        now = datetime(2026, 6, 17, 10, 0)
        scheduler._pending_summary_delivery = {
            "kind": "summary",
            "title": "AI Coach 摘要",
            "fallback_body": "fallback",
            "now": now,
            "detail_url": None,
            "silent": False,
            "prefer_notify": None,
            "submitted_at": datetime.now() - timedelta(seconds=2),
            "timeout_seconds": 1,
        }
        delivered = []

        def fake_deliver(kind, title, body, *, now, detail_url=None, prefer_notify=None):
            delivered.append((kind, title, body, prefer_notify))

        scheduler._deliver_summary = fake_deliver

        scheduler._poll_summary_future()

        assert delivered == [("summary", "AI Coach 摘要", "fallback", None)]
        assert scheduler._summary_future is None
        assert scheduler._pending_summary_delivery is None

    def test_summary_notify_success_records_delivery_without_inbox(self, tmp_path, monkeypatch):
        scheduler = _scheduler_for_delivery(tmp_path)
        sent = []
        monkeypatch.setattr(
            "aw_coach.scheduler.send_notification",
            lambda title, body, detail_url=None: sent.append((title, body)) or True,
        )

        scheduler._deliver_summary(
            "summary",
            "AI Coach 摘要",
            "body",
            now=datetime(2026, 6, 17, 10, 0),
        )

        assert sent == [("AI Coach 摘要", "body")]
        assert scheduler.storage.get_inbox_items() == []
        logs = scheduler.storage.get_recent_delivery_logs()
        assert logs[0]["kind"] == "summary"
        assert logs[0]["channel"] == "notify"
        assert logs[0]["status"] == "sent"

    def test_budget_exempt_summary_does_not_block_daily_report(
        self, tmp_path, monkeypatch
    ):
        scheduler = _scheduler_for_delivery(tmp_path)
        monkeypatch.setattr(
            "aw_coach.scheduler.send_notification",
            lambda title, body, detail_url=None: True,
        )
        now = datetime(2026, 6, 17, 10, 0)

        for idx in range(8):
            scheduler._deliver_summary(
                "summary",
                f"AI Coach 摘要 {idx}",
                "body",
                now=now + timedelta(hours=idx),
            )
        scheduler._deliver_summary(
            "daily_report",
            "AI Coach 日报",
            "body",
            now=now + timedelta(hours=9),
        )

        logs = scheduler.storage.get_recent_delivery_logs(limit=1)
        assert logs[0]["kind"] == "daily_report"
        assert logs[0]["channel"] == "notify"
        assert logs[0]["status"] == "sent"
        assert scheduler._notification_gate.notifications_today == 0

    def test_notify_suppression_falls_back_to_inbox(self, tmp_path):
        scheduler = _scheduler_for_delivery(
            tmp_path,
            policy=PolicyConfig(quiet_hours_start="22:00", quiet_hours_end="08:00"),
        )

        scheduler._deliver_summary(
            "daily_report",
            "AI Coach 日报",
            "body",
            now=datetime(2026, 6, 17, 23, 0),
        )

        inbox = scheduler.storage.get_inbox_items()
        assert len(inbox) == 1
        assert "quiet_hours" in inbox[0]["reason"]
        logs = scheduler.storage.get_recent_delivery_logs(limit=2)
        assert logs[0]["channel"] == "inbox"
        assert logs[0]["status"] == "sent"
        assert logs[1]["channel"] == "notify"
        assert logs[1]["status"] == "suppressed"

    def test_classify_slices_rule_only_skips_classifier(self):
        scheduler = CoachScheduler.__new__(CoachScheduler)
        scheduler.classifier = SimpleNamespace(
            batch_classify=lambda slices: (_ for _ in ()).throw(AssertionError())
        )

        class Engine:
            def classify(self, app, title, url):
                return SimpleNamespace(activity_type="programming", confidence=1.0)

        scheduler._get_rule_engine = lambda: Engine()
        slices = [SimpleNamespace(primary_app="Code", primary_title="main.py", web_url=None)]

        results = scheduler._classify_slices(slices, allow_llm=False)

        assert results[0].activity_type == "programming"

    def test_task_confirm_requires_duration_and_daily_budget(self, tmp_path):
        delivery = DeliveryConfig(task_confirm_min_minutes=10, task_confirm_daily_limit=1)
        scheduler = _scheduler_for_delivery(
            tmp_path,
            report=ReportConfig(delivery=delivery),
        )
        delivered = []
        scheduler._deliver_message = lambda **kwargs: delivered.append(kwargs) or {
            "notified": False,
            "inbox": True,
        }
        state = SimpleNamespace(task_id="unknown", task_label="unknown")
        start = datetime(2026, 6, 17, 10, 0)

        scheduler._maybe_queue_task_confirm_inbox(state, start)
        scheduler._maybe_queue_task_confirm_inbox(state, start + timedelta(minutes=9))
        scheduler._maybe_queue_task_confirm_inbox(state, start + timedelta(minutes=10))
        scheduler._maybe_queue_task_confirm_inbox(
            SimpleNamespace(task_id="other", task_label="other"),
            start + timedelta(minutes=30),
        )

        assert len(delivered) == 1
        assert delivered[0]["kind"] == "task_confirm"

    def test_partial_hour_shutdown_uses_distinct_event_type(self, tmp_path, monkeypatch):
        scheduler = CoachScheduler.__new__(CoachScheduler)
        called = []

        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                value = datetime(2026, 6, 17, 10, 5, 0)
                return value if tz is None else value.replace(tzinfo=tz)

        monkeypatch.setattr("aw_coach.scheduler.datetime", FixedDateTime)
        scheduler._save_last_summary = lambda now: None
        scheduler._hourly_analyze = lambda start, end, **kwargs: called.append(
            (start, end, kwargs)
        ) or True
        scheduler._summary_executor = SimpleNamespace(
            shutdown=lambda wait=False, cancel_futures=True: None
        )

        scheduler._shutdown(None, None)

        assert called
        assert called[0][2]["allow_llm"] is False
        assert called[0][2]["event_type"] == "partial_hour_analysis"

    def test_context_snapshot_written_to_context_bucket(self, monkeypatch):
        scheduler = CoachScheduler.__new__(CoachScheduler)
        scheduler.config = SimpleNamespace(
            context_capture=ContextCaptureConfig(interval_seconds=60)
        )
        scheduler._context_bucket_created = False
        scheduler._last_context_capture = None
        inserted = []
        created = []

        class Client:
            def create_bucket(self, bucket_id, event_type, queued=False):
                created.append((bucket_id, event_type, queued))

            def insert_event(self, bucket_id, event):
                inserted.append((bucket_id, event))

        scheduler._collector = SimpleNamespace(
            hostname="host",
            client=Client(),
        )
        snapshot = SimpleNamespace(
            process_name="pytest",
            process_cwd="/repo",
            git_repo="aw-coach",
            git_branch="feat/context",
            terminal_command_summary="pytest tests",
            terminal_action="testing",
        )
        monkeypatch.setattr(
            "aw_coach.process_context.capture_process_context",
            lambda **kwargs: snapshot,
        )
        latest = SimpleNamespace(primary_app="gnome-terminal")

        result = scheduler._capture_context_snapshot(
            datetime(2026, 6, 17, 10, 0),
            latest,
        )

        assert result is snapshot
        assert created[0][0] == "aw-coach-context_host"
        assert inserted[0][0] == "aw-coach-context_host"
        assert inserted[0][1].data["type"] == "context_snapshot"
        assert inserted[0][1].data["git_repo"] == "aw-coach"

    def test_task_tracker_state_persists_through_scheduler_state(self, tmp_path):
        scheduler = CoachScheduler.__new__(CoachScheduler)
        scheduler.config = SimpleNamespace(
            tasks=TasksConfig(enabled=True),
            db_path=tmp_path / "coach.db",
        )
        scheduler._storage = Storage(scheduler.config.db_path)
        tracker = TaskSessionTracker()
        start = datetime(2026, 6, 17, 10, 0)
        task = WorkTask("aw-coach:main", "main", "aw-coach", "implement", 0.8)
        state = SimpleNamespace(
            likely_mode="coding",
            detected_signal=None,
            risk_level="normal",
        )
        tracker.update(task, state, start)
        scheduler._task_tracker = tracker

        scheduler._persist_task_tracker()

        scheduler._task_tracker = None
        restored = scheduler._restore_task_tracker()

        assert restored.current_session is not None
        assert restored.current_session.task_id == "aw-coach:main"
        assert restored.current_session.started_at == start


def _scheduler_for_delivery(
    tmp_path,
    *,
    report: Optional[ReportConfig] = None,
    policy: Optional[PolicyConfig] = None,
):
    scheduler = CoachScheduler.__new__(CoachScheduler)
    config = SimpleNamespace(
        report=report or ReportConfig(),
        policy=policy or PolicyConfig(),
        db_path=tmp_path / "coach.db",
        reports_dir=tmp_path / "reports",
    )
    scheduler.config = config
    scheduler._storage = Storage(config.db_path)
    scheduler._notification_gate = NotificationGate.from_config(config)
    scheduler._task_confirm_candidates = {}
    scheduler._task_confirm_count_date = None
    scheduler._task_confirm_count = 0
    return scheduler
