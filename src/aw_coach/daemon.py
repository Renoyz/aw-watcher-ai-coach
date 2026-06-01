#!/usr/bin/env python3
"""Daemon entry point for systemd/autostart. Wraps the scheduler."""

import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)


def main():
    from aw_coach.config import load_config
    from aw_coach.scheduler import run_scheduler
    from aw_coach.webserver import ReportServer

    logger = logging.getLogger("aw_coach")
    logger.info("aw-coach-daemon starting...")

    # Start local HTTP server for dashboard (avoids Snap Firefox file:// restrictions)
    config = load_config()
    web_dir = config.reports_dir / "web"
    web_dir.mkdir(parents=True, exist_ok=True)
    server = ReportServer(web_dir)
    server.start()

    try:
        run_scheduler(dashboard_url=server.dashboard_url)
    except KeyboardInterrupt:
        logger.info("Daemon stopped by user")
    except Exception as e:
        logger.error(f"Daemon crashed: {e}", exc_info=True)
        sys.exit(1)
    finally:
        server.stop()


if __name__ == "__main__":
    main()
