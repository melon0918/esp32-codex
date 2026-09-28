"""Direct strict-backup bridge tests with serial imports stubbed; no bridge process or hardware."""

from __future__ import annotations

import builtins
import importlib.util
import json
import re
import sys
import tempfile
import types
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BRIDGE_SCRIPT = PROJECT_ROOT / "bridge.py"


def load_bridge_module():
    fake_serial = types.ModuleType("serial")
    fake_serial.__path__ = []
    fake_serial.Serial = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("serial.Serial must never be called by this test")
    )
    fake_tools = types.ModuleType("serial.tools")
    fake_tools.__path__ = []
    fake_list_ports = types.ModuleType("serial.tools.list_ports")
    fake_list_ports.comports = lambda: []
    fake_tools.list_ports = fake_list_ports
    fake_serial.tools = fake_tools
    module_name = "esp32_bridge_no_hardware_fixture"
    spec = importlib.util.spec_from_file_location(module_name, BRIDGE_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load bridge module")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {
        "serial": fake_serial,
        "serial.tools": fake_tools,
        "serial.tools.list_ports": fake_list_ports,
    }):
        spec.loader.exec_module(module)
    return module


BRIDGE = load_bridge_module()


class FakeBoard(BRIDGE.Board):
    def __init__(
        self,
        workspace: str,
        *,
        present: bool = True,
        enum_error: bool = False,
        read_error: bool = False,
        crc_error: bool = False,
        use_board_read: bool = False,
        backup_error: bool = False,
    ) -> None:
        super().__init__(workspace, profile="generic")
        self.ser = object()
        self.present = present
        self.enum_error = enum_error
        self.read_error = read_error
        self.crc_error = crc_error
        self.use_board_read = use_board_read
        self.backup_error = backup_error
        self.commands: list[str] = []
        self.values: list[str] = []
        self.original = b"old original\x00bytes\xff"
        self.expected = b"new content\n"
        self._reading_old = False

    def interrupt(self, mute: bool = False) -> None:
        return None

    def stop_motors(self, mute: bool = False) -> None:
        return None

    def cmd(self, command: str, *args, **kwargs):
        self.commands.append(command)
        return ""

    def value(self, command: str, *args, **kwargs) -> str:
        self.values.append(command)
        if "CODEX_TARGET_PRESENT" in command:
            if self.enum_error:
                raise RuntimeError("parent directory listing failed")
            return "CODEX_TARGET_PRESENT" if self.present else "CODEX_TARGET_ABSENT"
        if "len(open(" in command:
            return str(len(self.original))
        if "binascii.hexlify" in command:
            match = re.search(r"\[(\d+):(\d+)\]", command)
            if not match:
                return ""
            start, end = (int(value) for value in match.groups())
            import binascii
            return binascii.hexlify(self.original[start:end]).decode("ascii")
        if "binascii.crc32(open(" in command:
            content = self.original if self._reading_old else self.expected
            crc = zlib.crc32(content) & 0xFFFFFFFF
            return str(crc ^ 1 if self.crc_error and self._reading_old else crc)
        if "os.remove(" in command:
            return "FILE_DELETED"
        if "os.stat(" in command:
            return str(len(self.expected))
        if "binascii.crc32" in command:
            return str(zlib.crc32(self.expected) & 0xFFFFFFFF)
        if "compile(" in command:
            return "COMPILE_OK"
        return ""

    def read_board_file(self, path, verify=False, max_bytes=None):
        if self.read_error:
            raise RuntimeError("readback CRC/length verification failed")
        if self.use_board_read:
            self._reading_old = True
            try:
                return super().read_board_file(path, verify=verify, max_bytes=max_bytes)
            finally:
                self._reading_old = False
        return self.original

    def save_verified_backup(self, path, data):
        if self.backup_error:
            raise RuntimeError("backup directory is not writable")
        return super().save_verified_backup(path, data)


class StrictBackupBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="esp32-strict-backup-")
        self.workspace = Path(self.temp.name)
        self.bridge = BRIDGE.Bridge(str(self.workspace), profile="generic")
        self._original_console_write = BRIDGE.console_write
        self._had_open = hasattr(BRIDGE, "open")
        self._original_open = getattr(BRIDGE, "open", None)

    def tearDown(self) -> None:
        BRIDGE.console_write = self._original_console_write
        if self._had_open:
            BRIDGE.open = self._original_open
        elif hasattr(BRIDGE, "open"):
            del BRIDGE.open
        self.temp.cleanup()

    def attach_board(self, **kwargs) -> FakeBoard:
        board = FakeBoard(str(self.workspace), **kwargs)
        self.bridge.board = board
        return board

    def assert_no_target_mutation(self, board: FakeBoard) -> None:
        self.assertFalse(any("f = open('/main.py', 'wb')" in command for command in board.commands))
        self.assertFalse(any("os.remove('/main.py')" in command for command in board.values))

    def test_verified_backup_keeps_raw_bytes_and_reports_explicit_state(self) -> None:
        board = self.attach_board()
        result = board.backup_before_mutation("/main.py")
        backup_path = Path(result["backupPath"])

        self.assertTrue(result["targetExisted"])
        self.assertTrue(result["backupVerified"])
        self.assertEqual(backup_path.parent, self.workspace)
        self.assertEqual(backup_path.read_bytes(), board.original)
        self.assertEqual(result["backupSize"], len(board.original))
        self.assertEqual(result["backupCrc"], "%08X" % (zlib.crc32(board.original) & 0xFFFFFFFF))

    def test_absent_target_is_distinct_from_verified_backup(self) -> None:
        board = self.attach_board(present=False)
        result = board.backup_before_mutation("/new.py")

        self.assertEqual(result, {
            "targetExisted": False,
            "backupVerified": False,
            "backupPath": "",
            "backupSize": 0,
            "backupCrc": "",
        })

    def test_enumeration_read_crc_and_unwritable_backup_fail_before_overwrite_or_delete(self) -> None:
        for failure in (
            {"enum_error": True},
            {"read_error": True},
            {"crc_error": True, "use_board_read": True},
            {"backup_error": True},
        ):
            with self.subTest(failure=failure):
                board = self.attach_board(**failure)
                with self.assertRaises(RuntimeError):
                    self.bridge.c_writefile({
                        "path": "/main.py", "content": "new", "strictBackup": True
                    })
                self.assert_no_target_mutation(board)
                board = self.attach_board(**failure)
                with self.assertRaises(RuntimeError):
                    self.bridge.c_deletefile({"path": "/main.py", "strictBackup": True})
                self.assert_no_target_mutation(board)

    def test_unavailable_backup_directory_fails_before_target_mutation(self) -> None:
        board = self.attach_board()
        board.workspace = str(self.workspace / "missing-backup-directory")
        with self.assertRaisesRegex(RuntimeError, "备份目录不可用"):
            self.bridge.c_writefile({
                "path": "/main.py", "content": "new", "strictBackup": True
            })
        self.assert_no_target_mutation(board)

    def test_missing_target_deletion_is_refused(self) -> None:
        board = self.attach_board(present=False)
        with self.assertRaisesRegex(RuntimeError, "未发出删除命令"):
            self.bridge.c_deletefile({"path": "/main.py", "strictBackup": True})
        self.assert_no_target_mutation(board)

    def test_backup_readback_failure_does_not_open_target_for_overwrite(self) -> None:
        board = self.attach_board()
        real_open = builtins.open

        def fail_backup_readback(path, mode="r", *args, **kwargs):
            if mode == "rb" and "codex_backup_" in str(path):
                raise OSError("injected backup readback failure")
            return real_open(path, mode, *args, **kwargs)

        BRIDGE.open = fail_backup_readback
        with self.assertRaisesRegex(RuntimeError, "回读失败"):
            self.bridge.c_writefile({
                "path": "/main.py", "content": "new", "strictBackup": True
            })
        self.assert_no_target_mutation(board)

    def test_legacy_commands_without_strict_flag_keep_dsh_default_path(self) -> None:
        board = self.attach_board(read_error=True)
        self._original_console_write = BRIDGE.console_write
        BRIDGE.console_write = lambda _text: None

        write_result = self.bridge.c_writefile({"path": "/main.py", "content": "new content\n"})
        self.assertTrue(write_result["ok"])
        self.assertNotIn("backupVerified", write_result)
        self.assertTrue(any("f = open('/main.py', 'wb')" in command for command in board.commands))

        board.commands.clear()
        board.values.clear()
        delete_result = self.bridge.c_deletefile({"path": "/main.py"})
        self.assertTrue(delete_result["ok"])
        self.assertNotIn("backupVerified", delete_result)
        self.assertTrue(any("os.remove('/main.py')" in command for command in board.values))

        board.commands.clear()
        board.expected = b"print('legacy')\n"
        download_result = board.download("print('legacy')\n", False, "/main.py")
        self.assertTrue(download_result["ok"])
        self.assertTrue(any("f = open('/main.py', 'wb')" in command for command in board.commands))


if __name__ == "__main__":
    unittest.main()
