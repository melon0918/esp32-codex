"""Bridge helpers use hidden subprocesses while preserving their JSON pipes."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


SERVER_DIR = Path(__file__).resolve().parents[1] / "mcp-server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from bridge_client import BridgeClient


class BridgeSpawnTests(unittest.TestCase):
    def test_mock_bridge_uses_no_window_and_keeps_stdio_pipes(self) -> None:
        client = BridgeClient(
            mode="mock",
            workspace=None,
            profile="generic",
            bridge_script=None,
            mock_script=str(Path(__file__).resolve().parents[1] / "tests" / "mock_bridge.py"),
            mock_scenario="fileops",
        )
        self._assert_spawn_flags(client)

    def test_explicit_bridge_helper_uses_no_window_and_keeps_stdio_pipes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="esp32-bridge-spawn-") as raw:
            root = Path(raw)
            script = root / "bridge.py"
            script.write_text("pass\n", encoding="utf-8")
            client = BridgeClient(
                mode="bridge",
                workspace=str(root),
                profile="generic",
                bridge_script=str(script),
                mock_script=str(Path(__file__).resolve().parents[1] / "tests" / "mock_bridge.py"),
                mock_scenario="fileops",
            )
            self._assert_spawn_flags(client)

    def _assert_spawn_flags(self, client: BridgeClient) -> None:
        process = MagicMock()
        process.stdout = None
        process.poll.return_value = None
        with patch("bridge_client.subprocess.Popen", return_value=process) as spawn:
            client.start()
            client.close()
        kwargs = spawn.call_args.kwargs
        expected = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.assertEqual(kwargs["creationflags"], expected)
        self.assertIs(kwargs["stdin"], subprocess.PIPE)
        self.assertIs(kwargs["stdout"], subprocess.PIPE)
        self.assertIs(kwargs["stderr"], subprocess.DEVNULL)


if __name__ == "__main__":
    unittest.main()
