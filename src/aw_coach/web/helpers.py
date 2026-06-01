"""Dashboard chart and timeline helpers."""

from __future__ import annotations

import html as html_lib
import json
from datetime import timedelta


def safe_json(value) -> str:
    return (
        json.dumps(value, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def activity_color(activity: str) -> str:
    colors = {
        "programming": "#2563eb",
        "writing": "#7c3aed",
        "design": "#db2777",
        "research": "#d97706",
        "meeting": "#059669",
        "admin": "#64748b",
        "social": "#dc2626",
        "entertainment": "#4f46e5",
    }
    return colors.get(activity, "#94a3b8")


def split_slice_by_hour(s):
    start = getattr(s, "start", None)
    end = getattr(s, "end", None)
    if start is None or end is None or end <= start:
        return []

    segments = []
    cursor = start
    while cursor < end:
        next_hour = cursor.replace(minute=0, second=0, microsecond=0) + timedelta(
            hours=1
        )
        segment_end = min(end, next_hour)
        duration = (segment_end - cursor).total_seconds()
        if duration > 0:
            segments.append((cursor, duration))
        cursor = segment_end
    return segments


def build_hourly_timeline(slices, rules):
    if not slices or not rules:
        return []
    from collections import defaultdict

    hourly = defaultdict(
        lambda: {"duration": 0, "apps": set(), "titles": set(), "activities": defaultdict(float)}
    )
    for s, r in zip(slices, rules):
        if getattr(s, "is_afk", False) or getattr(r, "skip_analysis", False):
            continue
        for start, duration in split_slice_by_hour(s):
            hour = start.hour
            hourly[hour]["duration"] += duration
            hourly[hour]["apps"].add(getattr(s, "primary_app", "unknown"))
            title = getattr(s, "primary_title", "")
            if title:
                hourly[hour]["titles"].add(title)
            hourly[hour]["activities"][r.activity_type] += duration

    result = []
    for hour in sorted(hourly.keys()):
        info = hourly[hour]
        activities = info["activities"]
        top_activity = max(activities.items(), key=lambda x: x[1])[0] if activities else "unknown"
        result.append(
            {
                "hour": hour,
                "duration": info["duration"] / 3600,
                "apps": list(info["apps"])[:3],
                "titles": list(info["titles"])[:2],
                "activity": top_activity,
            }
        )
    return result


def build_slice_timeline(slices, rules):
    items = []
    if not slices or not rules:
        return items

    for index, (s, r) in enumerate(zip(slices, rules)):
        if getattr(s, "is_afk", False) or getattr(r, "skip_analysis", False):
            continue
        items.append(
            {
                "id": str(index),
                "start": s.start.strftime("%H:%M"),
                "end": s.end.strftime("%H:%M"),
                "duration": s.duration / 3600,
                "app": s.primary_app,
                "title": s.primary_title,
                "url": s.web_url,
                "activity": r.activity_type,
                "confidence": r.confidence,
                "method": r.method,
                "timestamp": s.start.isoformat(),
            }
        )
    return items


def render_hourly_timeline(items) -> str:
    if not items:
        return '<p class="empty">暂无详细时间段数据</p>'

    html_items = []
    for item in items:
        apps = html_lib.escape(", ".join(str(app) for app in item["apps"]), quote=True)
        titles = html_lib.escape("; ".join(str(t) for t in item["titles"][:2]), quote=True)
        title_html = f'<div class="timeline-titles">{titles}</div>' if titles else ""
        activity = html_lib.escape(str(item["activity"]), quote=True)
        color = activity_color(str(item["activity"]))
        html_items.append(
            f"""<div class="timeline-item" style="border-left-color:{color}">
  <div class="timeline-time">{item["hour"]:02d}:00</div>
  <div class="timeline-content">
    <div class="timeline-activity">{activity}</div>
    <div class="timeline-apps">{apps}</div>
    {title_html}
    <div class="timeline-duration">{item["duration"]:.1f}h</div>
  </div>
</div>"""
        )
    return "\n".join(html_items)


def render_slice_timeline(items, interactive: bool = False) -> str:
    if not items:
        return '<p class="empty">暂无详细时间段数据</p>'

    html_items = []
    for item in items:
        title = html_lib.escape(str(item["title"] or ""), quote=True)
        app = html_lib.escape(str(item["app"] or "unknown"), quote=True)
        activity = html_lib.escape(str(item["activity"]), quote=True)
        confidence = float(item["confidence"])
        click_hint = " timeline-clickable" if interactive else ""
        color = activity_color(str(item["activity"]))
        attrs = (
            f' data-slice-id="{html_lib.escape(item["id"], quote=True)}"'
            f' data-activity="{activity}"'
        )
        html_items.append(
            f"""<div class="timeline-item{click_hint}" style="border-left-color:{color}"{attrs}>
  <div class="timeline-time">{item["start"]}-{item["end"]}</div>
  <div class="timeline-content">
    <div class="timeline-activity">{activity} <span class="confidence">{confidence:.2f}</span></div>
    <div class="timeline-apps">{app}</div>
    <div class="timeline-titles">{title}</div>
    <div class="timeline-duration">{item["duration"]:.2f}h</div>
  </div>
</div>"""
        )
    return "\n".join(html_items)


def render_death_loops(loops) -> str:
    if not loops:
        return '<p class="empty">今日未检测到切换循环</p>'

    rendered = []
    for loop in loops[:5]:
        apps = html_lib.escape(" ↔ ".join(str(a) for a in loop.get("apps", [])), quote=True)
        count = html_lib.escape(
            str(loop.get("alternations", loop.get("count", 0))), quote=True
        )
        rendered.append(f'<div class="loop-item">⚠️ {apps} （{count} 次切换）</div>')
    return "\n".join(rendered)


def render_suggestions(suggestions) -> str:
    if not suggestions:
        return "<li>今日表现不错，继续保持！</li>"
    return "".join(
        f"<li>{html_lib.escape(str(suggestion), quote=True)}</li>"
        for suggestion in suggestions
    )
