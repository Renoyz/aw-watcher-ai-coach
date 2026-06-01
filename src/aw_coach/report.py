"""Backward-compatible report facade.

Concrete implementations live in:
- aw_coach.reports.markdown
- aw_coach.reports.suggestions
- aw_coach.web.dashboard
- aw_coach.web.helpers
"""

from __future__ import annotations

from aw_coach.reports.markdown import ReportGenerator
from aw_coach.reports.suggestions import generate_rule_suggestions
from aw_coach.web.dashboard import generate_html_dashboard, generate_report_page
from aw_coach.web.helpers import (
    activity_color as _activity_color,
)
from aw_coach.web.helpers import (
    build_hourly_timeline as _build_hourly_timeline,
)
from aw_coach.web.helpers import (
    safe_json as _safe_json,
)
from aw_coach.web.helpers import (
    split_slice_by_hour as _split_slice_by_hour,
)

__all__ = [
    "ReportGenerator",
    "generate_rule_suggestions",
    "generate_html_dashboard",
    "generate_report_page",
    "_activity_color",
    "_build_hourly_timeline",
    "_safe_json",
    "_split_slice_by_hour",
]
