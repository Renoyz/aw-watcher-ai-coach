"""Configuration loading and validation."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib  # type: ignore[no-redef]

from pydantic import BaseModel, field_validator

VALID_BACKENDS = ("rule_only", "openai", "hybrid")
VALID_DELIVERY_CHANNELS = ("notify", "inbox", "both", "off")
VALID_COMMAND_ARGS_MODES = ("off", "summary", "full")

DEFAULT_CONFIG_PATH = Path("~/.config/activitywatch/aw-watcher-ai-coach.toml").expanduser()
DEFAULT_DATA_DIR = Path("~/.local/share/activitywatch/aw-watcher-ai-coach").expanduser()


class AnalysisConfig(BaseModel):
    deep_work_threshold_minutes: int = 15
    distraction_apps: List[str] = [
        "youtube", "bilibili", "twitter", "reddit", "tiktok"
    ]
    social_apps: List[str] = ["wechat", "qq", "slack", "telegram", "discord"]
    work_hours_start: str = "09:00"
    work_hours_end: str = "18:00"
    work_days: List[int] = [1, 2, 3, 4, 5]
    restrict_to_work_schedule: bool = False


class PolicyConfig(BaseModel):
    quiet_hours_enabled: bool = True
    quiet_hours_start: str = "22:00"
    quiet_hours_end: str = "08:00"


class DeliveryConfig(BaseModel):
    instant_summary: str = "notify"
    daily_report: str = "notify"
    morning_brief: str = "inbox"
    high_severity_signal: str = "notify"
    medium_signal: str = "inbox"
    task_confirm: str = "inbox"
    task_confirm_min_minutes: int = 10
    task_confirm_daily_limit: int = 3

    @field_validator(
        "instant_summary",
        "daily_report",
        "morning_brief",
        "high_severity_signal",
        "medium_signal",
        "task_confirm",
    )
    @classmethod
    def validate_channel(cls, v: str) -> str:
        if v not in VALID_DELIVERY_CHANNELS:
            raise ValueError(
                f"Invalid delivery channel '{v}'. Must be one of: "
                f"{VALID_DELIVERY_CHANNELS}"
            )
        return v


class ReportConfig(BaseModel):
    daily_report_time: str = "21:00"
    morning_brief_time: str = "09:00"
    instant_summary_interval_hours: int = 2
    notification_method: str = "both"
    daily_notification_budget: int = 4
    notification_budget_exempt_kinds: List[str] = [
        "summary",
        "daily_report",
        "morning_brief",
    ]
    notification_cooldown_seconds: int = 600
    hourly_backfill_hours: int = 168
    llm_timeout_seconds: int = 90
    background_ai_summary: bool = False
    silent_if_effective_hours_below: float = 0.5
    always_notify_signals: List[str] = ["stuck", "search_loop", "death_loop"]
    delivery: DeliveryConfig = DeliveryConfig()


class TasksConfig(BaseModel):
    enabled: bool = True
    project_roots: List[str] = []
    aliases: Dict[str, str] = {}
    branch_patterns: List[str] = ["feat/*", "fix/*", "issue-*"]
    user_task_label: str = ""
    user_task_id: str = ""


class ContextCaptureConfig(BaseModel):
    enabled: bool = True
    interval_seconds: int = 60
    command_args_mode: str = "summary"
    capture_cwd: bool = True
    capture_git: bool = True

    @field_validator("command_args_mode")
    @classmethod
    def validate_command_args_mode(cls, v: str) -> str:
        if v not in VALID_COMMAND_ARGS_MODES:
            raise ValueError(
                f"Invalid command_args_mode '{v}'. Must be one of: "
                f"{VALID_COMMAND_ARGS_MODES}"
            )
        return v


class CronJobConfig(BaseModel):
    schedule: str = "every 4h"
    template: str = "work_progress"
    delivery: str = "inbox"


class OpenAIConfig(BaseModel):
    """OpenAI-compatible API config. Also works for DeepSeek:
    base_url = "https://api.deepseek.com/v1", model = "deepseek-v4-flash"
    (deepseek-chat / deepseek-reasoner are deprecated on 2026-07-24)
    """

    api_key: str = ""
    model: str = "deepseek-v4-flash"
    base_url: str = "https://api.deepseek.com/v1"


class LocalLLMConfig(BaseModel):
    provider: str = "ollama"
    base_url: str = "http://localhost:11434"
    model: str = "llama3"


class AIConfig(BaseModel):
    backend: str = "hybrid"
    openai: OpenAIConfig = OpenAIConfig()
    local: LocalLLMConfig = LocalLLMConfig()

    @field_validator("backend")
    @classmethod
    def validate_backend(cls, v: str) -> str:
        if v not in VALID_BACKENDS:
            raise ValueError(f"Invalid backend '{v}'. Must be one of: {VALID_BACKENDS}")
        return v


class CostConfig(BaseModel):
    monthly_budget_usd: float = 2.86  # ~¥20/month at 2026-06 exchange rate
    alert_thresholds: List[float] = [0.5, 0.8, 1.0]


class ScreenshotConfig(BaseModel):
    enabled: bool = False
    retention_hours: int = 0
    blocklist_apps: List[str] = ["1password", "keepass", "bank", "password"]


class Config(BaseModel):
    analysis: AnalysisConfig = AnalysisConfig()
    report: ReportConfig = ReportConfig()
    policy: PolicyConfig = PolicyConfig()
    tasks: TasksConfig = TasksConfig()
    cron_jobs: List["CronJobConfig"] = []
    ai: AIConfig = AIConfig()
    cost: CostConfig = CostConfig()
    screenshot: ScreenshotConfig = ScreenshotConfig()
    context_capture: ContextCaptureConfig = ContextCaptureConfig()

    @property
    def data_dir(self) -> Path:
        return DEFAULT_DATA_DIR

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "aw-coach.db"


def _expand_env_vars(value: str) -> str:
    if value.startswith("${") and value.endswith("}"):
        env_name = value[2:-1]
        return os.environ.get(env_name, value)
    return value


def _expand_env_in_dict(d: dict) -> dict:
    result = {}
    for k, v in d.items():
        if isinstance(v, str):
            result[k] = _expand_env_vars(v)
        elif isinstance(v, dict):
            result[k] = _expand_env_in_dict(v)
        else:
            result[k] = v
    return result


def load_config(config_path: Optional[Path] = None) -> Config:
    path = config_path or DEFAULT_CONFIG_PATH

    if not path.exists():
        return Config()

    with open(path, "rb") as f:
        raw = tomllib.load(f)

    raw = _expand_env_in_dict(raw)
    return Config(**raw)
