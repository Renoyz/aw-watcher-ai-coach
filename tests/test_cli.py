"""Tests for CLI entry point."""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml
from click.testing import CliRunner

from aw_coach.cli import main
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
    assert "report" in result.output
    assert "doctor" in result.output
    assert "rule-test" in result.output


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
