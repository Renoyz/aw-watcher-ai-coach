"""Correction feedback helpers for turning user fixes into rules."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

import yaml

from aw_coach.rules.engine import DEFAULT_WEIGHTS, RuleEngine
from aw_coach.storage import Storage

VALID_ACTIVITY_TYPES = (
    "programming",
    "writing",
    "meeting",
    "research",
    "design",
    "entertainment",
    "admin",
    "social",
)


@dataclass
class RuleSuggestion:
    app: str
    corrected_type: str
    correction_count: int
    latest_corrected_at: str
    confidence: float
    original_types: List[str]
    status: str = "pending"

    @property
    def rule_name(self) -> str:
        return f"user_{_slugify(self.app)}"

    def to_rule(self) -> dict:
        return {
            "name": self.rule_name,
            "match_apps": [self.app],
            "default_type": self.corrected_type,
            "confidence": round(self.confidence, 2),
            "weight": DEFAULT_WEIGHTS.get(self.corrected_type, 0.0),
            "source": {
                "type": "correction_history",
                "correction_count": self.correction_count,
                "latest_corrected_at": self.latest_corrected_at,
                "original_types": self.original_types,
            },
        }


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    return slug or "rule"


def _split_original_types(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return sorted({item for item in raw.split(",") if item})


def build_pending_rule_suggestions(
    storage: Storage,
    engine: RuleEngine,
    min_count: int = 3,
) -> List[RuleSuggestion]:
    decisions = storage.get_rule_suggestion_decisions()
    suggestions: List[RuleSuggestion] = []

    for row in storage.get_rule_suggestion_stats(min_count=min_count):
        app = row["app"]
        corrected_type = row["corrected_type"]
        status = decisions.get((app.lower(), corrected_type), "pending")
        if status in {"accepted", "rejected"}:
            continue
        if engine.classify(app, "", None).confidence >= 0.85:
            continue

        count = int(row["correction_count"])
        confidence = min(0.70 + count * 0.05, 0.95)
        suggestions.append(
            RuleSuggestion(
                app=app,
                corrected_type=corrected_type,
                correction_count=count,
                latest_corrected_at=row["latest_corrected_at"],
                confidence=confidence,
                original_types=_split_original_types(row.get("original_types")),
                status=status,
            )
        )

    return suggestions


def append_user_rule(rules_path: Path, suggestion: RuleSuggestion) -> None:
    rules_path.parent.mkdir(parents=True, exist_ok=True)

    existing_rules = []
    if rules_path.exists():
        data = yaml.safe_load(rules_path.read_text(encoding="utf-8")) or {}
        existing_rules = data.get("rules", [])

    existing_rules = [
        rule
        for rule in existing_rules
        if rule.get("name") != suggestion.rule_name
        and suggestion.app not in rule.get("match_apps", [])
    ]
    existing_rules.append(suggestion.to_rule())

    rules_path.write_text(
        yaml.dump({"rules": existing_rules}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def resolve_type(value: str, valid_types: Iterable[str] = VALID_ACTIVITY_TYPES) -> Optional[str]:
    value = value.strip().lower()
    if value in valid_types:
        return value

    shortcuts = {item[0]: item for item in valid_types}
    return shortcuts.get(value)
