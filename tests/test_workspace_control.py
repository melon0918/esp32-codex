"""Step 5 local-workspace, board claim and policy mutation tests (no hardware)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mcp-server"))

from workspace_control import claim_workspace, policy_snapshot, set_workspace_policy, selectable_workspace
from workspaces import (
    WorkspaceError, _wsl_mount_to_windows_path, absolute_directory, detect_workspace,
)


class WorkspaceMutationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="codex-workspace-policy-")
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_wsl_mount_mapping_is_narrow_and_preserves_unicode_and_spaces(self):
        self.assertEqual(
            _wsl_mount_to_windows_path("/mnt/d/四足机器人 workspace"),
            "D:\\四足机器人 workspace",
        )
        self.assertEqual(_wsl_mount_to_windows_path("/mnt/C"), "C:\\")
        for value in ("/home/user/project", "/mnt/drive/project", "//mnt/d/project"):
            with self.subTest(value=value):
                self.assertIsNone(_wsl_mount_to_windows_path(value))

    @unittest.skipUnless(os.name == "nt", "WSL mount conversion targets the Windows MCP host")
    def test_absolute_directory_accepts_wsl_drive_spelling(self):
        root = self.root / "四足机器人 workspace"
        root.mkdir()
        windows_path = str(root.resolve())
        drive = windows_path[0].lower()
        tail = windows_path[3:].replace("\\", "/")
        wsl_path = f"/mnt/{drive}/{tail}"
        self.assertEqual(absolute_directory(wsl_path), root.resolve())

    def test_unrecognized_workspace_requires_claim_and_preserves_existing_board(self):
        self.assertFalse(detect_workspace(self.root)["detected"])
        info = claim_workspace(str(self.root), "generic", "四足机器人", "/main.py")
        self.assertTrue(info["claimed"])
        self.assertEqual(info["profile"], "generic")
        self.assertEqual(info["profileLabel"], "四足机器人")
        self.assertEqual(info["entry"], "/main.py")
        before = (self.root / "esp32-ide" / "board.json").read_bytes()
        with self.assertRaisesRegex(WorkspaceError, "not be overwritten"):
            claim_workspace(str(self.root), "hiwonder", None, "/corex.py")
        self.assertEqual((self.root / "esp32-ide" / "board.json").read_bytes(), before)

    def test_invalid_claim_leaves_no_board_file(self):
        for profile, label, entry in [
            ("bad", None, "/main.py"), ("generic", "\n", "/main.py"),
            ("generic", "valid", "../outside.py"), ("generic", "valid", "/program.txt"),
        ]:
            with self.subTest(profile=profile, label=label, entry=entry):
                with self.assertRaises(WorkspaceError):
                    claim_workspace(str(self.root), profile, label, entry)
                self.assertFalse((self.root / "esp32-ide" / "board.json").exists())

    def test_invalid_existing_board_is_not_silently_replaced_by_detection(self):
        config = self.root / "esp32-ide"
        config.mkdir()
        (config / "board.json").write_text("{invalid-json", encoding="utf-8")
        (self.root / "四路巡线.py").write_text("import Hiwonder\n", encoding="utf-8")
        self.assertTrue(detect_workspace(self.root)["detected"])
        with self.assertRaisesRegex(WorkspaceError, "board.json is invalid"):
            selectable_workspace(str(self.root))
        self.assertEqual((config / "board.json").read_text(encoding="utf-8"), "{invalid-json")

    def test_isolated_mock_test_namespace_never_changes_real_broker_identity(self):
        from broker.client import BrokerClient
        from broker.identity import current_user_sid
        namespace = "a" * 32
        with patch.dict(os.environ, {"ESP32_CODEX_TEST_NAMESPACE": namespace}):
            mock_client = BrokerClient(auth_token="0" * 64, backend_config={"mode": "mock"})
            real_client = BrokerClient(auth_token="0" * 64, backend_config={"mode": "bridge"})
        self.assertEqual(mock_client._sid, current_user_sid() + "-test-" + namespace)
        self.assertEqual(mock_client._identity_sid_for_testing, mock_client._sid)
        self.assertEqual(real_client._sid, current_user_sid())
        self.assertIsNone(real_client._identity_sid_for_testing)

    def test_policy_compare_and_swap_preserves_custom_fields_and_default(self):
        claim_workspace(str(self.root), "generic", None, None)
        start = policy_snapshot(str(self.root))
        self.assertEqual(start["policy"], "confirm-write")
        self.assertEqual(start["revision"], "missing")
        result = set_workspace_policy(str(self.root), "confirm-all", start["revision"])
        self.assertEqual(result["policy"], "confirm-all")
        saved = self.root / "esp32-ide" / "workbench-policy.json"
        data = json.loads(saved.read_text(encoding="utf-8"))
        data["customSetting"] = {"keep": True}
        saved.write_text(json.dumps(data), encoding="utf-8")
        latest = policy_snapshot(str(self.root))
        result = set_workspace_policy(str(self.root), "auto", latest["revision"])
        self.assertEqual(result["policy"], "auto")
        self.assertEqual(json.loads(saved.read_text(encoding="utf-8"))["customSetting"], {"keep": True})
        with self.assertRaisesRegex(WorkspaceError, "changed"):
            set_workspace_policy(str(self.root), "confirm-all", latest["revision"])

    def test_invalid_policy_file_is_preserved_and_never_overwritten(self):
        claim_workspace(str(self.root), "hiwonder", None, None)
        target = self.root / "esp32-ide" / "workbench-policy.json"
        target.write_text("not-json", encoding="utf-8")
        with self.assertRaisesRegex(WorkspaceError, "invalid"):
            set_workspace_policy(str(self.root), "auto", "missing")
        self.assertEqual(target.read_text(encoding="utf-8"), "not-json")
        with self.assertRaises(WorkspaceError):
            set_workspace_policy(str(self.root), "unsupported", "missing")


if __name__ == "__main__":
    unittest.main()
