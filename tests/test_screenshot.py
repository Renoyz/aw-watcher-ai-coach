"""Tests for screenshot module.

These tests mock image capture to avoid actually taking screenshots.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from aw_coach.screenshot import (
    ScreenshotTrigger,
    capture_and_analyze,
    classify_content_type,
    compute_brightness,
    compute_diff_ratio,
)


class TestClassifyContentType:
    def test_static(self):
        assert classify_content_type(0.01) == "static"
        assert classify_content_type(0.029) == "static"

    def test_scrolling(self):
        assert classify_content_type(0.05) == "scrolling"
        assert classify_content_type(0.15) == "scrolling"

    def test_video(self):
        assert classify_content_type(0.25) == "video"
        assert classify_content_type(0.40) == "video"

    def test_major_change(self):
        assert classify_content_type(0.60) == "major_change"
        assert classify_content_type(0.99) == "major_change"


class TestComputeBrightness:
    def test_black_image(self):
        from PIL import Image

        img = Image.new("RGB", (100, 100), color=(0, 0, 0))
        assert compute_brightness(img) == 0.0

    def test_white_image(self):
        from PIL import Image

        img = Image.new("RGB", (100, 100), color=(255, 255, 255))
        assert compute_brightness(img) == 1.0

    def test_gray_image(self):
        from PIL import Image

        img = Image.new("RGB", (100, 100), color=(128, 128, 128))
        bright = compute_brightness(img)
        assert 0.49 < bright < 0.51


class TestComputeDiffRatio:
    def test_identical(self):
        from PIL import Image

        img = Image.new("RGB", (100, 100), color=(128, 128, 128))
        assert compute_diff_ratio(img, img) == 0.0

    def test_completely_different(self):
        from PIL import Image

        img1 = Image.new("RGB", (100, 100), color=(0, 0, 0))
        img2 = Image.new("RGB", (100, 100), color=(255, 255, 255))
        diff = compute_diff_ratio(img1, img2)
        assert diff > 0.9

    def test_slightly_different(self):
        from PIL import Image

        img1 = Image.new("RGB", (100, 100), color=(128, 128, 128))
        img2 = Image.new("RGB", (100, 100), color=(130, 130, 130))
        diff = compute_diff_ratio(img1, img2)
        assert 0.0 < diff < 0.1


class TestScreenshotTrigger:
    def _make_state(self, mode, risk, block_min=0, app="Code", title="test"):
        from aw_coach.enriched_state import SemanticWorkState

        return SemanticWorkState(
            updated_at=datetime.now(timezone.utc).astimezone(),
            current_app=app,
            current_title=title,
            likely_mode=mode,
            risk_level=risk,
            active_block_minutes=block_min,
        )

    def test_no_trigger_normal(self):
        trigger = ScreenshotTrigger()
        state = self._make_state("coding", "normal", block_min=5)
        should, reason = trigger.should_capture(state)
        assert not should
        assert reason == "no_trigger"

    def test_trigger_unknown_long(self):
        trigger = ScreenshotTrigger()
        state = self._make_state("unknown", "normal", block_min=15)
        should, reason = trigger.should_capture(state)
        assert should
        assert "unknown" in reason

    def test_trigger_stuck_long(self):
        trigger = ScreenshotTrigger()
        state = self._make_state("debugging", "stuck", block_min=25)
        should, reason = trigger.should_capture(state)
        assert should
        assert "stuck" in reason

    def test_skip_sensitive_title(self):
        trigger = ScreenshotTrigger()
        state = self._make_state("browsing", "normal", title="Login Page")
        should, reason = trigger.should_capture(state)
        assert not should
        assert reason == "sensitive_title"

    def test_rate_limit(self):
        trigger = ScreenshotTrigger()
        now = datetime.now(timezone.utc).astimezone()
        # Fill history to max
        for i in range(20):
            trigger.record_capture(now, "Code", "test")
        state = self._make_state("unknown", "normal", block_min=15)
        should, reason = trigger.should_capture(state)
        assert not should
        assert "rate_limit" in reason

    def test_cooldown(self):
        trigger = ScreenshotTrigger()
        now = datetime.now(timezone.utc).astimezone()
        trigger.record_capture(now, "Code", "test")
        state = self._make_state("unknown", "normal", block_min=15)
        should, reason = trigger.should_capture(state)
        assert not should
        assert reason == "cooldown"


class TestCaptureAndAnalyze:
    @patch("aw_coach.screenshot.capture_screen")
    @patch("aw_coach.screenshot._is_tesseract_available")
    def test_successful_capture_no_ocr(self, mock_tesseract, mock_capture):
        from PIL import Image

        mock_tesseract.return_value = False
        img = Image.new("RGB", (100, 100), color=(128, 128, 128))
        mock_capture.return_value = img

        from aw_coach.enriched_state import SemanticWorkState

        state = SemanticWorkState(
            updated_at=datetime.now(timezone.utc).astimezone(),
            current_app="Code",
            current_title="test",
            likely_mode="unknown",
            risk_level="normal",
            active_block_minutes=15,
        )
        trigger = ScreenshotTrigger()

        result = capture_and_analyze(state, trigger)
        assert result is not None
        assert result.diff_ratio == 1.0  # no previous image
        assert result.content_type == "major_change"
        assert result.ocr_text is None  # tesseract unavailable
        assert result.brightness == 0.5

    @patch("aw_coach.screenshot.capture_screen")
    def test_capture_failed(self, mock_capture):
        mock_capture.return_value = None

        from aw_coach.enriched_state import SemanticWorkState

        state = SemanticWorkState(
            updated_at=datetime.now(timezone.utc).astimezone(),
            current_app="Code",
            current_title="test",
            likely_mode="unknown",
            risk_level="normal",
            active_block_minutes=15,
        )
        trigger = ScreenshotTrigger()

        result = capture_and_analyze(state, trigger)
        assert result is None
