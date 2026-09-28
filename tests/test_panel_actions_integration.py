"""Step 7 actual mock-broker panel actions, no GUI popup, serial or real bridge."""

from __future__ import annotations

import secrets
import sys
import tempfile
import unittest
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1] / "mcp-server"
sys.path.insert(0, str(SERVER_DIR))

from bridge_client import BridgeClient, BridgeFailure
from broker.adapter import BrokerBridgeClient
from broker.client import BrokerClient
from broker.identity import current_user_sid
from panel.actions import PanelActions
from panel.backend import PanelBackend


@unittest.skipUnless(sys.platform == "win32", "Windows named-pipe mock integration")
class PanelActionsIntegration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="panel-actions-")
        self.workspace = Path(self.temp.name)
        cfg = self.workspace / "esp32-ide"
        cfg.mkdir()
        (cfg / "board.json").write_text(
            '{"type":"generic","label":"Fixture","entry":"/main.py"}', encoding="utf-8"
        )
        (self.workspace / "my_program.py").write_text(
            "Kp = 7.0\nKi = 0.1\nKd = 0.01\n", encoding="utf-8"
        )
        script = str((SERVER_DIR.parent / "tests" / "mock_bridge.py").resolve())
        self.config = {
            "mode": "mock", "workspace": str(self.workspace), "profile": "generic",
            "bridge_script": None, "mock_script": script, "mock_scenario": "fileops",
            "allow_real_controls": False, "allow_real_writes": False, "local_bridge_owner": False,
        }
        self.sid = current_user_sid() + "-panel-action-" + secrets.token_hex(4)
        self.auth_path = self.workspace / "test-broker.token"
        self.local = BridgeClient(
            mode="mock", workspace=str(self.workspace), profile="generic",
            bridge_script=None, mock_script=script, mock_scenario="fileops",
        )
        self.client = BrokerClient(
            auth_path=self.auth_path, _identity_sid_for_testing=self.sid,
            backend_config=self.config,
        )
        self.backend = PanelBackend(BrokerBridgeClient(self.local, self.client))
        self.prompts = []
        self.accept = True
        def confirm(_title, message):
            self.prompts.append(message)
            return self.accept
        self.actions = PanelActions(self.backend, confirm)

    def tearDown(self):
        self.backend.close()
        owned = self.client._owned_process
        if owned is not None and owned.poll() is None:
            owned.terminate()
            owned.wait(timeout=5)
        self.temp.cleanup()

    def test_control_repl_decline_and_accept_are_shared_and_mock_only(self):
        self.backend.snapshot()
        disconnected = self.actions.control("disconnect")
        self.assertTrue(disconnected["ok"])
        self.assertFalse(self.backend.bridge.call_shared("status")["connected"])
        connected = self.actions.control("connect", port="MOCK0")
        self.assertTrue(connected["connected"])
        self.assertTrue(self.actions.control("run")["ran"])
        self.assertTrue(self.actions.control("interrupt")["interrupted"])
        self.assertTrue(self.actions.control("stop")["stopped"])
        self.accept = False
        self.assertFalse(self.actions.control("send", line="print(1)")["ok"])
        self.accept = True
        self.assertTrue(self.actions.control("send", line="print(1)")["sent"])
        self.assertEqual(len(self.prompts), 2)
        self.assertIn("print(1)", self.prompts[0])
        self.assertTrue(self.backend.simulated)
        self.assertIsNone(self.local._process)
        console = self.backend.bridge.read_console(since=None, max_chars=12000)
        self.assertIn("代码未执行", console["text"])
        self.assertNotIn("电机已停转", console["text"])

    def test_download_refused_then_strict_backup_and_source_change_rejected(self):
        self.backend.snapshot()
        before = self.backend.bridge.call_file("readfile", {"path": "/main.py", "raw": True, "maxBytes": 1024})
        self.accept = False
        result = self.actions.download("my_program.py", run=True)
        self.assertFalse(result["ok"])
        self.assertEqual(self.backend.bridge.call_file("readfile", {"path": "/main.py", "raw": True, "maxBytes": 1024}), before)
        self.accept = True
        result = self.actions.download("my_program.py", run=True)
        self.assertTrue(result["backupVerified"])
        self.assertTrue(result["ran"])
        self.assertTrue(result["compileOk"])
        after = self.backend.bridge.call_file("readfile", {"path": "/main.py", "raw": True, "maxBytes": 1024})
        self.assertNotEqual(after["crc"], before["crc"])
        def change_before_accept(_title, _message):
            (self.workspace / "my_program.py").write_text("print('changed')\n", encoding="utf-8")
            return True
        self.actions.confirm = change_before_accept
        with self.assertRaisesRegex(BridgeFailure, "发生变化"):
            self.actions.download("my_program.py")
        self.assertEqual(self.backend.bridge.call_file("readfile", {"path": "/main.py", "raw": True, "maxBytes": 1024}), after)
        self.assertIsNone(self.local._process)

    def test_other_client_change_while_confirming_rejects_old_epoch(self):
        self.backend.snapshot()
        peer = BrokerClient(auth_path=self.auth_path, _identity_sid_for_testing=self.sid,
                            backend_config=self.config)
        peer.connect()
        try:
            def concurrent_stop(_title, _message):
                epoch = peer.request("status")["status"]["_broker_control_epoch"]
                peer.request("control", {"command": "stop", "arguments": {}, "expected_epoch": epoch})
                return True
            self.actions.confirm = concurrent_stop
            (self.workspace / "esp32-ide" / "workbench-policy.json").write_text(
                '{"policy":"confirm-all"}', encoding="utf-8"
            )
            with self.assertRaisesRegex(BridgeFailure, "state changed"):
                self.actions.control("run")
        finally:
            peer.close()

    def test_claim_switch_and_policy_need_disconnect_and_each_confirmation(self):
        self.backend.snapshot()
        target = self.workspace / "new-workspace"
        target.mkdir()
        with self.assertRaisesRegex(BridgeFailure, "disconnect"):
            self.actions.workspace("claim", str(target), label="Next")
        self.actions.control("disconnect")
        self.accept = False
        self.assertFalse(self.actions.workspace("claim", str(target), label="Next")["ok"])
        self.assertFalse((target / "esp32-ide" / "board.json").exists())
        self.accept = True
        self.assertTrue(self.actions.workspace("claim", str(target), label="Next")["claimed"])
        self.assertEqual(self.actions.workspace("select", str(target))["profile"], "generic")
        self.assertEqual(self.backend.bridge.workspace, str(target.resolve()))
        self.actions.control("disconnect")
        self.accept = False
        self.assertFalse(self.actions.policy("auto")["ok"])
        self.accept = True
        self.assertEqual(self.actions.policy("confirm-all")["policy"], "confirm-all")
        self.assertEqual(self.backend.current_policy(), "confirm-all")


if __name__ == "__main__":
    unittest.main()
