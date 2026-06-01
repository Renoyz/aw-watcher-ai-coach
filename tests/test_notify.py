"""Tests for notification and scheduler modules."""

from unittest.mock import patch

from aw_coach.notify import send_notification


class TestNotify:
    @patch("aw_coach.notify._dbus_notify", return_value=None)
    @patch("aw_coach.notify.subprocess.run")
    @patch("aw_coach.notify.platform.system", return_value="Linux")
    def test_linux_notification(self, mock_system, mock_run, mock_dbus):
        result = send_notification("Test Title", "Test Body")
        assert result is True
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "notify-send"
        assert "Test Title" in args
        assert "Test Body" in args

    @patch("aw_coach.notify.subprocess.run")
    @patch("aw_coach.notify.platform.system", return_value="Darwin")
    def test_macos_notification(self, mock_system, mock_run):
        result = send_notification("Title", "Body")
        assert result is True
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "osascript"

    @patch("aw_coach.notify._dbus_notify", return_value=None)
    @patch("aw_coach.notify.platform.system", return_value="Linux")
    @patch("aw_coach.notify.subprocess.run", side_effect=FileNotFoundError)
    def test_notification_graceful_failure(self, mock_run, mock_system, mock_dbus):
        result = send_notification("Title", "Body")
        assert result is False
