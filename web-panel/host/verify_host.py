"""Small stdlib checks for the self-contained WebView host pages and APIs."""

from __future__ import annotations

from html.parser import HTMLParser
import re
import unittest
from unittest.mock import patch

import host


class AssetParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.references: list[str] = []
        self.ids: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if values.get("id"):
            self.ids.append(str(values["id"]))
        for key in ("src", "href"):
            value = values.get(key)
            if value:
                self.references.append(value)


class HostValidation(unittest.TestCase):
    def test_all_assets_are_inlined_and_ids_are_unique(self) -> None:
        page = host.load_inline_page()
        parser = AssetParser()
        parser.feed(page)
        self.assertEqual([], [ref for ref in parser.references if not ref.startswith("#")])
        self.assertEqual(len(parser.ids), len(set(parser.ids)))
        self.assertEqual("ESP32 Codex 网页面板 · UI-2", host.TITLE)
        self.assertIn("window.__ui2ReceiveState", page)
        self.assertIn("pywebview.api.ping", page)
        self.assertIn("MOCK", page)

    def test_demo_api_only_accepts_fixed_ping_kinds(self) -> None:
        api = host.DemoApi()
        self.assertEqual("pong · startup · #1 · MOCK", api.ping("startup")["message"])
        self.assertEqual("pong · button · #2 · MOCK", api.ping("button")["message"])
        for invalid in ("arbitrary", "connect", "run", None, [], {}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                api.ping(invalid)  # type: ignore[arg-type]
        self.assertEqual(2, api.ping_count)

    def test_source_has_no_remote_resource_reference(self) -> None:
        page = host.load_inline_page()
        self.assertIsNone(re.search(r"(?:src|href)\s*=\s*['\"](?:https?:)?//", page, re.I))

    def test_readonly_page_has_only_snapshot_read_entrypoint(self) -> None:
        page = host.load_readonly_page()
        parser = AssetParser()
        parser.feed(page)
        self.assertEqual([], [ref for ref in parser.references if not ref.startswith("#")])
        self.assertIn("window.pywebview.api.get_snapshot()", page)
        self.assertNotRegex(page, r"pywebview\.api\.(?:connect|call|request|perform|send|download)")
        self.assertIn("textContent", page)
        self.assertIn("共享控制台", page)
        self.assertIn("仅展示端口；不会自动连接", page)
        self.assertIn("MOCK", page)

        self.assertNotIn('id="ui4-operations"', page)
        self.assertNotIn('window.__ui4ShowConfirmation', page)

    def test_operations_page_has_only_local_named_actions(self) -> None:
        page = host.load_operations_page()
        parser = AssetParser()
        parser.feed(page)
        self.assertEqual(len(parser.ids), len(set(parser.ids)))
        self.assertEqual([], [ref for ref in parser.references if not ref.startswith("#")])
        self.assertIn('id="ui4-operations"', page)
        self.assertIn('id="ui4-confirm"', page)
        self.assertIn('api().perform(action,payload)', page)
        self.assertNotIn('confirmed:true', page)
        self.assertIn('if(busy)return; setBusy(true);', page)
        self.assertIn("workspace:'ui4-operations',console:'live-console'", page)
        self.assertIn('initializeOperations();',page)
        self.assertIn('if(initialized||!window.pywebview?.api)return;',page)

    def test_front_window_fits_dpi_scaled_work_area(self) -> None:
        full = host._fit_operations_window(0,0,2880,1704,192)
        self.assertEqual((900,820,270,16),
                         (full['width'],full['height'],full['x'],full['y']))
        small = host._fit_operations_window(0,0,1440,852,192)
        self.assertEqual((688,394),(small['width'],small['height']))
        self.assertLessEqual(small['y']+small['height'],426)

    def test_compact_operations_page_inlines_assets_and_uses_the_fixed_host_api(self) -> None:
        page = host.load_compact_operations_page()
        parser = AssetParser()
        parser.feed(page)
        self.assertEqual(len(parser.ids), len(set(parser.ids)))
        self.assertEqual([], [ref for ref in parser.references if not ref.startswith("#")])
        self.assertIn('id="serial-select"', page)
        self.assertIn('id="workspace-discover"', page)
        self.assertIn('window.__esp32CompactLive = true', page)
        self.assertIn('api().perform(action, payload)', page)
        self.assertIn('api().approve_current_operation()', page)
        self.assertIn('api().reject_current_operation()', page)
        self.assertIn('api.minimize_window()', page)
        self.assertIn('api.close_window()', page)
        self.assertIn('textContent', page)
        self.assertNotIn('confirmed:true', page)
        self.assertNotIn('fetch(', page)
        self.assertNotIn('WebSocket(', page)

    def test_compact_window_geometry_centers_and_respects_small_work_area(self) -> None:
        with patch.object(host, "operations_window_geometry", return_value={
            "x": 270, "y": 16, "width": 900, "height": 820,
            "minWidth": 420, "minHeight": 300, "displayDpi": 192,
        }):
            full = host.compact_window_geometry()
        self.assertEqual((450, 500, 495, 176),
                         (full["width"], full["height"], full["x"], full["y"]))

        with patch.object(host, "operations_window_geometry", return_value={
            "x": 16, "y": 16, "width": 688, "height": 394,
            "minWidth": 420, "minHeight": 300, "displayDpi": 192,
        }):
            small = host.compact_window_geometry()
        self.assertEqual((450, 394, 135, 16, 420, 360),
                         (small["width"], small["height"], small["x"], small["y"],
                          small["minWidth"], small["minHeight"]))

    def test_readonly_cli_matches_existing_fileops_panel_default(self) -> None:
        args = host.build_parser().parse_args(["--readonly"])
        self.assertEqual("fileops", args.mock_scenario)

    def test_public_snapshot_allowlists_and_bounds_untrusted_fields(self) -> None:
        raw = {
            "workspace": {"workspacePath": "C:\\work\\" + "x" * 2500,
                          "profile": "generic", "control_epoch": 987,
                          "info": {"profileLabel": "Fixture", "entry": "/main.py", "token": "secret"}},
            "status": {"connected": False, "port": "", "firmware": "1.0", "broker_pid": 4321},
            "ports": {"ports": [{"device": f"MOCK{i}", "serial": "private"} for i in range(50)]},
            "policy": {"policy": "confirm-write", "source": "default", "revision": "private"},
            "console": {"text": "c" * 15000, "cursor": 12, "dropped": False},
            "source": "mock_bridge", "simulated": True, "broker_pid": 4321, "token": "secret",
        }
        public = host.public_snapshot(raw, simulated=True)
        self.assertEqual(2048, len(public["workspace"]["path"]))
        self.assertEqual(32, len(public["ports"]))
        self.assertEqual(12000, len(public["console"]["text"]))
        serialized = __import__("json").dumps(public)
        for private in ("token", "revision", "control_epoch", "broker_pid", "serial"):
            self.assertNotIn(private, serialized)

    def test_readonly_api_reports_backend_error_without_leaking_methods(self) -> None:
        class BrokenBackend:
            simulated = True
            def snapshot(self):
                raise RuntimeError("broker unavailable for C:\\private\\user\\secret")
            def close(self):
                raise AssertionError("page must not be able to close backend")

        api = host.ReadOnlyApi(BrokenBackend())
        result = api.get_snapshot()
        self.assertEqual("共享状态暂时不可用", result["error"])
        self.assertNotIn("private", __import__("json").dumps(result))
        self.assertEqual(["get_snapshot"], [name for name in dir(api) if not name.startswith("_") and callable(getattr(api, name))])


if __name__ == "__main__":
    unittest.main(verbosity=2)
