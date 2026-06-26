"""Temporary interactive web server for dashboard corrections."""

from __future__ import annotations

import json
import logging
import threading
from datetime import date as date_type
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import urlparse

from aw_coach.ai.cost import CostController
from aw_coach.ai.summary import generate_ai_summary
from aw_coach.analyzer import PatternAnalyzer
from aw_coach.collector import DataCollector
from aw_coach.correction import VALID_ACTIVITY_TYPES
from aw_coach.rules.engine import RuleEngine
from aw_coach.storage import Storage
from aw_coach.time_utils import format_local_timestamp
from aw_coach.web.dashboard import dashboard_html
from aw_coach.web.helpers import build_slice_timeline

logger = logging.getLogger(__name__)


class InteractiveReportServer:
    def __init__(self, config, target: date_type, port: int = 5601):
        self.config = config
        self.target = target
        self.port = port
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._html = ""
        self._slices = []
        self._rules = []
        self._analysis = None
        self._timeline_by_id = {}

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def refresh(self) -> None:
        collector = DataCollector(client_name="aw-coach-web")
        start = datetime.combine(self.target, datetime.min.time())
        end = datetime.now() if self.target == date_type.today() else datetime.combine(
            self.target,
            datetime.max.time(),
        )
        self._slices = collector.fetch_range(start, end)

        engine = RuleEngine.with_all_rules()
        self._rules = [
            engine.classify(s.primary_app, s.primary_title, s.web_url)
            for s in self._slices
        ]
        analysis = PatternAnalyzer(self.config.analysis).analyze(self._slices, self._rules)
        self._analysis = analysis
        timeline = build_slice_timeline(self._slices, self._rules)
        self._timeline_by_id = {item["id"]: item for item in timeline}
        self._html = dashboard_html(
            self.config,
            self.target,
            analysis,
            slices=self._slices,
            rules=self._rules,
            interactive=True,
        )

    def start(self) -> None:
        if self._httpd is not None:
            return

        self.refresh()
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                path = urlparse(self.path).path
                if path in {"/", "/index.html"}:
                    self._send_html(server._html)
                    return
                if path == "/api/timeline":
                    self._send_json({"items": list(server._timeline_by_id.values())})
                    return
                if path == "/api/inbox":
                    storage = Storage(server.config.db_path)
                    items = storage.get_inbox_items(dismissed=False, limit=50)
                    for item in items:
                        item["local_timestamp"] = format_local_timestamp(item["timestamp"])
                    self._send_json({"items": items})
                    return
                if path == "/api/tasks":
                    storage = Storage(server.config.db_path)
                    day = server.target.isoformat()
                    summary = storage.get_task_daily_summary(day)
                    sessions = storage.get_task_sessions_for_day(day)
                    self._send_json({"summary": summary, "sessions": sessions})
                    return
                self.send_error(404)

            def do_POST(self) -> None:
                path = urlparse(self.path).path
                if path == "/api/summary":
                    try:
                        response = server.generate_summary()
                    except ValueError as e:
                        self._send_json({"error": str(e)}, status=400)
                        return
                    except Exception as e:
                        logger.exception("Summary API failed")
                        self._send_json({"error": str(e)}, status=500)
                        return

                    self._send_json(response)
                    return

                if path == "/api/inbox/dismiss":
                    try:
                        length = int(self.headers.get("Content-Length", "0"))
                        payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                        item_id = int(payload.get("id", 0))
                        storage = Storage(server.config.db_path)
                        storage.dismiss_inbox_item(item_id)
                        self._send_json({"ok": True, "id": item_id})
                    except (ValueError, TypeError) as e:
                        self._send_json({"error": str(e)}, status=400)
                    except Exception as e:
                        logger.exception("Inbox dismiss API failed")
                        self._send_json({"error": str(e)}, status=500)
                    return

                if path != "/api/corrections":
                    self.send_error(404)
                    return

                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                    response = server.add_correction(
                        str(payload.get("slice_id", "")),
                        str(payload.get("corrected_type", "")),
                    )
                except ValueError as e:
                    self._send_json({"error": str(e)}, status=400)
                    return
                except Exception as e:
                    logger.exception("Correction API failed")
                    self._send_json({"error": str(e)}, status=500)
                    return

                self._send_json(response)

            def log_message(self, format: str, *args) -> None:
                logger.debug(format % args)

            def _send_html(self, body: str, status: int = 200) -> None:
                data = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _send_json(self, body: dict, status: int = 200) -> None:
                data = json.dumps(body, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        start_port = self.port
        for port in range(start_port, start_port + 20):
            try:
                self._httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
                self.port = self._httpd.server_port
                break
            except OSError:
                logger.warning("Port %s already in use, trying %s", port, port + 1)
        if self._httpd is None:
            raise OSError(f"No available localhost port in range {start_port}-{start_port + 19}")

        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            daemon=True,
            name="aw-coach-interactive-web",
        )
        self._thread.start()

    def add_correction(self, slice_id: str, corrected_type: str) -> dict:
        if corrected_type not in VALID_ACTIVITY_TYPES:
            raise ValueError(f"Invalid corrected_type: {corrected_type}")

        item = self._timeline_by_id.get(slice_id)
        if item is None:
            raise ValueError(f"Unknown slice_id: {slice_id}")

        index = int(slice_id)
        source_slice = self._slices[index]
        source_rule = self._rules[index]
        Storage(self.config.db_path).add_correction(
            timestamp=source_slice.start.isoformat(),
            app=source_slice.primary_app,
            title=source_slice.primary_title,
            original_type=source_rule.activity_type,
            corrected_type=corrected_type,
        )
        return {
            "ok": True,
            "slice_id": slice_id,
            "app": source_slice.primary_app,
            "original_type": source_rule.activity_type,
            "corrected_type": corrected_type,
        }

    def generate_summary(self) -> dict:
        if self._analysis is None:
            raise ValueError("Analysis is not ready.")

        storage = Storage(self.config.db_path)
        try:
            cost_controller = CostController(self.config.cost, storage)
            corrections = storage.get_corrections_last_30_days()
            summary = generate_ai_summary(
                self._analysis,
                self.config,
                corrections=corrections,
                cost_controller=cost_controller,
            )
        finally:
            storage.close()

        return {
            "ok": True,
            "summary": summary,
        }

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
            self._thread = None
