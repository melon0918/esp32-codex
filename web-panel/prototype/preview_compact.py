"""Open the compact single-page MOCK prototype in an isolated native window."""
from __future__ import annotations

from pathlib import Path
import ctypes
import json
import re

import webview


ROOT = Path(__file__).resolve().parent
TITLE = "ESP32 Codex · 紧凑单页 MOCK 初稿"


class WindowControlsApi:
    """Expose only the two native window actions needed by the mock title bar."""

    def __init__(self) -> None:
        self._window = None

    def minimize_window(self) -> bool:
        if self._window is None:
            return False
        self._window.minimize()
        return True

    def close_window(self) -> bool:
        if self._window is None:
            return False
        self._window.destroy()
        return True


def inline_page() -> str:
    html = (ROOT / "compact.html").read_text(encoding="utf-8")
    css = (ROOT / "compact.css").read_text(encoding="utf-8")
    js = (ROOT / "compact.js").read_text(encoding="utf-8")
    html, css_count = re.subn(
        r'<link\s+rel="stylesheet"\s+href="compact\.css"\s*/?>',
        "<style>\n" + css + "\n</style>", html, count=1, flags=re.I,
    )
    html, js_count = re.subn(
        r'<script\s+src="compact\.js"\s*>\s*</script>',
        "<script>\n" + js + "\n</script>", html, count=1, flags=re.I,
    )
    if css_count != 1 or js_count != 1:
        raise RuntimeError("Compact preview must have exactly one local CSS and JS reference")
    if re.search(r"(?:src|href)\s*=\s*['\"](?:https?:)?//", html, flags=re.I):
        raise RuntimeError("External resources are not allowed in the compact preview")
    return html


if __name__ == "__main__":
    page = inline_page()
    api = WindowControlsApi()
    window = webview.create_window(
        TITLE, html=page, js_api=api, width=450, height=500, min_size=(420, 480),
        resizable=True, frameless=True, easy_drag=False, shadow=True,
        background_color="#f5f5fa",
    )
    api._window = window

    def record_window() -> None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        class Rect(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        user32.FindWindowW.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p)
        user32.FindWindowW.restype = ctypes.c_void_p
        handle = user32.FindWindowW(None, TITLE)
        rect = Rect()
        window_pixels = [0, 0]
        dpi = 0
        if handle:
            user32.GetWindowRect.argtypes = (ctypes.c_void_p, ctypes.POINTER(Rect))
            user32.GetWindowRect.restype = ctypes.c_bool
            user32.GetDpiForWindow.argtypes = (ctypes.c_void_p,)
            user32.GetDpiForWindow.restype = ctypes.c_uint
            if user32.GetWindowRect(handle, ctypes.byref(rect)):
                window_pixels = [rect.right - rect.left, rect.bottom - rect.top]
            dpi = user32.GetDpiForWindow(handle)
        receipt = {
            "title": TITLE,
            "hwnd": int(handle or 0),
            "size": [450, 500],
            "windowPixels": window_pixels,
            "dpi": dpi,
            "mode": "mock-preview-only",
            "frameless": True,
            "nativeTitleBar": False,
            "customWindowControls": ["minimize", "close"],
            "httpServer": False,
            "deviceConnected": False,
        }
        (ROOT.parent / "review" / "ui7-compact-result.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    window.events.shown += record_window
    webview.start(gui="edgechromium", debug=False, http_server=False, private_mode=True)
