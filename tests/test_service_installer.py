from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from aw_coach.service_installer import (
    ServiceError,
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

    monkeypatch.setattr(
        "aw_coach.service_installer.platform.system", lambda: "Windows"
    )
    monkeypatch.setattr(
        "aw_coach.service_installer.sys.executable", str(tmp_path / "python.exe")
    )
    monkeypatch.setattr(
        "aw_coach.service_installer.shutil.which",
        lambda name: str(tmp_path / "pythonw.exe") if name == "pythonw.exe" else None,
    )

    manager = ServiceManager(project_root=tmp_path, data_dir=logs_dir, runner=runner)
    manager.install()

    command = runner.call_args.args[0]
    script = command[-1]
    assert command[:2] == ["powershell.exe", "-NoProfile"]
    assert "Register-ScheduledTask" in script
    assert "-AllowStartIfOnBatteries" in script
    assert "-DontStopIfGoingOnBatteries" in script
    assert "-RestartCount 3" in script
    assert "-RestartInterval (New-TimeSpan -Minutes 1)" in script
    assert "New-ScheduledTaskPrincipal -UserId $env:USERNAME" in script
    assert "-Principal $principal" in script
    assert str(tmp_path / "pythonw.exe") in script
    assert "-m aw_coach.daemon" in script
    assert "$env:PYTHONPATH" in script
    assert f"'{src_dir}'" in script
    assert str(logs_dir / "aw-coach-daemon.log") in script
    assert str(logs_dir / "aw-coach-daemon.err.log") in script


def test_windows_install_uses_safe_quoting_for_spaces_and_apostrophes(tmp_path, monkeypatch):
    project_root = tmp_path / "repo's dir" / "my app"
    src_dir = project_root / "src"
    package_dir = src_dir / "aw_coach"
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    logs_dir = project_root / "data dir's"
    runner = Mock(return_value=SimpleNamespace(returncode=0, stdout="", stderr=""))
    python_exe = project_root / "py th'on.exe"

    monkeypatch.setattr("aw_coach.service_installer.platform.system", lambda: "Windows")
    monkeypatch.setattr(
        "aw_coach.service_installer.sys.executable",
        str(project_root / "python.exe"),
    )
    monkeypatch.setattr(
        "aw_coach.service_installer.shutil.which",
        lambda name: str(python_exe) if name == "pythonw.exe" else None,
    )

    manager = ServiceManager(project_root=project_root, data_dir=logs_dir, runner=runner)
    manager.install()

    script = runner.call_args.args[0][-1]
    expected_pythonpath = str(src_dir).replace("'", "''")
    assert '-Argument "' in script
    assert "-Argument '-NoProfile" not in script
    assert "-WindowStyle Hidden" in script
    assert f"`$env:PYTHONPATH = '{expected_pythonpath}'" in script
    assert "-Command " in script
    assert str(python_exe).replace("'", "''") in script


def test_uninstall_uses_unregister_task_command(tmp_path, monkeypatch):
    runner = Mock(return_value=SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr("aw_coach.service_installer.platform.system", lambda: "Windows")

    ServiceManager(project_root=tmp_path, data_dir=tmp_path, runner=runner).uninstall()

    script = runner.call_args.args[0][-1]
    assert "Unregister-ScheduledTask" in script


def test_start_uses_start_task_command(tmp_path, monkeypatch):
    runner = Mock(return_value=SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr("aw_coach.service_installer.platform.system", lambda: "Windows")

    ServiceManager(project_root=tmp_path, data_dir=tmp_path, runner=runner).start()

    script = runner.call_args.args[0][-1]
    assert "Start-ScheduledTask -TaskName 'aw-coach'" in script
    assert "if ($task)" in script


def test_stop_uses_stop_task_command(tmp_path, monkeypatch):
    runner = Mock(return_value=SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr("aw_coach.service_installer.platform.system", lambda: "Windows")

    ServiceManager(project_root=tmp_path, data_dir=tmp_path, runner=runner).stop()

    script = runner.call_args.args[0][-1]
    assert "Stop-ScheduledTask -TaskName 'aw-coach' -ErrorAction SilentlyContinue" in script


def test_non_windows_install_raises_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr("aw_coach.service_installer.platform.system", lambda: "Linux")
    manager = ServiceManager(project_root=tmp_path, data_dir=tmp_path)

    with pytest.raises(ServiceUnsupportedError, match="Windows Task Scheduler"):
        manager.install()


@pytest.mark.parametrize("method_name", ["install", "uninstall", "start", "stop", "status"])
def test_non_windows_methods_raise_clear_error(tmp_path, monkeypatch, method_name):
    monkeypatch.setattr("aw_coach.service_installer.platform.system", lambda: "Linux")
    manager = ServiceManager(project_root=tmp_path, data_dir=tmp_path)
    method = getattr(manager, method_name)

    with pytest.raises(ServiceUnsupportedError, match="Windows Task Scheduler"):
        method()


def test_status_parses_existing_task(tmp_path, monkeypatch):
    runner = Mock(return_value=SimpleNamespace(returncode=0, stdout="Ready\r\n", stderr=""))
    monkeypatch.setattr("aw_coach.service_installer.platform.system", lambda: "Windows")

    status = ServiceManager(project_root=tmp_path, data_dir=tmp_path, runner=runner).status()

    assert status == ServiceStatus(installed=True, state="Ready")
    assert "Get-ScheduledTask" in runner.call_args.args[0][-1]


def test_status_parses_run_key_state_and_daemon_pids(tmp_path, monkeypatch):
    runner = Mock(
        return_value=SimpleNamespace(
            returncode=0,
            stdout="RunKey\r\nPID:1234\r\nPID:5678\r\n",
            stderr="",
        )
    )
    monkeypatch.setattr("aw_coach.service_installer.platform.system", lambda: "Windows")

    status = ServiceManager(project_root=tmp_path, data_dir=tmp_path, runner=runner).status()

    assert status == ServiceStatus(
        installed=True,
        state="RunKey",
        daemon_pids=(1234, 5678),
    )


def test_status_parses_known_state_and_ignores_noise_lines(tmp_path, monkeypatch):
    runner = Mock(
        return_value=SimpleNamespace(
            returncode=0,
            stdout="note: checking services\r\nReady\r\nPID:1234\r\njunk output\r\n",
            stderr="",
        )
    )
    monkeypatch.setattr("aw_coach.service_installer.platform.system", lambda: "Windows")

    status = ServiceManager(project_root=tmp_path, data_dir=tmp_path, runner=runner).status()

    assert status == ServiceStatus(
        installed=True,
        state="Ready",
        daemon_pids=(1234,),
    )


def test_status_raises_service_error_on_runner_failure(tmp_path, monkeypatch):
    runner = Mock(
        return_value=SimpleNamespace(returncode=1, stdout="", stderr="boom")
    )
    monkeypatch.setattr("aw_coach.service_installer.platform.system", lambda: "Windows")
    manager = ServiceManager(project_root=tmp_path, data_dir=tmp_path, runner=runner)

    with pytest.raises(ServiceError, match="boom"):
        manager.status()


@pytest.mark.parametrize("method_name", ["install", "start", "stop", "uninstall"])
def test_windows_methods_raise_service_error_on_runner_failure(tmp_path, monkeypatch, method_name):
    runner = Mock(return_value=SimpleNamespace(returncode=1, stdout="", stderr="failed"))
    monkeypatch.setattr("aw_coach.service_installer.platform.system", lambda: "Windows")
    manager = ServiceManager(project_root=tmp_path, data_dir=tmp_path, runner=runner)
    method = getattr(manager, method_name)

    with pytest.raises(ServiceError, match="failed"):
        method()


def test_install_falls_back_to_hkcu_run_on_scheduler_access_denied(tmp_path, monkeypatch):
    src_dir = tmp_path / "src"
    package_dir = src_dir / "aw_coach"
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    logs_dir = tmp_path / "data"
    monkeypatch.setattr("aw_coach.service_installer.platform.system", lambda: "Windows")
    monkeypatch.setattr("aw_coach.service_installer.sys.executable", str(tmp_path / "python.exe"))
    monkeypatch.setattr(
        "aw_coach.service_installer.shutil.which",
        lambda name: str(tmp_path / "pythonw.exe") if name == "pythonw.exe" else None,
    )

    runner = Mock(
        side_effect=[
            SimpleNamespace(returncode=1, stdout="", stderr="Access is denied."),
            SimpleNamespace(returncode=0, stdout="", stderr=""),
        ]
    )

    ServiceManager(project_root=tmp_path, data_dir=logs_dir, runner=runner).install()

    assert runner.call_count == 2
    first_script = runner.call_args_list[0].args[0][-1]
    second_script = runner.call_args_list[1].args[0][-1]
    assert "Register-ScheduledTask" in first_script
    assert "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run" in second_script
    assert "New-ItemProperty" in second_script
    assert "aw-coach" in second_script


def test_status_reports_run_key_fallback(tmp_path, monkeypatch):
    runner = Mock(return_value=SimpleNamespace(returncode=0, stdout="RunKey\r\n", stderr=""))
    monkeypatch.setattr("aw_coach.service_installer.platform.system", lambda: "Windows")

    status = ServiceManager(project_root=tmp_path, data_dir=tmp_path, runner=runner).status()

    assert status == ServiceStatus(installed=True, state="RunKey")


def test_uninstall_removes_hkcu_run_entry(tmp_path, monkeypatch):
    runner = Mock(return_value=SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr("aw_coach.service_installer.platform.system", lambda: "Windows")

    ServiceManager(project_root=tmp_path, data_dir=tmp_path, runner=runner).uninstall()

    script = runner.call_args.args[0][-1]
    assert "Unregister-ScheduledTask" in script
    assert "Remove-ItemProperty" in script
    assert "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run" in script
    assert "aw-coach" in script


def test_start_can_launch_run_key_fallback(tmp_path, monkeypatch):
    runner = Mock(return_value=SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr("aw_coach.service_installer.platform.system", lambda: "Windows")

    ServiceManager(project_root=tmp_path, data_dir=tmp_path, runner=runner).start()

    script = runner.call_args.args[0][-1]
    assert "Start-ScheduledTask -TaskName 'aw-coach'" in script
    assert "Start-Process" in script
    assert "aw_coach.daemon" in script


def test_stop_terminates_daemon_processes_for_run_key_fallback(tmp_path, monkeypatch):
    runner = Mock(return_value=SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr("aw_coach.service_installer.platform.system", lambda: "Windows")

    ServiceManager(project_root=tmp_path, data_dir=tmp_path, runner=runner).stop()

    script = runner.call_args.args[0][-1]
    assert "Stop-ScheduledTask -TaskName 'aw-coach' -ErrorAction SilentlyContinue" in script
    assert "$currentPid = $PID" in script
    assert "-m aw_coach.daemon" in script
    assert "Get-CimInstance Win32_Process" in script
    assert "Where-Object" in script
    assert "ProcessId -ne $currentPid" in script
    assert "python.exe" in script
    assert "pythonw.exe" in script
    assert "-in @('python.exe', 'pythonw.exe')" in script


def test_status_script_includes_daemon_process_query(tmp_path, monkeypatch):
    runner = Mock(return_value=SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr("aw_coach.service_installer.platform.system", lambda: "Windows")

    ServiceManager(project_root=tmp_path, data_dir=tmp_path, runner=runner).status()

    script = runner.call_args.args[0][-1]
    assert "Get-CimInstance Win32_Process" in script
    assert "PID:" in script
    assert "python.exe" in script
    assert "pythonw.exe" in script
    assert "-m aw_coach.daemon" in script
