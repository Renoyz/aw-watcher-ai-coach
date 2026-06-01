"""Background scheduler for periodic analysis and reporting."""

from __future__ import annotations

import logging
import signal
import time
from collections import Counter
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Optional

from aw_coach.analyzer import PatternAnalyzer
from aw_coach.classifier import create_classifier
from aw_coach.collector import DataCollector, _local_to_utc
from aw_coach.config import Config, load_config
from aw_coach.notify import send_notification
from aw_coach.report import ReportGenerator, generate_html_dashboard

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from aw_coach.storage import Storage


class CoachScheduler:
    def __init__(self, config: Config, dashboard_url: Optional[str] = None):
        self.config = config
        self.dashboard_url = dashboard_url
        self.classifier = create_classifier(
            config,
            on_hybrid_fallback=lambda e: logger.warning(
                f"Failed to initialize hybrid backend: {e}. Falling back to rule_only."
            ),
        )
        self.analyzer = PatternAnalyzer(config.analysis)
        self.reporter = ReportGenerator(config)
        self._collector: Optional[DataCollector] = None
        self._running = False
        self._bucket_created = False
        self._storage: Optional["Storage"] = None

    @property
    def storage(self):
        if self._storage is None:
            from aw_coach.storage import Storage

            self._storage = Storage(self.config.db_path)
        return self._storage

    def _classify_slices(self, slices):
        """Classify slices using either RuleEngine or HybridBackend."""
        return self.classifier.batch_classify(slices)

    @property
    def collector(self) -> DataCollector:
        if self._collector is None:
            self._collector = DataCollector(client_name="aw-coach")
        return self._collector

    @property
    def bucket_id(self) -> str:
        return f"ai-coach_{self.collector.hostname}"

    def _ensure_bucket(self) -> None:
        if self._bucket_created:
            return
        try:
            self.collector.client.create_bucket(
                self.bucket_id,
                event_type="ai.coach.activity",
                queued=False,
            )
            self._bucket_created = True
        except Exception:
            self._bucket_created = True

    def _restore_last_summary(self) -> datetime:
        """Restore last_summary from persistent storage."""
        raw = self.storage.get_scheduler_state("last_summary")
        if raw:
            try:
                return datetime.fromisoformat(raw)
            except ValueError:
                pass
        return datetime.now()

    def _save_last_summary(self, dt: datetime) -> None:
        """Persist last_summary to SQLite."""
        try:
            self.storage.set_scheduler_state("last_summary", dt.isoformat())
        except Exception:
            pass

    def _restore_last_hourly(self, now: datetime) -> datetime:
        """Restore the end boundary of the last completed hourly analysis."""
        raw = self.storage.get_scheduler_state("last_hourly")
        if raw:
            try:
                return self._prev_hour_boundary(datetime.fromisoformat(raw))
            except ValueError:
                pass
        return self._prev_hour_boundary(now)

    def _save_last_hourly(self, dt: datetime) -> None:
        """Persist the end boundary of the last completed hourly analysis."""
        try:
            self.storage.set_scheduler_state("last_hourly", dt.isoformat())
        except Exception:
            logger.debug("Failed to persist last_hourly", exc_info=True)

    def run(self) -> None:
        self._running = True
        signal.signal(signal.SIGINT, self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)

        logger.info("AI Coach scheduler started")
        logger.info(f"  backend: {self.config.ai.backend}")
        logger.info(f"  report time: {self.config.report.daily_report_time}")

        last_hourly = self._restore_last_hourly(datetime.now())
        last_summary = self._restore_last_summary()
        last_hourly = self._catch_up_hourly(last_hourly, datetime.now())
        self._save_last_hourly(last_hourly)
        logger.info(f"  last_hourly restored: {last_hourly.strftime('%Y-%m-%d %H:%M')}")
        logger.info(f"  last_summary restored: {last_summary.strftime('%H:%M')}")

        while self._running:
            now = datetime.now()

            # Hourly analysis - aligned to clock hours
            next_hour = last_hourly + timedelta(hours=1)
            if now >= next_hour:
                if self._hourly_analyze(last_hourly, next_hour):
                    last_hourly = next_hour
                    self._save_last_hourly(last_hourly)

            # Instant summary
            interval = self.config.report.instant_summary_interval_hours
            if (now - last_summary).total_seconds() >= interval * 3600:
                self._send_instant_summary(now)
                last_summary = now
                self._save_last_summary(last_summary)

            # Daily report - check file to avoid duplicate on restart
            report_time = self.config.report.daily_report_time
            hour, minute = int(report_time.split(":")[0]), int(report_time.split(":")[1])
            if now.hour == hour and now.minute >= minute:
                report_path = (
                    self.config.reports_dir / "daily" / f"{now.date().isoformat()}.md"
                )
                if not report_path.exists():
                    self._generate_daily_report(now.date())

            time.sleep(60)

    @staticmethod
    def _prev_hour_boundary(dt: datetime) -> datetime:
        return dt.replace(minute=0, second=0, microsecond=0)

    def _shutdown(self, signum, frame) -> None:
        logger.info("Shutting down scheduler...")
        try:
            now = datetime.now()
            # Persist last_summary so timer survives restarts
            self._save_last_summary(now)
            last_boundary = self._prev_hour_boundary(now)
            if (now - last_boundary).total_seconds() > 60:
                logger.info(
                    f"Flushing partial hour: "
                    f"{last_boundary.strftime('%H:%M')}-{now.strftime('%H:%M')}"
                )
                self._hourly_analyze(last_boundary, now)
        except Exception as e:
            logger.warning(f"Flush on shutdown failed (non-fatal): {e}")
        self._running = False

    def _catch_up_hourly(self, last_hourly: datetime, now: datetime) -> datetime:
        """Analyze missed complete hours after a restart, capped to one day."""
        target = self._prev_hour_boundary(now)
        if last_hourly >= target:
            return last_hourly

        max_backfill_hours = 24
        earliest = target - timedelta(hours=max_backfill_hours)
        if last_hourly < earliest:
            logger.info(
                f"Limiting hourly backfill from {last_hourly.isoformat()} "
                f"to {earliest.isoformat()}"
            )
            last_hourly = earliest

        current = last_hourly
        while current < target:
            next_hour = current + timedelta(hours=1)
            if not self._hourly_analyze(current, next_hour):
                return current
            current = next_hour
            self._save_last_hourly(current)

        return current

    def _hourly_analyze(self, hour_start: datetime, hour_end: datetime) -> bool:
        try:
            slices = self.collector.fetch_range(hour_start, hour_end)
        except Exception as e:
            logger.warning(f"Failed to fetch data: {e}")
            return False

        if not slices:
            return True

        rules = self._classify_slices(slices)
        analysis = self.analyzer.analyze(slices, rules)

        # Determine dominant activity type
        type_counts = Counter(r.activity_type for r in rules)
        top_type = type_counts.most_common(1)[0][0] if type_counts else "unknown"
        avg_confidence = sum(r.confidence for r in rules) / len(rules) if rules else 0.0
        methods = set(r.method for r in rules)
        method = "rule" if all(m.startswith("rule") for m in methods) else "hybrid"

        # Write to ai-coach bucket
        self._ensure_bucket()
        if self._event_exists("hourly_analysis", hour_start, hour_end):
            logger.info(
                f"Hourly analysis already exists: {hour_start.strftime('%H:%M')}-"
                f"{hour_end.strftime('%H:%M')}"
            )
            return True

        try:
            from aw_core.models import Event

            event = Event(
                timestamp=_local_to_utc(hour_start),
                duration=timedelta(seconds=int((hour_end - hour_start).total_seconds())),
                data={
                    "schema_version": 2,
                    "type": "hourly_analysis",
                    "period_start": hour_start.isoformat(),
                    "period_end": hour_end.isoformat(),
                    "activity_type": top_type,
                    "confidence": round(avg_confidence, 3),
                    "classification_method": method,
                    "focus_score": analysis.focus_score,
                    "switch_count": analysis.switch_count,
                    "effective_hours": round(analysis.effective_hours, 3),
                    "deep_work_hours": round(analysis.deep_work_hours, 3),
                    "productivity_score": analysis.productivity_score,
                    "death_loops": analysis.death_loops,
                    "activity_breakdown": {
                        k: round(v, 3) for k, v in analysis.activity_breakdown.items()
                    },
                },
            )
            self.collector.client.insert_event(self.bucket_id, event)
            logger.info(
                f"Hourly analysis written: {hour_start.strftime('%H:%M')}-"
                f"{hour_end.strftime('%H:%M')} "
                f"effective={analysis.effective_hours:.1f}h focus={analysis.focus_score}"
            )
        except Exception as e:
            logger.warning(f"Failed to write hourly event to bucket: {e}")
            return False

        return True

    def _event_exists(self, event_type: str, period_start: datetime, period_end: datetime) -> bool:
        """Check whether an analysis event for the same period already exists."""
        try:
            events = self.collector.client.get_events(
                self.bucket_id,
                start=_local_to_utc(period_start),
                end=_local_to_utc(period_end),
            )
        except Exception:
            return False

        target_start = period_start.isoformat()
        target_end = period_end.isoformat()
        target_duration = int((period_end - period_start).total_seconds())
        target_timestamp = _local_to_utc(period_start)

        for event in events:
            data = getattr(event, "data", {})
            if data.get("type") != event_type:
                continue
            if data.get("period_start") == target_start and data.get("period_end") == target_end:
                return True

            timestamp = getattr(event, "timestamp", None)
            duration = getattr(event, "duration", None)
            duration_seconds = (
                int(duration.total_seconds()) if hasattr(duration, "total_seconds") else None
            )
            if timestamp == target_timestamp and duration_seconds == target_duration:
                return True

        return False

    def _should_notify(self) -> bool:
        return self.config.report.notification_method != "cli_only"

    def _send_instant_summary(self, now: datetime) -> None:
        if not self._should_notify():
            return

        interval = self.config.report.instant_summary_interval_hours
        start = now - timedelta(hours=interval)
        try:
            slices = self.collector.fetch_range(start, now)
        except Exception:
            return

        if not slices:
            return

        rules = self._classify_slices(slices)
        analysis = self.analyzer.analyze(slices, rules)

        # Generate dashboard for click-to-open
        detail_url = None
        try:
            generate_html_dashboard(self.config, now.date(), analysis, slices, rules)
            detail_url = self.dashboard_url
        except Exception:
            pass

        top_activity = ""
        if analysis.activity_breakdown:
            top_activity = max(analysis.activity_breakdown, key=analysis.activity_breakdown.get)

        # Build richer notification body
        body_lines = [
            f"有效工作: {analysis.effective_hours:.1f}h | 专注度: {analysis.focus_score}/100",
        ]
        if analysis.death_loops:
            body_lines.append(f"⚠️ 检测到 {len(analysis.death_loops)} 个切换循环")
        if top_activity:
            body_lines.append(f"主要活动: {top_activity}")

        suggestions = self.reporter._generate_suggestions(analysis, is_weekly=False)
        if suggestions:
            body_lines.append(f"💡 {suggestions[0]}")

        body = "\n".join(body_lines)
        send_notification(
            f"AI Coach 摘要 (过去{interval}h)",
            body,
            detail_url=detail_url,
        )

    def _generate_daily_report(self, report_date: date) -> None:
        start = datetime.combine(report_date, datetime.min.time())
        end = datetime.combine(report_date, datetime.max.time())

        try:
            slices = self.collector.fetch_range(start, end)
        except Exception as e:
            logger.error(f"Failed to generate daily report: {e}")
            return

        if not slices:
            return

        rules = self._classify_slices(slices)
        analysis = self.analyzer.analyze(slices, rules)
        report_text = self.reporter.generate_daily(report_date, analysis)

        # Save report to file
        reports_dir = self.config.reports_dir / "daily"
        reports_dir.mkdir(parents=True, exist_ok=True)
        report_path = reports_dir / f"{report_date.isoformat()}.md"
        report_path.write_text(report_text, encoding="utf-8")
        logger.info(f"Daily report saved: {report_path}")

        # Write report event to bucket
        self._ensure_bucket()
        try:
            from aw_core.models import Event
            event = Event(
                timestamp=_local_to_utc(datetime.combine(report_date, datetime.min.time())),
                duration=timedelta(seconds=0),
                data={
                    "type": "daily_report",
                    "report_date": report_date.isoformat(),
                    "report_path": str(report_path),
                    "total_hours": round(analysis.total_hours, 2),
                    "effective_hours": round(analysis.effective_hours, 2),
                    "deep_work_hours": round(analysis.deep_work_hours, 2),
                    "focus_score": analysis.focus_score,
                    "switch_count": analysis.switch_count,
                },
            )
            self.collector.client.insert_event(self.bucket_id, event)
        except Exception as e:
            logger.warning(f"Failed to write report event: {e}")

        # Generate dashboard for click-to-open
        detail_url = None
        try:
            generate_html_dashboard(self.config, report_date, analysis, slices, rules)
            detail_url = self.dashboard_url
        except Exception:
            pass

        # Send notification
        if self._should_notify():
            send_notification(
                f"AI Coach 日报 - {report_date.isoformat()}",
                f"有效工作 {analysis.effective_hours:.1f}h | "
                f"专注度 {analysis.focus_score}/100 | "
                f"深度工作 {analysis.deep_work_hours:.1f}h",
                detail_url=detail_url,
            )


def run_scheduler(dashboard_url: Optional[str] = None) -> None:
    config = load_config()
    scheduler = CoachScheduler(config, dashboard_url=dashboard_url)
    scheduler.run()
