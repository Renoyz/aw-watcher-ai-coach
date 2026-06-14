# ActivityWatch AI Coach

**Language:** English | [简体中文](README.zh-CN.md)

ActivityWatch AI Coach analyzes local ActivityWatch data and turns it into work-pattern summaries, reports, and lightweight coaching diagnostics.

The project is currently optimized for local-first Windows use with ActivityWatch already running.

## What It Does

- Reads local ActivityWatch events.
- Classifies work activity with rule-only or hybrid AI backends.
- Generates status, daily, and weekly reports.
- Runs an optional background daemon.
- Exposes Windows autostart diagnostics with service status, heartbeat, and log inspection.
- Tracks optional semantic context, process context, Git context, and screenshot signals.

## Requirements

- Python 3.9 or newer.
- ActivityWatch running locally.
- Git and PowerShell on Windows.
- Optional: GitHub CLI for repository workflows.

## Install For Development

From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev,ai,screenshot,web]"
```

Check the CLI:

```powershell
aw-coach --help
aw-coach doctor
```

If `aw-coach` is not on PATH, use:

```powershell
$env:PYTHONPATH='src'
python -m aw_coach.cli doctor
```

## Windows Background Service

Install autostart:

```powershell
aw-coach service install
```

The installer first tries Windows Task Scheduler. If normal user permissions cannot register the task, it falls back to the current-user Run key.

Start or stop the daemon:

```powershell
aw-coach service start
aw-coach service stop
```

Inspect service health:

```powershell
aw-coach service status
```

The status output includes:

- installation backend such as `Ready`, `Running`, `RunKey`, or `NotInstalled`
- daemon process IDs
- scheduler heartbeat freshness
- last daemon error when available
- stdout/stderr log paths

Read recent daemon logs:

```powershell
aw-coach service logs --lines 50
```

Run full diagnostics:

```powershell
aw-coach doctor
```

`doctor` includes a non-fatal `service/autostart` line. Service inspection failures should not block the rest of the health check.

## Common Windows Troubleshooting

If status shows `RunKey`, Task Scheduler registration was likely denied and the fallback autostart is active.

If heartbeat is stale:

```powershell
aw-coach service logs --lines 100
aw-coach service stop
aw-coach service start
aw-coach service status
```

If ActivityWatch is unavailable, start ActivityWatch first and rerun:

```powershell
aw-coach doctor
```

## Privacy Notes

This tool is local-first, but it can still process sensitive local activity metadata.

- ActivityWatch event data stays in your local ActivityWatch database.
- AI calls are controlled by the configured backend.
- Screenshot analysis is optional and should be treated as sensitive.
- Built-in rules can mark sensitive contexts as `skip_screenshot`.
- Review configuration before enabling screenshot or hybrid AI features.
- Do not commit local databases, reports, screenshots, logs, or secrets.

## Development Checks

Run lint:

```powershell
python -m ruff check .
```

Run tests:

```powershell
$env:PYTHONPATH='src'
python -m pytest tests/ -p no:anyio -q
```

Run both before pushing changes.

## GitHub Workflow

The repository uses:

- `main` as the stable default branch
- feature branches for active work
- GitHub Actions CI for lint and tests
- draft pull requests for large changes

Current recommended validation for a pull request:

```powershell
python -m ruff check .
$env:PYTHONPATH='src'; python -m pytest tests/ -p no:anyio -q
aw-coach service status
aw-coach service logs --lines 20
aw-coach doctor
```
