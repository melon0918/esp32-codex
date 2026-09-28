"""Panel window lifecycle contract tests with a fake Windows API."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SERVER_DIR = Path(__file__).resolve().parents[1] / "mcp-server"
sys.path.insert(0, str(SERVER_DIR))

from panel import lifecycle


class FakeUser32:
    def __init__(self, *, open_window: bool = True, minimized: bool = False):
        self.windows = {"ESP32 工作台": 1001} if open_window else {}
        self.minimized = minimized
        self.visible = open_window
        self.foreground = 0
        self.calls = []

    def FindWindowW(self, _class_name, title):
        return self.windows.get(title, 0)

    def IsIconic(self, _handle):
        return self.minimized

    def IsWindowVisible(self, _handle):
        return self.visible

    def GetForegroundWindow(self):
        return self.foreground

    def ShowWindow(self, handle, command):
        self.calls.append(("show", handle, command))
        if command == lifecycle.SW_MINIMIZE:
            self.minimized = True
        elif command == lifecycle.SW_RESTORE:
            self.minimized = False
            self.visible = True
        return True

    def SetForegroundWindow(self, handle):
        self.foreground = handle
        self.calls.append(("foreground", handle))
        return True

    def PostMessageW(self, handle, message, _wparam, _lparam):
        self.calls.append(("post", handle, message))
        if message == lifecycle.WM_CLOSE:
            self.windows.clear()
            self.visible = False
        return True


class PanelLifecycleTests(unittest.TestCase):
    def test_status_reports_closed_and_minimized_windows(self):
        with patch.object(lifecycle, "_windows_api", return_value=FakeUser32(open_window=False)):
            closed = lifecycle.panel_window_status()
        self.assertEqual(closed["state"], "closed")
        self.assertFalse(closed["open"])

        with patch.object(lifecycle, "_windows_api", return_value=FakeUser32(minimized=True)):
            minimized = lifecycle.panel_window_status()
        self.assertEqual(minimized["ui"], "web")
        self.assertEqual(minimized["state"], "minimized")
        self.assertTrue(minimized["open"])

    def test_focus_minimize_restore_and_close_use_normal_window_messages(self):
        api = FakeUser32(minimized=True)
        with patch.object(lifecycle, "_windows_api", return_value=api):
            focused = lifecycle.control_panel_window("focus")
            self.assertEqual(focused["state"], "focused")
            minimized = lifecycle.control_panel_window("minimize")
            self.assertTrue(minimized["minimized"])
            restored = lifecycle.control_panel_window("restore")
            self.assertFalse(restored["minimized"])
            closed = lifecycle.control_panel_window("close")
            self.assertTrue(closed["closing"])
            self.assertEqual(lifecycle.panel_window_status()["state"], "closed")

        self.assertIn(("post", 1001, lifecycle.WM_CLOSE), api.calls)

    def test_invalid_action_and_closed_window_fail_without_side_effects(self):
        api = FakeUser32(open_window=False)
        with patch.object(lifecycle, "_windows_api", return_value=api):
            invalid = lifecycle.control_panel_window("restart")
            closed = lifecycle.control_panel_window("close")
        self.assertEqual(invalid["errorCode"], "invalid_request")
        self.assertEqual(closed["errorCode"], "panel_closed")
        self.assertFalse(api.calls)


if __name__ == "__main__":
    unittest.main()
