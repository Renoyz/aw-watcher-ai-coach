"""Daily background insights for end-of-day reports."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional

SOURCE_VERSION = 1
CHAT_ACTIVITY_TYPES = {"meeting", "social", "chatting"}
RESEARCH_INTENTS = {"research", "ai_assisted", "browse", "unknown"}
BUILD_INTENTS = {"implement", "test", "debug", "commit", "programming", "terminal"}


@dataclass
class DailyInsight:
    date: str
    kind: str
    title: str
    body: str
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    suggestion: str = ""
    severity: float = 0.0
    confidence: float = 0.0
    source_version: int = SOURCE_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "DailyInsight":
        return cls(
            date=str(row.get("date") or ""),
            kind=str(row.get("kind") or "unknown"),
            title=str(row.get("title") or ""),
            body=str(row.get("body") or ""),
            evidence=_safe_json_list(row.get("evidence_json") or row.get("evidence")),
            suggestion=str(row.get("suggestion") or ""),
            severity=float(row.get("severity") or 0.0),
            confidence=float(row.get("confidence") or 0.0),
            source_version=int(row.get("source_version") or SOURCE_VERSION),
        )


def generate_daily_insights(
    day: date | str,
    storage,
    analysis: Optional[object] = None,
) -> List[DailyInsight]:
    """Generate evidence-backed daily insights from persisted local data."""
    day_str = day.isoformat() if isinstance(day, date) else str(day)
    sessions = _load_sessions(storage, day_str)
    if not sessions:
        return []

    insights = [
        _fragmented_main_task(day_str, sessions),
        _recovery_cost(day_str, sessions),
        _pseudo_progress(day_str, sessions, analysis),
        _productive_closure(day_str, sessions),
        _ignored_prompt(day_str, storage),
    ]
    ranked = [item for item in insights if item is not None]
    ranked.sort(key=lambda item: (item.severity, item.confidence), reverse=True)

    selected: List[DailyInsight] = []
    selected_kinds: set[str] = set()
    for item in ranked:
        if item.kind in selected_kinds:
            continue
        selected.append(item)
        selected_kinds.add(item.kind)
        if len(selected) >= 4:
            break
    return selected


def render_daily_insights(insights: Iterable[DailyInsight | Dict[str, Any]]) -> str:
    items = [_coerce_insight(item) for item in insights]
    items = [item for item in items if item is not None]
    if not items:
        return ""

    lines = ["## 额外观察", ""]
    for item in items[:3]:
        lines.append(f"- **{item.title}**：{item.body}")
        if item.evidence:
            evidence_text = _format_evidence(item.evidence[:2])
            if evidence_text:
                lines.append(f"  证据：{evidence_text}")
        if item.suggestion:
            lines.append(f"  可以尝试：{item.suggestion}")
    return "\n".join(lines)


def insights_to_jsonable(
    insights: Iterable[DailyInsight | Dict[str, Any]],
) -> List[Dict[str, Any]]:
    return [
        item.to_dict() if isinstance(item, DailyInsight) else dict(item)
        for item in insights
    ]


def _load_sessions(storage, day: str) -> List[Dict[str, Any]]:
    try:
        rows = storage.get_task_timeline(day)
    except Exception:
        return []
    sessions = [row for row in rows if float(row.get("accumulated_sec") or 0.0) > 0]
    return sorted(sessions, key=lambda row: str(row.get("started_at") or ""))


def _fragmented_main_task(day: str, sessions: List[Dict[str, Any]]) -> Optional[DailyInsight]:
    by_task: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in sessions:
        task_id = str(row.get("task_id") or "")
        if not task_id or task_id.startswith("unknown:"):
            continue
        by_task[task_id].append(row)
    if not by_task:
        return None

    task_id, parts = max(
        by_task.items(),
        key=lambda item: sum(float(row.get("accumulated_sec") or 0.0) for row in item[1]),
    )
    total_sec = sum(float(row.get("accumulated_sec") or 0.0) for row in parts)
    if len(parts) < 4 or total_sec < 45 * 60:
        return None
    label = str(parts[0].get("label") or task_id)
    longest = max(float(row.get("accumulated_sec") or 0.0) for row in parts)
    evidence = [
        {
            "task": label,
            "segments": len(parts),
            "total_minutes": round(total_sec / 60),
            "longest_minutes": round(longest / 60),
        }
    ]
    return DailyInsight(
        date=day,
        kind="fragmented_main_task",
        title="主任务被切成多段",
        body=(
            f"`{label}` 今天累计约 {total_sec / 3600:.1f}h，"
            f"但分成了 {len(parts)} 段，最长连续段约 {longest / 60:.0f} 分钟。"
        ),
        evidence=evidence,
        suggestion="明天开始同一任务前，先看 3 分钟 task timeline 或最近提交，降低恢复成本。",
        severity=min(1.0, len(parts) / 8),
        confidence=0.82,
    )


def _recovery_cost(day: str, sessions: List[Dict[str, Any]]) -> Optional[DailyInsight]:
    gaps = []
    previous_focus = None
    for row in sessions:
        intent = str(row.get("intent") or "").lower()
        label = str(row.get("label") or row.get("task_id") or "")
        started_at = _parse_datetime(row.get("started_at"))
        ended_at = _parse_datetime(row.get("ended_at"))
        if started_at is None:
            continue

        if previous_focus is not None and intent not in CHAT_ACTIVITY_TYPES:
            prev_end = previous_focus.get("ended_at")
            if prev_end is not None:
                gap_min = _minutes_between(prev_end, started_at)
                if gap_min is not None and gap_min >= 15:
                    gaps.append({
                        "after": previous_focus["label"],
                        "next": label,
                        "gap_minutes": round(gap_min),
                    })
            previous_focus = None

        if intent in CHAT_ACTIVITY_TYPES:
            previous_focus = {"label": label, "ended_at": ended_at}

    if not gaps:
        return None
    avg_gap = sum(item["gap_minutes"] for item in gaps) / len(gaps)
    return DailyInsight(
        date=day,
        kind="recovery_cost",
        title="沟通后的恢复间隔偏长",
        body=(
            f"今天有 {len(gaps)} 次会议/聊天后，超过 15 分钟才进入下一个非沟通任务，"
            f"平均间隔约 {avg_gap:.0f} 分钟。"
        ),
        evidence=gaps[:3],
        suggestion="可以把会议后第一步设成恢复上下文，而不是立即切到新的浏览或聊天窗口。",
        severity=min(1.0, avg_gap / 45),
        confidence=0.74,
    )


def _pseudo_progress(
    day: str,
    sessions: List[Dict[str, Any]],
    analysis: Optional[object],
) -> Optional[DailyInsight]:
    research_sec = 0.0
    build_sec = 0.0
    evidence = []
    for row in sessions:
        intent = str(row.get("intent") or "").lower()
        label = str(row.get("label") or row.get("task_id") or "")
        sec = float(row.get("accumulated_sec") or 0.0)
        if intent in RESEARCH_INTENTS or "research" in label.lower() or "ai" in label.lower():
            research_sec += sec
            evidence.append({"task": label, "minutes": round(sec / 60), "intent": intent})
        if intent in BUILD_INTENTS:
            build_sec += sec

    if analysis is not None:
        breakdown = getattr(analysis, "activity_breakdown", {}) or {}
        research_sec = max(
            research_sec,
            float(breakdown.get("research", 0.0) + breakdown.get("ai_assisted", 0.0)) * 3600,
        )
        build_sec = max(
            build_sec,
            float(breakdown.get("programming", 0.0) + breakdown.get("terminal", 0.0)) * 3600,
        )

    if research_sec < 45 * 60 or build_sec >= research_sec * 0.7:
        return None
    return DailyInsight(
        date=day,
        kind="pseudo_progress",
        title="研究/AI 辅助时间明显高于构建时间",
        body=(
            f"今天 research/AI 相关时间约 {research_sec / 3600:.1f}h，"
            f"构建类时间约 {build_sec / 3600:.1f}h，可能有一段是在积累上下文而不是直接产出。"
        ),
        evidence=evidence[:3],
        suggestion="下次类似状态超过 25 分钟时，可以先写一个最小测试或验证命令固定问题边界。",
        severity=min(1.0, research_sec / max(build_sec + 1, 3600)),
        confidence=0.68,
    )


def _productive_closure(day: str, sessions: List[Dict[str, Any]]) -> Optional[DailyInsight]:
    candidates = []
    for row in sessions:
        label = str(row.get("label") or row.get("task_id") or "")
        source = row.get("source") if isinstance(row.get("source"), dict) else {}
        evidence = row.get("evidence") if isinstance(row.get("evidence"), list) else []
        modes = row.get("modes") if isinstance(row.get("modes"), list) else []
        sec = float(row.get("accumulated_sec") or 0.0)
        if sec < 30 * 60:
            continue
        has_git = bool(source.get("git_branch")) or any(
            isinstance(item, dict) and item.get("source") == "git" for item in evidence
        )
        has_build_mode = any(str(mode).lower() in BUILD_INTENTS for mode in modes)
        if has_git or has_build_mode:
            candidates.append((sec, label, source, modes))

    if not candidates:
        return None
    sec, label, source, modes = max(candidates, key=lambda item: item[0])
    evidence = [{
        "task": label,
        "minutes": round(sec / 60),
        "git_branch": source.get("git_branch"),
        "modes": modes[:4],
    }]
    return DailyInsight(
        date=day,
        kind="productive_closure",
        title="今天有一段形成闭环的推进",
        body=(
            f"`{label}` 累计约 {sec / 3600:.1f}h，并带有 git 或构建类上下文，"
            "说明这段不是单纯停留。"
        ),
        evidence=evidence,
        suggestion="明天如果继续这个方向，可以先从这条任务时间轴和最近变更恢复上下文。",
        severity=0.45,
        confidence=0.72,
    )


def _ignored_prompt(day: str, storage) -> Optional[DailyInsight]:
    try:
        rows = storage.get_recent_delivery_logs(limit=30)
    except Exception:
        return None
    day_rows = [
        row for row in rows
        if str(row.get("timestamp") or "").startswith(day)
        and row.get("channel") in {"inbox", "notify"}
    ]
    if len(day_rows) < 3:
        return None
    counts = Counter(str(row.get("kind") or "unknown") for row in day_rows)
    kind, count = counts.most_common(1)[0]
    if count < 3:
        return None
    return DailyInsight(
        date=day,
        kind="ignored_prompt",
        title="同类提醒今天出现较多",
        body=(
            f"`{kind}` 类提示今天出现 {count} 次，"
            "可能更适合批量放在日报里看，而不是当天反复出现。"
        ),
        evidence=[{"kind": kind, "count": count}],
        suggestion="如果这类信息没有改变当天行动，可以把它保留为日终观察而不是即时提醒。",
        severity=min(1.0, count / 6),
        confidence=0.65,
    )


def _coerce_insight(item: DailyInsight | Dict[str, Any]) -> Optional[DailyInsight]:
    if isinstance(item, DailyInsight):
        return item
    if isinstance(item, dict):
        return DailyInsight.from_row(item)
    return None


def _format_evidence(evidence: List[Dict[str, Any]]) -> str:
    parts = []
    for item in evidence:
        if "task" in item and "minutes" in item:
            parts.append(f"{item['task']} {item['minutes']} 分钟")
        elif "segments" in item:
            parts.append(
                f"{item.get('task', '任务')} {item['segments']} 段，"
                f"最长 {item.get('longest_minutes', '-')} 分钟"
            )
        elif "gap_minutes" in item:
            parts.append(f"{item.get('after', '沟通')} 后间隔 {item['gap_minutes']} 分钟")
        elif "kind" in item and "count" in item:
            parts.append(f"{item['kind']} {item['count']} 次")
    return "；".join(parts)


def _safe_json_list(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    try:
        loaded = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(loaded, list):
        return []
    return [item for item in loaded if isinstance(item, dict)]


def _parse_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _minutes_between(start: datetime, end: datetime) -> Optional[float]:
    try:
        return (end - start).total_seconds() / 60
    except TypeError:
        try:
            return (
                end.replace(tzinfo=None) - start.replace(tzinfo=None)
            ).total_seconds() / 60
        except Exception:
            return None
