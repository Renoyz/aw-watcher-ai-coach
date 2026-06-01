"""Report generation modules."""

from aw_coach.reports.markdown import ReportGenerator
from aw_coach.reports.suggestions import generate_rule_suggestions

__all__ = ["ReportGenerator", "generate_rule_suggestions"]
