"""SQLite local state management - corrections, cost log, batch queue."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from os import PathLike
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from aw_coach.task_models import TaskSession, stable_session_uid
from aw_coach.time_utils import now_local_iso


class Storage:
    def __init__(self, db_path: Path):
        if not isinstance(db_path, (str, PathLike)):
            raise TypeError(
                f"db_path must be a filesystem path, got {type(db_path).__name__}"
            )
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._migrate()

    def _migrate(self) -> None:
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]

        if version < 1:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS cost_log (
                    id INTEGER PRIMARY KEY,
                    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
                    model TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    cost_usd REAL NOT NULL,
                    operation TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS batch_queue (
                    id INTEGER PRIMARY KEY,
                    slice_start TEXT NOT NULL,
                    slice_end TEXT NOT NULL,
                    app TEXT NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT,
                    rule_confidence REAL NOT NULL,
                    processed INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS corrections (
                    id INTEGER PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    app TEXT NOT NULL,
                    title TEXT NOT NULL,
                    original_type TEXT NOT NULL,
                    corrected_type TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                PRAGMA user_version = 1;
            """)

        if version < 2:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS scheduler_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                PRAGMA user_version = 2;
            """)

        if version < 3:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS rule_suggestion_decisions (
                    id INTEGER PRIMARY KEY,
                    app TEXT NOT NULL,
                    corrected_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    rule_name TEXT,
                    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                    UNIQUE(app, corrected_type)
                );
                PRAGMA user_version = 3;
            """)

        if version < 4:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS state_snapshots (
                    id INTEGER PRIMARY KEY,
                    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
                    state_json TEXT NOT NULL,
                    change_reason TEXT NOT NULL DEFAULT 'first_run'
                );
                CREATE INDEX IF NOT EXISTS idx_state_time ON state_snapshots(timestamp);
                PRAGMA user_version = 4;
            """)

        if version < 5:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS inbox (
                    id INTEGER PRIMARY KEY,
                    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
                    signal_type TEXT NOT NULL,
                    severity REAL NOT NULL,
                    evidence TEXT NOT NULL DEFAULT '',
                    reason TEXT NOT NULL DEFAULT '',
                    dismissed INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_inbox_time ON inbox(timestamp);
                PRAGMA user_version = 5;
            """)

        if version < 6:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS task_sessions (
                    id INTEGER PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    label TEXT NOT NULL,
                    project TEXT,
                    intent TEXT NOT NULL DEFAULT 'unknown',
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    accumulated_sec REAL NOT NULL DEFAULT 0,
                    modes_json TEXT NOT NULL DEFAULT '[]',
                    blockers_json TEXT NOT NULL DEFAULT '[]',
                    outcome TEXT NOT NULL DEFAULT 'in_progress',
                    confidence REAL NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_task_sessions_started ON task_sessions(started_at);
                CREATE TABLE IF NOT EXISTS task_daily_summary (
                    date TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    label TEXT NOT NULL,
                    total_sec REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY (date, task_id)
                );
                PRAGMA user_version = 6;
            """)

        if version < 7:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS delivery_log (
                    id INTEGER PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_delivery_log_time
                    ON delivery_log(timestamp);
                CREATE INDEX IF NOT EXISTS idx_delivery_log_kind
                    ON delivery_log(kind, status);
                PRAGMA user_version = 7;
            """)

        if version < 8:
            self._migrate_task_session_ledger()
            self._conn.execute("PRAGMA user_version = 8")

        if version < 9:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS daily_insights (
                    id INTEGER PRIMARY KEY,
                    date TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    evidence_json TEXT NOT NULL DEFAULT '[]',
                    suggestion TEXT NOT NULL DEFAULT '',
                    severity REAL NOT NULL DEFAULT 0,
                    confidence REAL NOT NULL DEFAULT 0,
                    source_version INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    UNIQUE(date, kind, title)
                );
                CREATE INDEX IF NOT EXISTS idx_daily_insights_date
                    ON daily_insights(date);
                PRAGMA user_version = 9;
            """)

        self._conn.commit()

    def _table_columns(self, table: str) -> set[str]:
        rows = self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {row["name"] for row in rows}

    def _add_column_if_missing(self, table: str, name: str, definition: str) -> None:
        if name not in self._table_columns(table):
            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    def _migrate_task_session_ledger(self) -> None:
        self._add_column_if_missing("task_sessions", "session_uid", "TEXT")
        self._add_column_if_missing(
            "task_sessions",
            "updated_at",
            "TEXT NOT NULL DEFAULT ''",
        )
        self._add_column_if_missing(
            "task_sessions",
            "evidence_json",
            "TEXT NOT NULL DEFAULT '[]'",
        )
        self._add_column_if_missing(
            "task_sessions",
            "source_json",
            "TEXT NOT NULL DEFAULT '{}'",
        )
        self._add_column_if_missing(
            "task_sessions",
            "version",
            "INTEGER NOT NULL DEFAULT 1",
        )

        rows = self._conn.execute(
            "SELECT id, task_id, started_at, session_uid FROM task_sessions "
            "ORDER BY id"
        ).fetchall()
        seen: set[str] = set()
        for row in rows:
            try:
                started_at = datetime.fromisoformat(row["started_at"])
                uid = row["session_uid"] or stable_session_uid(
                    row["task_id"], started_at
                )
            except (TypeError, ValueError):
                uid = row["session_uid"] or f"legacy-{row['id']}"
            if uid in seen:
                uid = f"{uid}-{row['id']}"
            seen.add(uid)
            if row["session_uid"] != uid:
                self._conn.execute(
                    "UPDATE task_sessions SET session_uid = ? WHERE id = ?",
                    (uid, row["id"]),
                )
        self._conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_task_sessions_uid "
            "ON task_sessions(session_uid)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_task_sessions_outcome "
            "ON task_sessions(outcome, started_at)"
        )

    # === Cost Log ===

    def record_cost(
        self, model: str, input_tokens: int, output_tokens: int, cost_usd: float, operation: str
    ) -> None:
        self._conn.execute(
            "INSERT INTO cost_log "
            "(timestamp, model, input_tokens, output_tokens, cost_usd, operation) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                datetime.now().isoformat(timespec="seconds"),
                model,
                input_tokens,
                output_tokens,
                cost_usd,
                operation,
            ),
        )
        self._conn.commit()

    def get_monthly_cost(self) -> float:
        now = datetime.now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        row = self._conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) as total "
            "FROM cost_log WHERE datetime(timestamp) >= datetime(?)",
            (month_start.isoformat(),),
        ).fetchone()
        return row["total"]

    def get_cost_breakdown(self) -> Dict[str, float]:
        now = datetime.now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        rows = self._conn.execute(
            "SELECT operation, SUM(cost_usd) as total FROM cost_log "
            "WHERE datetime(timestamp) >= datetime(?) GROUP BY operation",
            (month_start.isoformat(),),
        ).fetchall()
        return {row["operation"]: row["total"] for row in rows}

    # === Batch Queue ===

    def enqueue_batch_item(
        self,
        slice_start: str,
        slice_end: str,
        app: str,
        title: str,
        url: Optional[str],
        rule_confidence: float,
    ) -> int:
        cursor = self._conn.execute(
            "INSERT INTO batch_queue (slice_start, slice_end, app, title, url, rule_confidence) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (slice_start, slice_end, app, title, url, rule_confidence),
        )
        self._conn.commit()
        return cursor.lastrowid

    def get_pending_batch(self, limit: int = 8) -> List[Dict]:
        rows = self._conn.execute(
            "SELECT id, slice_start, slice_end, app, title, url, rule_confidence "
            "FROM batch_queue WHERE processed = 0 ORDER BY created_at LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def mark_batch_processed(self, ids: List[int]) -> None:
        if not ids:
            return
        placeholders = ",".join("?" * len(ids))
        self._conn.execute(
            f"UPDATE batch_queue SET processed = 1 WHERE id IN ({placeholders})", ids
        )
        self._conn.commit()

    # === Corrections ===

    def add_correction(
        self,
        timestamp: str,
        app: str,
        title: str,
        original_type: str,
        corrected_type: str,
    ) -> None:
        self._conn.execute(
            "INSERT INTO corrections (timestamp, app, title, original_type, corrected_type) "
            "VALUES (?, ?, ?, ?, ?)",
            (timestamp, app, title, original_type, corrected_type),
        )
        self._conn.commit()

    def get_corrections_last_30_days(self) -> List[Dict]:
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=30)).isoformat()
        rows = self._conn.execute(
            "SELECT * FROM corrections "
            "WHERE datetime(created_at) >= datetime(?) ORDER BY datetime(created_at) DESC",
            (cutoff,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_correction_counts(self) -> Dict[Tuple[str, str], int]:
        rows = self._conn.execute(
            "SELECT app, corrected_type, COUNT(*) as cnt "
            "FROM corrections GROUP BY app, corrected_type"
        ).fetchall()
        return {(row["app"], row["corrected_type"]): row["cnt"] for row in rows}

    def get_rule_suggestion_stats(self, min_count: int = 3) -> List[Dict]:
        rows = self._conn.execute(
            "SELECT LOWER(app) AS app_key, app, corrected_type, COUNT(*) AS correction_count, "
            "MAX(created_at) AS latest_corrected_at, "
            "GROUP_CONCAT(DISTINCT original_type) AS original_types "
            "FROM corrections GROUP BY LOWER(app), corrected_type HAVING COUNT(*) >= ? "
            "ORDER BY correction_count DESC, latest_corrected_at DESC",
            (min_count,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_rule_suggestion_decisions(self) -> Dict[Tuple[str, str], str]:
        rows = self._conn.execute(
            "SELECT app, corrected_type, status FROM rule_suggestion_decisions"
        ).fetchall()
        return {
            (row["app"].lower(), row["corrected_type"]): row["status"] for row in rows
        }

    def set_rule_suggestion_status(
        self,
        app: str,
        corrected_type: str,
        status: str,
        rule_name: Optional[str] = None,
    ) -> None:
        self._conn.execute(
            "INSERT INTO rule_suggestion_decisions "
            "(app, corrected_type, status, rule_name, updated_at) "
            "VALUES (?, ?, ?, ?, datetime('now')) "
            "ON CONFLICT(app, corrected_type) DO UPDATE SET "
            "status=excluded.status, rule_name=excluded.rule_name, "
            "updated_at=excluded.updated_at",
            (app.lower(), corrected_type, status, rule_name),
        )
        self._conn.commit()

    # === Scheduler State ===

    def get_scheduler_state(self, key: str, default: Optional[str] = None) -> Optional[str]:
        row = self._conn.execute(
            "SELECT value FROM scheduler_state WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default

    def set_scheduler_state(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO scheduler_state (key, value, updated_at) VALUES (?, ?, datetime('now')) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, value),
        )
        self._conn.commit()

    # === Inbox ===

    def add_inbox_item(
        self,
        signal_type: str,
        severity: float,
        evidence: str = "",
        reason: str = "",
    ) -> int:
        cursor = self._conn.execute(
            "INSERT INTO inbox (timestamp, signal_type, severity, evidence, reason) "
            "VALUES (?, ?, ?, ?, ?)",
            (now_local_iso(), signal_type, severity, evidence, reason),
        )
        self._conn.commit()
        return cursor.lastrowid

    def get_inbox_items(self, dismissed: bool = False, limit: int = 50) -> List[Dict]:
        rows = self._conn.execute(
            "SELECT id, timestamp, signal_type, severity, evidence, reason, dismissed "
            "FROM inbox WHERE dismissed = ? ORDER BY id DESC LIMIT ?",
            (1 if dismissed else 0, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def dismiss_inbox_item(self, item_id: int) -> None:
        self._conn.execute(
            "UPDATE inbox SET dismissed = 1 WHERE id = ?", (item_id,)
        )
        self._conn.commit()

    def get_inbox_item(self, item_id: int) -> Optional[Dict]:
        row = self._conn.execute(
            "SELECT id, timestamp, signal_type, severity, evidence, reason, dismissed "
            "FROM inbox WHERE id = ?",
            (item_id,),
        ).fetchone()
        return dict(row) if row else None

    # === Delivery Log ===

    def record_delivery(
        self,
        kind: str,
        channel: str,
        status: str,
        reason: str = "",
        title: str = "",
    ) -> int:
        cursor = self._conn.execute(
            "INSERT INTO delivery_log "
            "(timestamp, kind, channel, status, reason, title) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (now_local_iso(), kind, channel, status, reason, title),
        )
        self._conn.commit()
        return cursor.lastrowid

    def get_recent_delivery_logs(self, limit: int = 10) -> List[Dict]:
        rows = self._conn.execute(
            "SELECT id, timestamp, kind, channel, status, reason, title "
            "FROM delivery_log ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_recent_delivery_issue(self) -> Optional[Dict]:
        row = self._conn.execute(
            "SELECT id, timestamp, kind, channel, status, reason, title "
            "FROM delivery_log WHERE status IN ('failed', 'suppressed') "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    # === Daily insights ===

    def save_daily_insights(self, day: str, insights: List[object]) -> None:
        self._conn.executemany(
            "INSERT INTO daily_insights "
            "(date, kind, title, body, evidence_json, suggestion, severity, "
            "confidence, source_version, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now')) "
            "ON CONFLICT(date, kind, title) DO UPDATE SET "
            "body=excluded.body, evidence_json=excluded.evidence_json, "
            "suggestion=excluded.suggestion, severity=excluded.severity, "
            "confidence=excluded.confidence, source_version=excluded.source_version, "
            "created_at=datetime('now')",
            [
                (
                    day,
                    self._insight_value(insight, "kind"),
                    self._insight_value(insight, "title"),
                    self._insight_value(insight, "body"),
                    json.dumps(
                        self._insight_value(insight, "evidence", []),
                        ensure_ascii=False,
                    ),
                    self._insight_value(insight, "suggestion", ""),
                    self._insight_value(insight, "severity", 0.0),
                    self._insight_value(insight, "confidence", 0.0),
                    self._insight_value(insight, "source_version", 1),
                )
                for insight in insights
            ],
        )
        self._conn.commit()

    @staticmethod
    def _insight_value(insight: object, key: str, default=None):
        if isinstance(insight, dict):
            return insight.get(key, default)
        return getattr(insight, key, default)

    def get_daily_insights(self, day: str) -> List[Dict]:
        rows = self._conn.execute(
            "SELECT date, kind, title, body, evidence_json, suggestion, severity, "
            "confidence, source_version, created_at "
            "FROM daily_insights WHERE date = ? "
            "ORDER BY severity DESC, confidence DESC, id ASC",
            (day,),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["evidence"] = self._safe_json_list(item.get("evidence_json"))
            result.append(item)
        return result

    def delete_daily_insights(self, day: str) -> None:
        self._conn.execute("DELETE FROM daily_insights WHERE date = ?", (day,))
        self._conn.commit()

    # === Task sessions ===

    def save_task_session(
        self,
        task_id: str,
        label: str,
        project: Optional[str],
        intent: str,
        started_at: str,
        ended_at: Optional[str],
        accumulated_sec: float,
        modes: List[str],
        blockers: List[str],
        outcome: str,
        confidence: float,
    ) -> int:
        session = TaskSession(
            task_id=task_id,
            label=label,
            project=project,
            intent=intent,
            started_at=datetime.fromisoformat(started_at),
            ended_at=datetime.fromisoformat(ended_at) if ended_at else None,
            accumulated_sec=accumulated_sec,
            modes=modes,
            blockers=blockers,
            outcome=outcome,
            confidence=confidence,
        )
        self.upsert_task_session(session)
        row = self._conn.execute(
            "SELECT id FROM task_sessions WHERE session_uid = ?",
            (session.session_uid,),
        ).fetchone()
        return int(row["id"]) if row else 0

    def upsert_task_session(self, session: TaskSession) -> None:
        self._upsert_task_session_no_commit(session)
        self._conn.commit()

    @staticmethod
    def _task_session_params(session: TaskSession) -> Tuple:
        return (
            session.session_uid,
            session.task_id,
            session.label,
            session.project,
            session.intent,
            session.started_at.isoformat(),
            session.ended_at.isoformat() if session.ended_at else None,
            session.accumulated_sec,
            json.dumps(session.modes, ensure_ascii=False),
            json.dumps(session.blockers, ensure_ascii=False),
            session.outcome,
            session.confidence,
            json.dumps(
                [item.__dict__ for item in session.evidence],
                ensure_ascii=False,
            ),
            json.dumps(session.source, ensure_ascii=False),
            session.version,
        )

    def finish_task_session(
        self,
        session_uid: str,
        ended_at: str,
        outcome: str,
    ) -> None:
        self._conn.execute(
            "UPDATE task_sessions SET ended_at = ?, outcome = ?, updated_at = datetime('now') "
            "WHERE session_uid = ?",
            (ended_at, outcome, session_uid),
        )
        self._conn.commit()

    def upsert_task_daily_summary(
        self, day: str, task_id: str, label: str, total_sec: float
    ) -> None:
        self._conn.execute(
            "INSERT INTO task_daily_summary (date, task_id, label, total_sec) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(date, task_id) DO UPDATE SET "
            "label=excluded.label, "
            "total_sec=task_daily_summary.total_sec + excluded.total_sec",
            (day, task_id, label, total_sec),
        )
        self._conn.commit()

    def get_task_daily_summary(self, day: str) -> List[Dict]:
        rows = self._conn.execute(
            "SELECT task_id, label, total_sec FROM task_daily_summary "
            "WHERE date = ? ORDER BY total_sec DESC",
            (day,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_task_sessions_for_day(self, day: str) -> List[Dict]:
        return self.get_task_timeline(day)

    def get_active_task_session(self) -> Optional[Dict]:
        row = self._conn.execute(
            "SELECT * FROM task_sessions "
            "WHERE outcome = 'in_progress' OR ended_at IS NULL "
            "ORDER BY datetime(started_at) DESC, id DESC LIMIT 1"
        ).fetchone()
        return self._task_row_to_dict(row) if row else None

    def get_task_timeline(self, day: str) -> List[Dict]:
        rows = self._conn.execute(
            "SELECT * FROM task_sessions WHERE date(started_at) = ? ORDER BY started_at",
            (day,),
        ).fetchall()
        return [self._task_row_to_dict(row) for row in rows]

    def get_task_session_summary(self, day: str) -> List[Dict]:
        rows = self._conn.execute(
            "SELECT task_id, label, SUM(accumulated_sec) AS total_sec "
            "FROM task_sessions WHERE date(started_at) = ? "
            "GROUP BY task_id, label ORDER BY total_sec DESC",
            (day,),
        ).fetchall()
        return [dict(row) for row in rows if row["total_sec"]]

    def rebuild_task_daily_summary(self, day: str) -> None:
        rows = self.get_task_session_summary(day)
        self._conn.execute("DELETE FROM task_daily_summary WHERE date = ?", (day,))
        self._conn.executemany(
            "INSERT INTO task_daily_summary (date, task_id, label, total_sec) "
            "VALUES (?, ?, ?, ?)",
            [
                (day, row["task_id"], row["label"], row["total_sec"])
                for row in rows
            ],
        )
        self._conn.commit()

    def replace_task_sessions_for_day(self, day: str, sessions: List[TaskSession]) -> None:
        try:
            self._conn.execute("BEGIN")
            self._conn.execute(
                "DELETE FROM task_sessions WHERE date(started_at) = ?", (day,)
            )
            for session in sessions:
                self._upsert_task_session_no_commit(session)
            self._rebuild_task_daily_summary_no_commit(day)
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def _upsert_task_session_no_commit(self, session: TaskSession) -> None:
        self._conn.execute(
            "INSERT INTO task_sessions "
            "(session_uid, task_id, label, project, intent, started_at, ended_at, "
            "accumulated_sec, modes_json, blockers_json, outcome, confidence, "
            "evidence_json, source_json, version, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now')) "
            "ON CONFLICT(session_uid) DO UPDATE SET "
            "task_id=excluded.task_id, label=excluded.label, project=excluded.project, "
            "intent=excluded.intent, started_at=excluded.started_at, "
            "ended_at=excluded.ended_at, accumulated_sec=excluded.accumulated_sec, "
            "modes_json=excluded.modes_json, blockers_json=excluded.blockers_json, "
            "outcome=excluded.outcome, confidence=excluded.confidence, "
            "evidence_json=excluded.evidence_json, source_json=excluded.source_json, "
            "version=excluded.version, updated_at=datetime('now')",
            self._task_session_params(session),
        )

    def _rebuild_task_daily_summary_no_commit(self, day: str) -> None:
        rows = self.get_task_session_summary(day)
        self._conn.execute("DELETE FROM task_daily_summary WHERE date = ?", (day,))
        self._conn.executemany(
            "INSERT INTO task_daily_summary (date, task_id, label, total_sec) "
            "VALUES (?, ?, ?, ?)",
            [
                (day, row["task_id"], row["label"], row["total_sec"])
                for row in rows
            ],
        )

    def task_session_from_row(self, row: Dict[str, Any]) -> TaskSession:
        return TaskSession.from_dict(row)

    def _task_row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        data = dict(row)
        data["modes"] = self._safe_json_list(data.get("modes_json"))
        data["blockers"] = self._safe_json_list(data.get("blockers_json"))
        data["evidence"] = self._safe_json_list(data.get("evidence_json"))
        data["source"] = self._safe_json_dict(data.get("source_json"))
        return data

    @staticmethod
    def _safe_json_list(raw: Any) -> List[Any]:
        try:
            value = json.loads(raw or "[]")
        except (TypeError, json.JSONDecodeError):
            return []
        return value if isinstance(value, list) else []

    @staticmethod
    def _safe_json_dict(raw: Any) -> Dict[str, Any]:
        try:
            value = json.loads(raw or "{}")
        except (TypeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    # === State Snapshots (change-only) ===

    def save_state_snapshot(self, state_json: str, change_reason: str) -> None:
        self._conn.execute(
            "INSERT INTO state_snapshots (timestamp, state_json, change_reason) VALUES (?, ?, ?)",
            (datetime.now().isoformat(timespec="seconds"), state_json, change_reason),
        )
        self._conn.commit()

    def get_last_state_snapshot(self) -> Optional[Dict]:
        row = self._conn.execute(
            "SELECT state_json, change_reason, timestamp FROM state_snapshots "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return {
            "state_json": row["state_json"],
            "change_reason": row["change_reason"],
            "timestamp": row["timestamp"],
        }

    def get_state_snapshots(self, since: Optional[str] = None, limit: int = 1000) -> List[Dict]:
        if since:
            rows = self._conn.execute(
                "SELECT timestamp, state_json, change_reason FROM state_snapshots "
                "WHERE datetime(timestamp) >= datetime(?) ORDER BY timestamp DESC LIMIT ?",
                (since, limit),
            )
        else:
            rows = self._conn.execute(
                "SELECT timestamp, state_json, change_reason FROM state_snapshots "
                "ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            )
        return [
            {
                "timestamp": r["timestamp"],
                "state_json": r["state_json"],
                "change_reason": r["change_reason"],
            }
            for r in rows
        ]

    def close(self) -> None:
        self._conn.close()
