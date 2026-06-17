"""Time helpers for persisted timestamps and user-facing display."""

from __future__ import annotations

from datetime import datetime, timezone


def now_local() -> datetime:
    """Return the current local time with timezone information."""
    return datetime.now(timezone.utc).astimezone()


def now_local_iso() -> str:
    """Return a timezone-aware local ISO timestamp."""
    return now_local().isoformat(timespec="seconds")


def parse_stored_timestamp(value: str) -> datetime:
    """Parse new ISO timestamps and legacy SQLite UTC timestamps.

    Older inbox rows were written with SQLite ``datetime('now')`` and have no
    timezone. Those strings use a space separator, so treat them as UTC.
    Newer Python-written ISO values include an offset and round-trip directly.
    """
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        if "T" not in raw:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone()
    return dt


def format_local_timestamp(value: str) -> str:
    """Format a persisted timestamp in local time for CLI/UI display."""
    return parse_stored_timestamp(value).astimezone().strftime("%Y-%m-%d %H:%M:%S")
