"""Pattern analysis - focus score, deep work, activity breakdown."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Dict, List, Optional, Tuple

from aw_coach.collector import ActivitySlice
from aw_coach.config import AnalysisConfig
from aw_coach.rules.engine import RuleResult

DEEP_WORK_TYPES = {"programming", "writing", "design", "research", "terminal"}
DISTRACTION_TYPES = {"entertainment", "social"}
# Non-deep-work types that briefly appear between deep-work blocks
# without resetting the streak (up to this many seconds).
PENETRATION_TOLERANCE = 180  # 3 minutes


@dataclass
class AnalysisResult:
    total_hours: float
    effective_hours: float
    deep_work_hours: float
    focus_score: int
    switch_count: int
    activity_breakdown: Dict[str, float]
    hourly_scores: List[Tuple[int, int]]
    productivity_score: int = 0
    death_loops: List[Dict] = field(default_factory=list)


class PatternAnalyzer:
    def __init__(self, config: AnalysisConfig):
        self.config = config
        self.deep_work_threshold = config.deep_work_threshold_minutes
        self.work_hours_start = self._parse_clock(config.work_hours_start)
        self.work_hours_end = self._parse_clock(config.work_hours_end)
        self.work_days = set(config.work_days)
        self.distraction_apps = {app.lower() for app in config.distraction_apps}
        self.social_apps = {app.lower() for app in config.social_apps}
        self.restrict_to_work_schedule = config.restrict_to_work_schedule

    def analyze(self, slices: List[ActivitySlice], rules: List[RuleResult]) -> AnalysisResult:
        if not slices:
            return AnalysisResult(
                total_hours=0.0,
                effective_hours=0.0,
                deep_work_hours=0.0,
                focus_score=0,
                switch_count=0,
                activity_breakdown={},
                hourly_scores=[],
            )

        total = self._total_hours(slices, rules)
        effective = self._effective_hours(slices, rules)
        deep_work = self._deep_work_hours(slices, rules)
        switches = self._count_switches(slices, rules)
        breakdown = self._activity_breakdown(slices, rules)
        hourly = self._hourly_scores(slices, rules)
        focus = self._focus_score(deep_work, switches, breakdown, total)
        productivity = self._productivity_score(slices, rules)
        loops = self._detect_death_loops(slices, rules)

        return AnalysisResult(
            total_hours=total,
            effective_hours=effective,
            deep_work_hours=deep_work,
            focus_score=focus,
            switch_count=switches,
            activity_breakdown=breakdown,
            hourly_scores=hourly,
            productivity_score=productivity,
            death_loops=loops,
        )

    @staticmethod
    def _parse_clock(value: str) -> time:
        try:
            return datetime.strptime(value, "%H:%M").time()
        except ValueError:
            return time(0, 0)

    def _include_in_analysis(self, s: ActivitySlice, r: RuleResult) -> bool:
        return bool(self._analysis_segments(s, r))

    def _analysis_segments(
        self, s: ActivitySlice, r: RuleResult, split_by_hour: bool = False
    ) -> List[ActivitySlice]:
        if s.is_afk or getattr(r, "skip_analysis", False):
            return []

        segments = self._clip_to_work_schedule(s)
        if not split_by_hour:
            return segments

        split_segments: List[ActivitySlice] = []
        for segment in segments:
            split_segments.extend(self._split_slice_by_hour(segment))
        return split_segments

    def _clip_to_work_schedule(self, s: ActivitySlice) -> List[ActivitySlice]:
        if s.end <= s.start or s.duration <= 0:
            return []
        if not self.restrict_to_work_schedule:
            return [s]

        segments: List[ActivitySlice] = []
        current_date = s.start.date() - timedelta(days=1)
        last_date = s.end.date()
        while current_date <= last_date:
            if not self.work_days or current_date.isoweekday() in self.work_days:
                work_start = datetime.combine(current_date, self.work_hours_start)
                end_date = current_date
                if self.work_hours_start > self.work_hours_end:
                    end_date = current_date + timedelta(days=1)
                work_end = datetime.combine(end_date, self.work_hours_end)
                if s.start.tzinfo is not None:
                    work_start = work_start.replace(tzinfo=s.start.tzinfo)
                    work_end = work_end.replace(tzinfo=s.start.tzinfo)

                segment_start = max(s.start, work_start)
                segment_end = min(s.end, work_end)
                if segment_start < segment_end:
                    segments.append(self._copy_segment(s, segment_start, segment_end))

            current_date += timedelta(days=1)

        return segments

    @staticmethod
    def _copy_segment(
        s: ActivitySlice, segment_start: datetime, segment_end: datetime
    ) -> ActivitySlice:
        return ActivitySlice(
            start=segment_start,
            end=segment_end,
            duration=(segment_end - segment_start).total_seconds(),
            is_afk=s.is_afk,
            primary_app=s.primary_app,
            primary_title=s.primary_title,
            web_url=s.web_url,
        )

    def _within_work_schedule(self, s: ActivitySlice) -> bool:
        # Kept for callers that only need a boolean. Metrics use
        # _clip_to_work_schedule so partial overlaps are preserved.
        return (
            not self.restrict_to_work_schedule
            or bool(self._clip_to_work_schedule(s))
        )

    def _activity_type(self, s: ActivitySlice, r: RuleResult) -> str:
        if r.activity_type != "unknown":
            return r.activity_type

        app_lower = s.primary_app.lower()
        if any(app in app_lower for app in self.distraction_apps):
            return "entertainment"
        if any(app in app_lower for app in self.social_apps):
            return "social"
        return r.activity_type

    def _total_hours(
        self, slices: List[ActivitySlice], rules: Optional[List[RuleResult]] = None
    ) -> float:
        if rules is None:
            return (
                sum(
                    segment.duration
                    for s in slices
                    if not s.is_afk
                    for segment in self._clip_to_work_schedule(s)
                )
                / 3600
            )
        return sum(
            segment.duration
            for s, r in zip(slices, rules)
            for segment in self._analysis_segments(s, r)
        ) / 3600

    def _effective_hours(self, slices: List[ActivitySlice], rules: List[RuleResult]) -> float:
        total = 0.0
        for s, r in zip(slices, rules):
            if self._activity_type(s, r) in DISTRACTION_TYPES:
                continue
            total += sum(segment.duration for segment in self._analysis_segments(s, r))
        return total / 3600

    def _deep_work_hours(self, slices: List[ActivitySlice], rules: List[RuleResult]) -> float:
        if not slices:
            return 0.0

        AFK_GAP_TOLERANCE = 300  # seconds - 5min breaks don't interrupt

        deep_seconds = 0.0
        current_type = None
        current_streak_sec = 0.0
        penetration_sec = 0.0  # Time in tolerated non-deep-work gaps

        for s, r in zip(slices, rules):
            if getattr(r, "skip_analysis", False):
                if current_streak_sec >= self.deep_work_threshold * 60:
                    deep_seconds += current_streak_sec
                current_type = None
                current_streak_sec = 0.0
                penetration_sec = 0.0
                continue

            if s.is_afk:
                if s.duration <= AFK_GAP_TOLERANCE:
                    continue  # Short break, streak continues
                else:
                    # Long AFK breaks the streak
                    if current_streak_sec >= self.deep_work_threshold * 60:
                        deep_seconds += current_streak_sec
                    current_type = None
                    current_streak_sec = 0.0
                    penetration_sec = 0.0
                    continue

            segments = self._analysis_segments(s, r)
            if not segments:
                if current_streak_sec >= self.deep_work_threshold * 60:
                    deep_seconds += current_streak_sec
                current_type = None
                current_streak_sec = 0.0
                penetration_sec = 0.0
                continue

            for segment in segments:
                activity_type = self._activity_type(segment, r)

                if activity_type not in DEEP_WORK_TYPES:
                    # Tolerate brief "unknown" gaps without breaking streak
                    if activity_type == "unknown" and penetration_sec < PENETRATION_TOLERANCE:
                        penetration_sec += segment.duration
                        continue

                    if current_streak_sec >= self.deep_work_threshold * 60:
                        deep_seconds += current_streak_sec
                    current_type = None
                    current_streak_sec = 0.0
                    penetration_sec = 0.0
                    continue

                if activity_type == current_type:
                    current_streak_sec += segment.duration
                    penetration_sec = 0.0
                else:
                    if current_streak_sec >= self.deep_work_threshold * 60:
                        deep_seconds += current_streak_sec
                    current_type = activity_type
                    current_streak_sec = segment.duration
                    penetration_sec = 0.0

        if current_streak_sec >= self.deep_work_threshold * 60:
            deep_seconds += current_streak_sec

        return deep_seconds / 3600

    def _count_switches(self, slices: List[ActivitySlice], rules: List[RuleResult]) -> int:
        if len(rules) < 2:
            return 0

        # Build sustained segments: merge consecutive same-type entries
        DEBOUNCE_THRESHOLD = 30  # seconds - segments shorter than this are noise
        segments: List[Tuple[str, float]] = []  # (activity_type, total_duration)

        for s, r in zip(slices, rules):
            activity_type = self._activity_type(s, r)
            for segment in self._analysis_segments(s, r):
                if segments and segments[-1][0] == activity_type:
                    segments[-1] = (
                        activity_type,
                        segments[-1][1] + segment.duration,
                    )
                else:
                    segments.append((activity_type, segment.duration))

        # Filter out noise segments (< threshold) by absorbing into neighbors
        filtered: List[str] = []
        for activity_type, duration in segments:
            if duration < DEBOUNCE_THRESHOLD:
                continue  # Skip brief flickers
            if filtered and filtered[-1] == activity_type:
                continue  # Same as previous sustained segment
            filtered.append(activity_type)

        # Count transitions in filtered list
        return max(0, len(filtered) - 1)

    def _activity_breakdown(
        self, slices: List[ActivitySlice], rules: List[RuleResult]
    ) -> Dict[str, float]:
        totals: Dict[str, float] = defaultdict(float)
        for s, r in zip(slices, rules):
            activity_type = self._activity_type(s, r)
            for segment in self._analysis_segments(s, r):
                totals[activity_type] += segment.duration

        total_sec = sum(totals.values())
        if total_sec == 0:
            return {}

        return {k: v / 3600 for k, v in totals.items()}

    def _hourly_scores(
        self, slices: List[ActivitySlice], rules: List[RuleResult]
    ) -> List[Tuple[int, int]]:
        hourly: Dict[int, List[Tuple[ActivitySlice, RuleResult]]] = defaultdict(list)
        for s, r in zip(slices, rules):
            for segment in self._analysis_segments(s, r, split_by_hour=True):
                hourly[segment.start.hour].append((segment, r))

        scores = []
        for hour in sorted(hourly.keys()):
            items = hourly[hour]
            h_slices = [s for s, _ in items]
            h_rules = [r for _, r in items]
            deep = self._deep_work_hours(h_slices, h_rules)
            switches = self._count_switches(h_slices, h_rules)
            breakdown = self._activity_breakdown(h_slices, h_rules)
            total = self._total_hours(h_slices, h_rules)
            score = self._focus_score(deep, switches, breakdown, total)
            scores.append((hour, score))

        return scores

    @staticmethod
    def _split_slice_by_hour(s: ActivitySlice) -> List[ActivitySlice]:
        """Split a slice at hour boundaries for accurate hourly aggregation."""
        if s.end <= s.start or s.duration <= 0:
            return []

        segments: List[ActivitySlice] = []
        cursor = s.start
        while cursor < s.end:
            next_hour = cursor.replace(minute=0, second=0, microsecond=0) + timedelta(
                hours=1
            )
            segment_end = min(s.end, next_hour)
            segment_duration = (segment_end - cursor).total_seconds()
            if segment_duration > 0:
                segments.append(
                    ActivitySlice(
                        start=cursor,
                        end=segment_end,
                        duration=segment_duration,
                        is_afk=s.is_afk,
                        primary_app=s.primary_app,
                        primary_title=s.primary_title,
                        web_url=s.web_url,
                    )
                )
            cursor = segment_end

        return segments

    def _focus_score(
        self,
        deep_work_hours: float,
        switch_count: int,
        breakdown: Dict[str, float],
        total_hours: float,
    ) -> int:
        score = 60.0

        deep_work_bonus = min(deep_work_hours * 2 * 10, 30)
        score += deep_work_bonus

        switch_penalty = min(switch_count * 3, 30)
        score -= switch_penalty

        if total_hours > 0:
            distraction_hours = sum(
                breakdown.get(t, 0) for t in DISTRACTION_TYPES
            )
            distraction_ratio = distraction_hours / total_hours
            score -= distraction_ratio * 40

        return int(max(0, min(100, score)))

    def _productivity_score(self, slices: List[ActivitySlice], rules: List["RuleResult"]) -> int:
        """Weighted productivity score (0-100). Uses rule weight for nuance."""
        from aw_coach.rules.engine import DEFAULT_WEIGHTS

        total_weighted = 0.0
        total_duration = 0.0

        for s, r in zip(slices, rules):
            activity_type = self._activity_type(s, r)
            weight = (
                r.weight
                if r.weight is not None
                else DEFAULT_WEIGHTS.get(activity_type, 0.0)
            )
            for segment in self._analysis_segments(s, r):
                total_weighted += weight * segment.duration
                total_duration += segment.duration

        if total_duration == 0:
            return 0

        raw = total_weighted / total_duration  # Range: -0.5 to 1.0
        normalized = (raw + 0.5) / 1.5 * 100  # Map to 0-100
        return int(max(0, min(100, normalized)))

    def _detect_death_loops(
        self, slices: List[ActivitySlice], rules: Optional[List[RuleResult]] = None
    ) -> List[Dict]:
        """Detect A↔B repetitive switching patterns (>=3 full alternations)."""
        if len(slices) < 6:
            return []

        if rules is None:
            apps = [
                segment.primary_app
                for s in slices
                if not s.is_afk
                for segment in self._clip_to_work_schedule(s)
            ]
        else:
            apps = [
                s.primary_app
                for s, r in zip(slices, rules)
                if self._include_in_analysis(s, r)
            ]
        if len(apps) < 6:
            return []

        loops = []
        i = 0
        while i < len(apps) - 5:
            a, b = apps[i], apps[i + 1]
            if a == b:
                i += 1
                continue

            alternations = 0
            j = i
            while j < len(apps):
                expected_a = a if (j - i) % 2 == 0 else b
                if apps[j] == expected_a:
                    if (j - i) % 2 == 1:
                        alternations += 1
                    j += 1
                else:
                    break

            if alternations >= 3:
                loops.append({
                    "apps": [a, b],
                    "alternations": alternations,
                    "start_index": i,
                })
                i = j
            else:
                i += 1

        return loops
