"""Background scheduler for periodic analysis and reporting."""

from __future__ import annotations

import json
import logging
import signal
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Dict, List, Optional

from aw_coach.analyzer import ANALYSIS_SCHEMA_VERSION, PatternAnalyzer
from aw_coach.background_summary import generate_background_summary
from aw_coach.classifier import create_classifier
from aw_coach.collector import DataCollector, _local_to_utc
from aw_coach.config import Config, load_config
from aw_coach.notification_gate import NotificationGate
from aw_coach.notify import send_notification
from aw_coach.policy import ActionDecision, PolicyEngine
from aw_coach.report import ReportGenerator, generate_html_dashboard
from aw_coach.state import CoachAgentStateMachine

logger = logging.getLogger(__name__)
TASK_TRACKER_STATE_KEY = "task_tracker"

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

        # Phase 2: policy engine for event-driven actions
        self._policy_engine: Optional["PolicyEngine"] = None
        self._notification_gate = NotificationGate.from_config(config)

        # Phase 3: agent state machine
        self._state_machine: Optional["CoachAgentStateMachine"] = None

        # Cached rule engine (avoid reloading YAML every minute)
        self._rule_engine = None

        # Task perception
        self._task_signal_extractor = None
        self._task_fusion = None
        self._task_tracker = None
        self._task_confirm_candidates: Dict[str, datetime] = {}
        self._task_confirm_count_date: Optional[date] = None
        self._task_confirm_count = 0
        self._context_bucket_created = False
        self._last_context_capture: Optional[datetime] = None

        # Background LLM summary worker
        self._summary_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="aw-coach-summary"
        )
        self._summary_future = None
        self._last_morning_brief_date: Optional[date] = None
        self._semantic_payload: Optional[Dict] = None
        from aw_coach.cron_runner import CronRunner

        self._cron_runner = CronRunner.from_config(config.cron_jobs)

    @property
    def storage(self):
        if self._storage is None:
            from aw_coach.storage import Storage

            self._storage = Storage(self.config.db_path)
        return self._storage

    def _classify_slices(self, slices, *, allow_llm: bool = True):
        """Classify slices using either RuleEngine or HybridBackend."""
        if not allow_llm:
            engine = self._get_rule_engine()
            return [
                engine.classify(s.primary_app, s.primary_title, s.web_url)
                for s in slices
            ]
        return self.classifier.batch_classify(slices)

    # ------------------------------------------------------------------
    # Phase 1: semantic enrichment (per-minute state update)
    # ------------------------------------------------------------------

    def _update_semantic_state(self, now: datetime) -> None:
        """Fetch recent activity, enrich with semantics, persist to SQLite."""
        try:
            from aw_coach.chain_analyzer import ChainAnalyzer
            from aw_coach.enriched_state import EnrichedStateAssembler
            if self._enricher is None:
                self._enricher = EnrichedStateAssembler()
            if self._chain_analyzer is None:
                self._chain_analyzer = ChainAnalyzer()

            engine = self._get_rule_engine()

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
                recent_rules = [
                    engine.classify(r.primary_app, r.primary_title, r.web_url)
                    for r in sorted_recent
                ]
                self._annotate_task_slices(sorted_recent, recent_rules)
                keys = [self._semantic_switch_key(s) for s in sorted_recent]
                if any(keys):
                    filtered_keys = [key for key in keys if key]
                    switches = sum(
                        1
                        for i in range(len(filtered_keys) - 1)
                        if filtered_keys[i] != filtered_keys[i + 1]
                    )
                else:
                    types = [r.activity_type for r in recent_rules]
                    switches = sum(
                        1 for i in range(len(types) - 1) if types[i] != types[i + 1]
                    )

            # --- Live process enrichment + durable context capture --------
            context_snapshot = self._capture_context_snapshot(now, latest)
            live_terminal_cmd = (
                context_snapshot.terminal_command_summary
                if context_snapshot is not None
                else None
            )
            # ---------------------------------------------------------------

            # --- Preliminary state (for screenshot trigger decision) ------
            prelim_state = self._enricher.assemble(
                app=latest.primary_app,
                title=latest.primary_title,
                url=getattr(latest, "web_url", None),
                active_block_minutes=block_sec // 60,
                rule_activity=rule.activity_type,
                switches_last_5min=switches,
                terminal_command=live_terminal_cmd,
            )

            # --- Screenshot analysis (optional lightweight visual signal) ---
            screenshot_result = None
            try:
                from aw_coach.screenshot import (
                    ScreenshotTrigger,
                    capture_and_analyze,
                )

                if self.config.screenshot.enabled:
                    if self._screenshot_trigger is None:
                        self._screenshot_trigger = ScreenshotTrigger(
                            enabled=self.config.screenshot.enabled,
                            blocklist_apps=self.config.screenshot.blocklist_apps,
                        )
                    # Feed per-minute state history for same-title trigger
                    self._screenshot_trigger.record_state(
                        now, latest.primary_app, latest.primary_title
                    )
                    screenshot_result, captured_img = capture_and_analyze(
                        prelim_state,
                        self._screenshot_trigger,
                        self._last_screenshot_image,
                        rule_skip_screenshot=getattr(rule, "skip_screenshot", False),
                    )
                    if captured_img is not None:
                        self._last_screenshot_image = captured_img
            except Exception:
                logger.debug("Screenshot analysis failed", exc_info=True)
            # ---------------------------------------------------------------

            # --- Build history for detectors (last 9 *prior* slices) ----
            # Exclude `latest` so detectors/chain don't see the current slice
            # twice (once unrefined, once refined).
            sorted_slices = sorted(slices, key=lambda s: s.end)
            prior_slices = sorted_slices[:-1]  # drop latest
            history_records = []
            for s in prior_slices[-9:]:
                sr = engine.classify(s.primary_app, s.primary_title, s.web_url)
                history_records.append(
                    self._enricher.assemble(
                        app=s.primary_app,
                        title=s.primary_title,
                        url=getattr(s, "web_url", None),
                        active_block_minutes=getattr(s, "duration", 60) // 60,
                        rule_activity=sr.activity_type,
                    )
                )
            # ---------------------------------------------------------------

            # --- Final state (with OCR + live terminal + context stack) ---
            state = self._enricher.assemble(
                app=latest.primary_app,
                title=latest.primary_title,
                url=getattr(latest, "web_url", None),
                active_block_minutes=block_sec // 60,
                rule_activity=rule.activity_type,
                switches_last_5min=switches,
                terminal_command=live_terminal_cmd,
                screen_ocr_text=screenshot_result.ocr_text if screenshot_result else None,
                screen_diff_ratio=screenshot_result.diff_ratio if screenshot_result else None,
                screen_content_type=screenshot_result.content_type if screenshot_result else None,
                history=history_records,
            )
            self._apply_context_snapshot_to_state(state, context_snapshot)

            # --- Context Stack update -------------------------------------
            if self._context_stack is None:
                from aw_coach.context_stack import ContextStack
                self._context_stack = ContextStack()
            self._context_stack.update(state, now)
            # Override active_block_minutes with context-stack accumulated time
            cs_minutes = self._context_stack.get_active_block_minutes()
            if cs_minutes > 0:
                state = self._enricher.assemble(
                    app=latest.primary_app,
                    title=latest.primary_title,
                    url=getattr(latest, "web_url", None),
                    active_block_minutes=cs_minutes,
                    rule_activity=rule.activity_type,
                    switches_last_5min=switches,
                    terminal_command=live_terminal_cmd,
                    screen_ocr_text=screenshot_result.ocr_text if screenshot_result else None,
                    screen_diff_ratio=screenshot_result.diff_ratio if screenshot_result else None,
                    screen_content_type=(
                        screenshot_result.content_type if screenshot_result else None
                    ),
                    history=history_records,
                )
                self._apply_context_snapshot_to_state(state, context_snapshot)
                # Sync context stack with corrected block time
                self._context_stack.update(state, now)
            # ---------------------------------------------------------------

            # --- Chain analysis (build from final state) ------------------
            chain_records = list(history_records)
            # Append the final (refined) state so chain uses OCR-corrected mode
            chain_records.append(state)
            chain = self._chain_analyzer.analyze(chain_records)

            state = self._apply_task_perception(state, rule, now)

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
            self._semantic_payload = payload
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

            # Phase 2+3: state machine + policy-driven action from detected signals
            self._notification_gate.reset_daily_if_needed(now)
            sm = self._get_state_machine()
            sm.auto_advance(now)
            if state.agent_signal is not None:
                sm.transition_to(sm.state, signal_type=state.agent_signal.signal_type)
                policy = self._get_policy_engine()
                gate_kwargs = self._notification_gate.blackboard_kwargs(now)
                decision = policy.decide(
                    signal_type=state.agent_signal.signal_type,
                    severity=state.agent_signal.severity,
                    evidence=self._format_signal_evidence(state),
                    in_focus_block=state.detected_signal == "focused",
                    focus_block_minutes=cs_minutes,
                    **gate_kwargs,
                )
                self._apply_policy_decision(decision, state.agent_signal, now)
            elif (
                self.config.tasks.enabled
                and state.task_confidence > 0
                and state.task_confidence < 0.4
            ):
                self._maybe_queue_task_confirm_inbox(state, now)
            self._persist_state_machine()

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

    def _get_state_machine(self) -> CoachAgentStateMachine:
        if self._state_machine is None:
            raw = self.storage.get_scheduler_state("agent_state")
            if raw:
                try:
                    self._state_machine = CoachAgentStateMachine.from_json(raw)
                except Exception:
                    logger.debug("Failed to restore state machine, starting fresh")
                    self._state_machine = CoachAgentStateMachine()
            else:
                self._state_machine = CoachAgentStateMachine()
        return self._state_machine

    def _persist_state_machine(self) -> None:
        if self._state_machine is not None:
            try:
                self.storage.set_scheduler_state(
                    "agent_state", self._state_machine.to_json()
                )
            except Exception:
                logger.debug("State machine persist failed", exc_info=True)

    def _get_rule_engine(self):
        if self._rule_engine is None:
            from aw_coach.rules.engine import RuleEngine

            self._rule_engine = RuleEngine.with_all_rules()
        return self._rule_engine

    def _get_policy_engine(self) -> PolicyEngine:
        if self._policy_engine is None:
            self._policy_engine = PolicyEngine(
                max_notifications_per_day=self.config.report.daily_notification_budget,
                cooldown_seconds=self.config.report.notification_cooldown_seconds,
            )
        return self._policy_engine

    @staticmethod
    def _semantic_switch_key(s) -> Optional[str]:
        task_id = getattr(s, "task_id", None)
        if task_id and not str(task_id).startswith("unknown:"):
            return str(task_id)
        git_repo = getattr(s, "git_repo", None)
        if git_repo:
            return f"repo:{git_repo}:{getattr(s, 'git_branch', '') or ''}"
        project = getattr(s, "semantic_project", None)
        if project:
            return f"project:{project}"
        return None

    @staticmethod
    def _apply_context_snapshot_to_state(state, snapshot) -> None:
        if snapshot is None:
            return
        if getattr(snapshot, "git_repo", None):
            state.git_repo = snapshot.git_repo
            state.git_branch = snapshot.git_branch
            if not state.semantic_project:
                state.semantic_project = snapshot.git_repo
        if getattr(snapshot, "terminal_action", None):
            state.semantic_action = snapshot.terminal_action

    def _apply_task_perception(self, state, rule, now: datetime):
        if not self.config.tasks.enabled:
            return state
        from aw_coach.git_context import GitContext, get_git_context_for_project
        from aw_coach.task_fusion import TaskFusionEngine
        from aw_coach.task_signals import TaskSignalExtractor

        if self._task_signal_extractor is None:
            self._task_signal_extractor = TaskSignalExtractor(self.config.tasks)
        if self._task_fusion is None:
            self._task_fusion = TaskFusionEngine()
        if self._task_tracker is None:
            self._task_tracker = self._restore_task_tracker()
        current = self._task_tracker.current_session
        if current is not None and self._task_fusion.state.confirmed_task_id is None:
            self._task_fusion.restore_confirmed(
                task_id=current.task_id,
                label=current.label,
                project=current.project,
                intent=current.intent,
                confidence=current.confidence,
            )

        git_ctx = None
        if state.git_repo:
            git_ctx = GitContext(repo_name=state.git_repo, branch=state.git_branch)
        elif state.semantic_project:
            try:
                git_ctx = get_git_context_for_project(
                    state.semantic_project,
                    project_roots=self.config.tasks.project_roots or None,
                )
            except Exception:
                logger.debug("Git context lookup failed", exc_info=True)

        candidate = self._task_signal_extractor.extract(
            app=state.current_app,
            title=state.current_title,
            url=state.current_url,
            likely_mode=state.likely_mode,
            activity_type=rule.activity_type,
            git_ctx=git_ctx,
            filename=state.semantic_filename,
            project=state.semantic_project,
        )
        task = self._task_fusion.resolve(candidate)
        evidence = self._task_session_evidence(candidate, task, rule)
        source = self._task_session_source(state, rule)
        self._task_tracker.update(
            task,
            state,
            now,
            evidence=evidence,
            source=source,
        )
        self._persist_current_task_session()
        self._persist_task_tracker()

        state.task_id = task.task_id
        state.task_label = task.label
        state.task_intent = task.intent
        state.task_confidence = task.confidence
        return state

    def _restore_task_tracker(self):
        from aw_coach.task_tracker import TaskSessionTracker

        try:
            active = self.storage.get_active_task_session()
            if active:
                session = self.storage.task_session_from_row(active)
                return TaskSessionTracker.from_active_session(
                    session,
                    last_update=self._parse_stored_datetime(
                        active.get("updated_at")
                    ),
                )
        except Exception:
            logger.debug("Failed to restore active task session", exc_info=True)

        raw = self.storage.get_scheduler_state(TASK_TRACKER_STATE_KEY)
        if raw:
            try:
                return TaskSessionTracker.from_json(raw)
            except Exception:
                logger.debug("Failed to restore task tracker, starting fresh", exc_info=True)
        return TaskSessionTracker()

    @staticmethod
    def _parse_stored_datetime(value):
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            return None

    @staticmethod
    def _task_session_evidence(candidate, task, rule):
        from aw_coach.task_models import TaskEvidence

        evidence = []
        if task.task_id == candidate.task_id:
            evidence.extend(candidate.evidence)
        else:
            evidence.append(
                TaskEvidence(
                    "fusion",
                    f"retained:{task.task_id}; candidate:{candidate.task_id}",
                    task.confidence,
                )
            )
        evidence.append(TaskEvidence("rule", rule.activity_type, rule.confidence))
        return evidence

    @staticmethod
    def _task_session_source(state, rule) -> Dict:
        return {
            "app": state.current_app,
            "title": state.current_title,
            "url": state.current_url,
            "git_repo": state.git_repo,
            "git_branch": state.git_branch,
            "semantic_project": state.semantic_project,
            "semantic_filename": state.semantic_filename,
            "likely_mode": state.likely_mode,
            "rule_activity": rule.activity_type,
            "rule_confidence": rule.confidence,
        }

    def _persist_task_tracker(self) -> None:
        tasks_config = getattr(getattr(self, "config", None), "tasks", None)
        if (
            tasks_config is None
            or not getattr(tasks_config, "enabled", False)
            or self._task_tracker is None
        ):
            return
        try:
            self.storage.set_scheduler_state(
                TASK_TRACKER_STATE_KEY,
                self._task_tracker.to_json(),
            )
        except Exception:
            logger.debug("Task tracker persist failed", exc_info=True)

    def _persist_current_task_session(self) -> None:
        if not self.config.tasks.enabled or self._task_tracker is None:
            return
        current = self._task_tracker.current_session
        if current is None:
            return
        try:
            self.storage.upsert_task_session(current)
            self.storage.rebuild_task_daily_summary(current.started_at.date().isoformat())
        except Exception:
            logger.debug("Current task session persist failed", exc_info=True)

    @staticmethod
    def _format_signal_evidence(state) -> str:
        base = ""
        if state.agent_signal is not None:
            base = state.agent_signal.evidence
        if state.task_label:
            prefix = f"[{state.task_label}] "
            if base and not base.startswith(prefix):
                return prefix + base
            if not base:
                return prefix.strip()
        return base

    def _maybe_queue_task_confirm_inbox(self, state, now: datetime) -> None:
        key = f"task_confirm:{state.task_id}"
        if self._task_confirm_count_date != now.date():
            self._task_confirm_count_date = now.date()
            self._task_confirm_count = 0

        if (
            self._task_confirm_count
            >= self.config.report.delivery.task_confirm_daily_limit
        ):
            return

        first_seen = self._task_confirm_candidates.setdefault(key, now)
        required_seconds = self.config.report.delivery.task_confirm_min_minutes * 60
        if (now - first_seen).total_seconds() < required_seconds:
            return

        sent_key = f"task_confirm_sent:{state.task_id}"
        elapsed = self._notification_gate.seconds_since(sent_key, now)
        if elapsed is not None and elapsed < 3600:
            return

        self._deliver_message(
            kind="task_confirm",
            title="AI Coach · task_confirm",
            body=f"无法确认当前任务：{state.task_label or state.task_id}",
            severity=0.4,
            reason="低置信度任务识别，请运行 aw-coach task confirm",
            now=now,
            delivery=self.config.report.delivery.task_confirm,
        )
        self._notification_gate.record_event(sent_key, now)
        self._task_confirm_count += 1

    def _apply_policy_decision(self, decision: ActionDecision, signal, now: datetime) -> None:
        """Execute the action chosen by the policy engine, respecting the state machine."""
        sm = self._get_state_machine()

        if decision.action == "log_only":
            logger.debug(f"Policy: log_only | {decision.reason} | {signal.signal_type}")
            sm.transition_to(sm.state, signal_type=signal.signal_type, action="log_only")
            self._persist_state_machine()
            return

        if decision.action == "inbox":
            if not sm.may_inbox(signal.signal_type):
                logger.debug(
                    f"State machine blocked inbox for {signal.signal_type} "
                    f"(state={sm.state.name})"
                )
                return
            logger.info(f"Policy: inbox | {decision.reason} | {signal.signal_type}")
            self._deliver_message(
                kind=signal.signal_type,
                title=f"AI Coach · {signal.signal_type}",
                body=decision.evidence or signal.evidence,
                severity=signal.severity,
                reason=decision.reason,
                now=now,
                delivery=self.config.report.delivery.medium_signal,
            )
            sm.transition_to(sm.state, signal_type=signal.signal_type, action="inbox")
            self._persist_state_machine()
            return

        if decision.action == "notify_now":
            if not sm.may_notify(signal.signal_type):
                logger.debug(
                    f"State machine blocked notify for {signal.signal_type} "
                    f"(state={sm.state.name})"
                )
                return
            logger.info(f"Policy: notify_now | {decision.reason} | {signal.signal_type}")
            try:
                sm.transition_to(sm.state, signal_type=signal.signal_type, action="notify")
                result = self._deliver_message(
                    kind=signal.signal_type,
                    title=f"AI Coach · {signal.signal_type}",
                    body=decision.evidence or signal.evidence,
                    severity=signal.severity,
                    reason=decision.reason,
                    now=now,
                    delivery=self.config.report.delivery.high_severity_signal,
                )
                if result.get("notified"):
                    sm.record_notification()
                    sm.transition_to(
                        sm.state,
                        signal_type=signal.signal_type,
                        action="await_feedback",
                    )
            except Exception:
                logger.debug("Notify failed", exc_info=True)
            self._persist_state_machine()
            return

    def _record_delivery(
        self,
        kind: str,
        channel: str,
        status: str,
        reason: str = "",
        title: str = "",
    ) -> None:
        try:
            self.storage.record_delivery(kind, channel, status, reason, title)
        except Exception:
            logger.debug("Delivery log save failed", exc_info=True)

    def _delivery_for_kind(self, kind: str) -> str:
        delivery = self.config.report.delivery
        if kind == "summary":
            return delivery.instant_summary
        if kind == "daily_report":
            return delivery.daily_report
        if kind == "morning_brief":
            return delivery.morning_brief
        if kind == "task_confirm":
            return delivery.task_confirm
        return delivery.medium_signal

    def _deliver_message(
        self,
        *,
        kind: str,
        title: str,
        body: str,
        severity: float,
        reason: str,
        now: datetime,
        delivery: str,
        detail_url: Optional[str] = None,
    ) -> Dict[str, bool]:
        """Deliver one user-facing message and log all channel outcomes."""
        result = {"notified": False, "inbox": False}
        if delivery == "off":
            self._record_delivery(kind, "none", "suppressed", "delivery_off", title)
            return result

        wants_notify = delivery in {"notify", "both"}
        wants_inbox = delivery in {"inbox", "both"}
        inbox_reason = reason
        require_budget = self._requires_notification_budget(kind, severity)

        if wants_notify:
            if not self._should_notify():
                inbox_reason = "通知被抑制: cli_only"
                self._record_delivery(kind, "notify", "suppressed", "cli_only", title)
            else:
                allowed, deny_reason = self._notification_gate.allow_notify(
                    kind,
                    now=now,
                    require_budget=require_budget,
                )
                if allowed:
                    sent = send_notification(title, body, detail_url=detail_url)
                    if sent:
                        self._notification_gate.record_notify(
                            kind,
                            now,
                            consume_budget=require_budget,
                        )
                        result["notified"] = True
                        self._record_delivery(kind, "notify", "sent", reason, title)
                    else:
                        inbox_reason = "通知发送失败，已转入 inbox"
                        self._record_delivery(kind, "notify", "failed", "send_failed", title)
                else:
                    inbox_reason = f"通知被抑制: {deny_reason}"
                    self._record_delivery(kind, "notify", "suppressed", deny_reason, title)

        if wants_inbox or (wants_notify and not result["notified"]):
            try:
                self.storage.add_inbox_item(
                    signal_type=kind,
                    severity=severity,
                    evidence=body,
                    reason=inbox_reason,
                )
                result["inbox"] = True
                self._record_delivery(kind, "inbox", "sent", inbox_reason, title)
            except Exception:
                self._record_delivery(kind, "inbox", "failed", "save_failed", title)
                logger.debug("Inbox save failed", exc_info=True)
        return result

    def _requires_notification_budget(self, kind: str, severity: float) -> bool:
        if severity >= 0.8:
            return False
        exempt = set(getattr(self.config.report, "notification_budget_exempt_kinds", []))
        return kind not in exempt

    def _deliver_summary(
        self,
        kind: str,
        title: str,
        body: str,
        *,
        now: datetime,
        detail_url: Optional[str] = None,
        prefer_notify: Optional[bool] = None,
        delivery: Optional[str] = None,
    ) -> None:
        """Deliver summary via notify or inbox using NotificationGate."""
        self._save_summary_archive(kind, title, body, now)

        selected = delivery or self._delivery_for_kind(kind)
        if prefer_notify is not None:
            selected = "notify" if prefer_notify else "inbox"
        self._deliver_message(
            kind=kind,
            title=title,
            body=body,
            severity=0.5,
            reason="摘要存档",
            now=now,
            detail_url=detail_url,
            delivery=selected,
        )

    def _save_summary_archive(self, kind: str, title: str, body: str, now: datetime) -> None:
        try:
            archive_dir = self.config.reports_dir / "summaries"
            archive_dir.mkdir(parents=True, exist_ok=True)
            stamp = now.strftime("%Y-%m-%d-%H%M")
            path = archive_dir / f"{stamp}-{kind}.md"
            path.write_text(f"# {title}\n\n{body}\n", encoding="utf-8")
        except Exception:
            logger.debug("Summary archive failed", exc_info=True)

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
        if ps.get("task_id") != cs.get("task_id"):
            return "task_change"
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

    @property
    def context_bucket_id(self) -> str:
        return f"aw-coach-context_{self.collector.hostname}"

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

    def _ensure_context_bucket(self) -> None:
        if self._context_bucket_created:
            return
        try:
            self.collector.client.create_bucket(
                self.context_bucket_id,
                event_type="aw.coach.context",
                queued=False,
            )
            self._context_bucket_created = True
        except Exception:
            self._context_bucket_created = True

    def _capture_context_snapshot(self, now: datetime, latest):
        cfg = getattr(self.config, "context_capture", None)
        if cfg is None or not cfg.enabled:
            return None

        interval = max(10, int(cfg.interval_seconds))
        if (
            self._last_context_capture is not None
            and (now - self._last_context_capture).total_seconds() < interval
        ):
            return None
        self._last_context_capture = now

        try:
            from aw_coach.process_context import capture_process_context

            snapshot = capture_process_context(
                active_app=latest.primary_app,
                command_args_mode=cfg.command_args_mode,
                capture_cwd=cfg.capture_cwd,
                capture_git=cfg.capture_git,
            )
        except Exception:
            logger.debug("Context capture failed", exc_info=True)
            return None

        if snapshot is None:
            return None

        if not any(
            [
                snapshot.process_name,
                snapshot.process_cwd,
                snapshot.git_repo,
                snapshot.terminal_command_summary,
            ]
        ):
            return snapshot

        try:
            from aw_core.models import Event

            self._ensure_context_bucket()
            event = Event(
                timestamp=_local_to_utc(now - timedelta(seconds=interval)),
                duration=timedelta(seconds=interval),
                data={
                    "schema_version": 1,
                    "type": "context_snapshot",
                    "process_name": snapshot.process_name,
                    "process_cwd": snapshot.process_cwd,
                    "git_repo": snapshot.git_repo,
                    "git_branch": snapshot.git_branch,
                    "terminal_command_summary": snapshot.terminal_command_summary,
                    "terminal_action": snapshot.terminal_action,
                },
            )
            self.collector.client.insert_event(self.context_bucket_id, event)
        except Exception:
            logger.debug("Context snapshot write failed", exc_info=True)

        return snapshot

    def _annotate_task_slices(self, slices, rules) -> None:
        if not self.config.tasks.enabled:
            return
        try:
            from aw_coach.task_signals import annotate_task_slices

            annotate_task_slices(slices, rules, self.config.tasks)
        except Exception:
            logger.debug("Historical task annotation failed", exc_info=True)

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
        self._reconcile_recent_hourly(datetime.now())
        self._save_last_hourly(last_hourly)
        logger.info(f"  last_hourly restored: {last_hourly.strftime('%Y-%m-%d %H:%M')}")
        logger.info(f"  last_summary restored: {last_summary.strftime('%H:%M')}")

        while self._running:
            now = datetime.now()

            # Phase 1: per-minute semantic state update
            try:
                self._update_semantic_state(now)
            except Exception:
                logger.debug("Semantic state update error in loop", exc_info=True)

            # Hourly analysis - aligned to clock hours
            next_hour = last_hourly + timedelta(hours=1)
            if now >= next_hour:
                if self._hourly_analyze(last_hourly, next_hour):
                    last_hourly = next_hour
                    self._save_last_hourly(last_hourly)

            # Instant summary
            interval = self.config.report.instant_summary_interval_hours
            if (now - last_summary).total_seconds() >= interval * 3600:
                try:
                    self._send_instant_summary(now)
                except Exception:
                    logger.debug("Instant summary failed", exc_info=True)
                last_summary = now
                self._save_last_summary(last_summary)

            # Daily report - scheduler_state marker (CLI manual report must not block)
            report_time = self.config.report.daily_report_time
            hour, minute = int(report_time.split(":")[0]), int(report_time.split(":")[1])
            if now.hour == hour and now.minute >= minute:
                if not self._daily_report_already_done(now.date()):
                    try:
                        if self._generate_daily_report(now.date()):
                            self._mark_daily_report_done(now.date())
                    except Exception:
                        logger.debug("Daily report failed", exc_info=True)

            # Morning brief
            brief_time = self.config.report.morning_brief_time
            bh, bm = int(brief_time.split(":")[0]), int(brief_time.split(":")[1])
            if (
                now.hour == bh
                and now.minute >= bm
                and self._last_morning_brief_date != now.date()
            ):
                try:
                    self._send_morning_brief(now)
                except Exception:
                    logger.debug("Morning brief failed", exc_info=True)
                self._last_morning_brief_date = now.date()

            try:
                self._poll_summary_future()
            except Exception:
                logger.debug("Summary poll failed", exc_info=True)
            self._run_due_cron_jobs(now)
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
                self._hourly_analyze(
                    last_boundary,
                    now,
                    allow_llm=False,
                    event_type="partial_hour_analysis",
                )
            self._persist_task_tracker()
        except Exception as e:
            logger.warning(f"Flush on shutdown failed (non-fatal): {e}")
        try:
            self._summary_executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        self._running = False

    def _catch_up_hourly(self, last_hourly: datetime, now: datetime) -> datetime:
        """Analyze missed complete hours after a restart, capped to one day."""
        target = self._prev_hour_boundary(now)
        if last_hourly >= target:
            return last_hourly

        max_backfill_hours = max(1, self.config.report.hourly_backfill_hours)
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
            if not self._hourly_analyze(current, next_hour, allow_llm=False):
                return current
            current = next_hour
            self._save_last_hourly(current)

        return current

    def _reconcile_recent_hourly(self, now: datetime) -> None:
        """Fill missing completed hourly events in the configured lookback window."""
        target = self._prev_hour_boundary(now)
        max_backfill_hours = max(1, self.config.report.hourly_backfill_hours)
        current = target - timedelta(hours=max_backfill_hours)
        while current < target:
            next_hour = current + timedelta(hours=1)
            if not self._event_exists("hourly_analysis", current, next_hour):
                if not self._hourly_analyze(current, next_hour, allow_llm=False):
                    logger.debug(
                        "Hourly reconciliation stopped at %s-%s",
                        current.isoformat(),
                        next_hour.isoformat(),
                    )
                    return
            current = next_hour

    def _hourly_analyze(
        self,
        hour_start: datetime,
        hour_end: datetime,
        *,
        allow_llm: bool = True,
        event_type: str = "hourly_analysis",
    ) -> bool:
        try:
            slices = self.collector.fetch_range(hour_start, hour_end)
        except Exception as e:
            logger.warning(f"Failed to fetch data: {e}")
            return False

        if not slices:
            return True

        rules = self._classify_slices(slices, allow_llm=allow_llm)
        self._annotate_task_slices(slices, rules)
        analysis = self.analyzer.analyze(slices, rules)

        # Determine dominant activity type
        type_counts = Counter(r.activity_type for r in rules)
        top_type = type_counts.most_common(1)[0][0] if type_counts else "unknown"
        avg_confidence = sum(r.confidence for r in rules) / len(rules) if rules else 0.0
        methods = set(r.method for r in rules)
        method = "rule" if all(m.startswith("rule") for m in methods) else "hybrid"

        # Write to ai-coach bucket
        self._ensure_bucket()
        if self._event_exists(event_type, hour_start, hour_end):
            logger.info(
                f"Hourly analysis already exists: "
                f"{hour_start.strftime('%Y-%m-%d %H:%M')}-"
                f"{hour_end.strftime('%Y-%m-%d %H:%M')}"
            )
            return True

        try:
            from aw_core.models import Event

            event = Event(
                timestamp=_local_to_utc(hour_start),
                duration=timedelta(seconds=int((hour_end - hour_start).total_seconds())),
                data={
                    "schema_version": ANALYSIS_SCHEMA_VERSION,
                    "type": event_type,
                    "period_start": hour_start.isoformat(),
                    "period_end": hour_end.isoformat(),
                    "activity_type": top_type,
                    "confidence": round(avg_confidence, 3),
                    "classification_method": method,
                    "focus_score": analysis.focus_score,
                    "switch_count": analysis.switch_count,
                    "task_switch_count": analysis.task_switch_count,
                    "effective_hours": round(analysis.effective_hours, 3),
                    "deep_work_hours": round(analysis.deep_work_hours, 3),
                    "productivity_score": analysis.productivity_score,
                    "death_loops": analysis.death_loops,
                    "activity_breakdown": {
                        k: round(v, 3) for k, v in analysis.activity_breakdown.items()
                    },
                    "task_breakdown": {
                        k: round(v, 3) for k, v in analysis.task_breakdown.items()
                    },
                    "task_deep_work_breakdown": {
                        k: round(v, 3)
                        for k, v in analysis.task_deep_work_breakdown.items()
                    },
                },
            )
            self.collector.client.insert_event(self.bucket_id, event)
            logger.info(
                f"Hourly analysis written: "
                f"{hour_start.strftime('%Y-%m-%d %H:%M')}-"
                f"{hour_end.strftime('%Y-%m-%d %H:%M')} "
                f"effective={analysis.effective_hours:.1f}h focus={analysis.focus_score}"
            )
        except Exception as e:
            logger.warning(f"Failed to write hourly event to bucket: {e}")
            return False

        try:
            self._persist_completed_sessions()
        except Exception:
            logger.debug("Hourly task session persist failed", exc_info=True)

        return True

    def _daily_report_state_key(self, report_date: date) -> str:
        return f"daily_report_done:{report_date.isoformat()}"

    def _daily_report_already_done(self, report_date: date) -> bool:
        return self.storage.get_scheduler_state(
            self._daily_report_state_key(report_date)
        ) == "1"

    def _mark_daily_report_done(self, report_date: date) -> None:
        try:
            self.storage.set_scheduler_state(
                self._daily_report_state_key(report_date), "1"
            )
        except Exception:
            logger.debug("Failed to mark daily report done", exc_info=True)

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
            if (
                event_type == "hourly_analysis"
                and data.get("schema_version", 0) < ANALYSIS_SCHEMA_VERSION
            ):
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

    def _build_rule_summary_body(self, analysis, interval: float) -> str:
        top_activity = ""
        if analysis.activity_breakdown:
            top_activity = max(analysis.activity_breakdown, key=analysis.activity_breakdown.get)
        body_lines = [
            f"有效工作: {analysis.effective_hours:.1f}h | 专注度: {analysis.focus_score}/100",
        ]
        if analysis.death_loops:
            body_lines.append(f"检测到 {len(analysis.death_loops)} 个切换循环")
        if top_activity:
            body_lines.append(f"主要活动: {top_activity}")
        suggestions = self.reporter._generate_suggestions(analysis, is_weekly=False)
        if suggestions:
            body_lines.append(suggestions[0])
        return "\n".join(body_lines)

    def _get_cost_controller(self):
        from aw_coach.ai.cost import CostController

        return CostController(self.config.cost, self.storage)

    def _get_task_sessions_for_summary(self) -> Optional[List]:
        if self._task_tracker is None:
            return None
        sessions = []
        current = self._task_tracker.current_session
        if current is not None:
            sessions.append(current)
        sessions.extend(self._task_tracker.completed_sessions)
        return sessions or None

    def _submit_background_summary(
        self,
        analysis,
        *,
        kind: str,
        title: str,
        fallback_body: str,
        now: datetime,
        detail_url: Optional[str] = None,
        active_signals: Optional[List[str]] = None,
        silent_on_failure: bool = False,
        prefer_notify_fallback: Optional[bool] = None,
    ) -> None:
        if not self.config.report.background_ai_summary:
            self._deliver_summary(
                kind,
                title,
                fallback_body,
                now=now,
                detail_url=detail_url,
                prefer_notify=prefer_notify_fallback,
            )
            return

        semantic = self._semantic_payload
        corrections = self.storage.get_corrections_last_30_days()
        task_sessions = self._get_task_sessions_for_summary()
        config = self.config

        def _work():
            from aw_coach.ai.cost import CostController
            from aw_coach.storage import Storage

            # The scheduler's Storage connection belongs to the daemon thread.
            # Background workers need their own SQLite connection.
            cost = CostController(config.cost, Storage(config.db_path))
            return generate_background_summary(
                analysis,
                config,
                cost_controller=cost,
                semantic_state=semantic,
                corrections=corrections,
                task_sessions=task_sessions,
                active_signals=active_signals,
            )

        if self._summary_future is not None and not self._summary_future.done():
            self._deliver_summary(
                kind, title, fallback_body, now=now, detail_url=detail_url, prefer_notify=False
            )
            return

        self._summary_future = self._summary_executor.submit(_work)
        self._pending_summary_delivery = {
            "kind": kind,
            "title": title,
            "fallback_body": fallback_body,
            "now": now,
            "detail_url": detail_url,
            "silent": silent_on_failure,
            "prefer_notify": prefer_notify_fallback,
            "submitted_at": datetime.now(),
            "timeout_seconds": self.config.report.llm_timeout_seconds,
        }

    def _poll_summary_future(self) -> None:
        pending = getattr(self, "_pending_summary_delivery", None)
        if self._summary_future is None:
            return

        if pending is not None and not self._summary_future.done():
            submitted_at = pending.get("submitted_at")
            timeout_seconds = pending.get(
                "timeout_seconds", self.config.report.llm_timeout_seconds
            )
            if (
                isinstance(submitted_at, datetime)
                and (datetime.now() - submitted_at).total_seconds() >= timeout_seconds
            ):
                logger.warning(
                    "Background summary timed out after %ss; using fallback",
                    timeout_seconds,
                )
                self._summary_future.cancel()
                self._summary_future = None
                if not pending.get("silent"):
                    self._deliver_summary(
                        pending["kind"],
                        pending["title"],
                        pending["fallback_body"],
                        now=pending["now"],
                        detail_url=pending.get("detail_url"),
                        prefer_notify=pending.get("prefer_notify", True),
                    )
                self._pending_summary_delivery = None
            return

        if not self._summary_future.done():
            return
        try:
            ai_text = self._summary_future.result()
        except Exception:
            logger.debug("Background summary future failed", exc_info=True)
            ai_text = None
        finally:
            self._summary_future = None

        if pending is None:
            return
        body = ai_text or pending["fallback_body"]
        if ai_text is None and pending.get("silent"):
            self._pending_summary_delivery = None
            return
        self._deliver_summary(
            pending["kind"],
            pending["title"],
            body,
            now=pending["now"],
            detail_url=pending.get("detail_url"),
            prefer_notify=pending.get("prefer_notify", True),
        )
        self._pending_summary_delivery = None

    def _generate_ai_summary_with_timeout(
        self,
        analysis,
        *,
        active_signals: Optional[List[str]] = None,
    ) -> Optional[str]:
        """Generate an AI summary with a bounded wait and worker-local storage."""
        config = self.config
        semantic = self._semantic_payload
        corrections = self.storage.get_corrections_last_30_days()
        task_sessions = self._get_task_sessions_for_summary()

        def _work():
            from aw_coach.ai.cost import CostController
            from aw_coach.storage import Storage

            cost = CostController(config.cost, Storage(config.db_path))
            return generate_background_summary(
                analysis,
                config,
                cost_controller=cost,
                semantic_state=semantic,
                corrections=corrections,
                task_sessions=task_sessions,
                active_signals=active_signals,
            )

        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="aw-coach-ai-once")
        future = executor.submit(_work)
        try:
            return future.result(timeout=self.config.report.llm_timeout_seconds)
        except TimeoutError:
            future.cancel()
            logger.warning(
                "AI summary timed out after %ss; using fallback",
                self.config.report.llm_timeout_seconds,
            )
            return None
        except Exception:
            logger.debug("AI summary failed", exc_info=True)
            return None
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    def _send_instant_summary(self, now: datetime) -> None:
        interval = self.config.report.instant_summary_interval_hours
        start = now - timedelta(hours=interval)
        try:
            slices = self.collector.fetch_range(start, now)
        except Exception:
            logger.debug("Instant summary fetch failed", exc_info=True)
            return

        if not slices:
            return

        rules = self._classify_slices(slices)
        self._annotate_task_slices(slices, rules)
        analysis = self.analyzer.analyze(slices, rules)

        detail_url = None
        try:
            generate_html_dashboard(self.config, now.date(), analysis, slices, rules)
            detail_url = self.dashboard_url
        except Exception:
            logger.debug("Dashboard generation failed", exc_info=True)

        from aw_coach.background_summary import should_silent_summary

        active_signals = []
        if self._semantic_payload:
            sig = self._semantic_payload.get("state", {}).get("detected_signal")
            if sig:
                active_signals.append(sig)

        silent = should_silent_summary(analysis, self.config, active_signals)
        if silent and not self.config.report.background_ai_summary:
            return

        fallback = self._build_rule_summary_body(analysis, interval)
        title = f"AI Coach 摘要 (过去{interval}h)"
        self._submit_background_summary(
            analysis,
            kind="summary",
            title=title,
            fallback_body=fallback,
            now=now,
            detail_url=detail_url,
            active_signals=active_signals,
            silent_on_failure=silent,
        )

    def _send_morning_brief(self, now: datetime) -> None:
        # Morning brief is part of the background-summary feature; opt-in only
        # so default upgrade does not introduce new notifications.
        if not self.config.report.background_ai_summary:
            return
        yesterday = now.date() - timedelta(days=1)
        start = datetime.combine(yesterday, datetime.min.time())
        try:
            slices = self.collector.fetch_range(start, now)
        except Exception:
            logger.debug("Morning brief fetch failed", exc_info=True)
            return
        if not slices:
            return
        rules = self._classify_slices(slices)
        self._annotate_task_slices(slices, rules)
        analysis = self.analyzer.analyze(slices, rules)
        fallback = self._build_rule_summary_body(analysis, (now - start).total_seconds() / 3600)
        self._submit_background_summary(
            analysis,
            kind="morning_brief",
            title=f"AI Coach 早报 - {now.date().isoformat()}",
            fallback_body=fallback,
            now=now,
            detail_url=self.dashboard_url,
        )

    def _generate_daily_report(self, report_date: date) -> bool:
        """Generate the daily report; True on success (marks the day done)."""
        start = datetime.combine(report_date, datetime.min.time())
        end = datetime.combine(report_date, datetime.max.time())

        try:
            slices = self.collector.fetch_range(start, end)
        except Exception as e:
            logger.error(f"Failed to generate daily report: {e}")
            return False

        if not slices:
            return False

        rules = self._classify_slices(slices)
        self._annotate_task_slices(slices, rules)
        analysis = self.analyzer.analyze(slices, rules)

        use_ai = self.config.report.background_ai_summary and self.config.ai.backend != "rule_only"
        # Flush/persist sessions first so today's breakdown includes them
        self._persist_task_sessions_for_day(report_date)
        task_breakdown = None
        if self.config.tasks.enabled:
            task_rows = self.storage.get_task_session_summary(report_date.isoformat())
            task_breakdown = {
                row["label"]: row["total_sec"] / 3600
                for row in task_rows
            } or analysis.task_breakdown or {
                row["label"]: row["total_sec"] / 3600
                for row in self.storage.get_task_daily_summary(report_date.isoformat())
            }
        inbox_items = self.storage.get_inbox_items(dismissed=False, limit=10)
        daily_insights = self._get_or_generate_daily_insights(report_date, analysis)
        report_text = self.reporter.generate_daily(
            report_date,
            analysis,
            use_ai=use_ai,
            project_breakdown=task_breakdown,
            inbox_items=inbox_items,
            daily_insights=daily_insights,
        )

        ai_summary = None
        if use_ai:
            ai_summary = self._generate_ai_summary_with_timeout(analysis)
            if ai_summary:
                report_text += f"\n\n## AI 总结\n\n{ai_summary}\n"

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
                    "task_switch_count": analysis.task_switch_count,
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

        notify_body = ai_summary or (
            f"有效工作 {analysis.effective_hours:.1f}h | "
            f"专注度 {analysis.focus_score}/100 | "
            f"深度工作 {analysis.deep_work_hours:.1f}h"
        )
        self._deliver_summary(
            "daily_report",
            f"AI Coach 日报 - {report_date.isoformat()}",
            notify_body,
            now=datetime.now(),
            detail_url=detail_url,
            delivery=self.config.report.delivery.daily_report,
        )
        return True

    def _get_or_generate_daily_insights(self, report_date: date, analysis) -> List[Dict]:
        try:
            from aw_coach.daily_insights import generate_daily_insights

            day = report_date.isoformat()
            rows = self.storage.get_daily_insights(day)
            if rows:
                return rows
            insights = generate_daily_insights(report_date, self.storage, analysis)
            if insights:
                self.storage.save_daily_insights(day, insights)
            return self.storage.get_daily_insights(day)
        except Exception:
            logger.debug("Daily insight generation failed", exc_info=True)
            return []

    def _run_due_cron_jobs(self, now: datetime) -> None:
        for job in self._cron_runner.due_jobs(now):
            try:
                start = now - timedelta(hours=4)
                slices = self.collector.fetch_range(start, now)
                if not slices:
                    self._cron_runner.mark_run(job, now)
                    continue
                rules = self._classify_slices(slices)
                self._annotate_task_slices(slices, rules)
                analysis = self.analyzer.analyze(slices, rules)
                fallback = self._build_rule_summary_body(analysis, 4)
                title = f"AI Coach · {job.template}"
                prefer = job.delivery == "notification"
                self._submit_background_summary(
                    analysis,
                    kind=f"cron:{job.template}",
                    title=title,
                    fallback_body=fallback,
                    now=now,
                    detail_url=self.dashboard_url,
                    prefer_notify_fallback=prefer,
                )
                self._cron_runner.mark_run(job, now)
            except Exception:
                logger.debug("Cron job failed: %s", job.template, exc_info=True)

    def _persist_completed_sessions(self) -> None:
        """Persist drained completed sessions without flushing the active one."""
        if not self.config.tasks.enabled or self._task_tracker is None:
            return
        changed_days: set[str] = set()
        for session in self._task_tracker.drain_completed():
            try:
                self.storage.upsert_task_session(session)
                changed_days.add(session.started_at.date().isoformat())
            except Exception:
                logger.debug("Task session persist failed", exc_info=True)
        for day in changed_days:
            try:
                self.storage.rebuild_task_daily_summary(day)
            except Exception:
                logger.debug("Task daily summary persist failed", exc_info=True)
        self._persist_task_tracker()

    def _persist_task_sessions_for_day(self, report_date: date) -> None:
        if not self.config.tasks.enabled or self._task_tracker is None:
            return
        self._task_tracker.flush(datetime.now())
        self._persist_completed_sessions()


def run_scheduler(dashboard_url: Optional[str] = None) -> None:
    config = load_config()
    scheduler = CoachScheduler(config, dashboard_url=dashboard_url)
    scheduler.run()
