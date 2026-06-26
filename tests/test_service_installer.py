from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from click.testing import CliRunner

from aw_coach.cli import main
from aw_coach.service_installer import (
    ServiceError,
    ServiceManager,
    ServiceStatus,
    ServiceUnsupportedError,
)


def _completed(returncode: int = 0, stdout: str = "", stderr: str = ""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_windows_install_uses_pythonw_module_and_source_pythonpath(tmp_path, monkeypatch):
    src_dir = tmp_path / "src"
    package_dir = src_dir / "aw_coach"
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    logs_dir = tmp_path / "data"
    runner = Mock(return_value=_completed())

    monkeypatch.setattr("aw_coach.service_installer.platform.system", lambda: "Windows")
    monkeypatch.setattr("aw_coach.service_installer.sys.executable", str(tmp_path / "python.exe"))
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
    assert '-Argument "-NoProfile' in script
    assert '-Argument "powershell.exe' not in script
    assert "-RestartCount 3" in script
    assert "New-ScheduledTaskPrincipal -UserId $env:USERNAME" in script
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
    runner = Mock(return_value=_completed())
    python_exe = project_root / "py th'on.exe"

    monkeypatch.setattr("aw_coach.service_installer.platform.system", lambda: "Windows")
    monkeypatch.setattr("aw_coach.service_installer.sys.executable", str(python_exe))
    monkeypatch.setattr(
        "aw_coach.service_installer.shutil.which",
        lambda name: str(python_exe) if name == "pythonw.exe" else None,
    )

    manager = ServiceManager(project_root=project_root, data_dir=logs_dir, runner=runner)
    manager.install()

    script = runner.call_args.args[0][-1]
    expected_pythonpath = str(src_dir).replace("'", "''")
    assert '-Argument "' in script
    assert "-WindowStyle Hidden" in script
    assert f"`$env:PYTHONPATH = '{expected_pythonpath}'" in script
    assert str(python_exe).replace("'", "''") in script


@pytest.mark.parametrize("method_name", ["install", "uninstall", "start", "stop", "status"])
def test_non_windows_methods_raise_clear_error(tmp_path, monkeypatch, method_name):
    monkeypatch.setattr("aw_coach.service_installer.platform.system", lambda: "Linux")
    manager = ServiceManager(project_root=tmp_path, data_dir=tmp_path)
    method = getattr(manager, method_name)

    with pytest.raises(ServiceUnsupportedError, match="Windows Task Scheduler"):
        method()


def test_status_parses_run_key_state_and_daemon_pids(tmp_path, monkeypatch):
    runner = Mock(return_value=_completed(stdout="RunKey\r\nPID:1234\r\nPID:5678\r\n"))
    monkeypatch.setattr("aw_coach.service_installer.platform.system", lambda: "Windows")

    status = ServiceManager(project_root=tmp_path, data_dir=tmp_path, runner=runner).status()

    assert status == ServiceStatus(
        installed=True,
        state="RunKey",
        daemon_pids=(1234, 5678),
    )


def test_status_parses_known_state_and_ignores_noise_lines(tmp_path, monkeypatch):
    runner = Mock(
        return_value=_completed(
            stdout="note: checking services\r\nReady\r\nPID:1234\r\njunk output\r\n"
        )
    )
    monkeypatch.setattr("aw_coach.service_installer.platform.system", lambda: "Windows")

    status = ServiceManager(project_root=tmp_path, data_dir=tmp_path, runner=runner).status()

    assert status == ServiceStatus(installed=True, state="Ready", daemon_pids=(1234,))


def test_status_raises_service_error_on_runner_failure(tmp_path, monkeypatch):
    runner = Mock(return_value=_completed(returncode=1, stderr="boom"))
    monkeypatch.setattr("aw_coach.service_installer.platform.system", lambda: "Windows")
    manager = ServiceManager(project_root=tmp_path, data_dir=tmp_path, runner=runner)

    with pytest.raises(ServiceError, match="boom"):
        manager.status()


def test_install_falls_back_to_hkcu_run_on_scheduler_access_denied(tmp_path, monkeypatch):
    logs_dir = tmp_path / "data"
    monkeypatch.setattr("aw_coach.service_installer.platform.system", lambda: "Windows")
    monkeypatch.setattr("aw_coach.service_installer.sys.executable", str(tmp_path / "python.exe"))
    monkeypatch.setattr(
        "aw_coach.service_installer.shutil.which",
        lambda name: str(tmp_path / "pythonw.exe") if name == "pythonw.exe" else None,
    )

    runner = Mock(
        side_effect=[
            _completed(returncode=1, stderr="Access is denied."),
            _completed(),
        ]
    )

    ServiceManager(project_root=tmp_path, data_dir=logs_dir, runner=runner).install()

    assert runner.call_count == 2
    first_script = runner.call_args_list[0].args[0][-1]
    second_script = runner.call_args_list[1].args[0][-1]
    assert "Register-ScheduledTask" in first_script
    assert "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run" in second_script
    assert "New-ItemProperty" in second_script
    assert "powershell.exe -NoProfile" in second_script


def test_start_and_stop_cover_run_key_daemon_processes(tmp_path, monkeypatch):
    runner = Mock(return_value=_completed())
    monkeypatch.setattr("aw_coach.service_installer.platform.system", lambda: "Windows")
    manager = ServiceManager(project_root=tmp_path, data_dir=tmp_path, runner=runner)

    manager.start()
    start_script = runner.call_args.args[0][-1]
    assert "Start-ScheduledTask -TaskName 'aw-coach'" in start_script
    assert "Start-Process" in start_script
    assert "-ArgumentList '-NoProfile" in start_script
    assert "aw_coach.daemon" in start_script

    manager.stop()
    stop_script = runner.call_args.args[0][-1]
    assert "Stop-ScheduledTask -TaskName 'aw-coach'" in stop_script
    assert "Get-CimInstance Win32_Process" in stop_script
    assert "-m aw_coach.daemon" in stop_script


def test_service_status_cli_reports_non_windows_error(tmp_path, monkeypatch):
    cfg = SimpleNamespace(data_dir=tmp_path)
    monkeypatch.setattr("aw_coach.service_installer.platform.system", lambda: "Linux")

    with patch("aw_coach.cli.load_config", return_value=cfg):
        result = CliRunner().invoke(main, ["service", "status"])

    assert result.exit_code != 0
    assert "Windows Task Scheduler" in result.output


def test_service_logs_cli_tails_stdout_and_stderr(tmp_path):
    (tmp_path / "aw-coach-daemon.log").write_text("one\ntwo\n", encoding="utf-8")
    (tmp_path / "aw-coach-daemon.err.log").write_text("err-one\nerr-two\n", encoding="utf-8")
    cfg = SimpleNamespace(data_dir=tmp_path)

    with patch("aw_coach.cli.load_config", return_value=cfg):
        result = CliRunner().invoke(main, ["service", "logs", "--lines", "1"])

    assert result.exit_code == 0
    assert "two" in result.output
    assert "one" not in result.output
    assert "err-two" in result.output
    assert "err-one" not in result.output
