"""Tiny HTTP server to serve reports dashboard via localhost.

Avoids Snap Firefox / Chromium restrictions on file:// URLs inside
hidden directories like ~/.local.
"""

from __future__ import annotations

import logging
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8899


class _QuietHandler(SimpleHTTPRequestHandler):
    """Suppress default request logging noise."""

    def log_message(self, format: str, *args) -> None:
        logger.debug(format % args)


class ReportServer:
    """Serve the reports/web directory over HTTP."""

    def __init__(self, web_dir: Path, port: int = DEFAULT_PORT):
        self.web_dir = web_dir.resolve()
        self.port = port
        self._httpd: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def dashboard_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/index.html"

    def start(self) -> bool:
        if self._thread is not None:
            return True

        def handler_class(*args, **kwargs):
            return _QuietHandler(*args, directory=str(self.web_dir), **kwargs)

        start_port = self.port
        for port in range(start_port, start_port + 20):
            try:
                self._httpd = HTTPServer(("127.0.0.1", port), handler_class)
                self.port = port
                break
            except OSError:
                logger.warning(f"Port {port} already in use, trying {port + 1}")

        if self._httpd is None:
            raise OSError(
                f"No available localhost port in range {start_port}-{start_port + 19}"
            )

        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            daemon=True,
            name="aw-coach-webserver",
        )
        self._thread.start()
        logger.info(
            f"Report server started at http://127.0.0.1:{self.port}/ "
            f"(serving {self.web_dir})"
        )
        return True

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd = None
            self._thread = None
            logger.info("Report server stopped")
