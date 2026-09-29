"""Single-instance Windows ESP32 panel launcher; no automatic serial connection."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path

from bridge_client import BridgeClient
from broker.adapter import BrokerBridgeClient
from broker.client import BrokerClient
from broker.identity import current_user_sid, user_key

from .backend import PanelBackend


PANEL_TITLE = "ESP32 Codex 控制面板"
WEB_PANEL_TITLE = "ESP32 工作台"
ERROR_ALREADY_EXISTS = 183


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("mock", "bridge"), default="mock")
    parser.add_argument("--ui", choices=("tk", "web"), default="web", help="compact web panel; Tk remains available")
    parser.add_argument("--workspace")
    parser.add_argument("--profile", choices=("generic", "hiwonder"))
    parser.add_argument("--mock-scenario", choices=("readonly", "normal", "busy", "control",
                                                    "fileops", "fileops-no-capability"), default="fileops")
    parser.add_argument("--bridge-script")
    parser.add_argument("--enable-control-tools", action="store_true")
    parser.add_argument("--enable-write-tools", action="store_true")
    parser.add_argument("--probe", action="store_true", help="Validate startup without creating Tk or connecting a broker")
    args = parser.parse_args(argv)
    if args.workspace:
        from workspaces import absolute_directory
        args.workspace = str(absolute_directory(args.workspace))
    if not args.profile:
        args.profile = "generic"
        if args.workspace:
            from workspaces import detect_workspace
            detected = detect_workspace(args.workspace)
            if detected.get("detected"):
                args.profile = detected["profile"]
    if args.mode == "bridge":
        if not args.workspace or not args.bridge_script:
            parser.error("real bridge needs explicit --workspace and --bridge-script")
        bridge_path = Path(args.bridge_script)
        if not bridge_path.is_absolute() or not bridge_path.is_file():
            parser.error("--bridge-script must be an existing absolute file")
        args.bridge_script = str(bridge_path.resolve())
        if args.enable_write_tools and not args.enable_control_tools:
            parser.error("real write tools require --enable-control-tools")
    elif args.bridge_script or args.enable_control_tools or args.enable_write_tools:
        parser.error("real-mode flags are only accepted with --mode bridge")
    return args


def make_backend(args: argparse.Namespace) -> PanelBackend:
    mock_script = Path(__file__).resolve().parents[2] / "tests" / "mock_bridge.py"
    client = BridgeClient(
        mode=args.mode,
        workspace=args.workspace,
        profile=args.profile,
        bridge_script=args.bridge_script,
        mock_script=str(mock_script),
        mock_scenario=args.mock_scenario,
        allow_real_controls=args.mode == "bridge" and args.enable_control_tools,
        allow_real_writes=args.mode == "bridge" and args.enable_write_tools,
    )
    # The facade adopts the exact backend_config at first use; it never connects
    # a physical serial port without an explicit user action in the panel.
    return PanelBackend(BrokerBridgeClient(client, BrokerClient()))


def acquire_panel_mutex() -> tuple[object | None, bool]:
    """One panel window per Windows user, independent of the broker's mutex."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    name = rf"Global\Esp32CodexPanel-{user_key(current_user_sid())}"
    handle = kernel.CreateMutexW(None, True, name)
    if not handle:
        raise OSError(ctypes.get_last_error(), "could not acquire panel mutex")
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel.CloseHandle(handle)
        return None, True
    return handle, False


def focus_existing_panel() -> bool:
    """Best-effort focus for a user-requested repeat launch; never starts a second GUI."""
    kernel = ctypes.WinDLL("user32", use_last_error=True)
    kernel.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
    kernel.FindWindowW.restype = wintypes.HWND
    kernel.IsIconic.argtypes = (wintypes.HWND,)
    kernel.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
    kernel.SetForegroundWindow.argtypes = (wintypes.HWND,)
    for title in (WEB_PANEL_TITLE, PANEL_TITLE):
        handle = kernel.FindWindowW(None, title)
        if handle:
            if kernel.IsIconic(handle):
                kernel.ShowWindow(handle, 9)  # SW_RESTORE
            return bool(kernel.SetForegroundWindow(handle))
    return False


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.probe:
        print(json.dumps({
            "ok": True, "mode": args.mode, "autoConnect": False,
            "guiCreated": False, "singleton": True, "ui": args.ui,
        }))
        return 0
    if os.name != "nt":
        raise RuntimeError("the ESP32 desktop panel requires Windows")
    handle, already_running = acquire_panel_mutex()
    if already_running:
        focus_existing_panel()
        return 0  # Existing per-user window retains the only panel mutex.
    backend: PanelBackend | None = None
    try:
        if args.ui == "web":
            import importlib.util
            host_path = Path(__file__).resolve().parents[2] / "web-panel" / "host" / "host.py"
            if not host_path.is_file():
                raise RuntimeError("packaged web panel host is missing")
            spec = importlib.util.spec_from_file_location("esp32_codex_web_panel_host", host_path)
            if spec is None or spec.loader is None:
                raise RuntimeError("cannot load packaged web panel host")
            web_host = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(web_host)
            backend = make_backend(args)
            return web_host.run_operations(args.workspace, args.profile, args.mock_scenario,
                                           backend=backend, mode=args.mode)
        import tkinter as tk
        from .view import create_information_panel

        root = tk.Tk()
        root.withdraw()  # Avoid flashing a blank window while initializing.
        backend = make_backend(args)
        create_information_panel(root, backend)
        root.deiconify()  # Only explicit user launches reach this branch.
        root.mainloop()
        return 0
    finally:
        if backend is not None:
            backend.close()
        if handle is not None:
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.ReleaseMutex.argtypes = (wintypes.HANDLE,)
            kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
            kernel.ReleaseMutex(handle)
            kernel.CloseHandle(handle)


def launch_panel_process(
    *, mode: str = "mock", workspace: str | None = None, ui: str = "web",
    profile: str = "generic", mock_scenario: str = "fileops",
    bridge_script: str | None = None, allow_real_controls: bool = False,
    allow_real_writes: bool = False,
) -> dict[str, object]:
    """Request one GUI launch without ever starting a console or serial connection."""
    import subprocess
    import sys

    if os.name != "nt":
        raise RuntimeError("the panel launcher requires Windows")
    if mode not in {"mock", "bridge"}:
        raise ValueError("mode must be mock or bridge")
    if ui not in {"tk", "web"}:
        raise ValueError("ui must be tk or web")
    # MCP servers can be hosted by Codex's base runtime even when the plugin
    # itself was installed into a private venv.  Launch with the package venv
    # first so the child has the same pywebview and bridge dependencies as the
    # MCP server; only use the host interpreter when this is a source checkout
    # without a package-local venv.
    package_root = Path(__file__).resolve().parents[2]
    package_pythonw = package_root / ".venv" / "Scripts" / "pythonw.exe"
    host_pythonw = Path(sys.executable).with_name("pythonw.exe")
    pythonw = package_pythonw if package_pythonw.is_file() else host_pythonw
    if not pythonw.is_file():
        raise RuntimeError("pythonw.exe is required for hidden-console panel startup")
    arguments = [
        str(pythonw), "-B", "-X", "utf8", "-m", "panel.launcher",
        "--mode", mode, "--ui", ui, "--profile", profile, "--mock-scenario", mock_scenario,
    ]
    if workspace:
        arguments.extend(["--workspace", workspace])
    if mode == "bridge":
        if not workspace or not bridge_script:
            raise RuntimeError("real panel mode needs existing explicit workspace and bridge script")
        arguments.extend(["--bridge-script", bridge_script])
        if allow_real_controls:
            arguments.append("--enable-control-tools")
        if allow_real_writes:
            if not allow_real_controls:
                raise RuntimeError("real write tools require real control tools")
            arguments.append("--enable-write-tools")
    elif bridge_script:
        raise RuntimeError("real-mode arguments cannot be used in mock mode")
    process = subprocess.Popen(
        arguments,
        cwd=str(Path(__file__).resolve().parents[1]),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
    )
    return {
        "ok": True, "launchRequested": True, "pid": process.pid,
        "simulated": mode == "mock", "autoConnect": False,
        "singleInstance": True, "ui": ui,
    }


if __name__ == "__main__":
    raise SystemExit(main())
