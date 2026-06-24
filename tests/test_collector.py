"""Tests for DataCollector - merge logic and slice generation."""

from datetime import datetime, timedelta

from aw_coach.collector import ActivitySlice, DataCollector, merge_events
from aw_coach.git_context import GitContext


def _make_window_event(ts: datetime, duration_sec: float, app: str, title: str):
    """Helper to create a mock window event."""
    return {
        "timestamp": ts,
        "duration": timedelta(seconds=duration_sec),
        "data": {"app": app, "title": title},
    }


def _make_afk_event(ts: datetime, duration_sec: float, status: str = "not-afk"):
    """Helper to create a mock AFK event."""
    return {
        "timestamp": ts,
        "duration": timedelta(seconds=duration_sec),
        "data": {"status": status},
    }


class TestMergeEvents:
    def test_single_window_not_afk(self):
        """Single window event during not-afk period produces one slice."""
        t0 = datetime(2026, 5, 30, 9, 0)
        windows = [_make_window_event(t0, 300, "vscode", "main.py - project")]
        afk = [_make_afk_event(t0, 300, "not-afk")]

        slices = merge_events(windows, afk)

        assert len(slices) == 1
        assert slices[0].primary_app == "vscode"
        assert slices[0].primary_title == "main.py - project"
        assert slices[0].is_afk is False
        assert slices[0].duration == 300

    def test_single_window_afk(self):
        """Window event during AFK period is marked as AFK."""
        t0 = datetime(2026, 5, 30, 9, 0)
        windows = [_make_window_event(t0, 300, "vscode", "main.py")]
        afk = [_make_afk_event(t0, 300, "afk")]

        slices = merge_events(windows, afk)

        assert len(slices) == 1
        assert slices[0].is_afk is True

    def test_multiple_consecutive_apps(self):
        """Multiple consecutive window events produce multiple slices."""
        t0 = datetime(2026, 5, 30, 9, 0)
        windows = [
            _make_window_event(t0, 600, "vscode", "main.py"),
            _make_window_event(t0 + timedelta(seconds=600), 300, "chrome", "Google"),
        ]
        afk = [_make_afk_event(t0, 900, "not-afk")]

        slices = merge_events(windows, afk)

        assert len(slices) == 2
        assert slices[0].primary_app == "vscode"
        assert slices[0].duration == 600
        assert slices[1].primary_app == "chrome"
        assert slices[1].duration == 300

    def test_afk_splits_slice(self):
        """AFK event in the middle splits a window event into non-afk and afk parts."""
        t0 = datetime(2026, 5, 30, 9, 0)
        windows = [_make_window_event(t0, 600, "vscode", "main.py")]
        afk = [
            _make_afk_event(t0, 300, "not-afk"),
            _make_afk_event(t0 + timedelta(seconds=300), 300, "afk"),
        ]

        slices = merge_events(windows, afk)

        assert len(slices) == 2
        assert slices[0].is_afk is False
        assert slices[0].duration == 300
        assert slices[1].is_afk is True
        assert slices[1].duration == 300

    def test_empty_events(self):
        """No events produces empty slice list."""
        slices = merge_events([], [])
        assert slices == []

    def test_no_afk_events_defaults_to_active(self):
        """If no AFK data, window events default to not-afk."""
        t0 = datetime(2026, 5, 30, 9, 0)
        windows = [_make_window_event(t0, 300, "vscode", "main.py")]

        slices = merge_events(windows, [])

        assert len(slices) == 1
        assert slices[0].is_afk is False

    def test_overlapping_windows_last_wins(self):
        """If windows overlap, the later event takes priority for overlapping time."""
        t0 = datetime(2026, 5, 30, 9, 0)
        windows = [
            _make_window_event(t0, 300, "vscode", "main.py"),
            _make_window_event(t0 + timedelta(seconds=120), 300, "chrome", "Google"),
        ]
        afk = [_make_afk_event(t0, 600, "not-afk")]

        slices = merge_events(windows, afk)

        # vscode 0-120s, chrome 120-420s
        assert len(slices) == 2
        assert slices[0].primary_app == "vscode"
        assert slices[0].duration == 120
        assert slices[1].primary_app == "chrome"
        assert slices[1].duration == 300

    def test_web_url_in_slice(self):
        """Window event with url data is captured in slice."""
        t0 = datetime(2026, 5, 30, 9, 0)
        windows = [{
            "timestamp": t0,
            "duration": timedelta(seconds=300),
            "data": {"app": "chrome", "title": "GitHub", "url": "https://github.com"},
        }]
        afk = [_make_afk_event(t0, 300, "not-afk")]

        slices = merge_events(windows, afk)

        assert slices[0].web_url == "https://github.com"

    def test_google_chrome_gets_web_url_enrichment(self):
        t0 = datetime(2026, 5, 30, 9, 0)
        windows = [_make_window_event(t0, 300, "Google-chrome", "ChatGPT")]
        afk = [_make_afk_event(t0, 300, "not-afk")]
        web = [{
            "timestamp": t0,
            "duration": timedelta(seconds=300),
            "data": {"url": "https://chatgpt.com/c/abc"},
        }]

        slices = merge_events(windows, afk, web_events=web)

        assert slices[0].web_url == "https://chatgpt.com/c/abc"

    def test_context_events_enrich_slices_by_overlap(self):
        t0 = datetime(2026, 5, 30, 9, 0)
        windows = [_make_window_event(t0, 300, "gnome-terminal", "pytest")]
        afk = [_make_afk_event(t0, 300, "not-afk")]
        context = [{
            "timestamp": t0 + timedelta(seconds=30),
            "duration": timedelta(seconds=120),
            "data": {
                "type": "context_snapshot",
                "process_name": "pytest",
                "process_cwd": "/home/yz/code/aw-coach",
                "git_repo": "aw-coach",
                "git_branch": "feat/context",
                "terminal_command_summary": "pytest tests",
                "terminal_action": "testing",
            },
        }]

        slices = merge_events(windows, afk, context_events=context)

        assert slices[0].process_name == "pytest"
        assert slices[0].process_cwd == "/home/yz/code/aw-coach"
        assert slices[0].git_repo == "aw-coach"
        assert slices[0].git_branch == "feat/context"
        assert slices[0].terminal_command == "pytest tests"
        assert slices[0].terminal_action == "testing"

    def test_context_git_snapshot_is_not_overwritten_by_current_cwd(self, monkeypatch):
        monkeypatch.setattr(
            "aw_coach.git_context.get_git_context_from_path",
            lambda _: GitContext(repo_name="current-repo", branch="current-branch"),
        )

        t0 = datetime(2026, 5, 30, 9, 0)
        windows = [_make_window_event(t0, 300, "gnome-terminal", "pytest")]
        afk = [_make_afk_event(t0, 300, "not-afk")]
        context = [{
            "timestamp": t0,
            "duration": timedelta(seconds=300),
            "data": {
                "type": "context_snapshot",
                "process_cwd": "/home/yz/code/aw-coach",
                "git_repo": "historical-repo",
                "git_branch": "historical-branch",
            },
        }]

        slices = merge_events(windows, afk, context_events=context)

        assert slices[0].git_repo == "historical-repo"
        assert slices[0].git_branch == "historical-branch"

    def test_missing_context_bucket_does_not_fetch_events(self):
        class FakeClient:
            def __init__(self):
                self.event_calls = 0

            def get_buckets(self):
                return {
                    "aw-watcher-window_host": {},
                    "aw-watcher-afk_host": {},
                }

            def get_events(self, *args, **kwargs):
                self.event_calls += 1
                raise AssertionError("context bucket should not be fetched")

        client = FakeClient()
        collector = DataCollector(client=client)
        collector._hostname = "host"

        t0 = datetime(2026, 5, 30, 9, 0)
        assert collector._fetch_context_events(t0, t0 + timedelta(minutes=1)) == []
        assert client.event_calls == 0


class TestHeartbeatMerging:
    """Tests for merging adjacent same-app events (heartbeat consolidation)."""

    def test_adjacent_same_app_merged(self):
        """Adjacent events with same app and <2min gap are merged."""
        t0 = datetime(2026, 5, 30, 9, 0)
        windows = [
            _make_window_event(t0, 5, "vscode", "main.py"),
            _make_window_event(t0 + timedelta(seconds=5), 3, "vscode", "main.py"),
            _make_window_event(t0 + timedelta(seconds=8), 4, "vscode", "main.py"),
        ]
        afk = [_make_afk_event(t0, 20, "not-afk")]

        slices = merge_events(windows, afk)

        # All 3 heartbeats merged into 1 slice
        assert len(slices) == 1
        assert slices[0].primary_app == "vscode"
        assert slices[0].duration == 12  # 5+3+4

    def test_same_app_with_large_gap_not_merged(self):
        """Same app events with gap > 2min stay separate."""
        t0 = datetime(2026, 5, 30, 9, 0)
        windows = [
            _make_window_event(t0, 60, "vscode", "main.py"),
            _make_window_event(t0 + timedelta(seconds=200), 60, "vscode", "util.py"),
        ]
        afk = [_make_afk_event(t0, 300, "not-afk")]

        slices = merge_events(windows, afk)

        assert len(slices) == 2

    def test_different_app_not_merged(self):
        """Adjacent events with different apps stay separate."""
        t0 = datetime(2026, 5, 30, 9, 0)
        windows = [
            _make_window_event(t0, 5, "vscode", "main.py"),
            _make_window_event(t0 + timedelta(seconds=5), 5, "chrome", "Google"),
        ]
        afk = [_make_afk_event(t0, 20, "not-afk")]

        slices = merge_events(windows, afk)

        assert len(slices) == 2
        assert slices[0].primary_app == "vscode"
        assert slices[1].primary_app == "chrome"

    def test_realistic_heartbeat_pattern(self):
        """Simulate realistic heartbeat: 30 events of 3s each for same app."""
        t0 = datetime(2026, 5, 30, 9, 0)
        windows = [
            _make_window_event(t0 + timedelta(seconds=i * 3), 3, "Cursor", "app.tsx")
            for i in range(30)
        ]
        afk = [_make_afk_event(t0, 100, "not-afk")]

        slices = merge_events(windows, afk)

        # All 30 heartbeats should merge into 1 slice of 90s
        assert len(slices) == 1
        assert slices[0].primary_app == "Cursor"
        assert slices[0].duration == 90


class TestActivitySlice:
    def test_slice_fields(self):
        """ActivitySlice holds all expected fields."""
        s = ActivitySlice(
            start=datetime(2026, 5, 30, 9, 0),
            end=datetime(2026, 5, 30, 9, 5),
            duration=300,
            is_afk=False,
            primary_app="vscode",
            primary_title="main.py",
            web_url=None,
        )
        assert s.primary_app == "vscode"
        assert s.web_url is None
        assert s.duration == 300

    def test_slice_domain_extraction(self):
        from aw_coach.collector import _enrich_slice_from_url
        s = ActivitySlice(
            start=datetime(2026, 5, 30, 9, 0),
            end=datetime(2026, 5, 30, 9, 5),
            duration=300,
            is_afk=False,
            primary_app="chrome",
            primary_title="GitHub",
            web_url="https://github.com/user/repo/issues/42",
        )
        _enrich_slice_from_url(s)
        assert s.domain == "github.com"
        assert s.url_path == "/user/repo/issues/42"
        assert s.site_type == "issue"

    def test_slice_site_type_github_repo(self):
        from aw_coach.collector import _enrich_slice_from_url
        s = ActivitySlice(
            start=datetime(2026, 5, 30, 9, 0),
            end=datetime(2026, 5, 30, 9, 5),
            duration=300,
            is_afk=False,
            primary_app="chrome",
            primary_title="repo/blob/main.py",
            web_url="https://github.com/user/repo/blob/main/src/main.py",
        )
        _enrich_slice_from_url(s)
        assert s.site_type == "repo"

    def test_slice_site_type_docs(self):
        from aw_coach.collector import _enrich_slice_from_url
        s = ActivitySlice(
            start=datetime(2026, 5, 30, 9, 0),
            end=datetime(2026, 5, 30, 9, 5),
            duration=300,
            is_afk=False,
            primary_app="chrome",
            primary_title="Python docs",
            web_url="https://docs.python.org/3/library/os.html",
        )
        _enrich_slice_from_url(s)
        assert s.site_type == "docs"

    def test_slice_enrich_in_merge_events(self):
        t0 = datetime(2026, 5, 30, 9, 0)
        windows = [{
            "timestamp": t0,
            "duration": timedelta(seconds=300),
            "data": {"app": "chrome", "title": "GitHub", "url": "https://github.com/user/repo/pull/1"},
        }]
        afk = [_make_afk_event(t0, 300, "not-afk")]

        slices = merge_events(windows, afk)
        assert len(slices) == 1
        assert slices[0].domain == "github.com"
        assert slices[0].site_type == "pr"
