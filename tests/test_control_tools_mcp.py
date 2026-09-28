"""Phase 3a gated control tools tested with mock and canned bridges only."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import ElicitResult


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVER_DIR = PROJECT_ROOT / "mcp-server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))
WEB_HOST_DIR = PROJECT_ROOT / "web-panel" / "host"
if str(WEB_HOST_DIR) not in sys.path:
    sys.path.insert(0, str(WEB_HOST_DIR))
SERVER_SCRIPT = PROJECT_ROOT / "mcp-server" / "server.py"
CONTROL_TOOLS = {
    "esp32_connect",
    "esp32_disconnect",
    "esp32_stop",
    "esp32_run",
    "esp32_interrupt",
    "esp32_repl_send",
}


@asynccontextmanager
async def open_session(args: list[str], *, elicitation_callback=None):
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-X", "utf8", str(SERVER_SCRIPT), "--test-broker-seed", "UnitTest", *args],
        cwd=str(PROJECT_ROOT),
    )
    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(
            read_stream, write_stream, elicitation_callback=elicitation_callback
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


def wsl_mount_path(path: Path) -> str:
    raw = str(path.resolve())
    return f"/mnt/{raw[0].lower()}/{raw[3:].replace(chr(92), '/') }"


def fake_bridge_source(log_path: Path, *, initially_connected: bool) -> str:
    """Build a no-serial canned bridge process for bridge-mode integration tests."""
    source = r'''from __future__ import annotations

import json
import sys
from pathlib import Path

log_path = Path(__LOG_PATH__)
connected = __CONNECTED__
current_port = "FAKE0" if connected else ""

for raw in sys.stdin:
    if not raw.strip():
        continue
    request = json.loads(raw)
    command = request.get("cmd")
    with log_path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(command) + "\n")

    if command == "status":
        data = {
            "connected": connected,
            "port": current_port,
            "busy": False,
            "boardInfo": "Canned test bridge",
            "workspace": "fixture-workspace",
        }
    elif command == "ports":
        data = {"ports": [{"device": "FAKE0", "description": "No hardware fixture", "ch340": False}]}
    elif command == "connect":
        connected = True
        current_port = request.get("port", "FAKE0")
        data = {"connected": True, "port": current_port, "boardInfo": "Canned test bridge"}
    elif command == "disconnect":
        connected = False
        current_port = ""
        data = {"connected": False}
    elif command == "stop":
        data = {"stopped": True}
    elif command == "run":
        data = {"ran": True}
    elif command == "interrupt":
        data = {"interrupted": True}
    elif command == "send":
        data = {"sent": True}
    else:
        print(json.dumps({"id": request.get("id"), "ok": False, "error": "unknown canned command"}), flush=True)
        continue

    print(json.dumps({"id": request.get("id"), "ok": True, "data": data}), flush=True)
'''
    return source.replace("__LOG_PATH__", repr(str(log_path))).replace(
        "__CONNECTED__", repr(initially_connected)
    )


class ExplicitControlToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="esp32-control-tools-")
        self.workspace = Path(self.temp.name)
        config = self.workspace / "esp32-ide"
        config.mkdir()
        (config / "board.json").write_text(
            json.dumps({"type": "generic", "label": "test fixture", "entry": "/main.py"}),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_policy(self, policy: str) -> None:
        (self.workspace / "esp32-ide" / "workbench-policy.json").write_text(
            json.dumps({"policy": policy}), encoding="utf-8"
        )

    def write_fake_bridge(self, *, initially_connected: bool = False) -> tuple[Path, Path]:
        script = self.workspace / "fake-bridge.py"
        command_log = self.workspace / "fake-bridge-commands.jsonl"
        script.write_text(
            fake_bridge_source(command_log, initially_connected=initially_connected),
            encoding="utf-8",
        )
        return script, command_log

    def bridge_args(self, script: Path, *, enable_controls: bool, policy: str) -> list[str]:
        self.write_policy(policy)
        args = [
            "--mode", "bridge",
            "--workspace", str(self.workspace),
            "--bridge-script", str(script),
        ]
        if enable_controls:
            args.append("--enable-control-tools")
        return args

    def make_real_panel_api(self, script: Path, *, enable_controls: bool):
        from bridge_client import BridgeClient
        from broker.adapter import BrokerBridgeClient
        from broker.client import BrokerClient
        from broker.identity import current_user_sid
        from panel.backend import PanelBackend
        from host import PanelOperationsApi

        workspace = str(self.workspace.resolve())
        bridge_script = str(script.resolve())
        controls, writes = bool(enable_controls), False
        identity_values = (
            "UnitTest", "bridge", workspace, "generic", "readonly", bridge_script,
            str(controls), str(writes),
        )
        test_identity = current_user_sid() + "-test-" + hashlib.sha256(
            "|".join(identity_values).encode("utf-8")
        ).hexdigest()[:24]
        local = BridgeClient(
            mode="bridge", workspace=workspace, profile="generic",
            bridge_script=bridge_script,
            mock_script=str(PROJECT_ROOT / "tests" / "mock_bridge.py"),
            mock_scenario="readonly", allow_real_controls=controls,
            allow_real_writes=writes,
        )
        broker = BrokerClient(
            backend_config={
                "mode": "bridge", "workspace": workspace, "profile": "generic",
                "bridge_script": bridge_script,
                "mock_script": str(PROJECT_ROOT / "tests" / "mock_bridge.py"),
                "mock_scenario": "readonly", "allow_real_controls": controls,
                "allow_real_writes": writes, "local_bridge_owner": False,
            },
            _identity_sid_for_testing=test_identity,
        )
        backend = PanelBackend(BrokerBridgeClient(local, broker))
        return backend, PanelOperationsApi(backend, window=None)

    async def wait_for_panel_confirmation(self, api, task: asyncio.Task) -> dict[str, Any]:
        for _ in range(120):
            snapshot = await asyncio.to_thread(api.get_snapshot)
            rows = snapshot.get("agentConfirmations")
            if isinstance(rows, list) and rows:
                return rows[0]
            if task.done():
                result = await task
                raise AssertionError(f"MCP request ended before panel confirmation: {result!r}")
            await asyncio.sleep(0.05)
        self.fail("panel did not receive the MCP confirmation request")

    @staticmethod
    def read_commands(log_path: Path) -> list[str]:
        if not log_path.exists():
            return []
        return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]

    async def test_real_control_tools_are_not_registered_by_default(self) -> None:
        script, command_log = self.write_fake_bridge()
        async with open_session(self.bridge_args(script, enable_controls=False, policy="auto")) as session:
            listed = await session.list_tools()

        names = {tool.name for tool in listed.tools}
        self.assertFalse(CONTROL_TOOLS & names)
        self.assertEqual(self.read_commands(command_log), [])

    async def test_workspace_and_policy_tools_are_available_but_mutations_fail_closed(self) -> None:
        target = self.workspace / "target workspace"
        (target / "esp32-ide").mkdir(parents=True)
        (target / "esp32-ide" / "board.json").write_text(
            json.dumps({"type": "generic", "label": "target", "entry": "/target.py"}),
            encoding="utf-8",
        )
        claim_target = self.workspace / "claim target"
        claim_target.mkdir()

        async with open_session([
            "--mode", "mock", "--workspace", str(self.workspace),
            "--mock-scenario", "control",
        ]) as session:
            listed = await session.list_tools()
            current = await object_result(await session.call_tool("esp32_workspace_current", {}))
            policy = await object_result(await session.call_tool("esp32_policy_get", {}))
            selected = await object_result(await session.call_tool("esp32_workspace_select", {
                "path": str(target), "expected_epoch": current["control_epoch"],
            }))
            claimed = await object_result(await session.call_tool("esp32_workspace_claim", {
                "path": str(claim_target), "profile": "generic",
                "expected_epoch": current["control_epoch"],
            }))
            changed = await object_result(await session.call_tool("esp32_policy_set", {
                "policy": "auto", "expected_revision": policy["revision"],
                "expected_epoch": current["control_epoch"],
            }))
            still_current = await object_result(await session.call_tool("esp32_workspace_current", {}))

        names = {tool.name for tool in listed.tools}
        self.assertTrue({
            "esp32_workspace_select", "esp32_workspace_claim", "esp32_policy_get", "esp32_policy_set",
        } <= names)
        self.assertTrue(policy["ok"])
        self.assertEqual(policy["policy"], "confirm-write")
        self.assertEqual(policy["revision"], "missing")
        for result in (selected, claimed, changed):
            self.assertFalse(result["ok"])
            self.assertEqual(result["errorCode"], "confirmation_required")
        self.assertEqual(still_current["workspacePath"], str(self.workspace.resolve()))
        self.assertFalse((claim_target / "esp32-ide" / "board.json").exists())
        self.assertFalse((self.workspace / "esp32-ide" / "workbench-policy.json").exists())

    async def test_confirmed_mock_workspace_and_policy_changes_use_epoch_and_revision(self) -> None:
        target = self.workspace / "selected workspace"
        (target / "esp32-ide").mkdir(parents=True)
        (target / "esp32-ide" / "board.json").write_text(
            json.dumps({"type": "hiwonder", "label": "mock target", "entry": "/robot.py"}),
            encoding="utf-8",
        )
        claim_target = self.workspace / "claimed workspace"
        claim_target.mkdir()
        prompts = []

        async def approve(_context, params):
            prompts.append(params)
            return ElicitResult(action="accept", content={"decision": "approve"})

        async with open_session([
            "--mode", "mock", "--workspace", str(self.workspace),
            "--mock-scenario", "control", "--allow-mock-elicitation",
        ], elicitation_callback=approve) as session:
            initial = await object_result(await session.call_tool("esp32_workspace_current", {}))
            selected = await object_result(await session.call_tool("esp32_workspace_select", {
                "path": wsl_mount_path(target), "expected_epoch": initial["control_epoch"],
            }))
            current = await object_result(await session.call_tool("esp32_workspace_current", {}))
            claimed = await object_result(await session.call_tool("esp32_workspace_claim", {
                "path": wsl_mount_path(claim_target), "profile": "generic",
                "expected_epoch": current["control_epoch"],
            }))
            policy = await object_result(await session.call_tool("esp32_policy_get", {}))
            changed = await object_result(await session.call_tool("esp32_policy_set", {
                "policy": "auto", "expected_revision": policy["revision"],
                "expected_epoch": current["control_epoch"],
            }))
            final = await object_result(await session.call_tool("esp32_workspace_current", {}))

        self.assertTrue(selected["ok"])
        self.assertEqual(selected["workspacePath"], str(target.resolve()))
        self.assertEqual(selected["profile"], "hiwonder")
        self.assertEqual(selected["entry"], "/robot.py")
        self.assertTrue(claimed["ok"])
        self.assertTrue((claim_target / "esp32-ide" / "board.json").is_file())
        self.assertTrue(changed["ok"])
        self.assertEqual(changed["policy"], "auto")
        self.assertEqual(final["workspacePath"], str(target.resolve()))
        self.assertEqual(final["control_epoch"], initial["control_epoch"] + 2)
        self.assertEqual(len(prompts), 3)

    async def test_real_policy_get_reads_workspace_file_without_starting_bridge(self) -> None:
        script, command_log = self.write_fake_bridge()
        self.write_policy("confirm-all")
        async with open_session(self.bridge_args(script, enable_controls=False, policy="confirm-all")) as session:
            result = await object_result(await session.call_tool("esp32_policy_get", {}))

        self.assertTrue(result["ok"])
        self.assertEqual(result["policy"], "confirm-all")
        self.assertEqual(result["dataSource"], "broker_policy")
        self.assertFalse(result["simulated"])
        self.assertEqual(len(result["revision"]), 64)
        self.assertEqual(self.read_commands(command_log), [])

    async def test_policy_revision_and_workspace_epoch_conflicts_do_not_mutate(self) -> None:
        self.write_policy("confirm-write")
        async with open_session([
            "--mode", "mock", "--workspace", str(self.workspace),
            "--mock-scenario", "control",
        ]) as session:
            current = await object_result(await session.call_tool("esp32_workspace_current", {}))
            initial_policy = await object_result(await session.call_tool("esp32_policy_get", {}))
            stale_epoch = await object_result(await session.call_tool("esp32_workspace_select", {
                "path": wsl_mount_path(self.workspace),
                "expected_epoch": current["control_epoch"] + 1,
            }))
            self.write_policy("confirm-all")
            stale_revision = await object_result(await session.call_tool("esp32_policy_set", {
                "policy": "auto", "expected_revision": initial_policy["revision"],
                "expected_epoch": current["control_epoch"],
            }))
            still_current = await object_result(await session.call_tool("esp32_workspace_current", {}))

        self.assertEqual(stale_epoch["errorCode"], "epoch_conflict")
        self.assertEqual(stale_revision["errorCode"], "revision_conflict")
        self.assertEqual(still_current["workspacePath"], str(self.workspace.resolve()))
        self.assertEqual(json.loads((self.workspace / "esp32-ide" / "workbench-policy.json").read_text())[
            "policy"], "confirm-all")

    async def test_connect_to_current_port_reports_already_connected(self) -> None:
        script, command_log = self.write_fake_bridge(initially_connected=True)
        async with open_session(self.bridge_args(script, enable_controls=True, policy="auto")) as session:
            result = await object_result(
                await session.call_tool("esp32_connect", {"port": "FAKE0"})
            )

        self.assertTrue(result["ok"])
        self.assertTrue(result["alreadyConnected"])
        self.assertTrue(result["connected"])
        self.assertEqual(self.read_commands(command_log), ["status"])

    async def test_explicit_mock_entrypoints_cover_control_shapes_without_hardware(self) -> None:
        self.write_policy("auto")
        args = [
            "--mode", "mock",
            "--mock-scenario", "control",
            "--workspace", str(self.workspace),
            "--enable-control-tools",
        ]
        async with open_session(args) as session:
            listed = await session.list_tools()
            connected = await object_result(await session.call_tool("esp32_connect", {"port": "MOCK0"}))
            ran = await object_result(await session.call_tool("esp32_run", {}))
            interrupted = await object_result(await session.call_tool("esp32_interrupt", {}))
            stopped = await object_result(await session.call_tool("esp32_stop", {}))
            repl = await object_result(await session.call_tool("esp32_repl_send", {"line": "print(1)"}))
            disconnected = await object_result(await session.call_tool("esp32_disconnect", {}))

        names = {tool.name for tool in listed.tools}
        self.assertTrue(CONTROL_TOOLS <= names)
        self.assertTrue(connected["connected"])
        self.assertTrue(ran["ran"])
        self.assertTrue(interrupted["bridgeReportedInterrupted"])
        self.assertFalse(interrupted["physicalStopConfirmed"])
        self.assertFalse(stopped["physicalStopConfirmed"])
        self.assertIn("generic 停止仅中断程序", stopped["stopMeaning"])
        self.assertTrue(repl["sent"])
        self.assertTrue(disconnected["connected"] is False)
        for result in (connected, ran, interrupted, stopped, repl, disconnected):
            self.assertEqual(result["source"], "mock_bridge")
            self.assertTrue(result["simulated"])

    async def test_real_confirmation_is_shown_and_approved_once_by_panel(self) -> None:
        script, command_log = self.write_fake_bridge()
        backend, panel_api = self.make_real_panel_api(script, enable_controls=True)
        async with open_session(self.bridge_args(script, enable_controls=True, policy="confirm-all")) as session:
            listed = await session.list_tools()
            task = asyncio.create_task(session.call_tool("esp32_connect", {"port": "FAKE0"}))
            try:
                pending = await self.wait_for_panel_confirmation(panel_api, task)
                self.assertEqual(pending["state"], "pending")
                self.assertIn("FAKE0", pending["action"])
                self.assertEqual(pending["profile"], "generic")
                self.assertEqual(pending["control_epoch"], 0)
                self.assertNotIn("request_digest", pending)
                self.assertEqual(pending["policy_revision"], hashlib.sha256(
                    (self.workspace / "esp32-ide" / "workbench-policy.json").read_bytes()
                ).hexdigest())
                names = {tool.name for tool in listed.tools}
                self.assertFalse(any("confirmation" in name and ("approve" in name or "resolve" in name) for name in names))
                self.assertNotIn("connect", self.read_commands(command_log))
                approved = await asyncio.to_thread(panel_api.approve_agent_confirmation, pending["id"])
                self.assertTrue(approved["ok"])
                repeated = await asyncio.to_thread(panel_api.approve_agent_confirmation, pending["id"])
                self.assertFalse(repeated["ok"])
                result = await object_result(await asyncio.wait_for(task, timeout=10))
            finally:
                if not task.done():
                    task.cancel()
                backend.close()

        self.assertTrue(result["connected"])
        commands = self.read_commands(command_log)
        self.assertIn("status", commands)
        self.assertIn("ports", commands)
        self.assertEqual(commands.count("connect"), 1)

    async def test_real_confirmation_rejection_never_sends_repl(self) -> None:
        script, command_log = self.write_fake_bridge(initially_connected=True)
        backend, panel_api = self.make_real_panel_api(script, enable_controls=True)
        async with open_session(self.bridge_args(script, enable_controls=True, policy="confirm-write")) as session:
            task = asyncio.create_task(session.call_tool("esp32_repl_send", {"line": "print(1)"}))
            try:
                pending = await self.wait_for_panel_confirmation(panel_api, task)
                self.assertIn("print(1)", pending["impact"])
                rejected = await asyncio.to_thread(panel_api.reject_agent_confirmation, pending["id"])
                self.assertTrue(rejected["ok"])
                result = await object_result(await asyncio.wait_for(task, timeout=10))
            finally:
                if not task.done():
                    task.cancel()
                backend.close()

        self.assertFalse(result["ok"])
        self.assertEqual(result["errorCode"], "confirmation_rejected")
        commands = self.read_commands(command_log)
        self.assertIn("status", commands)
        self.assertNotIn("send", commands)

    async def test_confirmation_status_and_cancel_do_not_approve_pending_operation(self) -> None:
        script, command_log = self.write_fake_bridge()
        backend, panel_api = self.make_real_panel_api(script, enable_controls=True)
        async with open_session(self.bridge_args(script, enable_controls=True, policy="confirm-all")) as session:
            listed = await session.list_tools()
            names = {tool.name for tool in listed.tools}
            self.assertIn("esp32_confirmation_status", names)
            self.assertIn("esp32_confirmation_cancel", names)
            self.assertFalse(any(
                "confirmation" in name and ("approve" in name or "resolve" in name)
                for name in names
            ))

            task = asyncio.create_task(session.call_tool("esp32_connect", {"port": "FAKE0"}))
            try:
                pending = await self.wait_for_panel_confirmation(panel_api, task)
                status = await object_result(await session.call_tool("esp32_confirmation_status", {}))
                self.assertTrue(status["ok"])
                row = next(item for item in status["pending"] if item["id"] == pending["id"])
                self.assertEqual(row["state"], "pending")
                self.assertNotIn("impact", row)
                self.assertNotIn("request_digest", row)
                self.assertNotIn("token", row)

                cancelled = await object_result(await session.call_tool(
                    "esp32_confirmation_cancel", {"confirmation_id": pending["id"]}
                ))
                self.assertTrue(cancelled["ok"])
                self.assertEqual(cancelled["state"], "cancelled")
                result = await object_result(await asyncio.wait_for(task, timeout=10))
            finally:
                if not task.done():
                    task.cancel()
                backend.close()

        self.assertFalse(result["ok"])
        self.assertEqual(result["errorCode"], "confirmation_expired")
        self.assertNotIn("connect", self.read_commands(command_log))

    async def test_pending_confirmation_is_invalidated_when_policy_revision_changes(self) -> None:
        script, command_log = self.write_fake_bridge()
        backend, panel_api = self.make_real_panel_api(script, enable_controls=True)
        async with open_session(self.bridge_args(script, enable_controls=True, policy="confirm-all")) as session:
            task = asyncio.create_task(session.call_tool("esp32_connect", {"port": "FAKE0"}))
            try:
                pending = await self.wait_for_panel_confirmation(panel_api, task)
                self.write_policy("auto")
                approved = await asyncio.to_thread(panel_api.approve_agent_confirmation, pending["id"])
                self.assertFalse(approved["ok"])
                self.assertEqual(approved["state"], "stale")
                result = await object_result(await asyncio.wait_for(task, timeout=10))
            finally:
                if not task.done():
                    task.cancel()
                backend.close()

        self.assertFalse(result["ok"])
        self.assertEqual(result["errorCode"], "epoch_conflict")
        self.assertNotIn("connect", self.read_commands(command_log))

    async def test_pending_confirmation_is_invalidated_by_workspace_epoch_change(self) -> None:
        script, command_log = self.write_fake_bridge()
        target = self.workspace / "other workspace"
        (target / "esp32-ide").mkdir(parents=True)
        (target / "esp32-ide" / "board.json").write_text(
            json.dumps({"type": "generic", "label": "other", "entry": "/main.py"}),
            encoding="utf-8",
        )
        backend, panel_api = self.make_real_panel_api(script, enable_controls=True)
        async with open_session(self.bridge_args(script, enable_controls=True, policy="confirm-all")) as session:
            task = asyncio.create_task(session.call_tool("esp32_connect", {"port": "FAKE0"}))
            try:
                pending = await self.wait_for_panel_confirmation(panel_api, task)
                selected = await asyncio.to_thread(backend.bridge.workspace_select, str(target))
                self.assertEqual(selected["control_epoch"], pending["control_epoch"] + 1)
                approved = await asyncio.to_thread(panel_api.approve_agent_confirmation, pending["id"])
                self.assertFalse(approved["ok"])
                result = await object_result(await asyncio.wait_for(task, timeout=10))
            finally:
                if not task.done():
                    task.cancel()
                backend.close()

        self.assertFalse(result["ok"])
        self.assertEqual(result["errorCode"], "epoch_conflict")
        self.assertNotIn("connect", self.read_commands(command_log))

    async def test_explicit_policy_change_uses_panel_confirmation_and_revision(self) -> None:
        script, command_log = self.write_fake_bridge()
        backend, panel_api = self.make_real_panel_api(script, enable_controls=True)
        async with open_session(self.bridge_args(script, enable_controls=True, policy="confirm-write")) as session:
            initial = await object_result(await session.call_tool("esp32_policy_get", {}))
            current = await object_result(await session.call_tool("esp32_workspace_current", {}))
            task = asyncio.create_task(session.call_tool("esp32_policy_set", {
                "policy": "auto", "expected_revision": initial["revision"],
                "expected_epoch": current["control_epoch"],
            }))
            try:
                pending = await self.wait_for_panel_confirmation(panel_api, task)
                self.assertIn("auto", pending["impact"])
                self.assertEqual(pending["effect"], "workspace")
                approved = await asyncio.to_thread(panel_api.approve_agent_confirmation, pending["id"])
                self.assertTrue(approved["ok"])
                result = await object_result(await asyncio.wait_for(task, timeout=10))
            finally:
                if not task.done():
                    task.cancel()
                backend.close()

        self.assertTrue(result["ok"])
        self.assertEqual(result["policy"], "auto")
        self.assertTrue(set(self.read_commands(command_log)) <= {"status", "ports"})

    async def test_real_workspace_selection_requires_panel_confirmation(self) -> None:
        script, command_log = self.write_fake_bridge()
        target = self.workspace / "selected robot workspace"
        (target / "esp32-ide").mkdir(parents=True)
        (target / "esp32-ide" / "board.json").write_text(
            json.dumps({"type": "hiwonder", "label": "selected fixture", "entry": "/robot.py"}),
            encoding="utf-8",
        )
        backend, panel_api = self.make_real_panel_api(script, enable_controls=True)
        async with open_session(self.bridge_args(script, enable_controls=True, policy="auto")) as session:
            initial = await object_result(await session.call_tool("esp32_workspace_current", {}))
            task = asyncio.create_task(session.call_tool("esp32_workspace_select", {
                "path": str(target), "expected_epoch": initial["control_epoch"],
            }))
            try:
                pending = await self.wait_for_panel_confirmation(panel_api, task)
                self.assertEqual(pending["effect"], "workspace")
                self.assertEqual(pending["action"], "选择工作区")
                self.assertIn(str(target), pending["target"])
                self.assertEqual(pending["workspacePath"], str(self.workspace.resolve()))
                self.assertNotIn("select", self.read_commands(command_log))
                approved = await asyncio.to_thread(panel_api.approve_agent_confirmation, pending["id"])
                self.assertTrue(approved["ok"])
                result = await object_result(await asyncio.wait_for(task, timeout=10))
            finally:
                if not task.done():
                    task.cancel()
                backend.close()

        self.assertTrue(result["ok"])
        self.assertEqual(result["workspacePath"], str(target.resolve()))
        self.assertEqual(result["profile"], "hiwonder")
        self.assertEqual(result["entry"], "/robot.py")
        self.assertEqual(result["control_epoch"], initial["control_epoch"] + 1)
        self.assertNotIn("connect", self.read_commands(command_log))

    async def test_explicit_real_control_config_uses_only_canned_bridge(self) -> None:
        script, command_log = self.write_fake_bridge()
        backend, panel_api = self.make_real_panel_api(script, enable_controls=True)
        async with open_session(self.bridge_args(script, enable_controls=True, policy="confirm-write")) as session:
            connected = await object_result(await session.call_tool("esp32_connect", {"port": "FAKE0"}))
            ran = await object_result(await session.call_tool("esp32_run", {}))
            interrupted = await object_result(await session.call_tool("esp32_interrupt", {}))
            stopped = await object_result(await session.call_tool("esp32_stop", {}))
            repl_task = asyncio.create_task(session.call_tool("esp32_repl_send", {"line": "print(1)"}))
            try:
                pending = await self.wait_for_panel_confirmation(panel_api, repl_task)
                rejected = await asyncio.to_thread(panel_api.reject_agent_confirmation, pending["id"])
                self.assertTrue(rejected["ok"])
                repl_denied = await object_result(await asyncio.wait_for(repl_task, timeout=10))
            finally:
                if not repl_task.done():
                    repl_task.cancel()
            disconnected = await object_result(await session.call_tool("esp32_disconnect", {}))
        backend.close()

        self.assertTrue(connected["connected"])
        self.assertTrue(ran["ran"])
        self.assertTrue(interrupted["bridgeReportedInterrupted"])
        self.assertFalse(interrupted["physicalStopConfirmed"])
        self.assertFalse(stopped["physicalStopConfirmed"])
        self.assertIn("generic 停止仅中断程序", stopped["stopMeaning"])
        self.assertFalse(repl_denied["ok"])
        self.assertEqual(repl_denied["errorCode"], "confirmation_rejected")
        self.assertFalse(disconnected["connected"])
        commands = self.read_commands(command_log)
        self.assertIn("connect", commands)
        self.assertIn("run", commands)
        self.assertIn("interrupt", commands)
        self.assertIn("stop", commands)
        self.assertIn("disconnect", commands)
        self.assertNotIn("send", commands)
        for result in (connected, ran, interrupted, stopped, disconnected):
            self.assertEqual(result["source"], "bridge_process")
            self.assertFalse(result["simulated"])

    async def test_hiwonder_stop_meaning_does_not_claim_physical_confirmation(self) -> None:
        (self.workspace / "esp32-ide" / "board.json").write_text(
            json.dumps({"type": "hiwonder", "entry": "/main.py"}), encoding="utf-8"
        )
        script, _command_log = self.write_fake_bridge(initially_connected=True)
        async with open_session(self.bridge_args(script, enable_controls=True, policy="auto")) as session:
            stopped = await object_result(await session.call_tool("esp32_stop", {}))

        self.assertEqual(stopped["profile"], "hiwonder")
        self.assertFalse(stopped["physicalStopConfirmed"])
        self.assertIn("尝试双电机零速", stopped["stopMeaning"])
        self.assertNotIn("实际停转已确认", stopped["stopMeaning"])


if __name__ == "__main__":
    unittest.main()
