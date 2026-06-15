"""Rule engine for activity classification."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from aw_coach.rules.loader import AppRule, SubRule, load_rules_from_dirs


@dataclass
class RuleResult:
    activity_type: str
    confidence: float
    method: str
    rule_name: Optional[str] = None
    weight: Optional[float] = None
    skip_analysis: bool = False
    skip_screenshot: bool = False


DEFAULT_WEIGHTS = {
    "programming": 1.0,
    "ai_assisted": 0.9,
    "writing": 0.9,
    "design": 0.9,
    "research": 0.8,
    "meeting": 0.4,
    "admin": 0.3,
    "social": -0.2,
    "entertainment": -0.5,
    "sensitive": 0.0,
    "unknown": 0.0,
}


class RuleEngine:
    def __init__(self, rules: List[AppRule]):
        self.rules = rules
        self._app_index: dict[str, AppRule] = {}
        self._build_index()

    def _build_index(self) -> None:
        for rule in self.rules:
            for app_name in rule.match_apps:
                self._app_index[app_name.lower()] = rule

    @classmethod
    def with_builtin_rules(cls) -> "RuleEngine":
        return cls.with_all_rules()

    @classmethod
    def with_all_rules(cls) -> "RuleEngine":
        """Load builtin rules + user custom rules from data dir."""
        from aw_coach.config import DEFAULT_DATA_DIR

        builtin_dir = Path(__file__).parent / "builtin"
        user_dir = DEFAULT_DATA_DIR / "rules"

        dirs = [builtin_dir]
        if user_dir.exists():
            dirs.append(user_dir)

        rules = load_rules_from_dirs(dirs)
        return cls(rules)

    def classify(self, app: str, title: str, url: Optional[str] = None) -> RuleResult:
        app_lower = app.lower()

        # 1. Exact app match
        rule = self._app_index.get(app_lower)
        if rule:
            return self._classify_with_rule(rule, title, url)

        # 2. Contains match - pick highest confidence among matches
        matches = []
        for key, rule in self._app_index.items():
            if key in app_lower or app_lower in key:
                matches.append(rule)
        if matches:
            best = max(matches, key=lambda r: r.confidence)
            return self._classify_with_rule(best, title, url)

        # 3. No match
        return RuleResult(
            activity_type="unknown",
            confidence=0.0,
            method="rule_miss",
            rule_name=None,
        )

    def _classify_with_rule(
        self, rule: AppRule, title: str, url: Optional[str]
    ) -> RuleResult:
        # Check sub-rules first for refinement
        if rule.sub_rules:
            for sub in rule.sub_rules:
                if self._sub_matches(sub, title, url):
                    w = (
                        sub.weight
                        if sub.weight is not None
                        else DEFAULT_WEIGHTS.get(sub.activity_type, 0.0)
                    )
                    return RuleResult(
                        activity_type=sub.activity_type,
                        confidence=sub.confidence,
                        method="rule_sub",
                        rule_name=rule.name,
                        weight=w,
                        skip_analysis=rule.skip_analysis,
                        skip_screenshot=rule.skip_screenshot,
                    )

        # Determine weight
        w = rule.weight if rule.weight is not None else DEFAULT_WEIGHTS.get(rule.default_type, 0.0)

        # If confidence already high, return directly
        if rule.confidence >= 0.85:
            return RuleResult(
                activity_type=rule.default_type,
                confidence=rule.confidence,
                method="rule_app_exact",
                rule_name=rule.name,
                weight=w,
                skip_analysis=rule.skip_analysis,
                skip_screenshot=rule.skip_screenshot,
            )

        # Low confidence app match (e.g., browser without sub-rule hit)
        return RuleResult(
            activity_type=rule.default_type,
            confidence=rule.confidence,
            method="rule_app_fuzzy",
            rule_name=rule.name,
            weight=w,
            skip_analysis=rule.skip_analysis,
            skip_screenshot=rule.skip_screenshot,
        )

    def _sub_matches(self, sub: SubRule, title: str, url: Optional[str]) -> bool:
        title_lower = title.lower()
        url_lower = (url or "").lower()

        if sub.match_titles:
            for pattern in sub.match_titles:
                if pattern.lower() in title_lower:
                    return True

        if sub.match_urls and url_lower:
            for pattern in sub.match_urls:
                if pattern.lower() in url_lower:
                    return True

        return False

    def has_confident_rule(self, app: str) -> bool:
        rule = self._app_index.get(app.lower())
        return rule is not None and rule.confidence >= 0.85
