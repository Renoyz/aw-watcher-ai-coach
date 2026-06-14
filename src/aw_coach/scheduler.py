"""Background scheduler for periodic analysis and reporting."""

from __future__ import annotations

import json
import logging
import os
import signal
import time
from collections import Counter
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Dict, Optional

from aw_coach.analyzer import PatternAnalyzer
from aw_coach.classifier import create_classifier
from aw_coach.collector import DataCollector, _local_to_utc
from aw_coach.config import Config, load_config
from aw_coach.notify import send_notification
from aw_coach.report import ReportGenerator, generate_html_dashboard

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from aw_coach.context_stack import ContextStack
    from aw_coach.screenshot import ScreenshotTrigger
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

        # Phase 1: semantic enrichment (lazy init)
        self._enricher = None
        self._chain_analyzer = None
        self._semantic_state_json: Optional[str] = None

        # Context Stack (Phase 1+): track primary context across interrupts
        self._context_stack: Optional["ContextStack"] = None

        # Screenshot analysis (lightweight visual signal)
        self._screenshot_trigger: Optional["ScreenshotTrigger"] = None
        self._last_screenshot_image = None

        # Change-only snapshot tracking
        self._last_snapshot_payload: Optional[Dict] = None

        # Service health heartbeat tracking
        self._started_at: Optional[datetime] = None
        self._last_service_error: Optional[str] = None
        self._last_service_error_at: Optional[datetime] = None

    @property
    def storage(self):
        if self._storage is None:
            from aw_coach.storage import Storage

            self._storage = Storage(self.config.db_path)
        return self._storage

    def _classify_slices(self, slices):
        """Classify slices using either RuleEngine or HybridBackend."""
        return self.classifier.batch_classify(slices)

    # ------------------------------------------------------------------
    # Phase 1: semantic enrichment (per-minute state update)
    # ------------------------------------------------------------------

    def _update_semantic_state(self, now: datetime) -> None:
        """Fetch recent activity, enrich with semantics, persist to SQLite."""
        try:
            from aw_coach.chain_analyzer import ChainAnalyzer
            from aw_coach.enriched_state import EnrichedStateAssembler
            from aw_coach.rules.engine import RuleEngine

            if self._enricher is None:
                self._enricher = EnrichedStateAssembler()
            if self._chain_analyzer is None:
                self._chain_analyzer = ChainAnalyzer()

            start = now - timedelta(minutes=30)
            slices = [
                s for s in self.collector.fetch_range(start, now)
                if not s.is_afk and getattr(s, "duration", 0) >= 3
            ]
            if not slices:
                return

            # Align timezone: slices may be offset-aware while `now` is naive
            _tz = getattr(slices[0].end, "tzinfo", None)
            if _tz and now.tzinfo is None:
                now = now.replace(tzinfo=_tz)

            engine = RuleEngine.with_all_rules()
            latest = max(slices, key=lambda s: s.end)
            rule = engine.classify(
                latest.primary_app, latest.primary_title, latest.web_url
            )

            # Rough block duration
            block_sec = 0
            for s in reversed(sorted(slices, key=lambda s: s.end)):
                s_rule = engine.classify(
                    s.primary_app, s.primary_title, s.web_url
                )
                if s_rule.activity_type == rule.activity_type:
                    block_sec += getattr(s, "duration", 60)
                else:
                    break

            # Switches in last 5 min
            recent_start = now - timedelta(minutes=5)
            recent = [s for s in slices if s.end >= recent_start]
            switches = 0
            if len(recent) >= 2:
                sorted_recent = sorted(recent, key=lambda s: s.end)
                types = [
                    engine.classify(r.primary_app, r.primary_title, r.web_url).activity_type
                    for r in sorted_recent
                ]
                switches = sum(
                    1 for i in range(len(types) - 1) if types[i] != types[i + 1]
                )

            state = self._enricher.assemble(
                app=latest.primary_app,
                title=latest.primary_title,
                url=getattr(latest, "web_url", None),
                active_block_minutes=block_sec // 60,
                rule_activity=rule.activity_type,
                switches_last_5min=switches,
            )

            # --- Context Stack update -------------------------------------
            if self._context_stack is None:
                from aw_coach.context_stack import ContextStack
                self._context_stack = ContextStack()
            self._context_stack.update(state, now)
            # Override active_block_minutes with context-stack accumulated time
            cs_minutes = self._context_stack.get_active_block_minutes()
            if cs_minutes > 0:
                # Re-assemble with corrected block time
                state = self._enricher.assemble(
                    app=latest.primary_app,
                    title=latest.primary_title,
                    url=getattr(latest, "web_url", None),
                    active_block_minutes=cs_minutes,
                    rule_activity=rule.activity_type,
                    switches_last_5min=switches,
                )
            # ---------------------------------------------------------------

            # Chain analysis (last 10 slices)
            chain_records = []
            for s in sorted(slices, key=lambda s: s.end)[-10:]:
                sr = engine.classify(s.primary_app, s.primary_title, s.web_url)
                chain_records.append(
                    self._enricher.assemble(
                        app=s.primary_app,
                        title=s.primary_title,
                        url=getattr(s, "web_url", None),
                        active_block_minutes=getattr(s, "duration", 60) // 60,
                        rule_activity=sr.activity_type,
                    )
                )
            chain = self._chain_analyzer.analyze(chain_records)

            # --- Screenshot analysis (optional lightweight visual signal) ---
            screenshot_result = None
            try:
                from aw_coach.screenshot import (
                    ScreenshotTrigger,
                    capture_and_analyze,
                    capture_screen,
                )

                if self._screenshot_trigger is None:
                    self._screenshot_trigger = ScreenshotTrigger()
                screenshot_result = capture_and_analyze(
                    state, self._screenshot_trigger, self._last_screenshot_image
                )
                if screenshot_result:
                    self._last_screenshot_image = capture_screen()
            except Exception:
                logger.debug("Screenshot analysis failed", exc_info=True)
            # ---------------------------------------------------------------

            import json

            payload = {
                "state": state.to_dict(),
                "chain": {
                    "pattern": chain.pattern,
                    "depth_score": chain.depth_score,
                    "fragmentation_score": chain.fragmentation_score,
                    "insight": chain.insight,
                },
                "context_stack": self._context_stack.to_dict(),
            }
            if screenshot_result:
                payload["screenshot"] = {
                    "diff_ratio": screenshot_result.diff_ratio,
                    "content_type": screenshot_result.content_type,
                    "brightness": screenshot_result.brightness,
                    "ocr_text": screenshot_result.ocr_text,
                    "trigger_reason": screenshot_result.trigger_reason,
                }
            self._semantic_state_json = json.dumps(payload, ensure_ascii=False)
            self.storage.set_scheduler_state("semantic_state", self._semantic_state_json)

            # Change-only snapshot: persist only when state meaningfully changes
            change_reason = self._detect_change(self._last_snapshot_payload, payload)
            if change_reason:
                try:
                    self.storage.save_state_snapshot(
                        self._semantic_state_json, change_reason
                    )
                    self._last_snapshot_payload = payload
                    logger.info(
                        f"State snapshot saved: {change_reason} | "
                        f"mode={state.likely_mode}, project={state.semantic_project}, "
                        f"risk={state.risk_level}"
                    )
                except Exception:
                    logger.debug("State snapshot save failed", exc_info=True)

            interrupt = self._context_stack.get_interruption_summary()
            logger.debug(
                f"Semantic state updated: mode={state.likely_mode}, "
                f"risk={state.risk_level}, pattern={chain.pattern}, "
                f"cs_depth={self._context_stack.depth}, "
                f"cs_minutes={cs_minutes}"
                + (f", interrupt={interrupt}" if interrupt else "")
            )
        except Exception:
            logger.debug("Semantic state update failed", exc_info=True)

    def _detect_change(self, prev: Optional[Dict], curr: Dict) -> Optional[str]:
        """Detect whether current state is meaningfully different from previous snapshot.

        Returns a change_reason string if a snapshot should be saved, or None if unchanged.
        """
        if prev is None:
            return "first_run"
        ps = prev.get("state", {})
        cs = curr.get("state", {})
        if ps.get("likely_mode") != cs.get("likely_mode"):
            return "mode_change"
        if ps.get("semantic_project") != cs.get("semantic_project"):
            return "project_change"
        if ps.get("risk_level") != cs.get("risk_level"):
            return "risk_change"
        p_cs = prev.get("context_stack", {})
        c_cs = curr.get("context_stack", {})
        if p_cs.get("depth") != c_cs.get("depth"):
            return "cs_depth_change"
        p_ss = prev.get("screenshot")
        c_ss = curr.get("screenshot")
        if c_ss and c_ss.get("ocr_text") and (
            not p_ss or p_ss.get("ocr_text") != c_ss.get("ocr_text")
        ):
            return "screenshot_trigger"
        return None

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

    def _write_service_health(
        self,
        now: datetime,
        status: str = "running",
        error: Optional[Exception | str] = None,
    ) -> None:
        """Persist service heartbeat state."""
        if self._started_at is None:
            self._started_at = now

        previous_payload = None
        previous_success = None
        try:
            raw = self.storage.get_scheduler_state("service_health")
            if raw:
                previous_payload = json.loads(raw)
                previous_success = previous_payload.get("last_success")
        except Exception:
            logger.debug("Failed to read previous service health", exc_info=True)
            previous_payload = None

        if error is not None:
            self._last_service_error = str(error)
            self._last_service_error_at = now
        elif status == "running":
            self._last_service_error = None
            self._last_service_error_at = None
        elif previous_payload is not None and self._last_service_error is None:
            self._last_service_error = previous_payload.get("last_error")
            error_at = previous_payload.get("last_error_at")
            if error_at and self._last_service_error_at is None:
                try:
                    self._last_service_error_at = datetime.fromisoformat(error_at)
                except (TypeError, ValueError):
                    self._last_service_error_at = None

        last_success = previous_success
        if status == "running" and error is None:
            last_success = now.isoformat()

        payload_json = json.dumps(
            {
                "schema_version": 1,
                "pid": os.getpid(),
                "started_at": self._started_at.isoformat(),
                "last_tick": now.isoformat(),
                "last_success": last_success,
                "last_error": self._last_service_error,
                "last_error_at": (
                    self._last_service_error_at.isoformat()
                    if self._last_service_error_at
                    else None
                ),
                "status": status,
            }
        )
        try:
            self.storage.set_scheduler_state("service_health", payload_json)
        except Exception:
            logger.debug("Failed to write service health", exc_info=True)
            return

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
        self._started_at = datetime.now()
        self._write_service_health(self._started_at, status="running")

        while self._running:
            now = datetime.now()
            try:
                # Phase 1: per-minute semantic state update
                self._update_semantic_state(now)

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

                self._write_service_health(now, status="running")
            except Exception as e:
                logger.debug("Scheduler loop error", exc_info=True)
                try:
                    self._write_service_health(now, status="running", error=e)
                except Exception:
                    logger.debug("Failed to write service health after loop error", exc_info=True)

            time.sleep(60)

    @staticmethod
    def _prev_hour_boundary(dt: datetime) -> datetime:
        return dt.replace(minute=0, second=0, microsecond=0)

    def _shutdown(self, signum, frame) -> None:
        logger.info("Shutting down scheduler...")
        now = datetime.now()
        try:
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
        finally:
            try:
                self._write_service_health(now, status="stopped")
            except Exception:
                logger.debug(
                    "Failed to write service health during shutdown", exc_info=True
                )
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
