"""System notification abstraction with click-to-open support."""

from __future__ import annotations

import logging
import platform
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)


def _fallback_notify_send(title: str, body: str) -> bool:
    """Fallback using notify-send command."""
    try:
        subprocess.run(
            ["notify-send", title, body],
            check=False,
            capture_output=True,
            timeout=5,
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.warning(f"notify-send failed: {e}")
        return False


def _dbus_notify(
    title: str, body: str, action_label: Optional[str] = None
) -> Optional[int]:
    """Send notification via dbus. Returns notification ID or None."""
    try:
        import dbus

        bus = dbus.SessionBus()
        notify_obj = bus.get_object(
            "org.freedesktop.Notifications", "/org/freedesktop/Notifications"
        )
        iface = dbus.Interface(notify_obj, "org.freedesktop.Notifications")

        actions = []
        if action_label:
            actions = [
                "default", "",       # click body -> "default"
                "open", action_label  # button -> "open"
            ]

        nid = iface.Notify(
            "aw-coach",
            0,
            "",
            title,
            body,
            actions,
            {},
            0,
        )
        return int(nid)
    except Exception as e:
        logger.debug(f"dbus notify failed: {e}")
        return None


def _listen_click(nid: int, url: str) -> None:
    """Listen for notification click in a separate subprocess.

    dbus-python's SessionBus is a singleton; if the main process creates
    a bus without a mainloop, signal receivers in threads cannot work.
    A standalone subprocess gets its own connection + GLib main loop,
    which is the only reliable way to capture ActionInvoked on GNOME.
    """
    # Escape the URL for safe insertion into the inline script.
    safe_url = url.replace('"', '\\"')

    script = f'''
import dbus
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib
import webbrowser

DBusGMainLoop(set_as_default=True)
bus = dbus.SessionBus()

def on_action(id, action):
    if int(id) == {nid} and action in ("default", "open"):
        webbrowser.open("{safe_url}")
        loop.quit()

bus.add_signal_receiver(
    on_action,
    signal_name="ActionInvoked",
    dbus_interface="org.freedesktop.Notifications",
    path="/org/freedesktop/Notifications",
)

loop = GLib.MainLoop()
GLib.timeout_add_seconds(30, loop.quit)
loop.run()
'''
    try:
        subprocess.Popen(
            ["python3", "-c", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.debug(f"Click listener subprocess started for nid={nid}")
    except Exception as e:
        logger.warning(f"Failed to start click listener: {e}")


def send_notification(
    title: str, body: str, detail_url: Optional[str] = None
) -> bool:
    """Send system notification.

    If ``detail_url`` is provided on Linux, the notification will include
    a "查看详情" action button. Clicking the notification body or the
    button opens the URL in the default browser.
    """
    system = platform.system()

    if system == "Darwin":
        try:
            script = f'display notification "{body}" with title "{title}"'
            subprocess.run(
                ["osascript", "-e", script],
                check=False,
                capture_output=True,
                timeout=5,
            )
            return True
        except Exception:
            return False
    elif system == "Windows":
        ps_script = (
            "[Windows.UI.Notifications.ToastNotificationManager, "
            "Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null\n"
            "$template = [Windows.UI.Notifications.ToastNotificationManager]"
            "::GetTemplateContent("
            "[Windows.UI.Notifications.ToastTemplateType]::ToastText02)\n"
            '$textNodes = $template.GetElementsByTagName("text")\n'
            f'$textNodes.Item(0).AppendChild($template.CreateTextNode("{title}")) '
            "| Out-Null\n"
            f'$textNodes.Item(1).AppendChild($template.CreateTextNode("{body}")) '
            "| Out-Null\n"
            "$toast = [Windows.UI.Notifications.ToastNotification]::new($template)\n"
            "[Windows.UI.Notifications.ToastNotificationManager]"
            '::CreateToastNotifier("aw-coach").Show($toast)\n'
        )
        try:
            subprocess.run(
                ["powershell", "-Command", ps_script],
                check=False,
                capture_output=True,
                timeout=10,
            )
            return True
        except Exception:
            return False
    elif system != "Linux":
        return False

    # Linux: try dbus with action
    action_label = "查看详情" if detail_url else None
    nid = _dbus_notify(title, body, action_label)

    if nid is not None:
        if detail_url:
            _listen_click(nid, detail_url)
        return True

    # Fallback to notify-send
    if detail_url:
        body += "\n📊 运行 `aw-coach serve` 查看交互式仪表盘"
    return _fallback_notify_send(title, body)
