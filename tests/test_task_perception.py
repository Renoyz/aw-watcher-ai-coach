"""Tests for task signals, fusion, and tracker."""

from __future__ import annotations

from datetime import datetime, timedelta

from aw_coach.config import TasksConfig
from aw_coach.enriched_state import SemanticWorkState
from aw_coach.task_fusion import TaskFusionEngine
from aw_coach.task_signals import TaskSignalExtractor
from aw_coach.task_tracker import TaskSessionTracker


def _state(**kwargs) -> SemanticWorkState:
    defaults = dict(
        updated_at=datetime.now(),
        current_app="Code",
        current_title="main.py - aw-coach",
        likely_mode="coding",
        activity_type="programming",
        semantic_project="aw-coach",
        semantic_filename="main.py",
    )
    defaults.update(kwargs)
    return SemanticWorkState(**defaults)


class TestTaskSignals:
    def test_file_and_project_task_id(self):
        ext = TaskSignalExtractor(TasksConfig())
        task = ext.extract(
            app="Code",
            title="scheduler.py - aw-coach",
            url=None,
            likely_mode="coding",
            activity_type="programming",
            project="aw-coach",
            filename="scheduler.py",
        )
        assert task.task_id == "aw-coach:scheduler.py"
        assert task.confidence >= 0.6

    def test_github_issue_url(self):
        ext = TaskSignalExtractor(TasksConfig())
        task = ext.extract(
            app="Chrome",
            title="Issue #42",
            url="https://github.com/org/repo/issues/42",
            likely_mode="researching",
            activity_type="research",
        )
        assert task.task_id == "github:repo#42"

    def test_ssh_remote_task_signal(self, monkeypatch):
        monkeypatch.setattr(
            "aw_coach.task_signals.socket.gethostname", lambda: "local-laptop"
        )
        cfg = TasksConfig(aliases={"ubuntu": "机器人主控"})
        ext = TaskSignalExtractor(cfg)
        task = ext.extract(
            app="gnome-terminal",
            title="sunrise@ubuntu: ~/x_system",
            url=None,
            likely_mode="terminal",
            activity_type="programming",
        )
        assert task.task_id == "ssh:ubuntu:x_system"
        assert task.label == "x_system@机器人主控"
        assert task.confidence == 0.7

    def test_local_prompt_no_ssh_signal(self, monkeypatch):
        monkeypatch.setattr(
            "aw_coach.task_signals.socket.gethostname", lambda: "local-laptop"
        )
        ext = TaskSignalExtractor(TasksConfig())
        task = ext.extract(
            app="gnome-terminal",
            title="yz@local-laptop: ~/projects",
            url=None,
            likely_mode="terminal",
            activity_type="programming",
        )
        assert not task.task_id.startswith("ssh:")

    def test_browser_webmail_no_ssh_signal(self):
        ext = TaskSignalExtractor(TasksConfig())
        task = ext.extract(
            app="firefox",
            title="yz@gmail.com: Re: 会议 — Mozilla Firefox",
            url=None,
            likely_mode="browsing",
            activity_type="admin",
        )
        assert not task.task_id.startswith("ssh:")


class TestTaskFusion:
    def test_hysteresis_prevents_jitter_split(self):
        from aw_coach.task_models import WorkTask

        engine = TaskFusionEngine()
        t1 = WorkTask("aw-coach:main.py", "main.py", "aw-coach", "implement", 0.65)
        r1 = engine.resolve(t1)
        assert r1.task_id == "aw-coach:main.py"

        t2 = WorkTask("other-repo:api", "other-repo API", "other-repo", "implement", 0.8)
        r2 = engine.resolve(t2)
        assert r2.task_id == "aw-coach:main.py"  # kept due to hysteresis

        r3 = engine.resolve(t2)
        assert r3.task_id == "other-repo:api"  # second cycle confirms switch


class TestTaskTracker:
    def test_tracks_session_duration(self):
        tracker = TaskSessionTracker()
        now = datetime(2026, 6, 11, 10, 0)
        from aw_coach.task_models import WorkTask

        task = WorkTask("aw-coach:main.py", "main.py", "aw-coach", "implement", 0.7)
        session = tracker.update(task, _state(), now)
        later = now + timedelta(minutes=2)
        tracker.update(task, _state(), later)
        assert session.accumulated_sec >= 60
