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

from aw_coach.analyzer import PatternAnalyzer
from aw_coach.collector import DataCollector
from aw_coach.correction import VALID_ACTIVITY_TYPES
from aw_coach.rules.engine import RuleEngine
from aw_coach.storage import Storage
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
                self.send_error(404)

            def do_POST(self) -> None:
                if urlparse(self.path).path != "/api/corrections":
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

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
            self._thread = None
