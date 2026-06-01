"""YAML rule loader."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import yaml


@dataclass
class SubRule:
    activity_type: str
    confidence: float
    match_titles: List[str] = field(default_factory=list)
    match_urls: List[str] = field(default_factory=list)
    weight: Optional[float] = None


@dataclass
class AppRule:
    name: str
    match_apps: List[str]
    default_type: str
    confidence: float
    sub_rules: List[SubRule] = field(default_factory=list)
    skip_screenshot: bool = False
    skip_analysis: bool = False
    weight: Optional[float] = None


def _parse_sub_rules(raw_subs: list) -> List[SubRule]:
    result = []
    for sub in raw_subs:
        result.append(SubRule(
            activity_type=sub.get("type", "unknown"),
            confidence=sub.get("confidence", 0.7),
            match_titles=sub.get("match_titles", []),
            match_urls=sub.get("match_urls", []),
            weight=sub.get("weight"),
        ))
    return result


def load_rules_from_file(path: Path) -> List[AppRule]:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not data or "rules" not in data:
        return []

    rules = []
    for raw in data["rules"]:
        rule = AppRule(
            name=raw.get("name", "unnamed"),
            match_apps=raw.get("match_apps", []),
            default_type=raw.get("default_type", raw.get("type", "unknown")),
            confidence=raw.get("confidence", 0.7),
            sub_rules=_parse_sub_rules(raw.get("sub_rules", [])),
            skip_screenshot=raw.get("skip_screenshot", False),
            skip_analysis=raw.get("skip_analysis", False),
            weight=raw.get("weight"),
        )
        rules.append(rule)

    return rules


def load_rules_from_dirs(directories: List[Path]) -> List[AppRule]:
    all_rules: List[AppRule] = []
    for directory in directories:
        if directory.exists():
            for path in sorted(directory.glob("*.yml")):
                all_rules.extend(load_rules_from_file(path))
            for path in sorted(directory.glob("*.yaml")):
                all_rules.extend(load_rules_from_file(path))
    return _merge_rules(all_rules)


def load_rules_from_dir(directory: Path) -> List[AppRule]:
    if not directory.exists():
        return []

    all_rules: List[AppRule] = []
    for path in sorted(directory.glob("*.yml")):
        all_rules.extend(load_rules_from_file(path))
    for path in sorted(directory.glob("*.yaml")):
        all_rules.extend(load_rules_from_file(path))

    return _merge_rules(all_rules)


def _merge_rules(rules: List[AppRule]) -> List[AppRule]:
    """Merge rules that share the same match_apps entries (combine sub_rules)."""
    seen: dict = {}  # app_name_lower -> AppRule index in result
    result: List[AppRule] = []

    for rule in rules:
        merged = False
        for app_name in rule.match_apps:
            key = app_name.lower()
            if key in seen:
                existing = result[seen[key]]
                existing.sub_rules.extend(rule.sub_rules)
                merged = True
                break

        if not merged:
            idx = len(result)
            result.append(rule)
            for app_name in rule.match_apps:
                seen[app_name.lower()] = idx

    return result
