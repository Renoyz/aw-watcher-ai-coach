# Windows Autostart Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Windows Task Scheduler based autostart management for `aw-coach-daemon`.

**Architecture:** Create a focused `aw_coach.service_installer` module that owns platform checks, command construction, and subprocess execution. Wire it into a new `aw-coach service` CLI group. Use Task Scheduler because it runs in the logged-in user session and avoids true Windows Service Session 0 limitations.

**Tech Stack:** Python standard library (`subprocess`, `sys`, `platform`, `shutil`, `pathlib`), Click CLI, pytest, Windows PowerShell scheduled task cmdlets.

---

### Task 1: Windows Service Installer Module

**Files:**
- Create: `src/aw_coach/service_installer.py`
- Create: `tests/test_service_installer.py`

- [ ] **Step 1: Write failing tests for command generation and platform guard**

Create `tests/test_service_installer.py` with:

```python
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from aw_coach.service_installer import (
    ServiceManager,
    ServiceStatus,
    ServiceUnsupportedError,
)


def test_windows_install_uses_pythonw_module_and_source_pythonpath(tmp_path, monkeypatch):
    src_dir = tmp_path / "src"
    package_dir = src_dir / "aw_coach"
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    logs_dir = tmp_path / "data"
    runner = Mock(return_value=SimpleNamespace(returncode=0, stdout="", stderr=""))

    monkeypatch.setattr("aw_coach.service_installer.platform.system", lambda: "Windows")
    monkeypatch.setattr("aw_coach.service_installer.sys.executable", str(tmp_path / "python.exe"))
    monkeypatch.setattr("aw_coach.service_installer.shutil.which", lambda name: str(tmp_path / "pythonw.exe") if name == "pythonw.exe" else None)

    manager = ServiceManager(project_root=tmp_path, data_dir=logs_dir, runner=runner)
    manager.install()

    command = runner.call_args.args[0]
    script = command[-1]
    assert command[:2] == ["powershell.exe", "-NoProfile"]
    assert "Register-ScheduledTask" in script
    assert str(tmp_path / "pythonw.exe") in script
    assert "-m aw_coach.daemon" in script
    assert f"PYTHONPATH={src_dir}" in script
    assert str(logs_dir / "aw-coach-daemon.log") in script
    assert str(logs_dir / "aw-coach-daemon.err.log") in script


def test_non_windows_install_raises_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr("aw_coach.service_installer.platform.system", lambda: "Linux")
    manager = ServiceManager(project_root=tmp_path, data_dir=tmp_path)

    with pytest.raises(ServiceUnsupportedError, match="Windows Task Scheduler"):
        manager.install()


def test_status_parses_existing_task(tmp_path, monkeypatch):
    runner = Mock(return_value=SimpleNamespace(returncode=0, stdout="Ready\\r\\n", stderr=""))
    monkeypatch.setattr("aw_coach.service_installer.platform.system", lambda: "Windows")

    status = ServiceManager(project_root=tmp_path, data_dir=tmp_path, runner=runner).status()

    assert status == ServiceStatus(installed=True, state="Ready")
    assert "Get-ScheduledTask" in runner.call_args.args[0][-1]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
$env:PYTHONPATH='src'; python -m pytest tests/test_service_installer.py -q
```

Expected: import failure because `aw_coach.service_installer` does not exist.

- [ ] **Step 3: Implement minimal service installer**

Create `src/aw_coach/service_installer.py` with these public APIs:

```python
from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence


TASK_NAME = "aw-coach"
TASK_DESCRIPTION = "ActivityWatch AI Coach background daemon"


class ServiceError(RuntimeError):
    pass


class ServiceUnsupportedError(ServiceError):
    pass


@dataclass(frozen=True)
class ServiceStatus:
    installed: bool
    state: str = "NotInstalled"


Runner = Callable[..., subprocess.CompletedProcess[str]]


class ServiceManager:
    def __init__(
        self,
        project_root: Path,
        data_dir: Path,
        runner: Optional[Runner] = None,
    ) -> None:
        self.project_root = Path(project_root)
        self.data_dir = Path(data_dir)
        self.runner = runner or subprocess.run

    def install(self) -> None:
        self._require_windows()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._run_powershell(self._install_script())

    def uninstall(self) -> None:
        self._require_windows()
        self._run_powershell(
            "$task = Get-ScheduledTask -TaskName 'aw-coach' -ErrorAction SilentlyContinue; "
            "if ($task) { Unregister-ScheduledTask -TaskName 'aw-coach' -Confirm:$false }"
        )

    def start(self) -> None:
        self._require_windows()
        self._run_powershell("Start-ScheduledTask -TaskName 'aw-coach'")

    def stop(self) -> None:
        self._require_windows()
        self._run_powershell("Stop-ScheduledTask -TaskName 'aw-coach' -ErrorAction SilentlyContinue")

    def status(self) -> ServiceStatus:
        self._require_windows()
        result = self._run_powershell(
            "$task = Get-ScheduledTask -TaskName 'aw-coach' -ErrorAction SilentlyContinue; "
            "if ($task) { $task.State } else { 'NotInstalled' }",
            check=False,
        )
        state = (result.stdout or "").strip().splitlines()[-1].strip() if result.stdout else "NotInstalled"
        return ServiceStatus(installed=state != "NotInstalled", state=state)
```

Also implement private helpers:

```python
    def _require_windows(self) -> None:
        if platform.system() != "Windows":
            raise ServiceUnsupportedError(
                "Windows Task Scheduler service management is only available on Windows."
            )

    def _python_executable(self) -> Path:
        pythonw = shutil.which("pythonw.exe")
        return Path(pythonw) if pythonw else Path(sys.executable)

    def _source_pythonpath(self) -> Optional[Path]:
        candidate = self.project_root / "src"
        if (candidate / "aw_coach").exists():
            return candidate
        return None

    def _quote_ps(self, value: Path | str) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    def _install_script(self) -> str:
        exe = self._quote_ps(self._python_executable())
        stdout = self._quote_ps(self.data_dir / "aw-coach-daemon.log")
        stderr = self._quote_ps(self.data_dir / "aw-coach-daemon.err.log")
        src = self._source_pythonpath()
        env_prefix = f"$env:PYTHONPATH={self._quote_ps(src)}; " if src else ""
        argument = self._quote_ps(f"-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command \"{env_prefix}& {exe} -m aw_coach.daemon *> {stdout} 2> {stderr}\"")
        return (
            "$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument " + argument + "; "
            "$trigger = New-ScheduledTaskTrigger -AtLogOn; "
            "$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1); "
            "Register-ScheduledTask -TaskName 'aw-coach' -Description 'ActivityWatch AI Coach background daemon' "
            "-Action $action -Trigger $trigger -Settings $settings -Force | Out-Null"
        )

    def _run_powershell(self, script: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = self.runner(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            text=True,
        )
        if check and result.returncode != 0:
            raise ServiceError((result.stderr or result.stdout or "PowerShell command failed").strip())
        return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```powershell
$env:PYTHONPATH='src'; python -m pytest tests/test_service_installer.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Self-review**

Check that this module has no Click dependency, no ActivityWatch dependency, and does not require administrator rights.

### Task 2: CLI Service Commands

**Files:**
- Modify: `src/aw_coach/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing CLI tests**

Append to `tests/test_cli.py`:

```python
def test_service_status_command_reports_installed(monkeypatch, tmp_path):
    from aw_coach.service_installer import ServiceStatus

    class FakeServiceManager:
        def __init__(self, project_root, data_dir):
            self.project_root = project_root
            self.data_dir = data_dir

        def status(self):
            return ServiceStatus(installed=True, state="Ready")

    monkeypatch.setattr("aw_coach.cli.ServiceManager", FakeServiceManager)
    monkeypatch.setattr("aw_coach.cli.load_config", lambda: Config(data_dir=tmp_path))

    result = CliRunner().invoke(main, ["service", "status"])

    assert result.exit_code == 0
    assert "installed" in result.output.lower()
    assert "Ready" in result.output


def test_service_install_command_calls_manager(monkeypatch, tmp_path):
    calls = []

    class FakeServiceManager:
        def __init__(self, project_root, data_dir):
            calls.append(("init", project_root, data_dir))

        def install(self):
            calls.append(("install",))

    monkeypatch.setattr("aw_coach.cli.ServiceManager", FakeServiceManager)
    monkeypatch.setattr("aw_coach.cli.load_config", lambda: Config(data_dir=tmp_path))

    result = CliRunner().invoke(main, ["service", "install"])

    assert result.exit_code == 0
    assert calls[-1] == ("install",)
    assert "installed" in result.output.lower()


def test_service_command_reports_service_errors(monkeypatch, tmp_path):
    from aw_coach.service_installer import ServiceUnsupportedError

    class FakeServiceManager:
        def __init__(self, project_root, data_dir):
            pass

        def start(self):
            raise ServiceUnsupportedError("Windows Task Scheduler only")

    monkeypatch.setattr("aw_coach.cli.ServiceManager", FakeServiceManager)
    monkeypatch.setattr("aw_coach.cli.load_config", lambda: Config(data_dir=tmp_path))

    result = CliRunner().invoke(main, ["service", "start"])

    assert result.exit_code != 0
    assert "Windows Task Scheduler only" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
$env:PYTHONPATH='src'; python -m pytest tests/test_cli.py -q
```

Expected: failure because `service` command group and `aw_coach.cli.ServiceManager` are missing.

- [ ] **Step 3: Add CLI imports and helper**

Near existing imports in `src/aw_coach/cli.py`, add:

```python
from aw_coach.service_installer import ServiceError, ServiceManager
```

Add helper:

```python
def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _service_manager() -> ServiceManager:
    config = load_config()
    return ServiceManager(project_root=_project_root(), data_dir=Path(config.data_dir))
```

- [ ] **Step 4: Add `service` command group**

Before the `if __name__ == "__main__"` block if present, or near other top-level commands, add:

```python
@main.group(name="service")
def service_group() -> None:
    """Manage Windows autostart for the background daemon."""


@service_group.command("install")
def service_install() -> None:
    """Install or update the Windows logon autostart task."""
    try:
        _service_manager().install()
    except ServiceError as e:
        raise click.ClickException(str(e)) from e
    click.echo("aw-coach service installed.")


@service_group.command("uninstall")
def service_uninstall() -> None:
    """Remove the Windows logon autostart task."""
    try:
        _service_manager().uninstall()
    except ServiceError as e:
        raise click.ClickException(str(e)) from e
    click.echo("aw-coach service uninstalled.")


@service_group.command("start")
def service_start() -> None:
    """Start the scheduled daemon task now."""
    try:
        _service_manager().start()
    except ServiceError as e:
        raise click.ClickException(str(e)) from e
    click.echo("aw-coach service started.")


@service_group.command("stop")
def service_stop() -> None:
    """Stop the scheduled daemon task."""
    try:
        _service_manager().stop()
    except ServiceError as e:
        raise click.ClickException(str(e)) from e
    click.echo("aw-coach service stopped.")


@service_group.command("status")
def service_status() -> None:
    """Show scheduled daemon task status."""
    try:
        status = _service_manager().status()
    except ServiceError as e:
        raise click.ClickException(str(e)) from e
    if status.installed:
        click.echo(f"aw-coach service installed: {status.state}")
    else:
        click.echo("aw-coach service not installed.")
```

Also add `service` to the no-subcommand command list.

- [ ] **Step 5: Run CLI tests**

Run:

```powershell
$env:PYTHONPATH='src'; python -m pytest tests/test_cli.py tests/test_service_installer.py -q
```

Expected: all tests pass.

### Task 3: Integration Verification and Documentation

**Files:**
- Modify: `AGENT.md`
- No production code changes unless verification exposes a bug.

- [ ] **Step 1: Add a short Windows autostart note**

In `AGENT.md`, add a small Windows section explaining:

```markdown
### Windows autostart

On Windows, `aw-coach service install` registers a Task Scheduler task named `aw-coach` for the current user. It runs `aw-coach-daemon` at logon in the user session, which keeps desktop notifications and ActivityWatch local APIs available.

Useful commands:

```powershell
aw-coach service install
aw-coach service start
aw-coach service status
aw-coach service stop
aw-coach service uninstall
```
```

- [ ] **Step 2: Run full automated verification**

Run:

```powershell
python -m ruff check .
$env:PYTHONPATH='src'; python -m pytest tests/ -p no:anyio -q
```

Expected: ruff passes; pytest passes.

- [ ] **Step 3: Run real Windows Task Scheduler verification**

Run from the repository:

```powershell
$env:PYTHONPATH='src'
python -c "from aw_coach.cli import main; main()" service install
python -c "from aw_coach.cli import main; main()" service status
python -c "from aw_coach.cli import main; main()" service start
Start-Sleep -Seconds 5
python -c "from aw_coach.cli import main; main()" service status
Get-ScheduledTask -TaskName aw-coach | Select-Object TaskName,State
python -c "from aw_coach.cli import main; main()" doctor
```

Expected:

- Install exits 0.
- Status reports installed.
- Scheduled task exists and can start.
- `doctor` still reaches ActivityWatch and the `ai-coach_*` bucket.

- [ ] **Step 4: Leave autostart installed unless the user asks to remove it**

Do not run `service uninstall` at the end. The user's requested end state is Windows autostart enabled.
