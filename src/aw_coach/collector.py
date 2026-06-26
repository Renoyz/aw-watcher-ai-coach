"""Data collection and event merging from ActivityWatch."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse


def _local_to_utc(dt: datetime) -> datetime:
    """Convert naive/local datetime to UTC for aw-server queries."""
    if dt.tzinfo is None:
        local_tz = datetime.now().astimezone().tzinfo
        dt = dt.replace(tzinfo=local_tz)
    return dt.astimezone(timezone.utc)


def _utc_to_local(dt: datetime) -> datetime:
    """Convert UTC datetime (from aw-server) to local timezone."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone()


@dataclass
class ActivitySlice:
    start: datetime
    end: datetime
    duration: float  # seconds
    is_afk: bool
    primary_app: str
    primary_title: str
    web_url: Optional[str] = None
    # Enriched fields (Phase 3)
    domain: Optional[str] = None
    url_path: Optional[str] = None
    site_type: Optional[str] = None
    process_name: Optional[str] = None
    process_cwd: Optional[str] = None
    git_repo: Optional[str] = None
    git_branch: Optional[str] = None
    terminal_command: Optional[str] = None
    terminal_action: Optional[str] = None
    screen_context: Optional[str] = None
    semantic_project: Optional[str] = None
    task_id: Optional[str] = None
    task_label: Optional[str] = None
    task_confidence: float = 0.0


BROWSER_APPS = frozenset([
    "chrome", "google-chrome", "google-chrome-stable", "chromium",
    "firefox", "firefox_firefox", "safari", "edge",
    "arc", "brave", "vivaldi", "opera", "librewolf", "firefox-esr",
])


def merge_events(
    windows: List[Dict[str, Any]],
    afk: List[Dict[str, Any]],
    web_events: Optional[List[Dict[str, Any]]] = None,
    context_events: Optional[List[Dict[str, Any]]] = None,
) -> List[ActivitySlice]:
    if not windows:
        return []

    timeline: List[Dict[str, Any]] = []

    for event in windows:
        ts = event["timestamp"]
        dur = event["duration"]
        dur_sec = dur.total_seconds() if hasattr(dur, "total_seconds") else float(dur)
        end = ts + timedelta(seconds=dur_sec)
        data = event["data"]
        timeline.append({
            "start": ts,
            "end": end,
            "app": data.get("app", "unknown"),
            "title": data.get("title", ""),
            "url": data.get("url"),
        })

    timeline.sort(key=lambda x: x["start"])

    merged_windows: List[Dict[str, Any]] = []
    for entry in timeline:
        if merged_windows and entry["start"] < merged_windows[-1]["end"]:
            merged_windows[-1]["end"] = entry["start"]
            if merged_windows[-1]["end"] <= merged_windows[-1]["start"]:
                merged_windows.pop()
        merged_windows.append(entry)

    # Heartbeat consolidation: merge adjacent same-app entries with gap < 2min
    MERGE_GAP = 120  # seconds
    consolidated: List[Dict[str, Any]] = []
    for entry in merged_windows:
        if (
            consolidated
            and entry["app"] == consolidated[-1]["app"]
            and (entry["start"] - consolidated[-1]["end"]).total_seconds() < MERGE_GAP
        ):
            consolidated[-1]["end"] = entry["end"]
            if entry.get("url"):
                consolidated[-1]["url"] = entry["url"]
        else:
            consolidated.append(dict(entry))

    merged_windows = consolidated

    afk_intervals: List[Dict[str, Any]] = []
    for event in afk:
        ts = event["timestamp"]
        dur = event["duration"]
        dur_sec = dur.total_seconds() if hasattr(dur, "total_seconds") else float(dur)
        end = ts + timedelta(seconds=dur_sec)
        status = event["data"].get("status", "afk")
        afk_intervals.append({"start": ts, "end": end, "is_afk": status == "afk"})

    afk_intervals.sort(key=lambda x: x["start"])

    def is_afk_at(t: datetime) -> bool:
        if not afk_intervals:
            return False
        for interval in afk_intervals:
            if interval["start"] <= t < interval["end"]:
                return interval["is_afk"]
        return False

    slices: List[ActivitySlice] = []

    for win in merged_windows:
        split_points = [win["start"]]

        for ai in afk_intervals:
            if win["start"] < ai["start"] < win["end"]:
                split_points.append(ai["start"])
            if win["start"] < ai["end"] < win["end"]:
                split_points.append(ai["end"])

        split_points.append(win["end"])
        split_points = sorted(set(split_points))

        for i in range(len(split_points) - 1):
            seg_start = split_points[i]
            seg_end = split_points[i + 1]
            seg_dur = (seg_end - seg_start).total_seconds()
            if seg_dur <= 0:
                continue

            slices.append(ActivitySlice(
                start=seg_start,
                end=seg_end,
                duration=seg_dur,
                is_afk=is_afk_at(seg_start),
                primary_app=win["app"],
                primary_title=win["title"],
                web_url=win.get("url"),
            ))

    # Enrich browser slices with web bucket URLs
    if web_events:
        _enrich_with_web_urls(slices, web_events)
    if context_events:
        _enrich_with_context_events(slices, context_events)

    # Phase 3: enrich with domain, path, site_type
    # Note: process/git enrichment is NOT done here because it reads live
    # system state and would corrupt historical slices.  The scheduler
    # enriches only the latest slice on its tick.
    for s in slices:
        _enrich_slice_from_url(s)
        _enrich_slice_from_git(s)

    return slices


def _enrich_with_web_urls(
    slices: List[ActivitySlice], web_events: List[Dict[str, Any]]
) -> None:
    """Attach URL from web bucket to browser slices (longest overlapping web event wins)."""
    parsed_web = []
    for event in web_events:
        ts = event["timestamp"]
        dur = event["duration"]
        dur_sec = dur.total_seconds() if hasattr(dur, "total_seconds") else float(dur)
        parsed_web.append({
            "start": ts,
            "end": ts + timedelta(seconds=dur_sec),
            "url": event["data"].get("url", ""),
            "duration": dur_sec,
        })

    for s in slices:
        if s.web_url:
            continue
        if s.primary_app.lower() not in BROWSER_APPS:
            continue

        best_url = None
        best_overlap = 0.0
        for w in parsed_web:
            overlap_start = max(s.start, w["start"])
            overlap_end = min(s.end, w["end"])
            overlap = (overlap_end - overlap_start).total_seconds()
            if overlap > best_overlap:
                best_overlap = overlap
                best_url = w["url"]

        if best_url:
            s.web_url = best_url


def _enrich_with_context_events(
    slices: List[ActivitySlice], context_events: List[Dict[str, Any]]
) -> None:
    """Attach lightweight process/git context by greatest time overlap."""
    parsed = []
    for event in context_events:
        data = event.get("data", {}) or {}
        if data.get("type") != "context_snapshot":
            continue
        ts = event["timestamp"]
        dur = event["duration"]
        dur_sec = dur.total_seconds() if hasattr(dur, "total_seconds") else float(dur)
        parsed.append({
            "start": ts,
            "end": ts + timedelta(seconds=dur_sec),
            "data": data,
        })

    for s in slices:
        best = None
        best_overlap = 0.0
        for ctx in parsed:
            overlap_start = max(s.start, ctx["start"])
            overlap_end = min(s.end, ctx["end"])
            overlap = (overlap_end - overlap_start).total_seconds()
            if overlap > best_overlap:
                best_overlap = overlap
                best = ctx["data"]
        if not best:
            continue

        s.process_name = best.get("process_name")
        s.process_cwd = best.get("process_cwd")
        s.git_repo = best.get("git_repo")
        s.git_branch = best.get("git_branch")
        s.terminal_command = (
            best.get("terminal_command_summary")
            or best.get("terminal_command")
        )
        s.terminal_action = best.get("terminal_action")


# ---------------------------------------------------------------------------
# Phase 3: slice enrichment helpers
# ---------------------------------------------------------------------------

def _enrich_slice_from_url(s: ActivitySlice) -> None:
    """Extract domain, url_path, site_type from web_url."""
    if not s.web_url:
        return
    try:
        parsed = urlparse(s.web_url)
    except Exception:
        return
    s.domain = parsed.netloc.lower()
    s.url_path = parsed.path

    path = parsed.path.lower()
    netloc = parsed.netloc.lower()

    # Site type inference
    if "/issues/" in path or "/issue/" in path:
        s.site_type = "issue"
    elif "/pull/" in path or "/merge_requests/" in path:
        s.site_type = "pr"
    elif "/blob/" in path or "/tree/" in path or "/commit/" in path:
        s.site_type = "repo"
    elif any(
        d in netloc
        for d in ("stackoverflow", "docs.", "readthedocs", "python.org", "developer.mozilla.org")
    ):
        s.site_type = "docs"
    elif "/search" in path or any(
        d in netloc for d in ("google.com", "bing.com", "duckduckgo.com")
    ):
        s.site_type = "search"
    elif any(d in netloc for d in ("youtube.com", "bilibili.com", "vimeo.com")):
        s.site_type = "video"
    elif any(d in netloc for d in ("slack.com", "discord.com", "telegram.org", "web.telegram.org")):
        s.site_type = "chat"
    else:
        s.site_type = "other"


def _enrich_slice_from_git(s: ActivitySlice) -> None:
    """Do not infer historical git context from the current filesystem.

    Context snapshots already carry the repo/branch observed at capture time.
    Re-reading ``process_cwd`` later would make old reports depend on whatever
    branch that directory is on today.
    """
    return


class DataCollector:
    def __init__(self, client=None, client_name: str = "aw-coach-cli"):
        if client is None:
            from aw_client import ActivityWatchClient
            client = ActivityWatchClient(client_name)
        self.client = client
        self._hostname: Optional[str] = None

    @property
    def hostname(self) -> str:
        if self._hostname is None:
            self._hostname = self._detect_hostname()
        return self._hostname

    def _detect_hostname(self) -> str:
        buckets = self.client.get_buckets()
        for bid in buckets:
            if bid.startswith("aw-watcher-window_"):
                return bid.removeprefix("aw-watcher-window_")
        raise RuntimeError(
            "No aw-watcher-window bucket found. Is ActivityWatch running?"
        )

    def fetch_range(self, start: datetime, end: datetime) -> List[ActivitySlice]:
        # Convert local query range to UTC for aw-server
        utc_start = _local_to_utc(start)
        utc_end = _local_to_utc(end)

        windows = self.client.get_events(
            f"aw-watcher-window_{self.hostname}", start=utc_start, end=utc_end
        )
        afk = self.client.get_events(
            f"aw-watcher-afk_{self.hostname}", start=utc_start, end=utc_end
        )

        # Try to fetch web bucket data (may not exist)
        web_events_raw = self._fetch_web_events(utc_start, utc_end)
        context_events_raw = self._fetch_context_events(utc_start, utc_end)

        win_dicts = [
            {
                "timestamp": e.timestamp,
                "duration": e.duration,
                "data": e.data,
            }
            for e in windows
        ]
        afk_dicts = [
            {
                "timestamp": e.timestamp,
                "duration": e.duration,
                "data": e.data,
            }
            for e in afk
        ]
        web_dicts = [
            {
                "timestamp": e.timestamp,
                "duration": e.duration,
                "data": e.data,
            }
            for e in web_events_raw
        ]
        context_dicts = [
            {
                "timestamp": e.timestamp,
                "duration": e.duration,
                "data": e.data,
            }
            for e in context_events_raw
        ]

        slices = merge_events(
            win_dicts,
            afk_dicts,
            web_events=web_dicts,
            context_events=context_dicts,
        )
        # Convert all timestamps to local time for downstream analysis
        for s in slices:
            s.start = _utc_to_local(s.start)
            s.end = _utc_to_local(s.end)
        return slices

    def _fetch_web_events(self, start, end) -> list:
        """Fetch web watcher events. Returns empty list if bucket doesn't exist."""
        buckets = self.client.get_buckets()
        web_bucket = None
        for bid in buckets:
            if bid.startswith("aw-watcher-web-"):
                web_bucket = bid
                break
        if not web_bucket:
            return []
        try:
            return self.client.get_events(web_bucket, start=start, end=end)
        except Exception:
            return []

    def _fetch_context_events(self, start, end) -> list:
        """Fetch aw-coach context events. Returns empty list if bucket doesn't exist."""
        bucket = f"aw-coach-context_{self.hostname}"
        try:
            buckets = self.client.get_buckets()
        except Exception:
            return []
        if bucket not in buckets:
            return []
        try:
            return self.client.get_events(bucket, start=start, end=end)
        except Exception:
            return []

    def fetch_today(self) -> List[ActivitySlice]:
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        return self.fetch_range(today, datetime.now())
