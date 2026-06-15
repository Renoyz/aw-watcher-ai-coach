"""Activity chain analyzer: understand user behaviour patterns over time.

Phase 1 基础版：纯规则 + 统计，零 LLM 成本。
预留了 `llm_analyze_chain` 接口，后续可按需接入。

Input: a sequence of activity records (SemanticWorkState or raw slices)
Output: pattern label + depth assessment + optional insight
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from aw_coach.enriched_state import SemanticWorkState


@dataclass
class ChainAnalysisResult:
    """Result of analyzing an activity chain."""

    pattern: str           # e.g. "deep_coding", "debug_cycle", "research_loop",
                           #      "context_switching", "meeting_block", "idle"
    depth_score: float     # 0.0-1.0, higher = deeper focus
    fragmentation_score: float  # 0.0-1.0, higher = more fragmented
    insight: Optional[str] = None  # Human-readable insight
    confidence: float = 1.0  # Rule-based = 1.0, LLM = 0.0-1.0


# ---------------------------------------------------------------------------
# Rule-based chain analysis (zero LLM cost)
# ---------------------------------------------------------------------------

class ChainAnalyzer:
    """Analyze sequences of SemanticWorkState to detect behaviour patterns."""

    # Minimum records to attempt pattern detection
    MIN_RECORDS = 3

    # Time window for chain analysis (default: last 30 minutes)
    DEFAULT_WINDOW_MIN = 30

    # Modes considered "deep work"
    DEEP_MODES = frozenset({"coding", "debugging", "testing", "writing", "researching"})

    # Modes considered "shallow / distracting"
    SHALLOW_MODES = frozenset({"browsing", "chatting", "terminal"})

    def analyze(
        self,
        records: List[SemanticWorkState],
        window_min: int = DEFAULT_WINDOW_MIN,
    ) -> ChainAnalysisResult:
        """Analyze a sequence of work-state records.

        Args:
            records: Chronological list of SemanticWorkState (newest last)
            window_min: How many minutes back to consider
        """
        if len(records) < self.MIN_RECORDS:
            return ChainAnalysisResult(
                pattern="insufficient_data",
                depth_score=0.0,
                fragmentation_score=0.0,
                insight="数据不足，无法分析模式",
            )

        # Filter to window
        now = datetime.now(timezone.utc).astimezone()
        cutoff = now - timedelta(minutes=window_min)
        window = [r for r in records if r.updated_at >= cutoff]

        if len(window) < self.MIN_RECORDS:
            window = records[-self.MIN_RECORDS :]

        modes = [r.likely_mode for r in window]
        risks = [r.risk_level for r in window]
        apps = [r.current_app for r in window]

        # 1. Detect pattern
        pattern = self._detect_pattern(modes, apps, risks)

        # 2. Compute depth score
        depth = self._compute_depth_score(window, modes)

        # 3. Compute fragmentation
        frag = self._compute_fragmentation(window, modes)

        # 4. Generate insight
        insight = self._generate_insight(pattern, depth, frag, window)

        return ChainAnalysisResult(
            pattern=pattern,
            depth_score=round(depth, 2),
            fragmentation_score=round(frag, 2),
            insight=insight,
            confidence=1.0,
        )

    # --- Pattern detection -------------------------------------------------

    def _detect_pattern(
        self, modes: List[str], apps: List[str], risks: List[str]
    ) -> str:
        """Detect high-level pattern from mode sequence."""
        if not modes:
            return "insufficient_data"

        # Count mode frequencies
        mode_counts: Dict[str, int] = {}
        for m in modes:
            mode_counts[m] = mode_counts.get(m, 0) + 1
        total = len(modes)
        dominant = max(mode_counts, key=mode_counts.get)
        dominant_ratio = mode_counts[dominant] / total

        # Deep coding block: >70% coding, low fragmentation
        if dominant == "coding" and dominant_ratio >= 0.7:
            return "deep_coding"

        # Debug cycle: repeated coding + browser/terminal + coding
        if self._is_debug_cycle(modes):
            return "debug_cycle"

        # Research loop: repeated browsing + coding
        if self._is_research_loop(modes):
            return "research_loop"

        # Meeting block
        if dominant == "meeting" and dominant_ratio >= 0.6:
            return "meeting_block"

        # Context switching: no dominant mode, many unique modes
        unique_modes = len(set(modes))
        if unique_modes >= 4 or dominant_ratio < 0.4:
            return "context_switching"

        # Idle / AFK: only when almost everything is unknown
        # AND the most recent record is also unknown/idle
        if dominant == "unknown" and dominant_ratio >= 0.7:
            if modes and modes[-1] not in ("unknown", "idle"):
                pass  # user is currently doing something concrete; don't call it idle
            else:
                return "idle"

        # Fallback: named by dominant mode
        return dominant

    def _is_debug_cycle(self, modes: List[str]) -> bool:
        """Detect debug cycle: coding interleaved with research/browsing/terminal.

        Requires:
            - at least one terminal or debugging step
            - coding makes up >= 40% of the sequence
            - at least 2 coding↔other transitions
        """
        if len(modes) < 4:
            return False
        # Must contain terminal or debugging
        if not any(m in ("terminal", "debugging") for m in modes):
            return False
        # Coding should be a significant part
        if modes.count("coding") < len(modes) * 0.4:
            return False
        # Look for alternating pattern: coding -> (researching/browsing/terminal) -> coding
        transitions = 0
        for i in range(len(modes) - 1):
            a, b = modes[i], modes[i + 1]
            if a == "coding" and b in ("researching", "browsing", "terminal", "debugging"):
                transitions += 1
            elif a in ("researching", "browsing", "terminal", "debugging") and b == "coding":
                transitions += 1
        # Need at least 2 coding↔other transitions
        return transitions >= 2

    def _is_research_loop(self, modes: List[str]) -> bool:
        """Detect research loop: repeated browsing + coding without terminal."""
        if len(modes) < 4:
            return False
        has_browsing = "browsing" in modes or "researching" in modes
        has_coding = "coding" in modes
        if not (has_browsing and has_coding):
            return False
        # Check if there's no terminal/debugging (distinguish from debug cycle)
        has_terminal = "terminal" in modes or "debugging" in modes
        if has_terminal:
            return False
        # Alternating pattern
        transitions = 0
        for i in range(len(modes) - 1):
            a, b = modes[i], modes[i + 1]
            if a in ("coding", "writing") and b in ("browsing", "researching"):
                transitions += 1
            elif a in ("browsing", "researching") and b in ("coding", "writing"):
                transitions += 1
        return transitions >= 2

    # --- Scoring -----------------------------------------------------------

    def _compute_depth_score(
        self, window: List[SemanticWorkState], modes: List[str]
    ) -> float:
        """Compute depth score 0.0-1.0 based on deep work ratio + block length."""
        if not window:
            return 0.0

        total = len(modes)
        deep_count = sum(1 for m in modes if m in self.DEEP_MODES)
        deep_ratio = deep_count / total

        # Bonus for long uninterrupted blocks
        longest_block = self._longest_consecutive_mode(modes)
        block_bonus = min(longest_block / 10, 0.3)  # cap at 0.3 for 10+ consecutive

        score = deep_ratio * 0.7 + block_bonus
        return min(1.0, score)

    def _compute_fragmentation(
        self, window: List[SemanticWorkState], modes: List[str]
    ) -> float:
        """Compute fragmentation 0.0-1.0. Higher = more fragmented."""
        if len(modes) < 2:
            return 0.0

        # Mode switches per record, with exemptions for reasonable work-flow transitions
        switches = 0
        for i in range(len(modes) - 1):
            a, b = modes[i], modes[i + 1]
            if a == b:
                continue
            # Exempt coding↔researching/debugging if the research/debug step is brief
            if self._is_reasonable_transition(window, i, a, b):
                switches += 0.5  # count as half a switch
            else:
                switches += 1.0

        switch_ratio = switches / (len(modes) - 1)

        # Risk-level contributions
        risk_boost = sum(
            0.1 for r in window if r.risk_level in ("fragmented", "distracted")
        )
        risk_boost = min(risk_boost, 0.3)

        return min(1.0, switch_ratio * 0.7 + risk_boost)

    def _is_reasonable_transition(
        self, window: List[SemanticWorkState], idx: int, a: str, b: str
    ) -> bool:
        """Return True if a→b is a normal coding workflow (e.g. coding→researching→coding)."""
        reasonable_pairs = {
            ("coding", "researching"),
            ("researching", "coding"),
            ("coding", "debugging"),
            ("debugging", "coding"),
            ("coding", "testing"),
            ("testing", "coding"),
        }
        if (a, b) not in reasonable_pairs:
            return False
        # Only exempt if the non-coding step is brief (< 15 min)
        non_coding = a if a != "coding" else b
        if non_coding in ("researching", "debugging", "testing"):
            record = window[idx] if idx < len(window) else None
            if record and getattr(record, "active_block_minutes", 0) < 15:
                return True
        return False

    @staticmethod
    def _longest_consecutive_mode(modes: List[str]) -> int:
        """Return length of longest run of identical modes."""
        if not modes:
            return 0
        best = current = 1
        for i in range(1, len(modes)):
            if modes[i] == modes[i - 1]:
                current += 1
                best = max(best, current)
            else:
                current = 1
        return best

    # --- Insight generation ------------------------------------------------

    def _generate_insight(
        self,
        pattern: str,
        depth: float,
        frag: float,
        window: List[SemanticWorkState],
    ) -> Optional[str]:
        """Generate human-readable insight."""
        if pattern == "deep_coding":
            block_mins = window[-1].active_block_minutes if window else 0
            block_text = f"{block_mins} 分钟" if block_mins >= 1 else "不足 1 分钟"
            return f"深度编码中，已连续 {block_text}"

        if pattern == "debug_cycle":
            return "Debug 循环：在编码和查询/测试之间反复切换"

        if pattern == "research_loop":
            return "研究模式：在编码和查阅资料之间切换"

        if pattern == "context_switching":
            if depth >= 0.5:
                return f"工作状态活跃，在编码和查阅资料间切换（碎片度 {int(frag*100)}%）"
            return f"上下文频繁切换（碎片度 {int(frag*100)}%），建议聚焦单一任务"

        if pattern == "meeting_block":
            return "会议/沟通时段"

        if pattern == "idle":
            return "当前无明显工作活动"

        if depth >= 0.7:
            return "高度专注状态"

        if frag >= 0.6:
            if depth >= 0.5:
                return "工作状态活跃，在多个任务间流转"
            return "注意力分散，建议整理任务清单"

        return None


# ---------------------------------------------------------------------------
# LLM interface (placeholder for Phase 1+ extension)
# ---------------------------------------------------------------------------

def llm_analyze_chain(
    records: List[SemanticWorkState],
    api_key: Optional[str] = None,
) -> ChainAnalysisResult:
    """Optional LLM-based chain analysis.

    This is a placeholder. When enabled, it would:
    1. Format records into a concise prompt
    2. Call LLM with cost limiting
    3. Parse JSON response into ChainAnalysisResult

    Args:
        records: Activity sequence
        api_key: Optional API key override

    Returns:
        ChainAnalysisResult with confidence < 1.0 (indicating LLM origin)
    """
    # Placeholder: fall back to rule-based
    analyzer = ChainAnalyzer()
    result = analyzer.analyze(records)
    # Mark as LLM result (even though it's rule-based for now)
    result.confidence = 0.0
    result.insight = "[LLM not enabled] " + (result.insight or "")
    return result
