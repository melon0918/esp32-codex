"""MCP client integration checks using only the bundled no-hardware bridge."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVER_SCRIPT = PROJECT_ROOT / "mcp-server" / "server.py"
sys.path.insert(0, str(PROJECT_ROOT / "mcp-server"))
from bridge_client import BridgeClient, BridgeFailure
from workspaces import WorkspaceError, detect_workspace, list_workspaces
from server import Esp32McpTools, _sanitize_output


@asynccontextmanager
async def open_mcp_session(
    workspace: Path,
    scenario: str = "readonly",
    *,
    mode: str = "mock",
    bridge_script: Path | None = None,
):
    args = [
        "-X",
        "utf8",
        str(SERVER_SCRIPT),
        "--mode",
        mode,
        "--mock-scenario",
        scenario,
        "--workspace",
        str(workspace),
        "--test-broker-seed", "UnitTest",
    ]
    if bridge_script is not None:
        args.extend(["--bridge-script", str(bridge_script)])
    parameters = StdioServerParameters(
        command=sys.executable,
        args=args,
        cwd=str(PROJECT_ROOT),
    )
    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            yield session


async def result_data(result: Any) -> dict[str, Any]:
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured
    for block in getattr(result, "content", []):
        text = getattr(block, "text", None)
        if isinstance(text, str):
            try:
                value = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
    raise AssertionError(f"MCP tool did not return a JSON object: {result!r}")


class ReadOnlyMcpTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="esp32-mcp-")
        # Keep discovery's parent private; the shared Windows Temp can exceed
        # MAX_CANDIDATES and hide this fixture even when the scanner is correct.
        self.root = Path(self.temp.name) / "workspace"
        (self.root / "esp32-ide").mkdir(parents=True)
        (self.root / "esp32-ide" / "board.json").write_text(
            json.dumps({"type": "generic", "label": "模拟通用板", "entry": "/main.py"}),
            encoding="utf-8",
        )
        (self.root / "main.py").write_text("print('fixture')\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    async def test_mcp_sdk_client_discovers_phase2_tools(self) -> None:
        async with open_mcp_session(self.root) as session:
            response = await session.list_tools()
        names = {tool.name for tool in response.tools}
        self.assertLessEqual(
            {
                "esp32_workspace_list",
                "esp32_workspace_info",
                "esp32_capabilities",
                "esp32_snapshot",
                "esp32_panel_status",
                "esp32_panel_control",
                "esp32_serial_ports",
                "esp32_status",
                "esp32_console_read",
                "esp32_board_files_list",
                "esp32_board_file_read",
            },
            names,
        )

    async def test_mock_bridge_returns_explicitly_simulated_read_only_data(self) -> None:
        async with open_mcp_session(self.root) as session:
            ports = await result_data(await session.call_tool("esp32_serial_ports", {}))
            status = await result_data(await session.call_tool("esp32_status", {}))
            console = await result_data(await session.call_tool("esp32_console_read", {}))
            files = await result_data(await session.call_tool("esp32_board_files_list", {}))
            file_data = await result_data(
                await session.call_tool("esp32_board_file_read", {"path": "/main.py"})
            )

        for result in (ports, status, console, files, file_data):
            self.assertEqual(result["source"], "mock_bridge")
            self.assertTrue(result["simulated"])
        self.assertEqual(ports["ports"][0]["device"], "MOCK0")
        self.assertTrue(status["connected"])
        self.assertIn("不是实体设备", status["boardInfo"])
        self.assertIn("不是实体设备输出", console["text"])
        self.assertEqual(files["files"][0]["name"], "main.py")
        self.assertEqual(file_data["content"], "test")

    async def test_capabilities_and_snapshot_report_flags_and_consistent_mock_state(self) -> None:
        async with open_mcp_session(self.root, scenario="control") as session:
            capabilities = await result_data(await session.call_tool("esp32_capabilities", {}))
            snapshot = await result_data(await session.call_tool("esp32_snapshot", {}))

        self.assertFalse(capabilities["toolGroups"]["control"]["enabled"])
        self.assertFalse(capabilities["toolGroups"]["write"]["registered"])
        self.assertTrue(capabilities["runtimeGates"]["strictBackupRequiredBeforeMutation"])
        self.assertEqual(capabilities["source"], "mock_bridge")
        self.assertTrue(snapshot["ok"])
        self.assertEqual(snapshot["control_epoch"], snapshot["state"]["workspace"]["control_epoch"])
        self.assertEqual(snapshot["source"], "mock_bridge")
        self.assertTrue(snapshot["simulated"])

    async def test_sensitive_board_config_is_withheld_and_json_text_is_redacted(self) -> None:
        async with open_mcp_session(self.root, scenario="fileops") as session:
            result = await result_data(await session.call_tool(
                "esp32_board_file_read", {"path": "/wifi.json"}
            ))

        self.assertTrue(result["redacted"])
        self.assertEqual(result["content"], "敏感配置文件内容已隐藏。")
        self.assertNotIn("fixture-only-ssid", result["content"])
        self.assertNotIn("fixture-only-password", result["content"])

        sanitized, fields = _sanitize_output({
            "content": '{"api_key":"fixture-key","nested":{"token":"fixture-token"}}',
        })
        self.assertNotIn("fixture-key", sanitized["content"])
        self.assertNotIn("fixture-token", sanitized["content"])
        self.assertEqual(set(fields), {"api_key", "token"})

    def test_unknown_bridge_outcome_is_not_marked_retryable(self) -> None:
        client = BridgeClient(
            mode="mock",
            workspace=str(self.root),
            profile="generic",
            bridge_script=None,
            mock_script=str(PROJECT_ROOT / "tests" / "mock_bridge.py"),
        )
        tools = Esp32McpTools(client, entry="/main.py", allow_mock_elicitation=False)
        try:
            result = tools._bridge_error(BridgeFailure(
                "broker request transport failed; lease was released"
            ))
        finally:
            tools.close()

        self.assertEqual(result["errorCode"], "outcome_unknown")
        self.assertFalse(result["retryable"])
        self.assertIn("不要重放", result["error"])

    async def test_two_mcp_clients_share_broker_read_only_results(self) -> None:
        async with open_mcp_session(self.root, scenario="fileops") as first:
            async with open_mcp_session(self.root, scenario="fileops") as second:
                first_status = await result_data(await first.call_tool("esp32_status", {}))
                second_status = await result_data(await second.call_tool("esp32_status", {}))
                first_ports = await result_data(await first.call_tool("esp32_serial_ports", {}))
                second_ports = await result_data(await second.call_tool("esp32_serial_ports", {}))
                first_console = await result_data(await first.call_tool("esp32_console_read", {}))
                second_console = await result_data(await second.call_tool("esp32_console_read", {}))

        self.assertTrue(first_status["connected"])
        self.assertEqual(first_status["workspace"], str(self.root))
        self.assertEqual(first_status, second_status)
        self.assertEqual(first_ports, second_ports)
        self.assertEqual(first_console["text"], second_console["text"])
        self.assertEqual(first_console["cursor"], second_console["cursor"])
        for result in (first_status, first_ports, first_console):
            self.assertEqual(result["source"], "mock_bridge")
            self.assertTrue(result["simulated"])

    async def test_workspace_detection_reads_board_json_without_claiming(self) -> None:
        async with open_mcp_session(self.root) as session:
            listed = await result_data(
                await session.call_tool("esp32_workspace_list", {"root": str(self.root.parent)})
            )
            info = await result_data(
                await session.call_tool("esp32_workspace_info", {"path": str(self.root)})
            )

        self.assertTrue(any(item["workspacePath"] == str(self.root) for item in listed["workspaces"]))
        self.assertEqual(info["profile"], "generic")
        self.assertEqual(info["profileLabel"], "模拟通用板")
        self.assertEqual(info["entry"], "/main.py")
        self.assertEqual(info["source"], "local_workspace")
        self.assertFalse(info["simulated"])

    async def test_workspace_current_is_shared_readonly_and_does_not_start_bridge(self) -> None:
        async with open_mcp_session(self.root, scenario="control") as session:
            state = await result_data(await session.call_tool("esp32_workspace_current", {}))
        self.assertEqual(state["workspacePath"], str(self.root.resolve()))
        self.assertEqual(state["info"]["entry"], "/main.py")
        self.assertEqual(state["source"], "broker_workspace")
        self.assertTrue(state["simulated"])

    async def test_board_path_traversal_is_rejected_before_bridge_call(self) -> None:
        async with open_mcp_session(self.root) as session:
            data = await result_data(
                await session.call_tool("esp32_board_file_read", {"path": "/../secret.py"})
            )
        self.assertFalse(data["ok"])
        self.assertIn("不能包含", data["error"])
        self.assertEqual(data["source"], "mock_bridge")

    async def test_disconnected_bridge_explains_dsh_without_disconnecting_it(self) -> None:
        async with open_mcp_session(self.root, scenario="normal") as session:
            status = await result_data(await session.call_tool("esp32_status", {}))
            files = await result_data(await session.call_tool("esp32_board_files_list", {}))
        self.assertFalse(status["connected"])
        self.assertFalse(files["ok"])
        self.assertIn("DSH", files["error"])
        self.assertIn("不会结束或断开其他进程", files["error"])

    async def test_access_denied_suggests_manual_dsh_release(self) -> None:
        async with open_mcp_session(self.root, scenario="busy") as session:
            ports = await result_data(await session.call_tool("esp32_serial_ports", {}))
        self.assertFalse(ports["ok"])
        self.assertTrue(ports["simulated"])
        self.assertIn("COM9", ports["error"])
        self.assertIn("DSH", ports["error"])
        self.assertIn("不会结束或断开其他进程", ports["error"])

    async def test_real_bridge_file_tools_are_blocked_without_starting_bridge(self) -> None:
        sentinel = self.root / "bridge-started.txt"
        bridge_script = self.root / "bridge-that-must-not-run.py"
        bridge_script.write_text(
            f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('started', encoding='utf-8')\n",
            encoding="utf-8",
        )
        async with open_mcp_session(
            self.root,
            mode="bridge",
            bridge_script=bridge_script,
        ) as session:
            files = await result_data(await session.call_tool("esp32_board_files_list", {}))
            file_data = await result_data(
                await session.call_tool("esp32_board_file_read", {"path": "/main.py"})
            )

        for result in (files, file_data):
            self.assertFalse(result["ok"])
            self.assertFalse(result["simulated"])
            self.assertIn("中断程序", result["error"])
        self.assertEqual(files["source"], "bridge_process")
        self.assertFalse(sentinel.exists())


class WorkspaceListTests(unittest.TestCase):
    def test_inaccessible_candidate_is_skipped_and_readable_workspace_is_kept(self) -> None:
        with tempfile.TemporaryDirectory(prefix="esp32-workspace-list-") as raw_root:
            root = Path(raw_root)
            inaccessible = root / "inaccessible"
            inaccessible.mkdir()
            readable = root / "readable"
            (readable / "esp32-ide").mkdir(parents=True)
            (readable / "esp32-ide" / "board.json").write_text(
                json.dumps({"type": "generic", "label": "可读工作区", "entry": "/main.py"}),
                encoding="utf-8",
            )

            def detect_with_permission_error(candidate: str | Path) -> dict[str, Any]:
                if Path(candidate).name == "inaccessible":
                    raise WorkspaceError("模拟访问拒绝")
                return detect_workspace(candidate)

            with patch("workspaces.detect_workspace", side_effect=detect_with_permission_error):
                result = list_workspaces(str(root))

        self.assertTrue(any(item["workspacePath"] == str(readable) for item in result["workspaces"]))
        self.assertEqual(result["skippedCandidates"], 1)


if __name__ == "__main__":
    unittest.main()
