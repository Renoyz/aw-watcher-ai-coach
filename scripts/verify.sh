#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

python3 -m ruff check .
PYTHONPATH=src python3 -m pytest tests/ -p no:anyio -q
python3 -m build --wheel --no-isolation

python3 - <<'PY'
from pathlib import Path
from zipfile import ZipFile

wheels = sorted(Path("dist").glob("*.whl"))
if not wheels:
    raise SystemExit("No wheel produced in dist/")

with ZipFile(wheels[-1]) as wheel:
    names = set(wheel.namelist())

for required in (
    "aw_coach/rules/builtin/cn.yml",
    "aw_coach/rules/builtin/global.yml",
    "aw_coach/web/templates/dashboard.html",
    "aw_coach/web/templates/report.html",
):
    if required not in names:
        raise SystemExit(f"Missing packaged data: {required}")

print(f"Verified wheel package data: {wheels[-1].name}")
PY

rm -rf build dist src/*.egg-info
