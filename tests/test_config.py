"""Tests for configuration system."""

import os
import tempfile
from pathlib import Path

import pytest


def test_default_config_no_file():
    """Config loads with all defaults when no TOML file exists."""
    from aw_coach.config import Config, load_config

    config = load_config(config_path=Path("/nonexistent/path.toml"))
    assert isinstance(config, Config)
    assert config.ai.backend == "hybrid"
    assert config.cost.monthly_budget_usd == 2.86
    assert config.analysis.deep_work_threshold_minutes == 15
    assert config.report.daily_report_time == "21:00"
    assert config.report.delivery.instant_summary == "notify"
    assert config.report.delivery.daily_report == "notify"
    assert config.report.delivery.medium_signal == "inbox"
    assert config.report.llm_timeout_seconds == 90


def test_partial_config_merges_with_defaults():
    """A partial TOML only overrides specified keys, rest stays default."""
    from aw_coach.config import load_config

    toml_content = """
[ai]
backend = "hybrid"

[cost]
monthly_budget_usd = 10.0
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as f:
        f.write(toml_content)
        f.flush()
        config = load_config(config_path=Path(f.name))

    os.unlink(f.name)

    assert config.ai.backend == "hybrid"
    assert config.cost.monthly_budget_usd == 10.0
    # Defaults preserved
    assert config.analysis.deep_work_threshold_minutes == 15
    assert config.report.daily_report_time == "21:00"


def test_full_config_loads():
    """A complete TOML file loads without error."""
    from aw_coach.config import load_config

    toml_content = """
[analysis]
deep_work_threshold_minutes = 30
distraction_apps = ["youtube", "tiktok"]
social_apps = ["wechat"]
work_hours_start = "10:00"
work_hours_end = "19:00"
work_days = [1, 2, 3, 4, 5]

[report]
daily_report_time = "22:00"
instant_summary_interval_hours = 3
notification_method = "cli_only"
llm_timeout_seconds = 45

[report.delivery]
instant_summary = "both"
daily_report = "inbox"
medium_signal = "notify"

[ai]
backend = "openai"

[cost]
monthly_budget_usd = 20.0
alert_thresholds = [0.5, 0.9]
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as f:
        f.write(toml_content)
        f.flush()
        config = load_config(config_path=Path(f.name))

    os.unlink(f.name)

    assert config.analysis.deep_work_threshold_minutes == 30
    assert config.analysis.distraction_apps == ["youtube", "tiktok"]
    assert config.report.daily_report_time == "22:00"
    assert config.report.llm_timeout_seconds == 45
    assert config.report.delivery.instant_summary == "both"
    assert config.report.delivery.daily_report == "inbox"
    assert config.report.delivery.medium_signal == "notify"
    assert config.ai.backend == "openai"
    assert config.cost.monthly_budget_usd == 20.0


def test_invalid_backend_raises():
    """Invalid backend value should raise a validation error."""
    from aw_coach.config import load_config

    toml_content = """
[ai]
backend = "invalid_backend"
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as f:
        f.write(toml_content)
        f.flush()
        with pytest.raises(ValueError):
            load_config(config_path=Path(f.name))

    os.unlink(f.name)


def test_invalid_delivery_channel_raises():
    """Invalid delivery channel value should raise a validation error."""
    from aw_coach.config import load_config

    toml_content = """
[report.delivery]
instant_summary = "desktop"
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as f:
        f.write(toml_content)
        f.flush()
        with pytest.raises(ValueError):
            load_config(config_path=Path(f.name))

    os.unlink(f.name)


def test_config_env_var_expansion():
    """Environment variables in api_key should be expandable."""
    from aw_coach.config import load_config

    os.environ["TEST_AW_COACH_KEY"] = "sk-test-123"
    toml_content = """
[ai]
backend = "openai"

[ai.openai]
api_key = "${TEST_AW_COACH_KEY}"
model = "gpt-4o-mini"
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as f:
        f.write(toml_content)
        f.flush()
        config = load_config(config_path=Path(f.name))

    os.unlink(f.name)
    del os.environ["TEST_AW_COACH_KEY"]

    assert config.ai.openai.api_key == "sk-test-123"


def test_config_data_paths():
    """Config should provide correct data directory paths."""
    from aw_coach.config import Config

    config = Config()
    assert config.data_dir.name == "aw-watcher-ai-coach"
    assert config.reports_dir.name == "reports"
    assert config.db_path.name == "aw-coach.db"
