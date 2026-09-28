"""UI-4 mock-only PanelActions integration checks; Windows broker, no hardware."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER_DIR = ROOT / "mcp-server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from host import CompactPanelApi, PanelOperationsApi


class FakeWindow:
    def __init__(self):
        self.condition = threading.Condition()
        self.dialogs: list[dict[str, str]] = []

    def evaluate_js(self, source: str):
        prefix = "window.__ui4ShowConfirmation("
        if not source.startswith(prefix) or not source.endswith(")"):
            raise AssertionError("unexpected host-to-page call")
        data = json.loads(source[len(prefix):-1])
        with self.condition:
            self.dialogs.append(data)
            self.condition.notify_all()

    def wait_for_dialog(self, count: int, timeout: float = 5.0):
        deadline = time.monotonic() + timeout
        with self.condition:
            while len(self.dialogs) < count:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AssertionError("confirmation was not displayed")
                self.condition.wait(remaining)
            return self.dialogs[count - 1]


class ActionsIntegration(unittest.TestCase):
    def setUp(self):
        self.old_namespace = os.environ.get("ESP32_CODEX_TEST_NAMESPACE")
        os.environ["ESP32_CODEX_TEST_NAMESPACE"] = secrets.token_hex(16)
        self.backends = []
        self.processes = []
        self.temp = tempfile.TemporaryDirectory(prefix="esp32-ui4-")
        self.root = Path(self.temp.name)

    def tearDown(self):
        for backend in self.backends:
            owned = backend.bridge._broker._owned_process
            if owned is not None:
                self.processes.append(owned)
            backend.close()
        for process in self.processes:
            if process.poll() is None:
                subprocess.run(
                    ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            try:
                process.wait(timeout=5)
            except Exception:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
        self.temp.cleanup()
        if self.old_namespace is None:
            os.environ.pop("ESP32_CODEX_TEST_NAMESPACE", None)
        else:
            os.environ["ESP32_CODEX_TEST_NAMESPACE"] = self.old_namespace

    def make_workspace(self, name: str = "project") -> Path:
        path = self.root / name
        config_dir = path / "esp32-ide"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "board.json").write_text(
            json.dumps({"type": "generic", "label": "UI-4 fixture", "entry": "/main.py"}),
            encoding="utf-8",
        )
        (path / "main.py").write_text("print('mock ui4')\n", encoding="utf-8")
        return path

    def make_api(self, *, scenario="control", workspace=None):
        from panel.backend import PanelBackend

        backend = PanelBackend.default_mock(
            workspace=str(workspace) if workspace else None,
            profile="generic", scenario=scenario,
        )
        self.backends.append(backend)
        window = FakeWindow()
        return backend, window, PanelOperationsApi(backend, window, confirmation_timeout=5)

    def invoke_with_confirmation(self, api, window, action, payload, *, accept: bool, number: int):
        result = {}
        thread = threading.Thread(target=lambda: result.setdefault("value", api.perform(action, payload)))
        thread.start()
        try:
            dialog = window.wait_for_dialog(number)
        except AssertionError as error:
            if thread.is_alive():
                thread.join(timeout=1)
            raise AssertionError(f"{error}; action result={result.get('value')!r}") from error
        self.assertTrue(dialog["target"])
        self.assertTrue(dialog["impact"])
        resolution = api.approve_current_operation() if accept else api.reject_current_operation()
        self.assertTrue(resolution["ok"])
        thread.join(timeout=10)
        self.assertFalse(thread.is_alive(), "confirmation did not release the action call")
        return result["value"]

    def test_workspace_discovery_claim_and_select_are_confirmed(self):
        backend, window, api = self.make_api(scenario="normal")
        path = self.root / "claimed"
        path.mkdir()
        (path / "main.py").write_text("print('claim fixture')\n", encoding="utf-8")
        discovered = api.discover_workspaces(str(self.root))
        self.assertEqual([], discovered["workspaces"])

        fields = {"path": str(path), "profile": "generic", "label": "UI-4 fixture", "entry": "/main.py"}
        denied = self.invoke_with_confirmation(
            api, window, "workspace_claim", fields, accept=False, number=1
        )
        self.assertFalse(denied["ok"])
        self.assertFalse((path / "esp32-ide" / "board.json").exists())
        self.assertFalse(denied["snapshot"]["workspace"]["path"])

        claimed = self.invoke_with_confirmation(
            api, window, "workspace_claim", fields, accept=True, number=2
        )
        self.assertTrue(claimed["ok"], claimed)
        self.assertTrue((path / "esp32-ide" / "board.json").is_file())
        found = api.discover_workspaces(str(self.root))
        self.assertEqual(str(path), found["workspaces"][0]["workspacePath"])

        selected = self.invoke_with_confirmation(
            api, window, "workspace_select", {"path": str(path)}, accept=True, number=3
        )
        self.assertTrue(selected["ok"], selected)
        self.assertEqual(str(path), selected["snapshot"]["workspace"]["path"])

    def test_compact_api_adds_only_minimize_and_close_window_calls(self):
        backend, _, _ = self.make_api(scenario="normal")

        class Window:
            minimized = False
            destroyed = False

            def minimize(self):
                self.minimized = True

            def destroy(self):
                self.destroyed = True

        window = Window()
        api = CompactPanelApi(backend, window)
        exposed = {name for name in dir(api) if not name.startswith("_") and callable(getattr(api, name))}
        self.assertEqual({"get_snapshot", "discover_workspaces", "perform", "approve_current_operation",
                          "reject_current_operation", "minimize_window", "close_window"}, exposed)
        self.assertTrue(api.minimize_window())
        self.assertTrue(window.minimized)
        self.assertTrue(api.close_window())
        self.assertTrue(window.destroyed)

    def test_policy_control_repl_cancel_epoch_race_and_safe_actions(self):
        workspace = self.make_workspace()
        backend, window, api = self.make_api(workspace=workspace)

        # Page cannot provide approval fields or arbitrary broker commands.
        for action, payload in (
            ("connect", {"port": "MOCK0", "confirmed": True}),
            ("__rpc__", {"command": "connect"}),
            ("run", {"token": "made-up"}),
        ):
            refused = api.perform(action, payload)
            self.assertFalse(refused["ok"])
            self.assertFalse(refused["snapshot"]["status"]["connected"])
        self.assertEqual(
            {"get_snapshot", "discover_workspaces", "perform", "approve_current_operation", "reject_current_operation"},
            {name for name in dir(api) if not name.startswith("_") and callable(getattr(api, name))},
        )

        policy = self.invoke_with_confirmation(
            api, window, "policy_set", {"policy": "confirm-all"}, accept=True, number=1
        )
        self.assertTrue(policy["ok"], policy)
        connected = self.invoke_with_confirmation(
            api, window, "connect", {"port": "MOCK0"}, accept=True, number=2
        )
        self.assertTrue(connected["ok"], connected)
        self.assertTrue(connected["snapshot"]["status"]["connected"])

        # A second broker client changes the epoch while a page confirmation is open.
        from broker.client import BrokerClient

        peer = BrokerClient(backend_config=backend.bridge._backend_config())
        peer.connect()
        self.processes.append(peer._owned_process) if peer._owned_process is not None else None
        result = {}
        action_thread = threading.Thread(
            target=lambda: result.setdefault("value", api.perform("interrupt", {}))
        )
        action_thread.start()
        window.wait_for_dialog(3)
        state = peer.request("status")["status"]
        epoch = state["_broker_control_epoch"]
        peer.request("control", {
            "command": "run", "arguments": {}, "expected_epoch": epoch,
        })
        api.approve_current_operation()
        action_thread.join(timeout=10)
        self.assertFalse(action_thread.is_alive())
        self.assertFalse(result["value"]["ok"], "stale approved plan must fail closed")
        self.assertTrue(result["value"]["snapshot"]["status"]["connected"])
        peer.close()

        console_before = api.get_snapshot()["console"]["text"]
        run = self.invoke_with_confirmation(api, window, "run", {}, accept=False, number=4)
        self.assertFalse(run["ok"])
        self.assertEqual(console_before, run["snapshot"]["console"]["text"])
        repl = self.invoke_with_confirmation(
            api, window, "repl", {"line": "print('one line')"}, accept=True, number=5
        )
        self.assertTrue(repl["ok"], repl)
        self.assertIn("REPL 行已接收", repl["snapshot"]["console"]["text"])
        before_safe = len(window.dialogs)
        stopped = api.perform("stop", {})
        self.assertTrue(stopped["ok"], stopped)
        self.assertEqual(before_safe, len(window.dialogs), "stop follows PanelActions safe-action policy")
        disconnected = api.perform("disconnect", {})
        self.assertTrue(disconnected["ok"], disconnected)
        self.assertFalse(disconnected["snapshot"]["status"]["connected"])

    def test_download_uses_mock_strict_backup_and_refreshes_snapshot(self):
        workspace = self.make_workspace("download")
        backend, window, api = self.make_api(scenario="fileops", workspace=workspace)
        self.assertTrue(api.get_snapshot()["status"]["connected"])
        downloaded = self.invoke_with_confirmation(
            api, window, "download", {"filename": "main.py"}, accept=True, number=1
        )
        self.assertTrue(downloaded["ok"], downloaded)
        self.assertTrue(downloaded["details"]["targetExisted"])
        self.assertTrue(downloaded["details"]["backupVerified"])
        self.assertGreater(downloaded["details"]["backupSize"], 0)

        download_run = self.invoke_with_confirmation(
            api, window, "download_run", {"filename": "main.py"}, accept=True, number=2
        )
        self.assertTrue(download_run["ok"], download_run)
        self.assertTrue(download_run["details"]["targetExisted"])
        self.assertTrue(download_run["details"]["backupVerified"])
        self.assertTrue(download_run["details"]["ran"])
        self.assertTrue(download_run["snapshot"]["status"]["connected"])


if __name__ == "__main__":
    if os.name != "nt":
        print("Skipped: verify_actions.py requires Windows named pipes; all operations are mock-only.")
        raise SystemExit(0)
    unittest.main(verbosity=2)
