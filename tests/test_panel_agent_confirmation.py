"""Panel-only confirmation surface tests; no GUI, serial port, or hardware."""

from __future__ import annotations

import sys
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVER_DIR = PROJECT_ROOT / "mcp-server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))
HOST_DIR = PROJECT_ROOT / "web-panel" / "host"
if str(HOST_DIR) not in sys.path:
    sys.path.insert(0, str(HOST_DIR))

from bridge_client import BridgeFailure, is_outcome_unknown
from host import PanelOperationsApi


class FakeBackend:
    simulated = True

    def __init__(self):
        self.bridge = SimpleNamespace(mode="mock", simulated=True)
        self.decisions = []

    def snapshot(self):
        return {
            "workspace": {"workspacePath": "C:/fixture", "profile": "generic",
                          "info": {"entry": "/main.py", "profileLabel": "四足机器人"}},
            "status": {"connected": False, "busy": False},
            "ports": {"ports": []},
            "policy": {"policy": "confirm-write", "source": "fixture"},
            "console": {"text": "", "cursor": 0, "dropped": False},
            "agentConfirmations": [{
                "id": "A" * 32, "state": "pending", "action": "写入 /main.py",
                "effect": "write", "title": "写入板载文件", "target": "FAKE0 /main.py",
                "impact": "写入 12 bytes，SHA-256 abc…", "workspacePath": "C:/fixture",
                "profile": "generic", "entry": "/main.py", "control_epoch": 4,
                "policy_revision": "b" * 64, "expires_in_ms": 45000,
                "request_digest": "secret-digest", "auth_token": "secret-token",
            }],
        }

    def resolve_agent_confirmation(self, confirmation_id, *, approve):
        self.decisions.append((confirmation_id, approve))
        return {"ok": True, "state": "approved" if approve else "rejected"}


class PanelAgentConfirmationTests(unittest.TestCase):
    def test_uncertain_control_failure_is_distinguished_from_preflight_failure(self):
        backend = FakeBackend()
        backend.simulated = False
        backend.bridge = SimpleNamespace(mode="bridge", simulated=False)
        api = PanelOperationsApi(backend, window=None)

        with patch(
            "panel.actions.PanelActions.control",
            side_effect=BridgeFailure("broker request transport failed; lease was released"),
        ):
            uncertain = api.perform("run")

        self.assertFalse(uncertain["ok"])
        self.assertIn("结果未知", uncertain["error"])
        self.assertIn("不要重放", uncertain["error"])
        self.assertNotIn("transport failed", uncertain["error"])

        with patch(
            "panel.actions.PanelActions.control",
            side_effect=BridgeFailure("workspace is not selected"),
        ):
            ordinary = api.perform("run")

        self.assertFalse(ordinary["ok"])
        self.assertNotIn("结果未知", ordinary["error"])
        self.assertNotIn("workspace is not selected", ordinary["error"])

    def test_outcome_unknown_classifier_covers_transport_timeout_and_invalid_reply(self):
        self.assertTrue(is_outcome_unknown(BridgeFailure("transport failed")))
        self.assertTrue(is_outcome_unknown(BridgeFailure("命令超时")))
        self.assertTrue(is_outcome_unknown(BridgeFailure("broker returned an invalid result")))
        self.assertFalse(is_outcome_unknown(BridgeFailure("workspace is not selected")))

    def test_operations_api_exposes_summary_but_not_internal_digest_or_token(self):
        backend = FakeBackend()
        api = PanelOperationsApi(backend, window=None)
        snapshot = api.get_snapshot()
        self.assertEqual(len(snapshot["agentConfirmations"]), 1)
        item = snapshot["agentConfirmations"][0]
        self.assertEqual(item["control_epoch"], 4)
        self.assertEqual(item["policy_revision"], "b" * 64)
        self.assertNotIn("request_digest", item)
        self.assertNotIn("auth_token", item)
        self.assertTrue(api.approve_agent_confirmation(item["id"])["ok"])
        self.assertEqual(backend.decisions, [("A" * 32, True)])

    def test_invalid_confirmation_ids_are_rejected_without_resolving(self):
        backend = FakeBackend()
        api = PanelOperationsApi(backend, window=None)
        self.assertFalse(api.reject_agent_confirmation("too-short")["ok"])
        self.assertEqual(backend.decisions, [])

    def test_real_confirmation_panel_opens_when_device_control_tools_are_disabled(self):
        backend = FakeBackend()
        backend.simulated = False
        backend.bridge = SimpleNamespace(
            mode="bridge", simulated=False, allow_real_controls=False,
        )
        api = PanelOperationsApi(backend, window=None)
        snapshot = api.get_snapshot()
        self.assertFalse(snapshot["simulated"])
        self.assertEqual(snapshot["agentConfirmations"][0]["id"], "A" * 32)
        self.assertTrue(api.approve_agent_confirmation("A" * 32)["ok"])

    @unittest.skipUnless(os.name == "nt", "Windows launcher argument validation")
    def test_real_web_panel_can_launch_without_device_control_tools(self):
        from panel.launcher import launch_panel_process, parse_args

        with tempfile.TemporaryDirectory(prefix="esp32-panel-confirmation-") as temp:
            workspace = Path(temp)
            bridge_script = workspace / "bridge.py"
            bridge_script.write_text("# fixture only\n", encoding="utf-8")
            args = parse_args([
                "--mode", "bridge", "--workspace", str(workspace),
                "--bridge-script", str(bridge_script), "--ui", "web",
            ])
            self.assertFalse(args.enable_control_tools)

            fake_process = SimpleNamespace(pid=24680)
            with patch("subprocess.Popen", return_value=fake_process) as popen:
                result = launch_panel_process(
                    mode="bridge", workspace=str(workspace), ui="web", profile="generic",
                    bridge_script=str(bridge_script), allow_real_controls=False,
                )

            self.assertTrue(result["ok"])
            self.assertFalse(result["simulated"])
            self.assertFalse(result["autoConnect"])
            argv = popen.call_args.args[0]
            self.assertNotIn("--enable-control-tools", argv)
            parsed_child = parse_args(argv[argv.index("panel.launcher") + 1:])
            self.assertFalse(parsed_child.enable_control_tools)

    def test_compact_page_contains_separate_agent_confirmation_controls(self):
        from host import operations_markup, operations_script
        markup = operations_markup()
        script = operations_script()
        self.assertIn("ui4-agent-confirm", markup)
        self.assertIn("等待面板用户确认", markup)
        self.assertIn("approve_agent_confirmation(id)", script)
        self.assertIn("reject_agent_confirmation(id)", script)
        self.assertIn("textContent", script)


if __name__ == "__main__":
    unittest.main()
