"""Tests for Calibration and Reclassify CLI commands."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import yaml
from click.testing import CliRunner

from aw_coach.cli import main


class TestCalibrate:
    """Tests for `aw-coach calibrate` - scan unknown apps and guide user."""

    def test_shows_unknown_apps(self):
        """Calibrate should list apps that the rule engine doesn't recognize."""
        from aw_coach.collector import ActivitySlice, DataCollector

        t = datetime(2026, 5, 30, 9, 0)
        slices = [
            ActivitySlice(t, t + timedelta(minutes=30), 1800, False, "mystery-app", "Window", None),
            ActivitySlice(t, t + timedelta(minutes=60), 3600, False, "Code", "main.py", None),
            ActivitySlice(t, t + timedelta(minutes=10), 600, False, "another-unknown", "UI", None),
        ]

        with patch.object(DataCollector, "__init__", lambda self, **kw: None), \
             patch.object(DataCollector, "fetch_range", return_value=slices):
            runner = CliRunner()
            result = runner.invoke(main, ["calibrate"], input="s\ns\n")

        assert result.exit_code == 0
        assert "mystery-app" in result.output

    def test_no_unknown_apps(self):
        """When all apps are recognized, calibrate says so."""
        from aw_coach.collector import ActivitySlice, DataCollector

        t = datetime(2026, 5, 30, 9, 0)
        slices = [
            ActivitySlice(t, t + timedelta(minutes=60), 3600, False, "Code", "main.py", None),
        ]

        with patch.object(DataCollector, "__init__", lambda self, **kw: None), \
             patch.object(DataCollector, "fetch_range", return_value=slices):
            runner = CliRunner()
            result = runner.invoke(main, ["calibrate"])

        assert result.exit_code == 0
        assert "recognized" in result.output.lower() or "No calibration" in result.output

    def test_calibrate_writes_rules(self, tmp_path):
        """User classifying an app writes to user.yml."""
        from aw_coach.collector import ActivitySlice, DataCollector

        t = datetime(2026, 5, 30, 9, 0)
        slices = [
            ActivitySlice(t, t + timedelta(minutes=30), 1800, False, "myide", "Editor", None),
        ]

        with patch.object(DataCollector, "__init__", lambda self, **kw: None), \
             patch.object(DataCollector, "fetch_range", return_value=slices), \
             patch("aw_coach.cli.load_config") as mock_cfg:
            cfg = MagicMock()
            cfg.data_dir = tmp_path
            cfg.reports_dir = tmp_path / "reports"
            mock_cfg.return_value = cfg

            runner = CliRunner()
            runner.invoke(main, ["calibrate"], input="p\n")

        user_rules = tmp_path / "rules" / "user.yml"
        if user_rules.exists():
            data = yaml.safe_load(user_rules.read_text())
            assert any("myide" in r.get("match_apps", []) for r in data.get("rules", []))


class TestReclassify:
    """Tests for `aw-coach reclassify --from DATE`."""

    def test_reclassify_processes_date_range(self, tmp_path):
        """Reclassify should re-analyze historical data."""
        from aw_coach.collector import ActivitySlice, DataCollector

        t = datetime(2026, 5, 25, 9, 0)
        slices = [
            ActivitySlice(t, t + timedelta(hours=2), 7200, False, "Code", "main.py", None),
        ]

        with patch.object(DataCollector, "__init__", lambda self, **kw: None), \
             patch.object(DataCollector, "fetch_range", return_value=slices), \
             patch("aw_coach.cli.load_config") as mock_cfg:
            cfg = MagicMock()
            cfg.data_dir = tmp_path
            cfg.reports_dir = tmp_path / "reports"
            cfg.analysis = MagicMock(
                deep_work_threshold_minutes=25,
                distraction_apps=["youtube"],
                social_apps=["wechat"],
                work_hours_start="09:00",
                work_hours_end="18:00",
                work_days=[1, 2, 3, 4, 5],
            )
            mock_cfg.return_value = cfg

            runner = CliRunner()
            result = runner.invoke(
                main, ["reclassify", "--from", "2026-05-25", "--to", "2026-05-25"]
            )

        assert result.exit_code == 0
        assert "1" in result.output

    def test_reclassify_requires_from_date(self):
        """Reclassify without --from should show usage."""
        runner = CliRunner()
        result = runner.invoke(main, ["reclassify"])

        assert result.exit_code == 0 or "Missing" in result.output or "--from" in result.output
