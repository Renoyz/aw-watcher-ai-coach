from __future__ import annotations

import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

TASK_NAME = "aw-coach"
TASK_DESCRIPTION = "ActivityWatch AI Coach background daemon"


class ServiceError(RuntimeError):
    """Base error for service installer operations."""


class ServiceUnsupportedError(ServiceError):
    """Raised when service operations are not available on the current platform."""


Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class ServiceStatus:
    installed: bool
    state: str = "NotInstalled"
    daemon_pids: tuple[int, ...] = ()


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
        result = self._run_powershell(self._install_script(), check=False)
        if result.returncode == 0:
            return
        if self._is_access_denied(result):
            self._run_powershell(self._install_runkey_script())
            return
        self._raise_service_error(result)

    def uninstall(self) -> None:
        self._require_windows()
        self._run_powershell(self._uninstall_script())

    def start(self) -> None:
        self._require_windows()
        self._run_powershell(self._start_script())

    def stop(self) -> None:
        self._require_windows()
        self._run_powershell(self._stop_script())

    def status(self) -> ServiceStatus:
        self._require_windows()
        result = self._run_powershell(
            self._status_script(),
            check=True,
        )
        output = (result.stdout or "").strip()
        state = "NotInstalled"
        daemon_pids: list[int] = []
        valid_states = {
            "Ready",
            "Running",
            "Disabled",
            "Queued",
            "Unknown",
            "RunKey",
            "NotInstalled",
        }
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            match = re.fullmatch(r"PID:(\d+)", line)
            if match:
                daemon_pids.append(int(match.group(1)))
            elif line in valid_states:
                state = line
        return ServiceStatus(
            installed=state != "NotInstalled",
            state=state,
            daemon_pids=tuple(daemon_pids),
        )

    def _require_windows(self) -> None:
        if platform.system() != "Windows":
            raise ServiceUnsupportedError(
                "Windows Task Scheduler service management is only available on Windows."
            )

    def _python_executable(self) -> Path:
        pythonw = shutil.which("pythonw.exe")
        if pythonw is None:
            return Path(self._default_python_executable())
        return Path(pythonw)

    def _default_python_executable(self) -> str:
        return str(Path(sys.executable))

    def _source_pythonpath(self) -> Optional[Path]:
        candidate = self.project_root / "src"
        if (candidate / "aw_coach").exists():
            return candidate
        return None

    def _install_script(self) -> str:
        argument = self._quote_ps_double(self._powershell_launch_command())
        return (
            "$action = New-ScheduledTaskAction -Execute 'powershell.exe' "
            f"-Argument {argument}; "
            "$trigger = New-ScheduledTaskTrigger -AtLogOn; "
            "$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries "
            "-DontStopIfGoingOnBatteries -RestartCount 3 "
            "-RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable:$true; "
            "$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME "
            "-LogonType Interactive -RunLevel Limited; "
            f"Register-ScheduledTask -TaskName '{TASK_NAME}' -Description "
            f"'{TASK_DESCRIPTION}' -Action $action -Trigger $trigger "
            "-Settings $settings -Principal $principal -Force"
        )

    def _install_runkey_script(self) -> str:
        return (
            "New-Item -Path "
            + self._quote_ps(self._run_key_path())
            + " -Force | Out-Null; "
            "New-ItemProperty -Path "
            + self._quote_ps(self._run_key_path())
            + " -Name "
            + self._quote_ps(self._run_key_name())
            + " -Value "
            + self._quote_ps(self._run_key_value())
            + " -PropertyType String -Force"
        )

    def _start_script(self) -> str:
        return (
            "$task = Get-ScheduledTask -TaskName 'aw-coach' -ErrorAction SilentlyContinue; "
            "if ($task) { "
            "Start-ScheduledTask -TaskName 'aw-coach' "
            "} else { "
            "$runEntry = Get-ItemProperty -Path "
            + self._quote_ps(self._run_key_path())
            + " -Name "
            + self._quote_ps(self._run_key_name())
            + " -ErrorAction SilentlyContinue; "
            "if ($runEntry) { "
            "Start-Process -FilePath 'powershell.exe' "
            "-ArgumentList "
            + self._quote_ps(self._powershell_launch_command())
            + " -WindowStyle Hidden "
            "}}"
        )

    def _stop_script(self) -> str:
        return (
            "$currentPid = $PID; "
            "Stop-ScheduledTask -TaskName 'aw-coach' -ErrorAction SilentlyContinue; "
            "Get-CimInstance Win32_Process | "
            "Where-Object { $_.ProcessId -ne $currentPid "
            "-and $_.Name -in @('python.exe', 'pythonw.exe') "
            "-and $_.CommandLine -like '*-m aw_coach.daemon*' } | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
        )

    def _status_script(self) -> str:
        return (
            "$task = Get-ScheduledTask -TaskName 'aw-coach' -ErrorAction SilentlyContinue; "
            "if ($task) { $state = $task.State } "
            "elseif (Get-ItemProperty -Path "
            + self._quote_ps(self._run_key_path())
            + " -Name "
            + self._quote_ps(self._run_key_name())
            + " -ErrorAction SilentlyContinue) { $state = 'RunKey' } "
            "else { $state = 'NotInstalled' }; "
            "Write-Output $state; "
            "Get-CimInstance Win32_Process | "
            "Where-Object { $_.Name -in @('python.exe', 'pythonw.exe') "
            "-and $_.CommandLine -like '*-m aw_coach.daemon*' } | "
            "ForEach-Object { \"PID:$($_.ProcessId)\" }"
        )

    def _uninstall_script(self) -> str:
        return (
            "$task = Get-ScheduledTask -TaskName 'aw-coach' -ErrorAction SilentlyContinue; "
            "if ($task) { Unregister-ScheduledTask -TaskName 'aw-coach' -Confirm:$false }; "
            "Remove-ItemProperty -Path "
            + self._quote_ps(self._run_key_path())
            + " -Name "
            + self._quote_ps(self._run_key_name())
            + " -ErrorAction SilentlyContinue"
        )

    def _daemon_command(self) -> str:
        python_executable = self._quote_ps(self._python_executable())
        log_file = self._quote_ps(self.data_dir / "aw-coach-daemon.log")
        err_file = self._quote_ps(self.data_dir / "aw-coach-daemon.err.log")
        pythonpath = self._source_pythonpath()
        commands = []
        if pythonpath:
            commands.append(f"$env:PYTHONPATH = {self._quote_ps(pythonpath)}")
        commands.append(f"& {python_executable} -m aw_coach.daemon *> {log_file} 2> {err_file}")
        return "; ".join(commands)

    def _powershell_launch_command(self) -> str:
        return (
            "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command "
            + self._quote_ps_double(self._daemon_command())
        )

    def _run_key_path(self) -> str:
        return "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run"

    def _run_key_name(self) -> str:
        return "aw-coach"

    def _run_key_value(self) -> str:
        return self._powershell_launch_command()

    def _run_powershell(self, script: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        command = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ]
        result = self.runner(command, capture_output=True, text=True)
        if check and result.returncode != 0:
            self._raise_service_error(result)
        return result

    def _raise_service_error(self, result: subprocess.CompletedProcess[str]) -> None:
        msg = result.stderr or result.stdout or "PowerShell command failed"
        raise ServiceError(msg.strip())

    def _is_access_denied(self, result: subprocess.CompletedProcess[str]) -> bool:
        output = f"{result.stderr or ''}\n{result.stdout or ''}".lower()
        return "access is denied" in output or "0x80070005" in output

    def _quote_ps(self, value: Path | str) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    def _quote_ps_double(self, value: str) -> str:
        escaped = (
            str(value)
            .replace("`", "``")
            .replace('"', '`"')
            .replace("$", "`$")
        )
        return f'"{escaped}"'
