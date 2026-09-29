"""Windows WebView2 runtime discovery when host environment variables are absent."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
HOST_DIR = ROOT / "web-panel" / "host"
sys.path.insert(0, str(HOST_DIR))

import host


class WebView2RuntimeDiscoveryTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "requires Windows WebView2 runtime paths")
    def test_runtime_is_found_when_program_files_variables_are_missing(self):
        drive = Path(sys.executable).drive or "C:"
        expected_application = (
            Path(drive + "\\") / "Program Files (x86)" /
            "Microsoft" / "EdgeWebView" / "Application"
        )
        if not expected_application.is_dir():
            self.skipTest("machine-wide WebView2 Runtime is not installed in the fallback path")
        with patch.dict(os.environ, {
            "SystemDrive": drive,
            "ProgramFiles(x86)": "",
            "ProgramFiles": "",
            "LOCALAPPDATA": "",
        }):
            found = host.webview2_runtime_dirs()
        self.assertTrue(found)
        self.assertTrue(all(Path(item).parent == expected_application for item in found))


if __name__ == "__main__":
    unittest.main()
