"""Configuration loading and validation."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List, Optional

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib  # type: ignore[no-redef]

from pydantic import BaseModel, field_validator

VALID_BACKENDS = ("rule_only", "openai", "hybrid")

DEFAULT_CONFIG_PATH = Path("~/.config/activitywatch/aw-watcher-ai-coach.toml").expanduser()
DEFAULT_DATA_DIR = Path("~/.local/share/activitywatch/aw-watcher-ai-coach").expanduser()


class AnalysisConfig(BaseModel):
    deep_work_threshold_minutes: int = 25
    distraction_apps: List[str] = [
        "youtube", "bilibili", "twitter", "reddit", "tiktok"
    ]
    social_apps: List[str] = ["wechat", "qq", "slack", "telegram", "discord"]
    work_hours_start: str = "09:00"
    work_hours_end: str = "18:00"
    work_days: List[int] = [1, 2, 3, 4, 5]
    restrict_to_work_schedule: bool = False


class ReportConfig(BaseModel):
    daily_report_time: str = "21:00"
    instant_summary_interval_hours: int = 1
    notification_method: str = "both"


class OpenAIConfig(BaseModel):
    """OpenAI-compatible API config. Also works for DeepSeek:
    base_url = "https://api.deepseek.com", model = "deepseek-v4-flash"
    (deepseek-chat / deepseek-reasoner are deprecated on 2026-07-24)
    """

    api_key: str = ""
    model: str = "gpt-4o-mini"
    base_url: str = "https://api.openai.com/v1"


class LocalLLMConfig(BaseModel):
    provider: str = "ollama"
    base_url: str = "http://localhost:11434"
    model: str = "llama3"


class AIConfig(BaseModel):
    backend: str = "rule_only"
    openai: OpenAIConfig = OpenAIConfig()
    local: LocalLLMConfig = LocalLLMConfig()

    @field_validator("backend")
    @classmethod
    def validate_backend(cls, v: str) -> str:
        if v not in VALID_BACKENDS:
            raise ValueError(f"Invalid backend '{v}'. Must be one of: {VALID_BACKENDS}")
        return v


class CostConfig(BaseModel):
    monthly_budget_usd: float = 5.0
    alert_thresholds: List[float] = [0.5, 0.8, 1.0]


class ScreenshotConfig(BaseModel):
    enabled: bool = False
    retention_hours: int = 0
    blocklist_apps: List[str] = ["1password", "keepass", "bank", "password"]


class Config(BaseModel):
    analysis: AnalysisConfig = AnalysisConfig()
    report: ReportConfig = ReportConfig()
    ai: AIConfig = AIConfig()
    cost: CostConfig = CostConfig()
    screenshot: ScreenshotConfig = ScreenshotConfig()

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
