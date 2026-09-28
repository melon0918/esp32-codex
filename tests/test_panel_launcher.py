"""UI-9 launcher default and package checks; never open a visible window or device."""

from __future__ import annotations

import contextlib
import asyncio
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SERVER_DIR = ROOT / "mcp-server"
sys.path.insert(0, str(SERVER_DIR))

from panel import launcher


class LauncherProbeTests(unittest.TestCase):
    def test_default_probe_does_not_create_tk_or_connect_a_broker(self):
        with patch("tkinter.Tk", side_effect=AssertionError("Tk must never be opened by --probe")):
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                self.assertEqual(launcher.main(["--probe"]), 0)
        payload = json.loads(stream.getvalue())
        self.assertEqual(payload["mode"], "mock")
        self.assertEqual(payload["ui"], "web")
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["autoConnect"])
        self.assertFalse(payload["guiCreated"])
        self.assertTrue(payload["singleton"])

    def test_web_opt_in_probe_and_real_mode_acceptance(self):
        stream = io.StringIO()
        with patch("tkinter.Tk", side_effect=AssertionError("probe cannot create GUI")):
            with contextlib.redirect_stdout(stream):
                self.assertEqual(launcher.main(["--probe", "--ui", "web"]), 0)
        payload = json.loads(stream.getvalue())
        self.assertEqual("web", payload["ui"])
        self.assertFalse(payload["guiCreated"])
        self.assertFalse(payload["autoConnect"])
        with tempfile.TemporaryDirectory(prefix="esp32-launcher-bridge-") as temp:
            bridge_script = Path(temp) / "bridge.py"
            bridge_script.write_text("# canned path fixture; never executed\n", encoding="utf-8")
            with self.assertRaises(SystemExit):
                launcher.parse_args(["--ui", "web", "--mode", "bridge", "--probe",
                                     "--workspace", str(ROOT)])
            args = launcher.parse_args(["--ui", "web", "--mode", "bridge", "--probe",
                                        "--workspace", str(ROOT), "--bridge-script", str(bridge_script),
                                        "--enable-control-tools"])
        self.assertEqual("bridge", args.mode)
        with self.assertRaises(ValueError):
            launcher.launch_panel_process(ui="invalid")

    def test_real_mode_is_opt_in_and_write_requires_control(self):
        with self.assertRaises(SystemExit):
            launcher.parse_args(["--mode", "bridge", "--probe"])
        with self.assertRaises(SystemExit):
            launcher.parse_args(["--mode", "mock", "--enable-control-tools", "--probe"])

    @unittest.skipUnless(os.name == "nt", "Windows mutex and pythonw launch")
    def test_second_mutex_acquisition_does_not_create_another_window(self):
        from broker.identity import current_user_sid
        import secrets
        import ctypes
        from ctypes import wintypes

        sid = current_user_sid() + "-panel-test-" + secrets.token_hex(8)
        with patch.object(launcher, "current_user_sid", return_value=sid):
            first, existing = launcher.acquire_panel_mutex()
            self.assertFalse(existing)
            self.assertIsNotNone(first)
            try:
                second, existing = launcher.acquire_panel_mutex()
                self.assertTrue(existing)
                self.assertIsNone(second)
            finally:
                kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel.ReleaseMutex.argtypes = (wintypes.HANDLE,)
                kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
                kernel.ReleaseMutex(first)
                kernel.CloseHandle(first)

    @unittest.skipUnless(os.name == "nt", "Windows singleton focus")
    def test_repeat_launch_focuses_existing_window_without_creating_gui(self):
        with patch.object(launcher, "acquire_panel_mutex", return_value=(None, True)), \
             patch.object(launcher, "focus_existing_panel", return_value=True) as focus, \
             patch("tkinter.Tk", side_effect=AssertionError("no second Tk")):
            self.assertEqual(0, launcher.main(["--ui", "web"]))
        focus.assert_called_once_with()

    @unittest.skipUnless(os.name == "nt", "Windows pythonw launch")
    def test_mcp_spawn_requests_consoleless_single_instance_without_starting_it(self):
        fake = Mock(pid=321)
        with patch("subprocess.Popen", return_value=fake) as spawned:
            result = launcher.launch_panel_process(mode="mock", profile="generic",
                                                   mock_scenario="fileops")
        self.assertTrue(result["ok"])
        self.assertTrue(result["launchRequested"])
        self.assertFalse(result["autoConnect"])
        self.assertTrue(result["simulated"])
        argv = spawned.call_args.args[0]
        self.assertIn("pythonw.exe", argv[0].lower())
        self.assertIn("panel.launcher", argv)
        self.assertEqual(argv[argv.index("--mode") + 1], "mock")
        self.assertNotIn("--bridge-script", argv)
        self.assertTrue(spawned.call_args.kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW)
        self.assertEqual("web", result["ui"])
        with patch("subprocess.Popen", return_value=fake) as tk_spawn:
            tk_result = launcher.launch_panel_process(ui="tk")
        self.assertEqual("tk", tk_result["ui"])
        tk_argv = tk_spawn.call_args.args[0]
        self.assertEqual("tk", tk_argv[tk_argv.index("--ui") + 1])
        with patch("subprocess.Popen", return_value=fake) as web_spawn:
            web_result = launcher.launch_panel_process(ui="web")
        self.assertEqual("web", web_result["ui"])
        web_argv = web_spawn.call_args.args[0]
        self.assertEqual("web", web_argv[web_argv.index("--ui") + 1])

    @unittest.skipUnless(os.name == "nt", "Windows pythonw launch")
    def test_mcp_spawn_prefers_the_package_venv_pythonw(self):
        with tempfile.TemporaryDirectory(prefix="esp32-panel-pythonw-") as temp:
            package_root = Path(temp)
            package_pythonw = package_root / ".venv" / "Scripts" / "pythonw.exe"
            package_pythonw.parent.mkdir(parents=True)
            package_pythonw.write_bytes(b"test executable path only")
            fake_launcher = package_root / "mcp-server" / "panel" / "launcher.py"
            fake_launcher.parent.mkdir(parents=True)
            fake_launcher.write_text("", encoding="utf-8")
            with patch.object(launcher, "__file__", str(fake_launcher)), \
                 patch("subprocess.Popen", return_value=Mock(pid=321)) as spawned:
                launcher.launch_panel_process(mode="mock", profile="generic")
            self.assertEqual(str(package_pythonw), spawned.call_args.args[0][0])
            self.assertEqual(str(package_root / "mcp-server"), spawned.call_args.kwargs["cwd"])


class PackagingContractTests(unittest.TestCase):
    def test_staged_build_copies_panel_without_touching_existing_installation(self):
        with tempfile.TemporaryDirectory(prefix="esp32-codex-stage8-") as temp:
            target = Path(temp) / "plugin"
            from shutil import copytree, ignore_patterns
            copytree(ROOT, target, ignore=ignore_patterns(".venv", "__pycache__", "*.pyc"))
            manifest = json.loads((target / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
            self.assertEqual("esp32-codex", manifest["name"])
            self.assertTrue((target / "mcp-server" / "panel" / "launcher.py").is_file())
            self.assertTrue((target / "mcp-server" / "panel" / "actions.py").is_file())
            self.assertTrue((target / "tests" / "test_panel_info.py").is_file())
            self.assertTrue((target / "web-panel" / "host" / "host.py").is_file())
            probe = subprocess.run(
                [sys.executable, "-B", "-m", "panel.launcher", "--probe"],
                cwd=target / "mcp-server", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            self.assertEqual(probe.returncode, 0, probe.stderr)
            payload = json.loads(probe.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual("web", payload["ui"])
            self.assertFalse(payload["autoConnect"])
            self.assertFalse(payload["guiCreated"])
            async def discover_packaged_tools():
                from mcp import ClientSession, StdioServerParameters
                from mcp.client.stdio import stdio_client

                params = StdioServerParameters(
                    command=sys.executable,
                    args=["-B", "-X", "utf8", str(target / "mcp-server" / "server.py"),
                          "--mode", "mock", "--mock-scenario", "fileops",
                          "--test-broker-seed", "ac07packagesmoke"],
                    cwd=str(target),
                )
                async with stdio_client(params) as (read_stream, write_stream):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        return {tool.name for tool in (await session.list_tools()).tools}

            names = asyncio.run(discover_packaged_tools())
            expected = {
                "esp32_workspace_list", "esp32_workspace_info", "esp32_workspace_current",
                "esp32_workspace_select", "esp32_workspace_claim", "esp32_policy_get",
                "esp32_policy_set", "esp32_confirmation_status", "esp32_confirmation_cancel",
                "esp32_open_panel", "esp32_panel_status", "esp32_panel_control",
                "esp32_capabilities", "esp32_snapshot", "esp32_serial_ports", "esp32_status",
                "esp32_console_read", "esp32_board_files_list", "esp32_board_file_read",
                "esp32_mock_policy_get", "esp32_mock_connect", "esp32_mock_disconnect",
                "esp32_mock_stop", "esp32_mock_run", "esp32_mock_interrupt", "esp32_mock_repl_send",
            }
            self.assertTrue(expected.issubset(names), sorted(expected - names))

    def test_install_script_declares_safe_user_only_start_menu_entry(self):
        source_path = ROOT / "scripts" / "install_personal_plugin.ps1"
        source = source_path.read_text(encoding="utf-8-sig")
        self.assertTrue(source_path.read_bytes().startswith(b"\xef\xbb\xbf"))
        self.assertIn("GetFolderPath('Programs')", source)
        self.assertIn("CreateShortcut", source)
        self.assertIn("pythonw.exe", source)
        self.assertIn("--mode bridge --workspace", source)
        self.assertIn("requirements-bridge.txt", source)
        self.assertIn("Start Menu shortcut already exists", source)
        self.assertIn("web-panel\\host\\requirements.txt", source)
        self.assertIn("ESP32 Codex 控制面板（Tk 回退）.lnk", source)
        self.assertIn("ESP32 Codex 网页面板 MOCK.lnk", source)
        self.assertIn("--ui web --mode bridge", source)
        self.assertIn("--ui web --mode mock", source)
        self.assertIn("--ui tk --mode mock", source)
        self.assertIn("$shortcut.WorkingDirectory = Join-Path $installRoot 'mcp-server'", source)
        self.assertIn("function Invoke-LocalProcess", source)
        self.assertIn("Start-Process -FilePath $FilePath", source)
        self.assertNotIn("Start-Process -FilePath $pythonw", source)


class SharedWorkspacePanelContextTests(unittest.TestCase):
    def test_panel_launch_uses_broker_workspace_instead_of_mcp_startup_arguments(self):
        from server import Esp32McpTools

        client = Mock(workspace=r"D:\startup-workspace", profile="hiwonder")
        client.workspace_current.return_value = {
            "workspacePath": r"D:\shared\quad-workspace",
            "profile": "generic",
            "entry": "/main.py",
            "control_epoch": 17,
        }
        tools = Esp32McpTools(
            client, entry="/corex.py", allow_mock_elicitation=False,
        )
        self.assertEqual(
            (r"D:\shared\quad-workspace", "generic"),
            tools._current_panel_launch_context(),
        )
        client.workspace_current.assert_called_once_with()


class McpPanelEntryTests(unittest.IsolatedAsyncioTestCase):
    async def test_mcp_open_panel_is_discoverable_but_test_fixture_never_launches_tk(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=sys.executable,
            args=["-B", "-X", "utf8", str(SERVER_DIR / "server.py"),
                  "--mode", "mock", "--mock-scenario", "fileops",
                  "--test-broker-seed", "panelstep8"],
            cwd=str(ROOT),
        )
        # The server's test seed rejects the UI launch without ever creating Tk.
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                available_tools = (await session.list_tools()).tools
                names = {tool.name for tool in available_tools}
                self.assertIn("esp32_open_panel", names)
                open_panel_tool = next(tool for tool in available_tools if tool.name == "esp32_open_panel")
                self.assertEqual("web", open_panel_tool.inputSchema["properties"]["ui"]["default"])
                response = await session.call_tool("esp32_open_panel", {})
                result = response.structuredContent
                if not isinstance(result, dict):
                    result = json.loads(response.content[0].text)
                self.assertFalse(result["ok"])
                self.assertIn("test fixtures", result["error"])
                web_response = await session.call_tool("esp32_open_panel", {"ui": "web"})
                web_result = web_response.structuredContent
                if not isinstance(web_result, dict):
                    web_result = json.loads(web_response.content[0].text)
                self.assertFalse(web_result["ok"])
                self.assertIn("test fixtures", web_result["error"])
                invalid_response = await session.call_tool("esp32_open_panel", {"ui": "invalid"})
                invalid_result = invalid_response.structuredContent
                if not isinstance(invalid_result, dict):
                    invalid_result = json.loads(invalid_response.content[0].text)
                self.assertFalse(invalid_result["ok"])


if __name__ == "__main__":
    unittest.main()
