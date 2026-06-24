"""Entry point for ``python -m aw_coach``.

With no arguments, keep the historical background-service behavior.  With
arguments, dispatch to the CLI so ``python -m aw_coach health`` does not
accidentally start the scheduler.
"""

from __future__ import annotations

import sys


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] != "daemon":
        from aw_coach.cli import main as cli_main

        cli_main(prog_name="aw-coach")
        return

    if len(sys.argv) > 1 and sys.argv[1] == "daemon":
        sys.argv.pop(1)

    from aw_coach.scheduler import run_scheduler

    run_scheduler()


if __name__ == "__main__":
    main()
