"""Mock-only phase 3a control and policy checks; never connects to hardware."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVER_SCRIPT = PROJECT_ROOT / "mcp-server" / "server.py"


@asynccontextmanager
async def open_mock_session(
    workspace: Path,
    *,
    policy: str | None = None,
    allow_elicitation: bool = False,
    elicitation_callback: Any = None,
    enable_controls: bool = False,
):
    config_dir = workspace / "esp32-ide"
    config_dir.mkdir(exist_ok=True)
    if policy is not None:
        (config_dir / "workbench-policy.json").write_text(
            json.dumps({"policy": policy}), encoding="utf-8"
        )
    args = [
        "-X",
        "utf8",
        str(SERVER_SCRIPT),
        "--mode",
        "mock",
        "--mock-scenario",
        "control",
        "--workspace",
        str(workspace),
        "--test-broker-seed", "UnitTest",
    ]
    if allow_elicitation:
        args.append("--allow-mock-elicitation")
    if enable_controls:
        args.append("--enable-control-tools")
    parameters = StdioServerParameters(
        command=sys.executable,
        args=args,
        cwd=str(PROJECT_ROOT),
    )
    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(
            read_stream,
            write_stream,
            elicitation_callback=elicitation_callback,
        ) as session:
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
    raise AssertionError(f"MCP tool did not return a JSON object: {result!r}")


class MockControlMcpTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="esp32-mcp-control-")
        self.workspace = Path(self.temp.name)
        (self.workspace / "esp32-ide").mkdir()
        (self.workspace / "esp32-ide" / "board.json").write_text(
            json.dumps({"type": "generic", "label": "模拟控制夹具", "entry": "/main.py"}),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    async def test_mock_only_tools_are_explicit_and_discoverable(self) -> None:
        async with open_mock_session(self.workspace) as session:
            result = await session.list_tools()
        tools = {tool.name: tool for tool in result.tools}
        expected = {
            "esp32_mock_policy_get",
            "esp32_mock_connect",
            "esp32_mock_disconnect",
            "esp32_mock_stop",
            "esp32_mock_run",
            "esp32_mock_interrupt",
            "esp32_mock_repl_send",
        }
        self.assertTrue(expected <= tools.keys())
        self.assertNotIn("confirmed", tools["esp32_mock_repl_send"].inputSchema.get("properties", {}))

    async def test_policy_default_is_confirm_write(self) -> None:
        self.workspace.joinpath("esp32-ide", "workbench-policy.json").unlink(missing_ok=True)
        async with open_mock_session(self.workspace) as session:
            policy = await object_result(await session.call_tool("esp32_mock_policy_get", {}))

        self.assertEqual(policy["policy"], "confirm-write")
        self.assertEqual(policy["source"], "default")
        self.assertEqual(policy["dataSource"], "local_workspace")
        self.assertFalse(policy["simulated"])

    async def test_mock_control_lifecycle_only_changes_simulated_state(self) -> None:
        (self.workspace / "esp32-ide" / "workbench-policy.json").write_text(
            json.dumps({"policy": "auto"}), encoding="utf-8"
        )
        async with open_mock_session(self.workspace) as session:
            policy = await object_result(await session.call_tool("esp32_mock_policy_get", {}))
            connected = await object_result(
                await session.call_tool("esp32_mock_connect", {"port": "MOCK0"})
            )
            ran = await object_result(await session.call_tool("esp32_mock_run", {}))
            interrupted = await object_result(await session.call_tool("esp32_mock_interrupt", {}))
            stopped = await object_result(await session.call_tool("esp32_mock_stop", {}))
            repl = await object_result(
                await session.call_tool("esp32_mock_repl_send", {"line": "print(1)"})
            )
            console = await object_result(await session.call_tool("esp32_console_read", {}))
            disconnected = await object_result(
                await session.call_tool("esp32_mock_disconnect", {})
            )
            status = await object_result(await session.call_tool("esp32_status", {}))

        self.assertEqual(policy["policy"], "auto")
        self.assertEqual(connected["port"], "MOCK0")
        self.assertTrue(ran["ran"])
        self.assertTrue(interrupted["bridgeReportedInterrupted"])
        self.assertFalse(stopped["physicalStopConfirmed"])
        self.assertIn("generic 停止仅中断程序", stopped["stopMeaning"])
        self.assertTrue(repl["sent"])
        self.assertFalse(repl["executed"])
        self.assertFalse(disconnected["connected"])
        self.assertFalse(status["connected"])
        self.assertIn("不表示电机或舵机实际停转", console["text"])
        self.assertNotIn("[桥] 电机已停转", console["text"])
        for result in (connected, ran, interrupted, stopped, repl, disconnected):
            self.assertEqual(result["source"], "mock_bridge")
            self.assertTrue(result["simulated"])

    async def test_confirm_all_acceptance_is_bound_to_simulated_operation(self) -> None:
        prompts: list[str] = []

        async def accept(_context: Any, params: Any) -> types.ElicitResult:
            prompts.append(params.message)
            return types.ElicitResult(action="accept", content={"decision": "approve"})

        async with open_mock_session(
            self.workspace,
            policy="confirm-all",
            allow_elicitation=True,
            elicitation_callback=accept,
        ) as session:
            connected = await object_result(
                await session.call_tool("esp32_mock_connect", {"port": "MOCK0"})
            )
            ran = await object_result(await session.call_tool("esp32_mock_run", {}))

        self.assertTrue(connected["connected"])
        self.assertTrue(ran["ran"])
        self.assertEqual(len(prompts), 2)
        self.assertIn("MOCK0", prompts[0])
        self.assertIn("不打开串口", prompts[0])
        self.assertIn("不会重启设备", prompts[1])

    async def test_confirm_write_requires_acceptance_for_repl(self) -> None:
        async def accept(_context: Any, _params: Any) -> types.ElicitResult:
            return types.ElicitResult(action="accept", content={"decision": "approve"})

        async with open_mock_session(
            self.workspace,
            policy="confirm-write",
            allow_elicitation=True,
            elicitation_callback=accept,
        ) as session:
            connected = await object_result(
                await session.call_tool("esp32_mock_connect", {"port": "MOCK0"})
            )
            repl = await object_result(
                await session.call_tool("esp32_mock_repl_send", {"line": "print(1)"})
            )

        self.assertTrue(connected["connected"])
        self.assertTrue(repl["sent"])
        self.assertFalse(repl["executed"])

    async def test_declined_confirmation_does_not_connect(self) -> None:
        async def decline(_context: Any, _params: Any) -> types.ElicitResult:
            return types.ElicitResult(action="decline")

        async with open_mock_session(
            self.workspace,
            policy="confirm-all",
            allow_elicitation=True,
            elicitation_callback=decline,
        ) as session:
            denied = await object_result(
                await session.call_tool("esp32_mock_connect", {"port": "MOCK0"})
            )
            status = await object_result(await session.call_tool("esp32_status", {}))

        self.assertFalse(denied["ok"])
        self.assertIn("未批准", denied["error"])
        self.assertFalse(status["connected"])

    async def test_unrecognized_confirmation_value_fails_closed(self) -> None:
        async def arbitrary_value(_context: Any, _params: Any) -> types.ElicitResult:
            return types.ElicitResult(action="accept", content={"decision": "approved"})

        async with open_mock_session(
            self.workspace,
            policy="confirm-all",
            allow_elicitation=True,
            elicitation_callback=arbitrary_value,
        ) as session:
            denied = await object_result(
                await session.call_tool("esp32_mock_connect", {"port": "MOCK0"})
            )
            status = await object_result(await session.call_tool("esp32_status", {}))

        self.assertFalse(denied["ok"])
        self.assertIn("未批准", denied["error"])
        self.assertFalse(status["connected"])

    async def test_malformed_confirmation_fails_closed(self) -> None:
        async def missing_decision(_context: Any, _params: Any) -> types.ElicitResult:
            return types.ElicitResult(action="accept", content={})

        async with open_mock_session(
            self.workspace,
            policy="confirm-all",
            allow_elicitation=True,
            elicitation_callback=missing_decision,
        ) as session:
            denied = await object_result(
                await session.call_tool("esp32_mock_connect", {"port": "MOCK0"})
            )
            status = await object_result(await session.call_tool("esp32_status", {}))

        self.assertFalse(denied["ok"])
        self.assertIn("未能提供用户确认", denied["error"])
        self.assertFalse(status["connected"])

    async def test_confirmation_without_client_capability_fails_closed(self) -> None:
        async with open_mock_session(
            self.workspace,
            policy="confirm-all",
            allow_elicitation=True,
        ) as session:
            denied = await object_result(
                await session.call_tool("esp32_mock_connect", {"port": "MOCK0"})
            )
            status = await object_result(await session.call_tool("esp32_status", {}))

        self.assertFalse(denied["ok"])
        self.assertIn("未能提供用户确认", denied["error"])
        self.assertFalse(status["connected"])

    async def test_confirmed_argument_cannot_bypass_repl_policy(self) -> None:
        async with open_mock_session(self.workspace, policy="confirm-write") as session:
            await session.call_tool("esp32_mock_connect", {"port": "MOCK0"})
            response = await session.call_tool(
                "esp32_mock_repl_send",
                {"line": "print(1)", "confirmed": True},
            )
            console = await object_result(await session.call_tool("esp32_console_read", {}))

        if not response.isError:
            denied = await object_result(response)
            self.assertFalse(denied["ok"])
        self.assertNotIn("REPL 行已接收", console["text"])

    async def test_real_bridge_mock_control_tool_refuses_before_start(self) -> None:
        sentinel = self.workspace / "bridge-started.txt"
        bridge_script = self.workspace / "must-not-run.py"
        bridge_script.write_text(
            f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('started')\n",
            encoding="utf-8",
        )
        args = [
            "-X", "utf8", str(SERVER_SCRIPT), "--mode", "bridge",
            "--workspace", str(self.workspace), "--bridge-script", str(bridge_script),
            "--test-broker-seed", "UnitTest",
        ]
        parameters = StdioServerParameters(
            command=sys.executable,
            args=args,
            cwd=str(PROJECT_ROOT),
        )
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await object_result(
                    await session.call_tool("esp32_mock_connect", {"port": "COM9"})
                )

        self.assertFalse(result["ok"])
        self.assertFalse(result["simulated"])
        self.assertIn("mock-only", result["error"])
        self.assertFalse(sentinel.exists())


if __name__ == "__main__":
    unittest.main()
