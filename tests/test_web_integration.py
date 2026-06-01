"""Tests for aw-watcher-web integration - URL-level browser classification."""

from datetime import datetime, timedelta

from aw_coach.collector import merge_events


def _make_window_event(ts, duration_sec, app, title):
    return {
        "timestamp": ts,
        "duration": timedelta(seconds=duration_sec),
        "data": {"app": app, "title": title},
    }


def _make_web_event(ts, duration_sec, url, title):
    return {
        "timestamp": ts,
        "duration": timedelta(seconds=duration_sec),
        "data": {"url": url, "title": title},
    }


class TestWebBucketIntegration:
    """Tests for merging aw-watcher-web URL data into browser slices."""

    def test_web_url_merged_into_browser_slice(self):
        """When browser is active and web bucket has URL data, slice gets the URL."""
        t0 = datetime(2026, 5, 30, 9, 0)
        windows = [_make_window_event(t0, 300, "firefox", "GitHub - firefox")]
        afk = [{"timestamp": t0, "duration": timedelta(seconds=300), "data": {"status": "not-afk"}}]
        web = [_make_web_event(t0, 300, "https://github.com/org/repo", "Pull Request #42")]

        slices = merge_events(windows, afk, web_events=web)

        assert len(slices) == 1
        assert slices[0].web_url == "https://github.com/org/repo"

    def test_multiple_urls_during_browser_session(self):
        """Multiple web events during a browser session: slice gets the longest URL."""
        t0 = datetime(2026, 5, 30, 9, 0)
        windows = [_make_window_event(t0, 600, "chrome", "tabs")]
        afk = [{"timestamp": t0, "duration": timedelta(seconds=600), "data": {"status": "not-afk"}}]
        web = [
            _make_web_event(t0, 100, "https://news.ycombinator.com", "HN"),
            _make_web_event(t0 + timedelta(seconds=100), 500, "https://github.com/pr", "PR Review"),
        ]

        slices = merge_events(windows, afk, web_events=web)

        # The dominant URL (longest duration) should be used
        assert slices[0].web_url == "https://github.com/pr"

    def test_no_web_events_no_url(self):
        """Without web bucket data, browser slices have no URL."""
        t0 = datetime(2026, 5, 30, 9, 0)
        windows = [_make_window_event(t0, 300, "chrome", "Some Page")]
        afk = [{"timestamp": t0, "duration": timedelta(seconds=300), "data": {"status": "not-afk"}}]

        slices = merge_events(windows, afk, web_events=[])

        assert slices[0].web_url is None

    def test_web_url_only_applied_to_browsers(self):
        """Web URLs should NOT be applied to non-browser apps."""
        t0 = datetime(2026, 5, 30, 9, 0)
        windows = [_make_window_event(t0, 300, "vscode", "main.py")]
        afk = [{"timestamp": t0, "duration": timedelta(seconds=300), "data": {"status": "not-afk"}}]
        web = [_make_web_event(t0, 300, "https://github.com", "GitHub")]

        slices = merge_events(windows, afk, web_events=web)

        # vscode is not a browser, URL should not be attached
        assert slices[0].web_url is None

    def test_web_url_improves_classification(self):
        """With URL data, browser classification should be more precise."""
        from aw_coach.rules.engine import RuleEngine

        engine = RuleEngine.with_builtin_rules()

        # Without URL: generic browser → research (low confidence)
        r1 = engine.classify("chrome", "Some Page - Google Chrome", None)

        # With URL: GitHub → programming
        r2 = engine.classify("chrome", "Some Page - Google Chrome", "https://github.com/org/repo")

        assert r2.activity_type == "programming"
        assert r2.confidence > r1.confidence
