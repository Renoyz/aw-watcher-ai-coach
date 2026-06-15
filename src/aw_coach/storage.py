"""SQLite local state management - corrections, cost log, batch queue."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from os import PathLike
from pathlib import Path
from typing import Dict, List, Optional, Tuple


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

        self._conn.commit()

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
            "VALUES (datetime('now'), ?, ?, ?, ?)",
            (signal_type, severity, evidence, reason),
        )
        self._conn.commit()
        return cursor.lastrowid

    def get_inbox_items(self, dismissed: bool = False, limit: int = 50) -> List[Dict]:
        rows = self._conn.execute(
            "SELECT id, timestamp, signal_type, severity, evidence, reason, dismissed "
            "FROM inbox WHERE dismissed = ? ORDER BY timestamp DESC LIMIT ?",
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
        import json

        cursor = self._conn.execute(
            "INSERT INTO task_sessions "
            "(task_id, label, project, intent, started_at, ended_at, accumulated_sec, "
            "modes_json, blockers_json, outcome, confidence) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task_id,
                label,
                project,
                intent,
                started_at,
                ended_at,
                accumulated_sec,
                json.dumps(modes, ensure_ascii=False),
                json.dumps(blockers, ensure_ascii=False),
                outcome,
                confidence,
            ),
        )
        self._conn.commit()
        return cursor.lastrowid

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
        rows = self._conn.execute(
            "SELECT * FROM task_sessions WHERE date(started_at) = ? ORDER BY started_at",
            (day,),
        ).fetchall()
        return [dict(row) for row in rows]

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
