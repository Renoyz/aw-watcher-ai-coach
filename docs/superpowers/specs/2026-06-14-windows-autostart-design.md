# Windows Autostart Design

## Goal

Add a Windows user-session autostart path for `aw-coach-daemon` so the coach can run after login without a manual terminal.

## Approach

Use Windows Task Scheduler rather than a real SCM Windows Service. `aw-coach-daemon` depends on user-session behavior such as desktop notifications, user profile paths, and ActivityWatch running in the same logged-in desktop session. A Task Scheduler task triggered at user logon gives the needed autostart behavior without admin-only service plumbing or Session 0 limitations.

## CLI

Add a `service` command group:

- `aw-coach service install`: register or update the scheduled task.
- `aw-coach service uninstall`: remove the scheduled task.
- `aw-coach service start`: start the task immediately.
- `aw-coach service stop`: stop the running task.
- `aw-coach service status`: show task existence and scheduler state.

On non-Windows platforms, these commands should fail clearly with a message saying Windows Task Scheduler service management is only available on Windows for now.

## Execution Model

The scheduled task runs the current Python executable with module execution:

```powershell
pythonw.exe -m aw_coach.daemon
```

When the project is being used from source, the task should include `PYTHONPATH=<repo>\src` so the installed task can run the current checkout. Logs go to the configured data directory:

- `aw-coach-daemon.log`
- `aw-coach-daemon.err.log`

The task should be registered under the current user, trigger on logon, allow battery operation, and restart on failure.

## Boundaries

This feature does not install ActivityWatch itself and does not convert `aw-coach` into a pywin32/nssm Windows service. It manages only the `aw-coach` background daemon autostart.

## Testing

Unit tests should verify command construction and CLI behavior without requiring Task Scheduler or administrator rights. Manual integration should verify on the current Windows machine that the task can be installed, started, queried, and stopped.
