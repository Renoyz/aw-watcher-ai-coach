# Service Health Design

## Goal

Make the Windows autostart daemon observable from the CLI. A user should be able to tell whether `aw-coach-daemon` is installed, running, recently ticking, and where to inspect logs.

## Scope

This design adds health visibility only. It does not change activity classification, AI behavior, notification policy, or report generation.

## Health State

The scheduler writes a JSON heartbeat into the existing SQLite `scheduler_state` table under key `service_health`. The payload records:

- `schema_version`
- `pid`
- `started_at`
- `last_tick`
- `last_success`
- `last_error`
- `last_error_at`
- `status`

`last_tick` is updated once per scheduler loop. `last_success` is updated after a loop completes without a top-level daemon error. On shutdown, status is set to `stopped`.

## Service Status

`ServiceManager.status()` remains responsible for Windows installation state. It also reports daemon process IDs by inspecting Windows processes for `python.exe`/`pythonw.exe` command lines containing `-m aw_coach.daemon`.

The CLI combines:

- installation backend: `Ready`, `RunKey`, or `NotInstalled`
- daemon process IDs
- scheduler heartbeat freshness
- last error
- daemon log paths

## Logs

Add `aw-coach service logs --lines N` to print recent daemon stdout/stderr log tails from the configured data directory.

## Doctor

`aw-coach doctor` includes a service health line on Windows when service inspection is available. This line should not make `doctor` fail when the service is not installed.
