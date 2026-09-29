"""Per-user desktop panel window discovery and lifecycle controls."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes


PANEL_TITLES = (
    ("web", "ESP32 工作台"),
    ("tk", "ESP32 Codex 控制面板"),
)
SW_MINIMIZE = 6
SW_RESTORE = 9
WM_CLOSE = 0x0010


def _windows_api():
    if os.name != "nt":
        return None
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
    user32.FindWindowW.restype = wintypes.HWND
    user32.IsWindowVisible.argtypes = (wintypes.HWND,)
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.IsIconic.argtypes = (wintypes.HWND,)
    user32.IsIconic.restype = wintypes.BOOL
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
    user32.ShowWindow.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    user32.PostMessageW.restype = wintypes.BOOL
    return user32


def _find_window(user32):
    for ui, title in PANEL_TITLES:
        handle = user32.FindWindowW(None, title)
        if handle:
            return handle, ui, title
    return None, None, None


def _status(user32) -> dict[str, object]:
    handle, ui, title = _find_window(user32)
    if not handle:
        return {
            "ok": True, "open": False, "ui": None, "state": "closed",
            "visible": False, "minimized": False, "foreground": False,
        }
    minimized = bool(user32.IsIconic(handle))
    visible = bool(user32.IsWindowVisible(handle))
    foreground = user32.GetForegroundWindow() == handle
    state = "minimized" if minimized else "focused" if foreground else "open"
    return {
        "ok": True, "open": True, "ui": ui, "title": title, "state": state,
        "visible": visible, "minimized": minimized, "foreground": foreground,
    }


def panel_window_status() -> dict[str, object]:
    user32 = _windows_api()
    if user32 is None:
        return {"ok": False, "errorCode": "unsupported_platform", "error": "面板窗口控制仅支持 Windows。"}
    return _status(user32)


def control_panel_window(action: str) -> dict[str, object]:
    if action not in {"focus", "minimize", "restore", "close"}:
        return {"ok": False, "errorCode": "invalid_request", "error": "action 必须是 focus/minimize/restore/close。"}
    user32 = _windows_api()
    if user32 is None:
        return {"ok": False, "errorCode": "unsupported_platform", "error": "面板窗口控制仅支持 Windows。"}
    handle, ui, _title = _find_window(user32)
    if not handle:
        return {"ok": False, "errorCode": "panel_closed", "error": "面板当前未打开。"}
    if action == "minimize":
        user32.ShowWindow(handle, SW_MINIMIZE)
    elif action in {"restore", "focus"}:
        user32.ShowWindow(handle, SW_RESTORE)
        if action == "focus" and not user32.SetForegroundWindow(handle):
            return {"ok": False, "errorCode": "focus_denied", "error": "Windows 未允许将面板置于前台。"}
    else:
        if not user32.PostMessageW(handle, WM_CLOSE, 0, 0):
            return {"ok": False, "errorCode": "close_failed", "error": "Windows 未接受面板关闭请求。"}
        return {"ok": True, "action": "close", "closing": True, "ui": ui}
    return {"ok": True, "action": action, **_status(user32)}
