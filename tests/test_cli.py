"""Tests for CLI entry point."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import yaml
from click.testing import CliRunner

from aw_coach.cli import _configure_console_output, main
from aw_coach.collector import ActivitySlice, DataCollector
from aw_coach.config import Config, load_config
from aw_coach.storage import Storage


def test_version_flag():
    runner = CliRunner()
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


def test_no_subcommand_shows_help():
    runner = CliRunner()
    result = runner.invoke(main, [])
    assert result.exit_code == 0
    assert "aw-coach" in result.output
    assert "status" in result.output
    assert "service" in result.output
    assert "report" in result.output
    assert "doctor" in result.output
    assert "rule-test" in result.output


def test_configure_console_output_tolerates_gbk_stdout():
    stream = Mock()
    stream.encoding = "cp936"
    stream.errors = "strict"

    _configure_console_output(stream)

    stream.reconfigure.assert_called_once_with(encoding="utf-8", errors="replace")


def test_service_status_command_reports_installed(monkeypatch, tmp_path):
    from aw_coach.service_installer import ServiceStatus

    class FakeServiceManager:
        def __init__(self, project_root, data_dir):
            self.project_root = project_root
            self.data_dir = data_dir

        def status(self):
            return ServiceStatus(installed=True, state="Ready")

    monkeypatch.setattr("aw_coach.cli.ServiceManager", FakeServiceManager)
    monkeypatch.setattr(
        "aw_coach.cli.load_config",
        lambda: SimpleNamespace(data_dir=tmp_path, db_path=tmp_path / "coach.db"),
    )

    result = CliRunner().invoke(main, ["service", "status"])

    assert result.exit_code == 0
    assert "installed" in result.output.lower()
    assert "Ready" in result.output


def test_service_status_command_reports_daemon_details_and_heartbeat(monkeypatch, tmp_path):
    from aw_coach.service_installer import ServiceStatus

    fresh_tick = (datetime.now() - timedelta(seconds=60)).isoformat()
    log_root = tmp_path

    class FakeServiceManager:
        def __init__(self, project_root, data_dir):
            self.project_root = project_root
            self.data_dir = data_dir

        def status(self):
            return ServiceStatus(
                installed=True,
                state="Running",
                daemon_pids=(1234, 5678),
            )

    class FakeStorage:
        def __init__(self, *_):
            pass

        def get_scheduler_state(self, key):
            if key != "service_health":
                return None
            return json.dumps(
                {
                    "last_tick": fresh_tick,
                    "last_error": "temporary issue",
                }
            )

    monkeypatch.setattr("aw_coach.cli.ServiceManager", FakeServiceManager)
    monkeypatch.setattr("aw_coach.cli.Storage", lambda *_args, **_kwargs: FakeStorage())
    monkeypatch.setattr(
        "aw_coach.cli.load_config",
        lambda: Config(data_dir=log_root, db_path=tmp_path / "coach.db"),
    )

    result = CliRunner().invoke(main, ["service", "status"])

    assert result.exit_code == 0
    assert "installed" in result.output.lower()
    assert "running" in result.output.lower()
    assert "1234" in result.output
    assert "5678" in result.output
    assert "heartbeat" in result.output.lower()
    assert "fresh" in result.output.lower()
    assert "last tick" in result.output.lower()
    assert "aw-coach-daemon.log" in result.output
    assert "aw-coach-daemon.err.log" in result.output
    assert "temporary issue" in result.output


def test_service_status_command_handles_invalid_heartbeat_payload(monkeypatch, tmp_path):
    from aw_coach.service_installer import ServiceStatus

    class FakeServiceManager:
        def __init__(self, project_root, data_dir):
            self.project_root = project_root
            self.data_dir = data_dir

        def status(self):
            return ServiceStatus(installed=True, state="Running")

    class FakeStorage:
        def __init__(self, *_):
            pass

        def get_scheduler_state(self, key):
            return "not-json" if key == "service_health" else None

    monkeypatch.setattr("aw_coach.cli.ServiceManager", FakeServiceManager)
    monkeypatch.setattr("aw_coach.cli.Storage", lambda *_args, **_kwargs: FakeStorage())
    monkeypatch.setattr(
        "aw_coach.cli.load_config",
        lambda: Config(data_dir=tmp_path, db_path=tmp_path / "coach.db"),
    )

    result = CliRunner().invoke(main, ["service", "status"])

    assert result.exit_code == 0
    assert "not installed" not in result.output.lower()
    assert "heartbeat" in result.output.lower()
    assert "malformed payload" in result.output.lower()


def test_service_status_command_handles_non_object_heartbeat_payload(monkeypatch, tmp_path):
    from aw_coach.service_installer import ServiceStatus

    class FakeServiceManager:
        def __init__(self, project_root, data_dir):
            self.project_root = project_root
            self.data_dir = data_dir

        def status(self):
            return ServiceStatus(installed=True, state="Running")

    class FakeStorage:
        def __init__(self, *_):
            pass

        def get_scheduler_state(self, key):
            return "[]" if key == "service_health" else None

    monkeypatch.setattr("aw_coach.cli.ServiceManager", FakeServiceManager)
    monkeypatch.setattr("aw_coach.cli.Storage", lambda *_args, **_kwargs: FakeStorage())
    monkeypatch.setattr(
        "aw_coach.cli.load_config",
        lambda: SimpleNamespace(data_dir=tmp_path, db_path=tmp_path / "coach.db"),
    )

    result = CliRunner().invoke(main, ["service", "status"])

    assert result.exit_code == 0
    assert "heartbeat: malformed payload" in result.output.lower()


def test_service_status_command_with_unreadable_heartbeat_reports_unavailable(
    monkeypatch,
    tmp_path,
):
    from aw_coach.service_installer import ServiceStatus

    class FakeServiceManager:
        def __init__(self, project_root, data_dir):
            self.project_root = project_root
            self.data_dir = data_dir

        def status(self):
            return ServiceStatus(installed=True, state="Running")

    class FakeStorage:
        def __init__(self, *_):
            pass

        def get_scheduler_state(self, key):
            if key != "service_health":
                return None
            raise OSError("db locked")

    monkeypatch.setattr("aw_coach.cli.ServiceManager", FakeServiceManager)
    monkeypatch.setattr("aw_coach.cli.Storage", lambda *_args, **_kwargs: FakeStorage())
    monkeypatch.setattr(
        "aw_coach.cli.load_config",
        lambda: SimpleNamespace(data_dir=tmp_path, db_path=tmp_path / "coach.db"),
    )

    result = CliRunner().invoke(main, ["service", "status"])

    assert result.exit_code == 0
    assert "heartbeat: unavailable" in result.output.lower()
    assert "malformed payload" not in result.output.lower()


def test_service_logs_command_prints_log_tails(monkeypatch, tmp_path):
    (tmp_path / "aw-coach-daemon.log").write_text(
        "stdout-1\nstdout-2\nstdout-3\n",
        encoding="utf-8",
    )
    (tmp_path / "aw-coach-daemon.err.log").write_text(
        "stderr-1\nstderr-2\nstderr-3\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "aw_coach.cli.load_config",
        lambda: SimpleNamespace(data_dir=tmp_path, db_path=tmp_path / "coach.db"),
    )

    result = CliRunner().invoke(main, ["service", "logs", "--lines", "2"])

    assert result.exit_code == 0
    assert "aw-coach-daemon.log" in result.output
    assert "aw-coach-daemon.err.log" in result.output
    assert "stdout-2" in result.output
    assert "stdout-3" in result.output
    assert "stderr-2" in result.output
    assert "stderr-3" in result.output
    assert "stdout-1" not in result.output
    assert "stderr-1" not in result.output


def test_service_logs_command_reports_missing_files(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "aw_coach.cli.load_config",
        lambda: SimpleNamespace(data_dir=tmp_path, db_path=tmp_path / "coach.db"),
    )

    result = CliRunner().invoke(main, ["service", "logs", "--lines", "2"])

    assert result.exit_code == 0
    assert "missing log file" in result.output.lower()


def test_service_logs_command_reports_unreadable_files(monkeypatch, tmp_path):
    (tmp_path / "aw-coach-daemon.log").mkdir()
    (tmp_path / "aw-coach-daemon.err.log").write_text("stderr-ok\n", encoding="utf-8")
    monkeypatch.setattr(
        "aw_coach.cli.load_config",
        lambda: SimpleNamespace(data_dir=tmp_path, db_path=tmp_path / "coach.db"),
    )

    result = CliRunner().invoke(main, ["service", "logs", "--lines", "2"])

    assert result.exit_code == 0
    assert "unreadable log file" in result.output.lower()
    assert "stderr-ok" in result.output


def test_service_status_command_keeps_runtime_info_when_not_installed(monkeypatch, tmp_path):
    from aw_coach.service_installer import ServiceStatus

    fresh_tick = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()

    class FakeServiceManager:
        def __init__(self, project_root, data_dir):
            self.project_root = project_root
            self.data_dir = data_dir

        def status(self):
            return ServiceStatus(
                installed=False,
                state="NotInstalled",
                daemon_pids=(1234,),
            )

    class FakeStorage:
        def __init__(self, *_):
            pass

        def get_scheduler_state(self, key):
            if key != "service_health":
                return None
            return json.dumps({"last_tick": fresh_tick})

    monkeypatch.setattr("aw_coach.cli.ServiceManager", FakeServiceManager)
    monkeypatch.setattr("aw_coach.cli.Storage", lambda *_args, **_kwargs: FakeStorage())
    monkeypatch.setattr(
        "aw_coach.cli.load_config",
        lambda: Config(data_dir=tmp_path, db_path=tmp_path / "coach.db"),
    )

    result = CliRunner().invoke(main, ["service", "status"])

    assert result.exit_code == 0
    assert "not installed" in result.output.lower()
    assert "daemon pids: 1234" in result.output.lower()
    assert "heartbeat: fresh" in result.output.lower()
    assert "aw-coach-daemon.log" in result.output
    assert "aw-coach-daemon.err.log" in result.output


def test_service_status_command_handles_aware_heartbeat_timestamp(monkeypatch, tmp_path):
    from aw_coach.service_installer import ServiceStatus

    fresh_tick = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()

    class FakeServiceManager:
        def __init__(self, project_root, data_dir):
            self.project_root = project_root
            self.data_dir = data_dir

        def status(self):
            return ServiceStatus(installed=True, state="Running")

    class FakeStorage:
        def __init__(self, *_):
            pass

        def get_scheduler_state(self, key):
            if key != "service_health":
                return None
            return json.dumps({"last_tick": fresh_tick})

    monkeypatch.setattr("aw_coach.cli.ServiceManager", FakeServiceManager)
    monkeypatch.setattr("aw_coach.cli.Storage", lambda *_args, **_kwargs: FakeStorage())
    monkeypatch.setattr(
        "aw_coach.cli.load_config",
        lambda: Config(data_dir=tmp_path, db_path=tmp_path / "coach.db"),
    )

    result = CliRunner().invoke(main, ["service", "status"])

    assert result.exit_code == 0
    assert "heartbeat: fresh" in result.output.lower()
    assert "malformed payload" not in result.output.lower()


def test_doctor_reports_service_autostart_status(monkeypatch, tmp_path):
    from aw_coach.service_installer import ServiceStatus

    class FakeServiceManager:
        def __init__(self, project_root, data_dir):
            pass

        def status(self):
            return ServiceStatus(installed=True, state="Ready", daemon_pids=(101, 202))

    class FakeStorage:
        def __init__(self, *_args, **_kwargs):
            pass

        def get_scheduler_state(self, key):
            if key == "service_health":
                return json.dumps(
                    {
                        "last_tick": datetime.now(timezone.utc).isoformat(),
                        "status": "running",
                    }
                )
            return None

    class FakeRuleEngine:
        @classmethod
        def with_builtin_rules(cls):
            return cls(rules=())

        def __init__(self, rules):
            self.rules = rules

    class FakeCollector:
        hostname = "test-host"

        def __init__(self, *args, **kwargs):
            self.client = SimpleNamespace(get_buckets=lambda: {})

    def fake_build_corrections(storage, engine):
        return []

    monkeypatch.setattr("aw_coach.cli.ServiceManager", FakeServiceManager)
    monkeypatch.setattr("aw_coach.cli.Storage", lambda *_args, **_kwargs: FakeStorage())
    monkeypatch.setattr("aw_coach.rules.engine.RuleEngine", FakeRuleEngine)
    monkeypatch.setattr(
        "aw_coach.cli.load_config",
        lambda: Config(data_dir=tmp_path, db_path=tmp_path / "coach.db"),
    )
    monkeypatch.setattr("aw_coach.collector.DataCollector", FakeCollector)
    monkeypatch.setattr(
        "aw_coach.correction.build_pending_rule_suggestions",
        fake_build_corrections,
    )

    runner = CliRunner()
    result = runner.invoke(main, ["doctor"])

    assert result.exit_code == 0
    assert "service/autostart" in result.output.lower()
    assert "installed" in result.output.lower()
    assert "pids=101, 202" in result.output.lower()
    assert "heartbeat:" in result.output.lower()


def test_doctor_service_status_errors_do_not_fail(monkeypatch, tmp_path):
    from aw_coach.service_installer import ServiceError

    class FakeServiceManager:
        def __init__(self, project_root, data_dir):
            pass

        def status(self):
            raise ServiceError("service unavailable")

    class FakeCollector:
        hostname = "test-host"

        def __init__(self, *args, **kwargs):
            self.client = SimpleNamespace(get_buckets=lambda: {})

    class FakeRuleEngine:
        @classmethod
        def with_builtin_rules(cls):
            return cls(rules=())

        def __init__(self, rules):
            self.rules = rules

    def fake_build_corrections(storage, engine):
        return []

    monkeypatch.setattr("aw_coach.cli.ServiceManager", FakeServiceManager)
    monkeypatch.setattr(
        "aw_coach.cli.load_config",
        lambda: Config(data_dir=tmp_path, db_path=tmp_path / "coach.db"),
    )
    class FakeStorage:
        def get_correction_counts(self):
            return {}

    monkeypatch.setattr("aw_coach.rules.engine.RuleEngine", FakeRuleEngine)
    monkeypatch.setattr("aw_coach.cli.Storage", lambda *_args, **_kwargs: FakeStorage())
    monkeypatch.setattr("aw_coach.collector.DataCollector", FakeCollector)
    monkeypatch.setattr(
        "aw_coach.correction.build_pending_rule_suggestions",
        fake_build_corrections,
    )

    result = CliRunner().invoke(main, ["doctor"])

    assert result.exit_code == 0
    assert "service/autostart: unavailable" in result.output.lower()


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


def test_service_status_command_reports_not_installed(monkeypatch, tmp_path):
    from aw_coach.service_installer import ServiceStatus

    class FakeServiceManager:
        def __init__(self, project_root, data_dir):
            self.project_root = project_root
            self.data_dir = data_dir

        def status(self):
            return ServiceStatus(installed=False, state="NotInstalled")

    monkeypatch.setattr("aw_coach.cli.ServiceManager", FakeServiceManager)
    monkeypatch.setattr("aw_coach.cli.load_config", lambda: Config(data_dir=tmp_path))

    result = CliRunner().invoke(main, ["service", "status"])

    assert result.exit_code == 0
    assert "not installed" in result.output.lower()


def test_service_stop_command_calls_manager(monkeypatch, tmp_path):
    calls = []

    class FakeServiceManager:
        def __init__(self, project_root, data_dir):
            calls.append(("init", project_root, data_dir))

        def stop(self):
            calls.append(("stop",))

    monkeypatch.setattr("aw_coach.cli.ServiceManager", FakeServiceManager)
    monkeypatch.setattr("aw_coach.cli.load_config", lambda: Config(data_dir=tmp_path))

    result = CliRunner().invoke(main, ["service", "stop"])

    assert result.exit_code == 0
    assert calls[-1] == ("stop",)
    assert "stopped" in result.output.lower()


def test_service_uninstall_command_calls_manager(monkeypatch, tmp_path):
    calls = []

    class FakeServiceManager:
        def __init__(self, project_root, data_dir):
            calls.append(("init", project_root, data_dir))

        def uninstall(self):
            calls.append(("uninstall",))

    monkeypatch.setattr("aw_coach.cli.ServiceManager", FakeServiceManager)
    monkeypatch.setattr("aw_coach.cli.load_config", lambda: Config(data_dir=tmp_path))

    result = CliRunner().invoke(main, ["service", "uninstall"])

    assert result.exit_code == 0
    assert calls[-1] == ("uninstall",)
    assert "uninstalled" in result.output.lower()


def test_rule_test_command():
    runner = CliRunner()
    result = runner.invoke(main, ["rule-test", "--app", "Code", "--title", "main.py"])
    assert result.exit_code == 0
    assert "programming" in result.output
    assert "confidence" in result.output


def test_rule_test_unknown_app():
    runner = CliRunner()
    result = runner.invoke(main, ["rule-test", "--app", "random-xyz", "--title", "window"])
    assert result.exit_code == 0
    assert "unknown" in result.output


def test_rule_test_with_url():
    runner = CliRunner()
    result = runner.invoke(main, [
        "rule-test", "--app", "chrome",
        "--title", "YouTube", "--url", "https://youtube.com"
    ])
    assert result.exit_code == 0
    assert "entertainment" in result.output


def test_cost_command_rule_only():
    runner = CliRunner()
    result = runner.invoke(main, ["cost"])
    assert result.exit_code == 0
    assert "$0.00" in result.output or "rule_only" in result.output


def test_verbose_flag():
    runner = CliRunner()
    result = runner.invoke(main, ["-v", "cost"])
    assert result.exit_code == 0


def test_quiet_flag():
    runner = CliRunner()
    result = runner.invoke(main, ["-q", "cost"])
    assert result.exit_code == 0


def test_config_path_command(monkeypatch, tmp_path):
    config_path = tmp_path / "coach.toml"
    monkeypatch.setattr("aw_coach.cli.DEFAULT_CONFIG_PATH", config_path)

    runner = CliRunner()
    result = runner.invoke(main, ["config", "path"])

    assert result.exit_code == 0
    assert str(config_path) in result.output


def test_config_show_command():
    runner = CliRunner()
    with patch("aw_coach.cli.load_config", return_value=Config()):
        result = runner.invoke(main, ["config", "show"])

    assert result.exit_code == 0
    assert '"backend": "hybrid"' in result.output
    assert '"enabled": true' in result.output


def test_config_set_writes_values(monkeypatch, tmp_path):
    config_path = tmp_path / "coach.toml"
    monkeypatch.setattr("aw_coach.cli.DEFAULT_CONFIG_PATH", config_path)

    runner = CliRunner()
    result = runner.invoke(main, ["config", "set", "ai.backend", "hybrid"])
    assert result.exit_code == 0

    result = runner.invoke(main, ["config", "set", "cost.monthly_budget_usd", "12.5"])
    assert result.exit_code == 0

    config = load_config(config_path=Path(config_path))
    assert config.ai.backend == "hybrid"
    assert config.cost.monthly_budget_usd == 12.5


def test_purge_deletes_generated_data(tmp_path):
    data_dir = tmp_path / "data"
    reports_dir = data_dir / "reports"
    screenshots_dir = data_dir / "screenshots"
    db_path = data_dir / "aw-coach.db"
    reports_dir.mkdir(parents=True)
    screenshots_dir.mkdir()
    (reports_dir / "daily.md").write_text("report", encoding="utf-8")
    (screenshots_dir / "shot.png").write_bytes(b"png")
    db_path.write_text("db", encoding="utf-8")

    cfg = SimpleNamespace(data_dir=data_dir, reports_dir=reports_dir, db_path=db_path)

    with patch("aw_coach.cli.load_config", return_value=cfg):
        runner = CliRunner()
        result = runner.invoke(main, ["purge", "--yes"])

    assert result.exit_code == 0
    assert not reports_dir.exists()
    assert not screenshots_dir.exists()
    assert not db_path.exists()


def test_doctor_calibrate_runs_calibration_flow():
    t = datetime(2026, 5, 30, 9, 0)
    slices = [
        ActivitySlice(
            t,
            t + timedelta(minutes=30),
            1800,
            False,
            "mystery-app",
            "Window",
            None,
        ),
    ]

    def fake_init(self, **kw):
        self._hostname = "test-host"
        self.client = SimpleNamespace(get_buckets=lambda: {})

    with patch.object(DataCollector, "__init__", fake_init), \
         patch.object(DataCollector, "fetch_range", return_value=slices):
        runner = CliRunner()
        result = runner.invoke(main, ["doctor", "--calibrate"], input="s\n")

    assert result.exit_code == 0
    assert "aw-server" in result.output
    assert "mystery-app" in result.output


def test_correct_review_records_low_confidence_correction(tmp_path):
    today = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)
    slices = [
        ActivitySlice(
            start=today,
            end=today + timedelta(minutes=15),
            duration=900,
            is_afk=False,
            primary_app="chrome",
            primary_title="Some random page",
            web_url="https://example.com",
        ),
        ActivitySlice(
            start=today + timedelta(minutes=15),
            end=today + timedelta(minutes=30),
            duration=900,
            is_afk=False,
            primary_app="Code",
            primary_title="main.py",
        ),
    ]
    cfg = SimpleNamespace(db_path=tmp_path / "coach.db")

    with patch("aw_coach.cli.load_config", return_value=cfg), \
         patch.object(DataCollector, "__init__", lambda self, **kw: None), \
         patch.object(DataCollector, "fetch_range", return_value=slices):
        runner = CliRunner()
        result = runner.invoke(main, ["correct", "--review"], input="programming\n")

    assert result.exit_code == 0
    assert "low-confidence" in result.output

    conn = sqlite3.connect(cfg.db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM corrections").fetchall()
    assert len(rows) == 1
    assert rows[0]["app"] == "chrome"
    assert rows[0]["original_type"] == "research"
    assert rows[0]["corrected_type"] == "programming"


def test_rule_suggest_accept_writes_rule_with_source_stats(tmp_path):
    cfg = SimpleNamespace(db_path=tmp_path / "coach.db", data_dir=tmp_path)
    storage = Storage(cfg.db_path)
    for _ in range(3):
        storage.add_correction("2026-05-30T09:00", "myapp", "Main", "unknown", "programming")

    with patch("aw_coach.cli.load_config", return_value=cfg):
        runner = CliRunner()
        result = runner.invoke(main, ["rule-suggest"], input="a\n")

    assert result.exit_code == 0
    assert "Pending rule suggestions" in result.output

    user_rules = tmp_path / "rules" / "user.yml"
    data = yaml.safe_load(user_rules.read_text(encoding="utf-8"))
    rule = data["rules"][0]
    assert rule["match_apps"] == ["myapp"]
    assert rule["default_type"] == "programming"
    assert rule["source"]["correction_count"] == 3
    assert rule["source"]["latest_corrected_at"]


def test_rule_suggest_edit_writes_edited_rule(tmp_path):
    cfg = SimpleNamespace(db_path=tmp_path / "coach.db", data_dir=tmp_path)
    storage = Storage(cfg.db_path)
    for _ in range(3):
        storage.add_correction("2026-05-30T09:00", "rawapp", "Main", "unknown", "admin")

    with patch("aw_coach.cli.load_config", return_value=cfg):
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["rule-suggest"],
            input="e\nCustom App\nwriting\n0.88\n",
        )

    assert result.exit_code == 0
    user_rules = tmp_path / "rules" / "user.yml"
    data = yaml.safe_load(user_rules.read_text(encoding="utf-8"))
    rule = data["rules"][0]
    assert rule["match_apps"] == ["Custom App"]
    assert rule["default_type"] == "writing"
    assert rule["confidence"] == 0.88


def test_rule_suggest_reject_hides_pending_suggestion(tmp_path):
    cfg = SimpleNamespace(db_path=tmp_path / "coach.db", data_dir=tmp_path)
    storage = Storage(cfg.db_path)
    for _ in range(3):
        storage.add_correction("2026-05-30T09:00", "rejectapp", "Main", "unknown", "admin")

    with patch("aw_coach.cli.load_config", return_value=cfg):
        runner = CliRunner()
        result = runner.invoke(main, ["rule-suggest"], input="r\n")
        assert result.exit_code == 0

        result = runner.invoke(main, ["rule-suggest"])

    assert result.exit_code == 0
    assert "No pending rule suggestions" in result.output


def test_correct_last_records_real_activity(tmp_path):
    now = datetime.now()
    slices = [
        ActivitySlice(
            start=now - timedelta(minutes=20),
            end=now - timedelta(minutes=5),
            duration=900,
            is_afk=False,
            primary_app="Code",
            primary_title="main.py",
        )
    ]
    cfg = SimpleNamespace(db_path=tmp_path / "coach.db")

    with patch("aw_coach.cli.load_config", return_value=cfg), \
         patch.object(DataCollector, "__init__", lambda self, **kw: None), \
         patch.object(DataCollector, "fetch_range", return_value=slices):
        runner = CliRunner()
        result = runner.invoke(main, ["correct", "--last", "admin"])

    assert result.exit_code == 0
    assert "Code" in result.output

    conn = sqlite3.connect(cfg.db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM corrections").fetchone()
    assert row["app"] == "Code"
    assert row["title"] == "main.py"
    assert row["original_type"] == "programming"
    assert row["corrected_type"] == "admin"


def test_correct_time_records_real_activity_range(tmp_path):
    today = datetime.now().replace(hour=14, minute=0, second=0, microsecond=0)
    slices = [
        ActivitySlice(
            start=today,
            end=today + timedelta(minutes=20),
            duration=1200,
            is_afk=False,
            primary_app="Code",
            primary_title="main.py",
        ),
        ActivitySlice(
            start=today + timedelta(minutes=20),
            end=today + timedelta(minutes=30),
            duration=600,
            is_afk=True,
            primary_app="",
            primary_title="",
        ),
    ]
    cfg = SimpleNamespace(db_path=tmp_path / "coach.db")

    with patch("aw_coach.cli.load_config", return_value=cfg), \
         patch.object(DataCollector, "__init__", lambda self, **kw: None), \
         patch.object(DataCollector, "fetch_range", return_value=slices):
        runner = CliRunner()
        result = runner.invoke(main, ["correct", "--time", "14:00-15:00", "admin"])

    assert result.exit_code == 0
    assert "1 correction" in result.output

    conn = sqlite3.connect(cfg.db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM corrections").fetchall()
    assert len(rows) == 1
    assert rows[0]["app"] == "Code"
    assert rows[0]["original_type"] == "programming"
    assert rows[0]["corrected_type"] == "admin"
