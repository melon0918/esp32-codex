"""UI-6 foreground WebView2 acceptance against an isolated mock broker.

This is an explicit desktop test: it creates a visible window, moves the mouse,
and saves real Windows screen captures. It never starts a real bridge or serial.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "mcp-server"
sys.path.insert(0, str(SERVER))
sys.path.insert(0, str(ROOT / "web-panel" / "host"))
import host
import webview
from panel.backend import PanelBackend

TITLE = host.OPERATIONS_TITLE
REVIEW = ROOT / "web-panel" / "review"
CAPTURE_SCRIPT = Path(__file__).with_name("capture_visible_ui6.ps1")
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
user32 = ctypes.WinDLL("user32", use_last_error=True)

class Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

class Rect(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(Rect)]
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(Point)]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
user32.GetDpiForWindow.argtypes = [wintypes.HWND]
user32.GetDpiForWindow.restype = wintypes.UINT
user32.WindowFromPoint.argtypes = [Point]
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.BringWindowToTop.argtypes = [wintypes.HWND]

def until(js, test, *, timeout=15):
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        try:
            last = window.evaluate_js(js)
            if test(last):
                return last
        except Exception:
            pass
        time.sleep(0.18)
    raise TimeoutError(f"JS condition: {js[:120]}; last={last!r}")

def js(expression):
    return window.evaluate_js(expression)

def hwnd():
    handle = user32.FindWindowW(None, TITLE)
    if not handle:
        raise RuntimeError("Expected foreground Windows window not found")
    return handle

def mouse_click(selector):
    """Send a real OS mouse click at the browser DOM element's visible center."""
    bounds = js("""(() => {
      const el=document.querySelector(%s);
      if(!el) return null;
      const r=el.getBoundingClientRect();
      return {x:r.left+r.width/2,y:r.top+r.height/2,w:r.width,h:r.height,
              viewWidth:innerWidth,viewHeight:innerHeight};
    })()""" % json.dumps(selector))
    if not bounds or bounds["w"] < 5 or bounds["h"] < 5:
        raise AssertionError(f"Element not usable: {selector}: {bounds}")
    if not 0 < bounds["x"] < bounds["viewWidth"] or not 0 < bounds["y"] < bounds["viewHeight"]:
        raise AssertionError(f"Element not in viewport: {selector}: {bounds}")
    handle = hwnd()
    rect = Rect()
    point = Point()
    if not user32.GetClientRect(handle, ctypes.byref(rect)):
        raise RuntimeError("GetClientRect failed")
    if not user32.ClientToScreen(handle, ctypes.byref(point)):
        raise RuntimeError("ClientToScreen failed")
    scale = (rect.right - rect.left) / bounds["viewWidth"]
    x = round(point.x + bounds["x"] * scale)
    y = round(point.y + bounds["y"] * scale)
    # Use actual Windows hit-testing; a screenshot helper can leave another
    # foreground app above the panel between two OS mouse clicks.
    user32.SetWindowPos(handle, wintypes.HWND(-1), 0, 0, 0, 0, 0x0003)
    user32.BringWindowToTop(handle)
    user32.SetForegroundWindow(handle)
    user32.SetCursorPos(x, y)
    time.sleep(0.20)
    hit = user32.WindowFromPoint(Point(x, y))
    root = user32.GetAncestor(hit, 2)
    foreground = user32.GetForegroundWindow()
    if int(root) != int(handle):
        raise AssertionError(f"Mouse hit another window: {root} != {handle}; foreground={foreground}")
    user32.mouse_event(0x0002, 0, 0, 0, None)
    time.sleep(0.09)
    user32.mouse_event(0x0004, 0, 0, 0, None)
    user32.SetWindowPos(handle, wintypes.HWND(-2), 0, 0, 0, 0, 0x0003)
    return {"selector": selector, "screen": [x, y], "scale": scale,
            "hitRoot": int(root), "foreground": int(foreground)}

def screenshot(label):
    filename = REVIEW / f"ui6-visible-{label}.png"
    ascii_path = Path(tempfile.gettempdir()) / f"esp32-ui6-visible-{os.getpid()}-{label}.png"
    proc = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(CAPTURE_SCRIPT), "-WindowHandle", str(int(hwnd())), "-Output", str(ascii_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=CREATE_NO_WINDOW, timeout=25,
    )
    if proc.returncode or not ascii_path.is_file() or ascii_path.stat().st_size < 8000:
        raise RuntimeError(f"Visible screenshot failed {label}: {proc.stdout} {proc.stderr}")
    shutil.move(str(ascii_path), str(filename))
    data = filename.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return {"file": filename.name, "bytes": len(data), "capture": proc.stdout.strip()}

def layout(label):
    data = js("""({width:innerWidth,height:innerHeight,
      documentWidth:document.documentElement.scrollWidth,
      bodyWidth:document.body.scrollWidth,
      zoom:document.documentElement.style.zoom||'100%',
      dpr:devicePixelRatio,
      modalHidden:document.getElementById('ui4-confirm').hidden})""")
    if data["documentWidth"] > data["width"] or data["bodyWidth"] > data["width"]:
        raise AssertionError(f"Horizontal overflow {label}: {data}")
    result["layouts"][label] = data
    return data

def checkpoint(label):
    result['checkpoint'] = label
    print('UI6_PHASE ' + label, flush=True)
    try:
        (REVIEW / 'ui6-visible-progress.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    except OSError:
        # File sync or concurrent reading may temporarily lock this diagnostic.
        pass


def exercise():
    try:
        checkpoint('worker-started')
        assert loaded.wait(30), "Visible WebView2 load timeout"
        checkpoint('window-loaded')
        until("document.getElementById('live-source')?.textContent.includes('MOCK') && document.getElementById('ui4-port').options.length>1", bool, timeout=25)
        result["nativeWindow"] = bool(hwnd())
        result["source"] = js("document.getElementById('live-source').textContent")
        result["port"] = js("document.getElementById('ui4-port').options[1].value")
        result["realBrokerConnected"] = api.get_snapshot()["status"]["connected"]
        result["nativeDpi"] = user32.GetDpiForWindow(hwnd())
        layout("initial")
        checkpoint('initial-layout')
        result["screenshots"].append(screenshot("main"))
        checkpoint('main-captured')
        js("document.getElementById('ui4-operations').scrollIntoView({block:'start'});true")
        time.sleep(0.35)
        result["screenshots"].append(screenshot("actions"))
        result["clicks"].append(mouse_click('[data-ui4-action="download"]'))
        until("!document.getElementById('ui4-confirm').hidden", bool)
        result["modalTitle"] = js("document.getElementById('ui4-confirm-title').textContent")
        result["modalTarget"] = js("document.getElementById('ui4-confirm-target').textContent")
        result["modalImpact"] = js("document.getElementById('ui4-confirm-impact').textContent")
        assert js('document.querySelector(\'[data-ui4-action="download"]\').disabled'), "Busy guard missing"
        result["screenshots"].append(screenshot("confirm"))
        result["clicks"].append(mouse_click('#ui4-confirm-reject'))
        until("document.getElementById('ui4-confirm').hidden && !document.querySelector('[data-ui4-action=\"download\"]').disabled", bool)
        result["cancelMessage"] = js("document.getElementById('ui4-action-status').textContent")
        assert "未执行" in result["cancelMessage"]
        result["clicks"].append(mouse_click('[data-ui4-action="download"]'))
        until("!document.getElementById('ui4-confirm').hidden", bool)
        result["clicks"].append(mouse_click("#ui4-confirm-approve"))
        until("document.getElementById('ui4-confirm').hidden && !document.querySelector('[data-ui4-action=\"download\"]').disabled", bool)
        result["approvedMessage"] = js("document.getElementById('ui4-action-status').textContent")
        assert "模拟下载完成" in result["approvedMessage"]
        js("document.getElementById('ui4-repl').value='';document.getElementById('ui4-send').click();true")
        until("document.getElementById('ui4-action-status').classList.contains('ui4-error')", bool)
        js("document.getElementById('ui4-action-status').scrollIntoView({block:'center'});true")
        result["errorMessage"] = js("document.getElementById('ui4-action-status').textContent")
        assert "REPL" in result["errorMessage"] or "失败" in result["errorMessage"]
        result["screenshots"].append(screenshot("error"))
        for view, target in [("workspace","ui4-operations"),("console","live-console"),("overview","live-snapshot")]:
            js("document.querySelector('.rail-link[data-view=%s]').click();true" % json.dumps(view))
            assert js("document.getElementById('crumb-current').textContent") == {"workspace":"工作区","console":"控制台","overview":"工作台"}[view]
            assert not js("document.getElementById('prototype-demo').open")
            result["navigation"].append(view)
            if view=="console":
                result["screenshots"].append(screenshot("console"))
        window.resize(700,700)
        time.sleep(.35)
        result["layouts"]["700x700"]=layout("700x700")
        result["screenshots"].append(screenshot("700x700"))
        window.resize(900,820)
        time.sleep(.35)
        for level in [1.25,1.50]:
            js("document.documentElement.style.zoom=%s;true" % json.dumps(str(int(level*100))+"%"))
            time.sleep(.45)
            label=f"zoom-{int(level*100)}"
            layout(label)
            result["screenshots"].append(screenshot(label))
        js("document.documentElement.style.zoom='100%';true")
        result["success"] = True
        checkpoint('success')
    except Exception as e:
        result["failure"] = repr(e)
        result["traceback"] = traceback.format_exc()
        checkpoint('failure')
    finally:
        print(json.dumps({'success':result['success'],'phase':result.get('checkpoint'),
                          'failure':result.get('failure'),'images':len(result['screenshots']),
                          'clicks':len(result['clicks'])},ensure_ascii=False),flush=True)
        REVIEW.mkdir(parents=True, exist_ok=True)
        (REVIEW / "ui6-visible-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        try:
            checkpoint('closing-window')
            window.destroy()
        except Exception:
            pass

if __name__ == "__main__":
    REVIEW.mkdir(parents=True, exist_ok=True)
    os.environ["ESP32_CODEX_TEST_ISOLATION_SEED"] = "ui6-visible-" + secrets.token_hex(6)
    result = {"success":False,"screenshots":[],"clicks":[],"layouts":{},"navigation":[]}
    loaded = threading.Event()
    with tempfile.TemporaryDirectory(prefix="esp32-ui6-visible-") as temp:
        workspace = Path(temp) / "workspace"
        (workspace / "esp32-ide").mkdir(parents=True)
        (workspace / "esp32-ide" / "board.json").write_text(
            json.dumps({"type":"generic","label":"UI6 MOCK","entry":"/main.py"}), encoding="utf-8"
        )
        (workspace / "main.py").write_text("print('ui6 mock')\n", encoding="utf-8")
        backend = PanelBackend.default_mock(workspace=str(workspace), profile="generic", scenario="fileops")
        api = host.PanelOperationsApi(backend, window=None, confirmation_timeout=30)
        geometry=host.operations_window_geometry()
        result['expectedGeometry']=geometry
        window = webview.create_window(
            TITLE, html=host.load_operations_page(), js_api=api,
            width=geometry['width'],height=geometry['height'],
            x=geometry['x'],y=geometry['y'],
            min_size=(geometry['minWidth'],geometry['minHeight']),
            background_color="#f6f6fa",hidden=False, focus=True
        )
        api._window = window
        window.events.loaded += lambda: (print('UI6_LOADED_EVENT',flush=True),loaded.set())
        print('UI6_WINDOW_CREATED',flush=True)
        window.events.closed += api._on_window_closed
        try:
            print('UI6_WEBVIEW_START',flush=True)
            webview.start(exercise,debug=False,http_server=False,private_mode=True)
            print('UI6_WEBVIEW_RETURNED',flush=True)
        finally:
            api._on_window_closed()
            backend.close()
    sys.exit(0 if result["success"] else 1)
