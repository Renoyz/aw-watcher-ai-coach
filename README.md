# ActivityWatch AI Coach

**Language:** English | [简体中文](README.zh-CN.md)

ActivityWatch AI Coach is a local-first work coach for ActivityWatch data. It combines
rules, optional LLM classification, pattern analysis, proactive assistance, background
summaries, and task perception.

## What It Does

- Reads local ActivityWatch window, AFK, and optional browser events.
- Classifies work activity with rule-only, hybrid, or OpenAI-compatible backends.
- Generates status, daily, and weekly reports.
- Runs an optional background daemon and web dashboard.
- Tracks semantic context, process context, Git context, task signals, and optional screenshots.
- Provides proactive assistance through an inbox and policy gate.
- Supports Windows autostart diagnostics for Task Scheduler and Run key setups.

## Requirements

- Python 3.9 or newer.
- ActivityWatch running locally.
- Optional: an OpenAI-compatible API key for hybrid or LLM-backed features.
- Optional on Windows: Git and PowerShell for service/autostart workflows.

## Install For Development

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev,ai,screenshot,web]"
```

On Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev,ai,screenshot,web]"
```

## Quick Start

```bash
aw-coach doctor
aw-coach health
aw-coach status
aw-coach state
aw-coach report
aw-coach report --full
aw-coach inbox list
aw-coach task list
aw-coach serve
```

If `aw-coach` is not on `PATH`, run with `PYTHONPATH=src`:

```bash
PYTHONPATH=src python -m aw_coach.cli doctor
```

## Background Service

Run the daemon directly:

```bash
aw-coach-daemon
```

On Windows, install and inspect autostart:

```powershell
aw-coach service install
aw-coach service start
aw-coach service status
aw-coach service logs --lines 50
```

The Windows installer first tries Task Scheduler. If normal user permissions cannot
register the task, it falls back to the current-user Run key.

## Configuration

Default path:

```text
~/.config/activitywatch/aw-watcher-ai-coach.toml
```

Example:

```toml
[ai]
backend = "hybrid"   # rule_only | hybrid | openai

[ai.openai]
api_key = "${DEEPSEEK_API_KEY}"
model = "deepseek-v4-flash"
base_url = "https://api.deepseek.com/v1"

[policy]
quiet_hours_enabled = true
quiet_hours_start = "22:00"
quiet_hours_end = "08:00"

[report]
instant_summary_interval_hours = 2
background_ai_summary = false
morning_brief_time = "09:00"
llm_timeout_seconds = 90

[report.delivery]
instant_summary = "notify"   # notify | inbox | both | off
daily_report = "notify"
morning_brief = "inbox"
medium_signal = "inbox"
high_severity_signal = "notify"
task_confirm = "inbox"
task_confirm_min_minutes = 10
task_confirm_daily_limit = 3

[tasks]
enabled = true
project_roots = ["~/projects", "~/下载/activitywatch"]

[screenshot]
enabled = false
```

With the default `hybrid` backend, the classifier stays local-only until
`ai.openai.api_key` is configured. Without an API key it falls back to rule-only
classification instead of making external calls.

## CLI Reference

| Command | Purpose |
| --- | --- |
| `aw-coach inbox list/dismiss/accept` | Proactive assistance inbox |
| `aw-coach task list/confirm/set/review` | Task perception and calibration |
| `aw-coach serve` | Interactive web dashboard |
| `aw-coach cost` | LLM cost statistics |
| `aw-coach health` | Daemon, delivery, and schedule health |
| `aw-coach config show/set/path` | Configuration management |
| `aw-coach service status/logs` | Windows service diagnostics |

## Privacy Notes

This tool is local-first, but it can still process sensitive local activity metadata.

- ActivityWatch event data stays in your local ActivityWatch database.
- AI calls are controlled by the configured backend.
- Screenshot analysis is optional and disabled by default.
- Built-in rules can mark sensitive contexts as `skip_screenshot`.
- Do not commit local databases, reports, screenshots, logs, or secrets.

## Development Checks

```bash
python -m ruff check .
PYTHONPATH=src python -m pytest tests/ -p no:anyio -q
```

## GitHub Workflow

- `main` is the stable default branch.
- Feature work should happen on topic branches.
- Use draft pull requests for large changes.

See `AGENT.md` and the design documents in `doc/` for deeper implementation notes.
