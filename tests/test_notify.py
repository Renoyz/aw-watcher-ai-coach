"""Tests for notification and scheduler modules."""

import json
from datetime import datetime
from unittest.mock import patch

from aw_coach.config import Config
from aw_coach.notify import send_notification
from aw_coach.scheduler import CoachScheduler


class TestNotify:
    @patch("aw_coach.notify._dbus_notify", return_value=None)
    @patch("aw_coach.notify.subprocess.run")
    @patch("aw_coach.notify.platform.system", return_value="Linux")
    def test_linux_notification(self, mock_system, mock_run, mock_dbus):
        result = send_notification("Test Title", "Test Body")
        assert result is True
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "notify-send"
        assert "Test Title" in args
        assert "Test Body" in args

    @patch("aw_coach.notify.subprocess.run")
    @patch("aw_coach.notify.platform.system", return_value="Darwin")
    def test_macos_notification(self, mock_system, mock_run):
        result = send_notification("Title", "Body")
        assert result is True
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "osascript"

    @patch("aw_coach.notify._dbus_notify", return_value=None)
    @patch("aw_coach.notify.platform.system", return_value="Linux")
    @patch("aw_coach.notify.subprocess.run", side_effect=FileNotFoundError)
    def test_notification_graceful_failure(self, mock_run, mock_system, mock_dbus):
        result = send_notification("Title", "Body")
        assert result is False


def test_scheduler_service_health_records_running_state(tmp_path, monkeypatch):
    monkeypatch.setattr("aw_coach.config.DEFAULT_DATA_DIR", tmp_path)
    cfg = Config(db_path=tmp_path / "coach.db")
    scheduler = CoachScheduler(cfg)

    now = datetime(2026, 6, 14, 10, 0, 0)
    scheduler._started_at = now
    scheduler._write_service_health(now, status="running")

    payload = json.loads(scheduler.storage.get_scheduler_state("service_health"))

    assert payload["schema_version"] == 1
    assert payload["status"] == "running"
    assert isinstance(payload["pid"], int)
    assert payload["last_tick"] == now.isoformat()
    assert payload["last_success"] == now.isoformat()
    assert payload["last_error"] is None


def test_scheduler_service_health_records_errors(tmp_path, monkeypatch):
    monkeypatch.setattr("aw_coach.config.DEFAULT_DATA_DIR", tmp_path)
    cfg = Config(db_path=tmp_path / "coach.db")
    scheduler = CoachScheduler(cfg)

    started = datetime(2026, 6, 14, 10, 0, 0)
    failed = datetime(2026, 6, 14, 10, 1, 0)
    scheduler._started_at = started
    scheduler._write_service_health(started, status="running")
    scheduler._write_service_health(failed, status="running", error=RuntimeError("boom"))

    payload = json.loads(scheduler.storage.get_scheduler_state("service_health"))

    assert payload["status"] == "running"
    assert payload["last_tick"] == failed.isoformat()
    assert payload["last_success"] == started.isoformat()
    assert payload["last_error"] == "boom"
    assert payload["last_error_at"] == failed.isoformat()


def test_scheduler_shutdown_records_stopped_state(tmp_path, monkeypatch):
    monkeypatch.setattr("aw_coach.config.DEFAULT_DATA_DIR", tmp_path)
    cfg = Config(db_path=tmp_path / "coach.db")
    scheduler = CoachScheduler(cfg)

    monkeypatch.setattr(scheduler, "_hourly_analyze", lambda _start, _end: True)
    scheduler._shutdown(None, None)

    payload = json.loads(scheduler.storage.get_scheduler_state("service_health"))
    assert payload["status"] == "stopped"


def test_scheduler_service_health_write_failure_is_tolerated(tmp_path, monkeypatch):
    monkeypatch.setattr("aw_coach.config.DEFAULT_DATA_DIR", tmp_path)
    cfg = Config(db_path=tmp_path / "coach.db")
    scheduler = CoachScheduler(cfg)

    now = datetime(2026, 6, 14, 10, 0, 0)
    scheduler._started_at = now
    monkeypatch.setattr(
        scheduler.storage,
        "set_scheduler_state",
        lambda key, value: (_ for _ in ()).throw(RuntimeError("db locked")),
    )

    scheduler._write_service_health(now, status="running")


def test_scheduler_service_health_malformed_previous_payload_is_tolerated(tmp_path, monkeypatch):
    monkeypatch.setattr("aw_coach.config.DEFAULT_DATA_DIR", tmp_path)
    cfg = Config(db_path=tmp_path / "coach.db")
    scheduler = CoachScheduler(cfg)

    now = datetime(2026, 6, 14, 10, 0, 0)
    scheduler._started_at = now
    values = {}

    def fake_set_scheduler_state(key, value):
        values[key] = value

    monkeypatch.setattr(
        scheduler.storage,
        "get_scheduler_state",
        lambda key, default=None: "{not-json",
    )
    monkeypatch.setattr(scheduler.storage, "set_scheduler_state", fake_set_scheduler_state)

    scheduler._write_service_health(now, status="running")

    payload = json.loads(values["service_health"])
    assert payload["schema_version"] == 1
    assert payload["status"] == "running"
    assert payload["last_tick"] == now.isoformat()


def test_scheduler_run_records_loop_error_in_service_health(tmp_path, monkeypatch):
    monkeypatch.setattr("aw_coach.config.DEFAULT_DATA_DIR", tmp_path)
    cfg = Config(db_path=tmp_path / "coach.db")
    scheduler = CoachScheduler(cfg)

    monkeypatch.setattr(scheduler, "_update_semantic_state", lambda _now: (_ for _ in ()).throw(
        RuntimeError("loop boom")
    ))
    monkeypatch.setattr(
        "aw_coach.scheduler.time.sleep",
        lambda _seconds: setattr(scheduler, "_running", False),
    )

    scheduler.run()

    payload = json.loads(scheduler.storage.get_scheduler_state("service_health"))
    assert payload["status"] == "running"
    assert payload["last_error"] == "loop boom"
    assert payload["last_error_at"] is not None
