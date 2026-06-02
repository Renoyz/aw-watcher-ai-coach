"""Context Stack: maintain semantic context across temporary interruptions.

Problem: briefly checking docs or replying to a message resets the focus block.
Solution: maintain a stack of "primary contexts". Short-lived shallow modes
(browsing, chatting) are treated as interrupts, not context switches.

Rules:
    1. Deep work (coding, debugging, testing, writing, researching) + > 3 min
       → push onto stack as primary context.
    2. Temporary switch to shallow mode (browsing, chatting, terminal)
       + < INTERRUPT_THRESHOLD_SEC → mark as interrupt, do NOT pop stack.
    3. Return to primary context → resume accumulated time.
    4. Stay in non-primary mode > SWITCH_THRESHOLD_SEC → real switch,
       pop old context and push new one.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from aw_coach.enriched_state import SemanticWorkState

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# A mode is considered "deep" if it represents focused work
_DEEP_MODES = frozenset({
    "coding", "debugging", "testing", "writing", "researching",
    "building", "editing", "reviewing", "committing",
})

# Shallow modes that are likely interrupts
_SHALLOW_MODES = frozenset({
    "browsing", "chatting", "terminal", "meeting",
})

# How long a shallow mode must last before it's considered a real switch
INTERRUPT_THRESHOLD_SEC = 180  # 3 minutes

# How long outside primary context before we consider it a real switch
SWITCH_THRESHOLD_SEC = 300  # 5 minutes

# Maximum inactive frames to retain in history (prevents memory bloat)
MAX_INACTIVE_HISTORY = 2


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ContextFrame:
    """A single frame on the context stack."""

    project: Optional[str]
    mode: str
    app: str
    title: str
    entered_at: datetime
    accumulated_sec: float = 0.0
    is_active: bool = True

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["entered_at"] = d["entered_at"].isoformat()
        return d

    @classmethod
    def from_state(cls, state: SemanticWorkState) -> "ContextFrame":
        return cls(
            project=state.semantic_project,
            mode=state.likely_mode,
            app=state.current_app,
            title=state.current_title,
            entered_at=state.updated_at,
        )


@dataclass
class ContextStack:
    """Stack of primary work contexts."""

    frames: List[ContextFrame] = field(default_factory=list)

    # Track update timing for accurate accumulation
    _last_update_at: Optional[datetime] = None

    # Track when we left the primary context for an interrupt
    _left_primary_at: Optional[datetime] = None
    _interrupt_mode: Optional[str] = None

    @property
    def primary(self) -> Optional[ContextFrame]:
        """Return the current primary context (top of stack)."""
        for f in reversed(self.frames):
            if f.is_active:
                return f
        return None

    @property
    def depth(self) -> int:
        return len(self.frames)

    def to_dict(self) -> Dict:
        return {
            "frames": [f.to_dict() for f in self.frames],
            "primary_mode": self.primary.mode if self.primary else None,
            "primary_project": self.primary.project if self.primary else None,
            "depth": self.depth,
        }

    # ------------------------------------------------------------------ #
    # Core logic
    # ------------------------------------------------------------------ #

    def update(self, state: SemanticWorkState, now: Optional[datetime] = None) -> None:
        """Update the stack with a new state snapshot.

        This should be called every minute (or whenever state changes).
        """
        if now is None:
            now = datetime.now(timezone.utc).astimezone()

        primary = self.primary

        # --- Empty stack: push first context --------------------------------
        if primary is None:
            self._push(ContextFrame.from_state(state))
            self._last_update_at = now
            return

        # Calculate elapsed time since last update (cap at 5 min to avoid spikes)
        elapsed_sec = 60
        if self._last_update_at is not None:
            elapsed_sec = min((now - self._last_update_at).total_seconds(), 300)
        self._last_update_at = now

        # --- Still in primary context ----------------------------------------
        if self._is_same_context(state, primary):
            if self._left_primary_at is not None:
                # Just returned from an interrupt
                self._left_primary_at = None
                self._interrupt_mode = None
            # Sync mode if it has become more specific (e.g. unknown -> coding)
            if primary.mode == "unknown" and state.likely_mode != "unknown":
                primary.mode = state.likely_mode
            # Accumulate actual elapsed time
            primary.accumulated_sec += elapsed_sec
            return

        # --- Left primary context --------------------------------------------
        if self._left_primary_at is None:
            primary.accumulated_sec += elapsed_sec
            self._left_primary_at = now
            self._interrupt_mode = state.likely_mode

        # How long have we been outside primary?
        away_sec = (now - self._left_primary_at).total_seconds()

        # Case A: shallow interrupt that returned quickly
        if away_sec < INTERRUPT_THRESHOLD_SEC:
            if state.likely_mode in _SHALLOW_MODES:
                # Still an interrupt, do nothing
                return
            # Deep mode during interrupt window — treat as real switch
            self._switch_to(state, now)
            return

        # Case B: away too long, or in another deep mode
        if away_sec >= SWITCH_THRESHOLD_SEC or state.likely_mode in _DEEP_MODES:
            self._switch_to(state, now)
            return

        # Case C: in-between — keep observing
        return

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _is_same_context(state: SemanticWorkState, frame: ContextFrame) -> bool:
        """Check if state matches the given frame."""
        # Same mode + same project (or both have no project)
        same_mode = state.likely_mode == frame.mode
        same_project = state.semantic_project == frame.project
        return same_mode and same_project

    def _push(self, frame: ContextFrame) -> None:
        self.frames.append(frame)
        # Prune old inactive frames to prevent memory bloat while keeping history
        active_frames = [f for f in self.frames if f.is_active]
        inactive_frames = [f for f in self.frames if not f.is_active]
        # Keep most recent inactive frames up to the limit
        kept_inactive = (
            inactive_frames[-MAX_INACTIVE_HISTORY:]
            if len(inactive_frames) > MAX_INACTIVE_HISTORY
            else inactive_frames
        )
        self.frames = active_frames + kept_inactive

    def _switch_to(self, state: SemanticWorkState, now: datetime) -> None:
        """Perform a real context switch: pop old, push new."""
        primary = self.primary
        if primary:
            primary.is_active = False

        self._push(ContextFrame.from_state(state))
        self._left_primary_at = None
        self._interrupt_mode = None

    def get_active_block_minutes(self) -> int:
        """Return accumulated minutes for the primary context."""
        primary = self.primary
        if primary is None:
            return 0
        return int(primary.accumulated_sec / 60)

    def get_interruption_summary(self) -> Optional[str]:
        """Human-readable summary of current interrupt state."""
        if self._interrupt_mode and self._left_primary_at:
            elapsed = datetime.now(timezone.utc).astimezone() - self._left_primary_at
            away_min = int(elapsed.total_seconds() / 60)
            return f"临时切换至 {self._interrupt_mode}（{away_min} 分钟）"
        return None
