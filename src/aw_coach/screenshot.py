"""Lightweight screenshot analysis module.

Strategy:
    - Capture screen only when trigger conditions are met
    - Core analysis: frame-to-frame difference detection (pure Pillow, zero deps)
    - Optional enhancement: OCR via tesseract (only if installed)
    - Store only analysis results, discard image immediately
    - Respect privacy: skip sensitive apps/URLs

What it tells you without OCR:
    - Difference ratio 0-3%   → user is reading static content (doc, image)
    - Difference ratio 3-20%  → user is scrolling / typing
    - Difference ratio 20-50% → user is watching video / animation
    - Difference ratio > 50%  → major context switch or full-screen change

Trigger rules:
    1. unknown mode > 10 min
    2. stuck risk + block > 20 min
    3. same window title > 30 min
    Max 5/hour, 20/day.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Set, Tuple

if TYPE_CHECKING:
    from PIL import Image

# ---------------------------------------------------------------------------
# Auto-configure local tesseract (bundled with project)
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).parent.parent.parent
_LOCAL_TESSERACT = _PROJECT_ROOT / ".local" / "bin" / "tesseract"
_LOCAL_TESSDATA = _PROJECT_ROOT / ".local" / "share" / "tessdata"

if _LOCAL_TESSERACT.exists():
    os.environ.setdefault("TESSDATA_PREFIX", str(_LOCAL_TESSDATA))
    try:
        import pytesseract
        pytesseract.pytesseract.tesseract_cmd = str(_LOCAL_TESSERACT)
    except ImportError:
        pass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configurable constants
# ---------------------------------------------------------------------------

MAX_PER_HOUR = 5
MAX_PER_DAY = 20

# Trigger thresholds
UNKNOWN_THRESHOLD_MIN = 10
STUCK_BLOCK_THRESHOLD_MIN = 20
SAME_TITLE_THRESHOLD_MIN = 30

# Apps to always skip (privacy-sensitive)
_SENSITIVE_APPS: Set[str] = set()
_SENSITIVE_TITLE_KEYWORDS = {
    "密码", "password", "login", "登录", "网银", "bank",
    "支付宝", "alipay", "微信钱包", "微信支付",
    "vpn", "ssh key", "private key", "token",
}

# OCR text length limits
MIN_OCR_LENGTH = 10
MAX_OCR_LENGTH = 2000

# Difference ratio thresholds for classification
DIFF_STATIC = 0.03       # 0-3%
DIFF_SCROLLING = 0.20    # 3-20%
DIFF_VIDEO = 0.50        # 20-50%


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ScreenshotAnalysisResult:
    captured_at: datetime
    app: str
    title: str
    diff_ratio: float          # 0.0-1.0, pixel difference from previous capture
    content_type: str          # static / scrolling / video / major_change
    brightness: float          # 0.0-1.0, average brightness
    ocr_text: Optional[str]    # None if tesseract unavailable or disabled
    trigger_reason: str


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------

class ScreenshotCaptureError(Exception):
    pass


def _capture_screen_mss() -> Optional["Image.Image"]:
    """Capture primary monitor using mss (fast, cross-platform)."""
    try:
        import mss
        from PIL import Image

        with mss.mss() as sct:
            monitor = sct.monitors[1]  # primary monitor
            screenshot = sct.grab(monitor)
            return Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")
    except Exception as e:
        logger.debug(f"mss capture failed: {e}")
        return None


def _capture_screen_pil() -> Optional["Image.Image"]:
    """Fallback: capture using PIL.ImageGrab."""
    try:
        from PIL import ImageGrab

        return ImageGrab.grab()
    except Exception as e:
        logger.debug(f"PIL capture failed: {e}")
        return None


def capture_screen() -> Optional["Image.Image"]:
    """Capture screen, trying mss first then PIL fallback."""
    img = _capture_screen_mss()
    if img is not None:
        return img
    return _capture_screen_pil()


# ---------------------------------------------------------------------------
# Analysis: difference detection (zero-dependency core)
# ---------------------------------------------------------------------------

def _resize_for_comparison(
    image: "Image.Image",
    size: Tuple[int, int] = (320, 180),
) -> "Image.Image":
    """Resize image to small size for fast comparison."""
    return image.convert("L").resize(size)


def compute_diff_ratio(
    current: "Image.Image",
    previous: "Image.Image",
) -> float:
    """Compute pixel difference ratio between two screenshots.

    Returns 0.0 (identical) to 1.0 (completely different).
    """
    try:
        cur_small = _resize_for_comparison(current)
        prev_small = _resize_for_comparison(previous)

        cur_pixels = list(cur_small.getdata())
        prev_pixels = list(prev_small.getdata())

        if len(cur_pixels) != len(prev_pixels):
            return 1.0

        total_diff = 0
        max_possible = 255 * len(cur_pixels)
        for a, b in zip(cur_pixels, prev_pixels):
            total_diff += abs(a - b)

        return min(1.0, total_diff / max_possible) if max_possible > 0 else 0.0
    except Exception as e:
        logger.debug(f"Diff computation failed: {e}")
        return 1.0


def classify_content_type(diff_ratio: float) -> str:
    """Classify screen content based on difference ratio."""
    if diff_ratio < DIFF_STATIC:
        return "static"
    if diff_ratio < DIFF_SCROLLING:
        return "scrolling"
    if diff_ratio < DIFF_VIDEO:
        return "video"
    return "major_change"


def compute_brightness(image: "Image.Image") -> float:
    """Compute average brightness (0.0 = black, 1.0 = white)."""
    try:
        gray = image.convert("L")
        pixels = list(gray.getdata())
        return sum(pixels) / (255.0 * len(pixels)) if pixels else 0.5
    except Exception:
        return 0.5


# ---------------------------------------------------------------------------
# Optional: OCR
# ---------------------------------------------------------------------------

_tesseract_available: Optional[bool] = None


def _is_tesseract_available() -> bool:
    """Check if tesseract command is available."""
    global _tesseract_available
    if _tesseract_available is not None:
        return _tesseract_available
    try:
        import pytesseract

        pytesseract.get_tesseract_version()
        _tesseract_available = True
    except Exception:
        _tesseract_available = False
    return _tesseract_available


def extract_text(image: "Image.Image") -> Optional[str]:
    """Run OCR on image if tesseract is available.

    Returns extracted text or None if tesseract unavailable.
    """
    if not _is_tesseract_available():
        return None

    try:
        import pytesseract

        # Downscale large images to speed up OCR
        max_dim = 1920
        w, h = image.size
        if w > max_dim or h > max_dim:
            ratio = max_dim / max(w, h)
            image = image.resize((int(w * ratio), int(h * ratio)))

        text = pytesseract.image_to_string(image, lang="chi_sim+eng")
        text = text.strip()
        if len(text) < MIN_OCR_LENGTH:
            return None
        return text[:MAX_OCR_LENGTH]
    except Exception as e:
        logger.warning(f"OCR failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Trigger logic
# ---------------------------------------------------------------------------

class ScreenshotTrigger:
    """Decide whether to capture a screenshot based on current state and history."""

    def __init__(
        self,
        enabled: bool = True,
        blocklist_apps: Optional[List[str]] = None,
    ) -> None:
        self.enabled = enabled
        self.blocklist_apps: Set[str] = set(
            (a.lower() for a in (blocklist_apps or []))
        )
        self._history: List[Tuple[datetime, str, str]] = []
        self._state_history: List[Tuple[datetime, str, str]] = []
        self._last_capture_at: Optional[datetime] = None

    def should_capture(
        self,
        state,  # SemanticWorkState
        now: Optional[datetime] = None,
        rule_skip_screenshot: bool = False,
    ) -> Tuple[bool, str]:
        """Return (should_capture, reason)."""
        if now is None:
            now = datetime.now(timezone.utc).astimezone()

        if not self.enabled:
            return False, "disabled"

        if rule_skip_screenshot:
            return False, "rule_skip"

        # Privacy: skip sensitive apps
        app_lower = state.current_app.lower()
        title_lower = state.current_title.lower()
        if app_lower in _SENSITIVE_APPS:
            return False, "sensitive_app"
        if app_lower in self.blocklist_apps:
            return False, "blocklist_app"
        for kw in _SENSITIVE_TITLE_KEYWORDS:
            if kw.lower() in title_lower:
                return False, "sensitive_title"

        # Rate limiting
        if self._last_capture_at:
            hour_ago = now - timedelta(hours=1)
            day_ago = now - timedelta(days=1)
            captures_last_hour = sum(1 for t, _, _ in self._history if t > hour_ago)
            captures_last_day = sum(1 for t, _, _ in self._history if t > day_ago)
            if captures_last_hour >= MAX_PER_HOUR:
                return False, "rate_limit_hour"
            if captures_last_day >= MAX_PER_DAY:
                return False, "rate_limit_day"

        # Cooldown: at least 2 minutes between captures
        if self._last_capture_at and (now - self._last_capture_at).total_seconds() < 120:
            return False, "cooldown"

        # Trigger 1: unknown mode for too long
        if state.likely_mode == "unknown" and state.active_block_minutes >= UNKNOWN_THRESHOLD_MIN:
            return True, f"unknown_{state.active_block_minutes}min"

        # Trigger 2: stuck for too long
        if state.risk_level == "stuck" and state.active_block_minutes >= STUCK_BLOCK_THRESHOLD_MIN:
            return True, f"stuck_{state.active_block_minutes}min"

        # Trigger 3: same title for too long (static content like video/image)
        same_title_min = self._same_title_duration_min(now, state.current_title)
        if same_title_min >= SAME_TITLE_THRESHOLD_MIN:
            return True, f"same_title_{same_title_min}min"

        return False, "no_trigger"

    def record_capture(self, now: datetime, app: str, title: str) -> None:
        self._last_capture_at = now
        self._history.append((now, app, title))
        # Prune old history (> 24h)
        cutoff = now - timedelta(days=1)
        self._history = [h for h in self._history if h[0] > cutoff]

    def record_state(self, now: datetime, app: str, title: str) -> None:
        """Record a per-minute state tick (independent of captures)."""
        self._state_history.append((now, app, title))
        # Prune old history (> 24h)
        cutoff = now - timedelta(days=1)
        self._state_history = [h for h in self._state_history if h[0] > cutoff]

    def _same_title_duration_min(self, now: datetime, title: str) -> int:
        """How many minutes the same title has been seen continuously.

        Uses per-minute state history rather than capture history so that
        the trigger works even when no previous screenshots were taken.
        """
        if not self._state_history:
            return 0
        streak = 0
        for t, _app, hist_title in reversed(self._state_history):
            if hist_title == title:
                streak = int((now - t).total_seconds() / 60)
            else:
                break
        return streak


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def capture_and_analyze(
    state,
    trigger: ScreenshotTrigger,
    previous_image: Optional["Image.Image"] = None,
    rule_skip_screenshot: bool = False,
) -> Tuple[Optional[ScreenshotAnalysisResult], Optional["Image.Image"]]:
    """One-shot: decide, capture, analyze, return result (or None if skipped).

    Args:
        state: SemanticWorkState
        trigger: ScreenshotTrigger instance (keeps history)
        previous_image: Previous screenshot for diff comparison
        rule_skip_screenshot: Whether the matched rule requests skipping screenshots

    Returns:
        Tuple of (ScreenshotAnalysisResult or None, captured Image or None)
    """
    now = datetime.now(timezone.utc).astimezone()

    should, reason = trigger.should_capture(
        state, now, rule_skip_screenshot=rule_skip_screenshot
    )
    if not should:
        logger.debug(f"Screenshot skipped: {reason}")
        return None, None

    img = capture_screen()
    if img is None:
        logger.warning("Screenshot capture failed")
        return None, None

    # Difference detection
    diff_ratio = 1.0
    if previous_image is not None:
        diff_ratio = compute_diff_ratio(img, previous_image)

    content_type = classify_content_type(diff_ratio)
    brightness = compute_brightness(img)

    # Optional OCR
    ocr_text = extract_text(img)

    trigger.record_capture(now, state.current_app, state.current_title)

    # Privacy: discard full-resolution image; keep only a tiny grayscale
    # thumbnail sufficient for frame-to-frame diff in the next tick.
    diff_reference = _resize_for_comparison(img)

    result = ScreenshotAnalysisResult(
        captured_at=now,
        app=state.current_app,
        title=state.current_title,
        diff_ratio=round(diff_ratio, 3),
        content_type=content_type,
        brightness=round(brightness, 2),
        ocr_text=ocr_text,
        trigger_reason=reason,
    )
    return result, diff_reference
