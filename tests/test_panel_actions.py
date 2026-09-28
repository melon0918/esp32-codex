"""Step 7: UI-origin per-operation approvals tested without any modal window."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mcp-server"))

from panel.actions import PanelActions
from panel.backend import PanelBackend


class StubBackend:
    needs_confirmation = staticmethod(PanelBackend.needs_confirmation)

    def __init__(self, policy="confirm-write"):
        self.policy = policy
        self.performed = []

    def current_policy(self):
        return self.policy

    def plan_control(self, command, *, port="", line=""):
        return {"command": command, "effect": "write" if command == "send" else "control",
                "target": "MOCK0", "impact": f"{command} {port} {line}",
                "epoch": 4, "arguments": {"line": line} if line else {}}

    def perform_control(self, plan):
        self.performed.append(("control", plan))
        return {"ran": True} if plan["command"] == "run" else {"sent": True}

    def plan_download(self, filename, *, run=False):
        return {"command": "download", "effect": "write", "target": "/main.py",
                "impact": f"Backup and download {filename}, run={run}", "sha256": "abc"}

    def perform_download(self, plan):
        self.performed.append(("download", plan))
        return {"backupVerified": True}

    def plan_workspace(self, action, path, **kwargs):
        return {"command": action, "effect": "write", "target": path,
                "impact": "create or switch workspace"}

    def perform_workspace(self, plan):
        self.performed.append(("workspace", plan))
        return {"claimed": True}

    def plan_policy(self, value):
        return {"command": "policy_set", "effect": "write", "target": "workspace",
                "impact": f"change to {value}"}

    def perform_policy(self, plan):
        self.performed.append(("policy", plan))
        return {"policy": "auto"}


class ApprovalTests(unittest.TestCase):
    def test_confirm_all_decline_prevents_control_and_prompt_has_target_and_impact(self):
        backend = StubBackend("confirm-all")
        prompts = []
        actions = PanelActions(backend, lambda title, msg: (prompts.append((title, msg)), False)[1])
        denied = actions.control("run")
        self.assertFalse(denied["ok"])
        self.assertEqual(backend.performed, [])
        self.assertIn("MOCK0", prompts[0][1])
        self.assertIn("影响", prompts[0][1])
        self.assertIn("run", prompts[0][0])

    def test_confirm_write_allows_control_but_repl_and_download_require_approval(self):
        backend = StubBackend("confirm-write")
        prompts = []
        actions = PanelActions(backend, lambda title, msg: (prompts.append(msg), False)[1])
        self.assertTrue(actions.control("run")["ok"])
        self.assertFalse(actions.control("send", line="print(1)")["ok"])
        self.assertFalse(actions.download("main.py")["ok"])
        self.assertEqual(len(backend.performed), 1)
        self.assertEqual(len(prompts), 2)
        self.assertIn("print(1)", prompts[0])
        self.assertIn("main.py", prompts[1])

    def test_release_actions_never_block_on_unavailable_confirmation(self):
        backend = StubBackend("confirm-all")
        actions = PanelActions(backend, lambda *_: self.fail("stop must not prompt"))
        self.assertTrue(actions.control("stop")["ok"])
        self.assertTrue(actions.control("disconnect")["ok"])

    def test_workspace_claim_and_policy_downgrade_always_require_explicit_confirm(self):
        backend = StubBackend("auto")
        attempts = []
        actions = PanelActions(backend, lambda title, msg: (attempts.append(msg), False)[1])
        self.assertFalse(actions.workspace("claim", "C:/workspace")["ok"])
        self.assertFalse(actions.policy("auto")["ok"])
        self.assertEqual(backend.performed, [])
        self.assertEqual(len(attempts), 2)

    def test_accept_executes_only_the_planned_single_operation(self):
        backend = StubBackend("confirm-all")
        actions = PanelActions(backend, lambda *_: True)
        result = actions.download("main.py", run=True)
        self.assertTrue(result["ok"])
        self.assertTrue(result["backupVerified"])
        self.assertEqual(len(backend.performed), 1)
        self.assertEqual(backend.performed[0][0], "download")


@unittest.skipUnless(sys.platform == "win32", "Windows mock broker integration")
class PanelApprovalBrokerTests(unittest.TestCase):
    def _panel(self, root, *, scenario, policy):
        import json
        import secrets
        from bridge_client import BridgeClient
        from broker.adapter import BrokerBridgeClient
        from broker.client import BrokerClient
        from broker.identity import current_user_sid

        config_dir = root / "esp32-ide"
        config_dir.mkdir()
        (config_dir / "board.json").write_text(
            json.dumps({"type": "generic", "entry": "/main.py", "label": "Fixture"}),
            encoding="utf-8",
        )
        (config_dir / "workbench-policy.json").write_text(
            json.dumps({"policy": policy}), encoding="utf-8",
        )
        script = str((Path(__file__).resolve().parents[1] / "tests" / "mock_bridge.py").resolve())
        local = BridgeClient(mode="mock", workspace=str(root), profile="generic",
                             bridge_script=None, mock_script=script, mock_scenario=scenario)
        broker = BrokerClient(
            auth_path=root / "test-broker.token",
            _identity_sid_for_testing=current_user_sid() + "-panel-actions-" + secrets.token_hex(5),
            backend_config={
                "mode": "mock", "workspace": str(root), "profile": "generic",
                "bridge_script": None, "mock_script": script, "mock_scenario": scenario,
                "allow_real_controls": False, "allow_real_writes": False,
                "local_bridge_owner": False,
            },
        )
        return PanelBackend(BrokerBridgeClient(local, broker)), broker, local

    def _close(self, backend, broker):
        backend.close()
        process = broker._owned_process
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=5)

    def test_denied_connect_and_run_never_reach_shared_mock_bridge(self):
        import tempfile
        with tempfile.TemporaryDirectory(prefix="panel-action-control-") as temp:
            backend, broker, local = self._panel(Path(temp), scenario="control", policy="confirm-all")
            prompts = []
            decline = PanelActions(backend, lambda title, text: (prompts.append(text), False)[1])
            accept = PanelActions(backend, lambda *_: True)
            try:
                result = decline.control("connect", port="MOCK0")
                self.assertFalse(result["ok"])
                self.assertFalse(backend.bridge.call_shared("status")["connected"])
                self.assertIn("MOCK0", prompts[-1])
                self.assertTrue(accept.control("connect", port="MOCK0")["connected"])
                before = backend.bridge.control_epoch
                result = decline.control("run")
                self.assertFalse(result["ok"])
                self.assertEqual(backend.bridge.control_epoch, before)
                self.assertTrue(backend.bridge.call_shared("status")["connected"])
                self.assertTrue(decline.control("stop")["stopped"])
                self.assertEqual(backend.bridge.control_epoch, before + 1)
                self.assertIsNone(local._process)
            finally:
                self._close(backend, broker)

    def test_download_denied_then_confirmed_uses_strict_backup_only_in_mock(self):
        import tempfile
        with tempfile.TemporaryDirectory(prefix="panel-action-fileops-") as temp:
            root = Path(temp)
            (root / "program.py").write_text("print('fixture')\n", encoding="utf-8")
            backend, broker, local = self._panel(root, scenario="fileops", policy="confirm-write")
            decline = PanelActions(backend, lambda *_: False)
            accept = PanelActions(backend, lambda *_: True)
            try:
                self.assertFalse(decline.download("program.py", run=True)["ok"])
                before = backend.bridge.call("readfile", {"path": "/main.py"})["content"]
                confirmed = accept.download("program.py", run=False)
                self.assertTrue(confirmed["ok"])
                self.assertTrue(confirmed["backupVerified"])
                self.assertEqual(confirmed["target"], "/main.py")
                self.assertFalse(confirmed["ran"])
                self.assertNotEqual(backend.bridge.call("readfile", {"path": "/main.py"})["content"], before)
                self.assertIsNone(local._process)
            finally:
                self._close(backend, broker)


if __name__ == "__main__":
    unittest.main()
