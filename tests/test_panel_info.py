"""Step 6: information panel is tested hidden; no foreground window or hardware."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

SERVER_DIR = Path(__file__).resolve().parents[1] / "mcp-server"
sys.path.insert(0, str(SERVER_DIR))

from panel.backend import PanelBackend


class PanelBackendTests(unittest.TestCase):
    def test_readonly_snapshot_does_not_issue_connect_or_mutation(self):
        fake = Mock()
        fake.simulated = True
        fake.source = "mock_bridge"
        fake.workspace_current.return_value = {
            "workspacePath": "fixture", "profile": "generic",
            "info": {"entry": "/main.py", "profileLabel": "四足机器人"},
        }
        fake.call_shared.side_effect = [
            {"connected": False, "port": "", "busy": False},
            {"ports": [{"device": "MOCK0"}]},
            {"connected": False, "port": "", "busy": False},
            {"ports": [{"device": "MOCK0"}]},
        ]
        fake.workspace_policy.return_value = {"policy": "confirm-write", "source": "default"}
        fake.read_console.return_value = {"text": "fixture", "cursor": 9, "dropped": False}
        backend = PanelBackend(fake)
        first = backend.snapshot()
        second = backend.snapshot()
        self.assertEqual(first["workspace"]["info"]["entry"], "/main.py")
        self.assertEqual(first["source"], "mock_bridge")
        self.assertTrue(first["simulated"])
        self.assertEqual(fake.read_console.call_args_list[0].kwargs["since"], None)
        self.assertEqual(fake.read_console.call_args_list[1].kwargs["since"], 9)
        self.assertEqual(fake.call_shared.call_count, 4)
        for method in ("call_control", "call_file", "workspace_select", "workspace_claim",
                       "workspace_policy_set"):
            getattr(fake, method).assert_not_called()
        backend.close()
        fake.close.assert_called_once()

    def test_no_workspace_uses_conservative_policy_without_policy_mutation(self):
        fake = Mock(simulated=True, source="mock_bridge")
        fake.workspace_current.return_value = {"workspacePath": None, "profile": "generic", "info": None}
        fake.call_shared.side_effect = [{"connected": False}, {"ports": []}]
        fake.read_console.return_value = {"text": "", "cursor": 0, "dropped": False}
        snapshot = PanelBackend(fake).snapshot()
        self.assertEqual(snapshot["policy"]["policy"], "confirm-write")
        fake.workspace_policy.assert_not_called()


@unittest.skipUnless(os.name == "nt", "Windows mock-broker integration")
class MockPanelIntegrationTests(unittest.TestCase):
    def test_readonly_panel_backend_uses_one_owned_mock_bridge(self):
        import secrets
        from bridge_client import BridgeClient
        from broker.adapter import BrokerBridgeClient
        from broker.client import BrokerClient
        from broker.identity import current_user_sid

        with tempfile.TemporaryDirectory(prefix="codex-panel-test-") as temp:
            workspace = Path(temp)
            config_dir = workspace / "esp32-ide"
            config_dir.mkdir()
            (config_dir / "board.json").write_text(
                '{"type":"generic","entry":"/main.py","label":"Fixture"}', encoding="utf-8"
            )
            script = SERVER_DIR.parent / "tests" / "mock_bridge.py"
            local = BridgeClient(mode="mock", workspace=str(workspace),
                                 profile="generic", bridge_script=None,
                                 mock_script=str(script), mock_scenario="fileops")
            pipe = BrokerClient(auth_path=workspace / "test-broker.token",
                                _identity_sid_for_testing=current_user_sid() + "-panel-" + secrets.token_hex(4),
                                backend_config={
                                    "mode": "mock", "workspace": str(workspace), "profile": "generic",
                                    "bridge_script": None, "mock_script": str(script.resolve()),
                                    "mock_scenario": "fileops", "allow_real_controls": False,
                                    "allow_real_writes": False, "local_bridge_owner": False,
                                })
            backend = PanelBackend(BrokerBridgeClient(local, pipe))
            try:
                data = backend.snapshot()
                self.assertEqual(data["source"], "mock_bridge")
                self.assertTrue(data["simulated"])
                self.assertEqual(data["status"]["port"], "MOCK0")
                self.assertEqual(data["ports"]["ports"][0]["device"], "MOCK0")
                self.assertEqual(data["workspace"]["info"]["entry"], "/main.py")
                self.assertIsNone(local._process)
            finally:
                backend.close()
                process = pipe._owned_process
                if process is not None and process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)

    def test_closing_panel_preserves_other_clients_mock_bridge_lease(self):
        import secrets
        from bridge_client import BridgeClient
        from broker.adapter import BrokerBridgeClient
        from broker.client import BrokerClient
        from broker.identity import current_user_sid

        with tempfile.TemporaryDirectory(prefix="codex-panel-lease-") as temp:
            workspace = Path(temp)
            (workspace / "esp32-ide").mkdir()
            (workspace / "esp32-ide" / "board.json").write_text(
                '{"type":"generic","entry":"/main.py","label":"Fixture"}', encoding="utf-8"
            )
            script = str((SERVER_DIR.parent / "tests" / "mock_bridge.py").resolve())
            config = {
                "mode": "mock", "workspace": str(workspace), "profile": "generic",
                "bridge_script": None, "mock_script": script, "mock_scenario": "fileops",
                "allow_real_controls": False, "allow_real_writes": False,
                "local_bridge_owner": False,
            }
            sid = current_user_sid() + "-panel-lease-" + secrets.token_hex(4)
            auth_path = workspace / "broker.token"
            owned = BrokerClient(auth_path=auth_path, _identity_sid_for_testing=sid,
                                 backend_config=config)
            peer = BrokerClient(auth_path=auth_path, _identity_sid_for_testing=sid,
                                backend_config=config)
            local = BridgeClient(mode="mock", workspace=str(workspace), profile="generic",
                                 bridge_script=None, mock_script=script, mock_scenario="fileops")
            backend = PanelBackend(BrokerBridgeClient(local, owned))
            try:
                backend.snapshot()
                peer.connect()
                first = peer.request("status")
                backend.close()
                second = peer.request("status")
                self.assertTrue(second["status"]["connected"])
                self.assertEqual(second["backend_pid"], first["backend_pid"])
                self.assertEqual(second["broker_pid"], first["broker_pid"])
                self.assertIsNone(owned._connection)
                self.assertIsNone(local._process)
            finally:
                backend.close()
                peer.close()
                process = owned._owned_process
                if process is not None and process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)

@unittest.skipUnless(os.name == "nt", "hidden Windows Tk widget test")
class HiddenInformationPanelTests(unittest.TestCase):
    def test_hidden_real_tk_widgets_show_mock_fields_without_mapping_window(self):
        import tkinter as tk
        from panel.view import create_information_panel

        class FakeBackend:
            def __init__(self):
                self.closed = False
                self.calls = 0
            def snapshot(self):
                self.calls += 1
                return {
                    "workspace": {
                        "workspacePath": r"C:\fixture\quad",
                        "profile": "generic",
                        "info": {"profileLabel": "四足机器人", "entry": "/main.py"},
                    },
                    "status": {"connected": True, "port": "MOCK0", "busy": False},
                    "ports": {"ports": [{"device": "MOCK0"}]},
                    "policy": {"policy": "confirm-write", "source": "workbench-policy.json"},
                    "console": {"text": "模拟控制台", "cursor": 12, "dropped": False},
                    "source": "mock_bridge", "simulated": True,
                }
            def close(self):
                self.closed = True

        # Tk is immediately withdrawn BEFORE processing any GUI event or idle task.
        root = tk.Tk()
        root.withdraw()
        backend = FakeBackend()
        try:
            panel = create_information_panel(root, backend, auto_refresh=False)
            root.update_idletasks()
            self.assertFalse(root.winfo_viewable(), "must never map an actual window")
            self.assertIn("四足机器人", panel._values["profile"].get())
            self.assertIn("/main.py", panel._values["profile"].get())
            self.assertIn("模拟数据", panel._values["source"].get())
            self.assertIn("MOCK0", panel._values["device"].get())
            self.assertIn("confirm-write", panel._values["policy"].get())
            self.assertIn("模拟控制台", panel.console.get("1.0", "end"))
            self.assertEqual(backend.calls, 1)
            panel.close()
            self.assertTrue(backend.closed)
        finally:
            if not backend.closed:
                root.destroy()

    def test_hidden_refresh_errors_never_raise_popup(self):
        import tkinter as tk
        from panel.view import create_information_panel

        fake = Mock()
        fake.snapshot.side_effect = RuntimeError("mock error")
        root = tk.Tk()
        root.withdraw()
        try:
            panel = create_information_panel(root, fake, auto_refresh=False)
            root.update_idletasks()
            self.assertFalse(root.winfo_viewable())
            self.assertIn("mock error", panel.error_var.get())
            self.assertFalse(panel.refresh())
            panel.close()
            fake.close.assert_called_once()
        finally:
            try:
                root.destroy()
            except tk.TclError:
                pass

    def test_hidden_tk_panel_routes_agent_confirmation_through_injected_user_prompt(self):
        import tkinter as tk
        from panel.view import create_information_panel

        class FakeBackend:
            def __init__(self):
                self.closed = False
                self.decisions = []
            def snapshot(self):
                return {
                    "workspace": {"workspacePath": r"C:\fixture", "profile": "generic",
                                  "info": {"profileLabel": "四足机器人", "entry": "/main.py"}},
                    "status": {"connected": False, "port": "", "busy": False},
                    "ports": {"ports": []},
                    "policy": {"policy": "confirm-write", "source": "fixture"},
                    "console": {"text": "", "cursor": 0, "dropped": False},
                    "agentConfirmations": [{
                        "id": "B" * 32, "state": "pending", "title": "写入板载文件",
                        "target": "FAKE0 /main.py", "impact": "写入夹具代码",
                        "workspacePath": r"C:\fixture", "profile": "generic",
                        "entry": "/main.py", "control_epoch": 2,
                        "policy_revision": "a" * 64, "expires_in_ms": 30000,
                    }],
                    "source": "mock_bridge", "simulated": True,
                }
            def resolve_agent_confirmation(self, confirmation_id, *, approve):
                self.decisions.append((confirmation_id, approve))
                return {"ok": True, "state": "approved" if approve else "rejected"}
            def close(self):
                self.closed = True

        root = tk.Tk()
        root.withdraw()
        backend = FakeBackend()
        prompts = []
        try:
            panel = create_information_panel(
                root, backend, auto_refresh=False,
                confirm_agent=lambda item: prompts.append(item) or True,
            )
            root.update_idletasks()
            self.assertFalse(root.winfo_viewable())
            self.assertEqual(prompts[0]["control_epoch"], 2)
            self.assertEqual(backend.decisions, [("B" * 32, True)])
            panel.close()
            self.assertTrue(backend.closed)
        finally:
            if not backend.closed:
                root.destroy()


    def test_hidden_workspace_discovery_and_selection_sync_never_maps_window(self):
        import tkinter as tk
        from panel.view import create_information_panel

        fake = Mock()
        fake.snapshot.return_value = {
            "workspace": {"workspacePath": r"C:\fixture\old", "profile": "generic",
                          "info": {"profileLabel": "Generic", "entry": "/main.py"}},
            "status": {"connected": False, "port": "", "busy": False},
            "ports": {"ports": [{"device": "MOCK0"}]},
            "policy": {"policy": "confirm-write", "source": "default"},
            "console": {"text": "", "cursor": 0, "dropped": False},
            "source": "mock_bridge", "simulated": True,
        }
        fake.current_policy.return_value = "confirm-all"
        fake.discover_workspaces.return_value = {
            "workspaces": [{"workspacePath": r"C:\fixture\old"},
                           {"workspacePath": r"C:\fixture\next"}],
        }
        root = tk.Tk()
        root.withdraw()
        try:
            panel = create_information_panel(root, fake, auto_refresh=False,
                                             confirm=lambda *_: self.fail("read-only discovery must not prompt"))
            panel.workspace_var.set(r"C:\fixture")
            panel._discover_workspaces()
            self.assertEqual(len(panel.workspace_select["values"]), 2)
            self.assertIn("2 个工作区", panel.error_var.get())
            fake.workspace_select.assert_not_called()
            panel._show_action_result(
                {"ok": True, "workspacePath": r"C:\fixture\next",
                 "profile": "hiwonder", "entry": "/corex.py"}, "select"
            )
            self.assertEqual(panel.entry_var.get(), "/corex.py")
            self.assertEqual(panel.profile_var.get(), "hiwonder")
            self.assertEqual(panel.policy_var.get(), "confirm-all")
            root.update_idletasks()
            self.assertFalse(root.winfo_viewable())
            panel.close()
            fake.close.assert_called_once()
        finally:
            try:
                root.destroy()
            except tk.TclError:
                pass

if __name__ == "__main__":
    unittest.main()
