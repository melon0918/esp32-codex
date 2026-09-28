"""Windows-only no-hardware integration check for a shared read-only mock broker."""

from __future__ import annotations

import os
import secrets
import sys
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER_DIR = ROOT / "mcp-server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))


@unittest.skipUnless(os.name == "nt", "requires Windows named pipes; mock only")
class SharedReadOnlyBroker(unittest.TestCase):
    def test_two_clients_share_snapshot_console_and_independent_leases(self) -> None:
        import panel.backend
        from broker.client import BrokerClient
        from host import ReadOnlyApi

        namespace = secrets.token_hex(16)
        previous = os.environ.get("ESP32_CODEX_TEST_NAMESPACE")
        os.environ["ESP32_CODEX_TEST_NAMESPACE"] = namespace
        backend = None
        peer = None
        owned_processes = []
        try:
            # Match the established MCP mock fixture exactly; no real bridge path,
            # device port, control flag, or mutation capability is configured.
            backend = panel.backend.PanelBackend.default_mock(
                workspace=None, profile="generic", scenario="fileops"
            )
            local = backend.bridge._local
            owned = backend.bridge._broker
            if owned._owned_process is not None:
                owned_processes.append(owned._owned_process)
            peer = BrokerClient(backend_config={
                "mode": "mock", "workspace": None, "profile": "generic",
                "bridge_script": None,
                "mock_script": str((ROOT / "tests" / "mock_bridge.py").resolve()),
                "mock_scenario": "fileops", "allow_real_controls": False,
                "allow_real_writes": False, "local_bridge_owner": False,
            })
            peer.connect()
            if peer._owned_process is not None:
                owned_processes.append(peer._owned_process)

            api = ReadOnlyApi(backend)
            ui_snapshot = api.get_snapshot()
            if owned._owned_process is not None and owned._owned_process not in owned_processes:
                owned_processes.append(owned._owned_process)
            peer_status = peer.request("status")["status"]
            peer_console = peer.request("console", {"since": None, "max_chars": 12000})
            self.assertTrue(ui_snapshot["simulated"])
            self.assertEqual("mock_bridge", local.source)
            self.assertEqual("MOCK0", ui_snapshot["status"]["port"])
            self.assertEqual(peer_status["port"], ui_snapshot["status"]["port"])
            self.assertEqual(ui_snapshot["console"]["text"], peer_console["text"])
            self.assertEqual("[模拟] 控制工具夹具就绪；这不是实体设备输出。\n", peer_console["text"])
            self.assertIsNone(local._process, "UI snapshot must not start a second local bridge")

            backend.close()
            backend = None
            # The peer lease keeps the single broker and its mock bridge alive.
            after_close = peer.request("status")["status"]
            self.assertEqual(peer_status["port"], after_close["port"])
            self.assertIsNone(owned._connection)
        finally:
            if backend is not None:
                backend.close()
            if peer is not None:
                peer.close()
            for owned_process in owned_processes:
                if owned_process.poll() is None:
                    subprocess.run(
                        ["taskkill.exe", "/PID", str(owned_process.pid), "/T", "/F"],
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, check=False,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                try:
                    owned_process.wait(timeout=5)
                except Exception:
                    if owned_process.poll() is None:
                        owned_process.terminate()
                        owned_process.wait(timeout=5)
            if previous is None:
                os.environ.pop("ESP32_CODEX_TEST_NAMESPACE", None)
            else:
                os.environ["ESP32_CODEX_TEST_NAMESPACE"] = previous

    def test_normal_scenario_renders_empty_workspace_and_disconnected_state(self) -> None:
        import panel.backend
        from host import ReadOnlyApi

        previous = os.environ.get("ESP32_CODEX_TEST_NAMESPACE")
        os.environ["ESP32_CODEX_TEST_NAMESPACE"] = secrets.token_hex(16)
        backend = None
        owned_processes = []
        try:
            backend = panel.backend.PanelBackend.default_mock(
                workspace=None, profile="generic", scenario="normal"
            )
            api = ReadOnlyApi(backend)
            snapshot = api.get_snapshot()
            owned_process = backend.bridge._broker._owned_process
            if owned_process is not None:
                owned_processes.append(owned_process)
            self.assertTrue(snapshot["simulated"])
            self.assertEqual("", snapshot["workspace"]["path"])
            self.assertFalse(snapshot["status"]["connected"])
            self.assertEqual("confirm-write", snapshot["policy"]["name"])
            self.assertFalse(hasattr(api, "connect"))
        finally:
            if backend is not None:
                backend.close()
            for owned_process in owned_processes:
                if owned_process.poll() is None:
                    subprocess.run(
                        ["taskkill.exe", "/PID", str(owned_process.pid), "/T", "/F"],
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, check=False,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                try:
                    owned_process.wait(timeout=5)
                except Exception:
                    if owned_process.poll() is None:
                        owned_process.terminate()
                        owned_process.wait(timeout=5)
            if previous is None:
                os.environ.pop("ESP32_CODEX_TEST_NAMESPACE", None)
            else:
                os.environ["ESP32_CODEX_TEST_NAMESPACE"] = previous


if __name__ == "__main__":
    unittest.main(verbosity=2)
