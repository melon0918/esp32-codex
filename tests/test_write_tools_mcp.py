"""Stage 3b write/PID MCP integration checks using no-hardware fixtures."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVER_SCRIPT = PROJECT_ROOT / "mcp-server" / "server.py"
sys.path.insert(0, str(PROJECT_ROOT / "mcp-server"))
from bridge_client import BridgeClient, BridgeFailure
from server import Esp32McpTools


WRITE_TOOLS = {
    "esp32_download",
    "esp32_board_file_write",
    "esp32_board_file_delete",
    "esp32_pid",
}


@asynccontextmanager
async def open_fileops_session(workspace: Path, *, enabled: bool = True, scenario: str = "fileops"):
    args = [
        "-X", "utf8", str(SERVER_SCRIPT),
        "--mode", "mock",
        "--mock-scenario", scenario,
        "--workspace", str(workspace),
        "--test-broker-seed", "UnitTest",
    ]
    if enabled:
        args.append("--enable-write-tools")
    parameters = StdioServerParameters(command=sys.executable, args=args, cwd=str(PROJECT_ROOT))
    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            yield session


async def object_result(result: Any) -> dict[str, Any]:
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured
    for block in getattr(result, "content", []):
        text = getattr(block, "text", None)
        if isinstance(text, str):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
    raise AssertionError(f"MCP tool did not return an object: {result!r}")


class WriteToolsMcpTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="esp32-write-tools-")
        self.workspace = Path(self.temp.name)
        config = self.workspace / "esp32-ide"
        config.mkdir()
        (config / "board.json").write_text(
            json.dumps({"type": "generic", "label": "test fixture", "entry": "/main.py"}),
            encoding="utf-8",
        )
        (config / "workbench-policy.json").write_text(
            json.dumps({"policy": "auto"}), encoding="utf-8"
        )
        (self.workspace / "program.py").write_text(
            "Kp = 1.0\nKi = 0.1\nKd = 0.01\nprint('download fixture')\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    async def test_mutation_tools_register_only_with_explicit_write_flag(self) -> None:
        async with open_fileops_session(self.workspace, enabled=False) as session:
            response = await session.list_tools()
        names = {tool.name for tool in response.tools}
        self.assertTrue(WRITE_TOOLS.isdisjoint(names))
        self.assertIn("esp32_board_files_list", names)
        self.assertIn("esp32_board_file_read", names)

    async def test_board_file_write_create_overwrite_and_delete_report_backup_state(self) -> None:
        async with open_fileops_session(self.workspace) as session:
            overwritten = await object_result(await session.call_tool(
                "esp32_board_file_write", {"path": "/demo.bin", "content": "updated"}
            ))
            created = await object_result(await session.call_tool(
                "esp32_board_file_write", {"path": "/new.txt", "content": "new file"}
            ))
            deleted = await object_result(await session.call_tool(
                "esp32_board_file_delete", {"path": "/demo.bin"}
            ))
            files = await object_result(await session.call_tool("esp32_board_files_list", {}))

        self.assertTrue(overwritten["ok"])
        self.assertTrue(overwritten["targetExisted"])
        self.assertTrue(overwritten["backupVerified"])
        self.assertEqual(overwritten["backupStatus"], "verified")
        self.assertEqual(overwritten["size"], len("updated"))
        self.assertTrue(created["ok"])
        self.assertFalse(created["targetExisted"])
        self.assertFalse(created["backupVerified"])
        self.assertEqual(created["backupStatus"], "target_absent")
        self.assertTrue(deleted["ok"])
        self.assertTrue(deleted["backupVerified"])
        self.assertTrue(deleted["targetExisted"])
        self.assertNotIn("demo.bin", {row["name"] for row in files["files"]})

    async def test_download_and_pid_use_detected_entry_and_verify_responses(self) -> None:
        async with open_fileops_session(self.workspace) as session:
            downloaded = await object_result(await session.call_tool(
                "esp32_download", {"filename": "program.py", "run": False}
            ))
            pid_before = await object_result(await session.call_tool("esp32_pid", {"action": "get"}))
            pid_written = await object_result(await session.call_tool(
                "esp32_pid", {"action": "set", "kp": 4.25, "run": True}
            ))
            pid_after = await object_result(await session.call_tool("esp32_pid", {"action": "get"}))

        self.assertEqual(downloaded["target"], "/main.py")
        self.assertTrue(downloaded["compileOk"])
        self.assertEqual(downloaded["backupStatus"], "verified")
        self.assertFalse(downloaded["ran"])
        self.assertEqual(pid_before["values"]["Kp"], 1.0)
        self.assertTrue(pid_written["backupVerified"])
        self.assertTrue(pid_written["ran"])
        self.assertEqual(pid_after["values"]["Kp"], 4.25)
        self.assertEqual(pid_after["values"]["Ki"], 0.1)

    async def test_missing_capability_refuses_mutation_before_mock_state_changes(self) -> None:
        async with open_fileops_session(
            self.workspace, scenario="fileops-no-capability"
        ) as session:
            before = await object_result(await session.call_tool(
                "esp32_board_file_read", {"path": "/main.py"}
            ))
            refused = await object_result(await session.call_tool(
                "esp32_board_file_write", {"path": "/main.py", "content": "changed"}
            ))
            after = await object_result(await session.call_tool(
                "esp32_board_file_read", {"path": "/main.py"}
            ))

        self.assertFalse(refused["ok"])
        self.assertIn("capability", refused["error"])
        self.assertEqual(before["content"], after["content"])

    def test_authorized_download_digest_is_rechecked_before_bridge_start(self) -> None:
        client = BridgeClient(
            mode="mock",
            workspace=str(self.workspace),
            profile="generic",
            bridge_script=None,
            mock_script=str(PROJECT_ROOT / "tests" / "mock_bridge.py"),
            mock_scenario="fileops",
        )
        tools = Esp32McpTools(
            client,
            entry="/main.py",
            allow_mock_elicitation=False,
            write_tools_enabled=True,
        )
        summary = tools.download_source_summary("program.py")
        (self.workspace / "program.py").write_text("print('changed after approval')\n", encoding="utf-8")
        with self.assertRaisesRegex(BridgeFailure, "发生变化"):
            tools.download("program.py", expected_sha256=summary["sha256"])
        self.assertIsNone(client._process)

    def test_workspace_escape_is_rejected_before_bridge_start(self) -> None:
        client = BridgeClient(
            mode="mock",
            workspace=str(self.workspace),
            profile="generic",
            bridge_script=None,
            mock_script=str(PROJECT_ROOT / "tests" / "mock_bridge.py"),
            mock_scenario="fileops",
        )
        tools = Esp32McpTools(
            client,
            entry="/main.py",
            allow_mock_elicitation=False,
            write_tools_enabled=True,
        )
        with self.assertRaises(BridgeFailure):
            tools.download_source_summary("../outside.py")
        self.assertIsNone(client._process)


if __name__ == "__main__":
    unittest.main()
