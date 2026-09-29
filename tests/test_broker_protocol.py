"""Unit and Windows integration tests for the local broker."""

from __future__ import annotations

import os
import json
import secrets
import subprocess
import sys
import threading
import tempfile
import time
import unittest
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1] / "mcp-server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from broker.client import BrokerClient, BrokerUnavailable, pipe_address
from broker.identity import current_user_sid
from broker.protocol import MAX_MESSAGE_BYTES, PROTOCOL_VERSION, ProtocolError, decode_message, encode_message


class FramingTests(unittest.TestCase):
    def test_json_round_trip_is_utf8_and_rejects_non_objects(self):
        wire = encode_message({"type": "status", "note": "模拟"})
        self.assertEqual(decode_message(wire[4:]), {"type": "status", "note": "模拟"})
        with self.assertRaises(ProtocolError):
            encode_message([1, 2])  # type: ignore[arg-type]
        with self.assertRaises(ProtocolError):
            encode_message({"value": "x" * MAX_MESSAGE_BYTES})
        with self.assertRaises(ProtocolError):
            decode_message(b"[]")
        with self.assertRaises(ProtocolError):
            decode_message(b'{"key":1,"key":2}')
        with self.assertRaises(ProtocolError):
            decode_message(b'{"value":NaN}')

    def test_large_file_json_uses_bounded_chunk_frames_and_checks_order(self):
        from broker.winpipe import PipeConnection
        from broker.protocol import CHUNK_BYTES, MAX_LOGICAL_BYTES

        class MemoryPipe(PipeConnection):
            def __init__(self):
                self.frames = []
            def send_bytes(self, payload):
                self.frames.append(payload)
            def recv_bytes(self):
                return self.frames.pop(0)

        pipe = MemoryPipe()
        object_value = {"ok": True, "data": {"contentBase64": "A" * (1024 * 1024)}}
        pipe.send_json(object_value)
        self.assertGreater(len(pipe.frames), 1)
        self.assertTrue(all(0 < len(frame) <= MAX_MESSAGE_BYTES for frame in pipe.frames))
        self.assertEqual(pipe.recv_json(), object_value)
        self.assertFalse(pipe.frames)
        pipe.send_json(object_value)
        bad = decode_message(pipe.frames[1])
        bad["index"] = 99
        pipe.frames[1] = encode_message(bad)[4:]
        with self.assertRaisesRegex(ProtocolError, "order"):
            pipe.recv_json()
        with self.assertRaises(ProtocolError):
            pipe.send_json({"content": "X" * MAX_LOGICAL_BYTES})


@unittest.skipUnless(os.name == "nt", "Windows named pipe integration tests")
class BrokerIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="esp32-broker-test-")
        cls.auth_path = Path(cls.temp.name) / "broker.token"
        cls.launched_processes = []

    @classmethod
    def tearDownClass(cls):
        for process in cls.launched_processes:
            if process is not None and process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
        cls.temp.cleanup()

    def setUp(self):
        self.test_sid = f"{current_user_sid()}-{os.getpid()}-{secrets.token_hex(4)}"
        self.clients: list[BrokerClient] = []

    def tearDown(self):
        for client in self.clients:
            client.close()
            if client._owned_process is not None:
                self.launched_processes.append(client._owned_process)

    def make_client(self, **kwargs):
        client = BrokerClient(
            auth_path=self.auth_path,
            _identity_sid_for_testing=self.test_sid,
            **kwargs,
        )
        self.clients.append(client)
        return client

    def backend_config(self, *, workspace: str | None = None, profile: str = "hiwonder", scenario: str = "control"):
        return {
            "mode": "mock", "workspace": workspace, "profile": profile,
            "bridge_script": None,
            "mock_script": str(SERVER_DIR.parent / "tests" / "mock_bridge.py"),
            "mock_scenario": scenario, "allow_real_controls": False,
            "allow_real_writes": False, "local_bridge_owner": False,
        }

    def test_two_clients_share_one_mock_backend_and_state(self):
        first = self.make_client()
        second = self.make_client()
        hello_a = first.connect()
        hello_b = second.connect()
        state_a = first.request("status")
        state_b = second.request("status")
        self.assertEqual(hello_a["broker_pid"], hello_b["broker_pid"])
        self.assertEqual(state_a["broker_pid"], state_b["broker_pid"])
        self.assertEqual(state_a["backend_pid"], state_b["backend_pid"])
        self.assertEqual(state_a["status"], state_b["status"])
        self.assertFalse(state_a["status"]["connected"])
        self.assertEqual(state_a["source"], "mock_bridge")
        self.assertTrue(state_a["simulated"])

    def test_real_bridge_clients_share_lease_when_only_mock_scenario_differs(self):
        config = self.backend_config(
            workspace=str(Path(self.temp.name).resolve()), profile="generic", scenario="readonly"
        )
        config.update({
            "mode": "bridge", "bridge_script": str((SERVER_DIR.parent / "bridge.py").resolve()),
            "allow_real_controls": True, "allow_real_writes": True,
        })
        panel_config = {**config, "mock_scenario": "fileops"}
        first = self.make_client(backend_config=config)
        second = self.make_client(backend_config=panel_config)

        first_lease = first.connect()
        second_lease = second.connect()

        self.assertEqual(first_lease["broker_pid"], second_lease["broker_pid"])
        self.assertEqual(second_lease["lease_count"], 2)

        restricted_config = {**panel_config, "allow_real_writes": False}
        restricted = self.make_client(backend_config=restricted_config)
        with self.assertRaisesRegex(BrokerUnavailable, "conflicts with an active"):
            restricted.connect()

    def test_two_clients_share_fileops_ports_status_and_console_cursor(self):
        workspace = str(Path(self.temp.name).resolve())
        config = self.backend_config(workspace=workspace, profile="generic", scenario="fileops")
        first = self.make_client(backend_config=config)
        second = self.make_client(backend_config=config)
        first.connect()
        second.connect()

        status_a = first.request("status")
        status_b = second.request("status")
        ports_a = first.request("ports")
        ports_b = second.request("ports")
        console_a = first.request("console", {"since": None, "max_chars": 12000})
        console_b = second.request("console", {"since": None, "max_chars": 12000})

        self.assertEqual(status_a["backend_pid"], status_b["backend_pid"])
        self.assertEqual(status_a["status"], status_b["status"])
        self.assertTrue(status_a["status"]["connected"])
        self.assertEqual(status_a["status"]["workspace"], workspace)
        self.assertEqual(ports_a["ports"], ports_b["ports"])
        self.assertFalse(ports_a["ports"][0]["ch340"])
        self.assertEqual(console_a["text"], console_b["text"])
        self.assertEqual(console_a["cursor"], console_b["cursor"])
        self.assertGreater(console_a["cursor"], 0)
        self.assertEqual(status_a["source"], "mock_bridge")
        self.assertTrue(status_a["simulated"])

    def test_two_clients_serialize_control_and_share_mock_state_and_console(self):
        config = self.backend_config(profile="generic", scenario="control")
        first = self.make_client(backend_config=config)
        second = self.make_client(backend_config=config)
        first.connect()
        second.connect()

        before = first.request("status")["status"]
        epoch = before["_broker_control_epoch"]
        connected = first.request("control", {
            "command": "connect", "arguments": {"port": "MOCK0"}, "expected_epoch": epoch,
        })
        shared = second.request("status")["status"]
        stopped = second.request("control", {
            "command": "stop", "arguments": {},
            "expected_epoch": shared["_broker_control_epoch"],
        })
        after = first.request("status")["status"]
        console_a = first.request("console", {"since": None, "max_chars": 12000})
        console_b = second.request("console", {"since": None, "max_chars": 12000})

        self.assertTrue(connected["data"]["connected"])
        self.assertTrue(shared["connected"])
        self.assertTrue(stopped["data"]["stopped"])
        self.assertTrue(after["connected"])
        self.assertIn("不表示电机或舵机实际停转", console_a["text"])
        self.assertEqual(console_a["text"], console_b["text"])
        self.assertEqual(console_a["cursor"], console_b["cursor"])

    def test_independent_mcp_tool_instances_share_epoch_and_reject_stale_actions(self):
        """Two independent MCP tool facades must use the broker's shared epoch."""
        from bridge_client import BridgeClient, BridgeFailure
        from broker.adapter import BrokerBridgeClient
        from server import Esp32McpTools

        workspace = str(Path(self.temp.name).resolve())
        config = self.backend_config(workspace=workspace, profile="generic", scenario="control")
        tools = []
        for _ in range(2):
            local = BridgeClient(
                mode="mock", workspace=workspace, profile="generic", bridge_script=None,
                mock_script=config["mock_script"], mock_scenario="control",
            )
            facade = BrokerBridgeClient(local, self.make_client(backend_config=config))
            tools.append(Esp32McpTools(
                facade, entry="/main.py", allow_mock_elicitation=False,
                control_tools_enabled=True,
            ))
        first, second = tools
        try:
            first_plan = first.prepare_connect("MOCK0", mock_only=False)
            self.assertTrue(first_plan["ok"])
            first.execute_control(
                "connect", {"port": first_plan["port"]},
                expected_epoch=first_plan["epoch"], mock_only=False,
            )
            for command, args in (("run", {}), ("stop", {}), ("interrupt", {}),
                                  ("send", {"line": "print(1)"})):
                with self.subTest(command=command):
                    plan = second.prepare_connected_action(command, mock_only=False)
                    self.assertTrue(plan["ok"])
                    self.assertEqual(plan["epoch"], second.client.control_epoch)
                    result = second.execute_control(
                        command, args, expected_epoch=plan["epoch"], mock_only=False,
                    )
                    self.assertIsInstance(result, dict)
            pending = first.prepare_connected_action("run", mock_only=False)
            changed = second.prepare_connected_action("interrupt", mock_only=False)
            second.execute_control(
                "interrupt", expected_epoch=changed["epoch"], mock_only=False,
            )
            with self.assertRaisesRegex(BridgeFailure, "state changed"):
                first.execute_control(
                    "run", expected_epoch=pending["epoch"], mock_only=False,
                )
            latest = second.prepare_connected_action("stop", mock_only=False)
            second.execute_control("stop", expected_epoch=latest["epoch"], mock_only=False)
            self.assertEqual(first.client.call("status")["connected"], True)
        finally:
            for instance in tools:
                instance.close()

    def test_file_facades_share_mutations_pid_download_and_one_bridge(self):
        from bridge_client import BridgeClient
        from broker.adapter import BrokerBridgeClient
        from server import Esp32McpTools

        workspace = Path(self.temp.name).resolve()
        (workspace / "shared.py").write_text("Kp = 3.0\nKi = 0.2\nKd = 0.03\n", encoding="utf-8")
        config = self.backend_config(workspace=str(workspace), profile="generic", scenario="fileops")
        facades = []
        instances = []
        for _ in range(2):
            local = BridgeClient(
                mode="mock", workspace=str(workspace), profile="generic",
                bridge_script=None, mock_script=config["mock_script"], mock_scenario="fileops",
            )
            facade = BrokerBridgeClient(local, self.make_client(backend_config=config))
            facades.append(facade)
            instances.append(Esp32McpTools(
                facade, entry="/main.py", allow_mock_elicitation=False,
                write_tools_enabled=True,
            ))
        first, second = instances
        try:
            written = first.board_file_write("/shared.txt", "from first")
            self.assertTrue(written["ok"])
            read = second.board_file_read("/shared.txt")
            self.assertEqual(read["content"], "from first")
            self.assertEqual(first.client._broker.request("status")["backend_pid"],
                             second.client._broker.request("status")["backend_pid"])
            self.assertTrue(all(facade._local._process is None for facade in facades))
            download = first.download("shared.py", run=False)
            self.assertTrue(download["compileOk"])
            self.assertEqual(second.pid_get()["values"]["Kp"], 3.0)
            updated = second.pid_set({"Kp": 5.0}, run=True)
            self.assertTrue(updated["backupVerified"])
            self.assertEqual(first.pid_get()["values"]["Kp"], 5.0)
            deleted = first.board_file_delete("/shared.txt")
            self.assertTrue(deleted["targetExisted"])
            self.assertNotIn("shared.txt", {row["name"] for row in second.board_files_list()["files"]})
            self.assertEqual(first.client.read_console(since=None, max_chars=12000)["cursor"],
                             second.client.read_console(since=None, max_chars=12000)["cursor"])
        finally:
            for tool in instances:
                tool.close()

    def test_file_rpc_denies_missing_strict_backup_and_real_flags(self):
        from broker.host import BrokerHost
        host = BrokerHost("0" * 64, test_identity_sid="S-1-5-21-test-file")
        host._backend_config = self.backend_config(scenario="fileops")
        with self.assertRaisesRegex(PermissionError, "strictBackup"):
            host._dispatch("file", {
                "command": "writefile", "arguments": {"path": "/main.py", "content": "x"},
                "expected_epoch": 0,
            })
        self.assertIsNone(host._bridge)
        host._backend_config = {
            **host._backend_config, "mode": "bridge", "allow_real_controls": True,
            "workspace": str(Path(self.temp.name).resolve()),
            "bridge_script": str((Path(self.temp.name) / "never-start.py").resolve()),
        }
        with self.assertRaisesRegex(PermissionError, "enable-write-tools"):
            host._dispatch("file", {
                "command": "writefile",
                "arguments": {"path": "/main.py", "content": "x", "strictBackup": True},
                "expected_epoch": 0,
            })
        self.assertIsNone(host._bridge)

    def test_large_file_payload_round_trip_through_single_broker(self):
        from bridge_client import BridgeClient
        from broker.adapter import BrokerBridgeClient
        from server import Esp32McpTools

        workspace = str(Path(self.temp.name).resolve())
        config = self.backend_config(workspace=workspace, profile="generic", scenario="fileops")
        local = BridgeClient(
            mode="mock", workspace=workspace, profile="generic", bridge_script=None,
            mock_script=config["mock_script"], mock_scenario="fileops",
        )
        facade = BrokerBridgeClient(local, self.make_client(backend_config=config))
        tool = Esp32McpTools(facade, entry="/main.py", allow_mock_elicitation=False,
                             write_tools_enabled=True)
        try:
            content = "Z" * MAX_MESSAGE_BYTES
            result = tool.board_file_write("/large.txt", content)
            self.assertTrue(result["ok"])
            raw, _crc = tool._raw_board_file("/large.txt")
            self.assertEqual(raw, content.encode("ascii"))
            self.assertIsNone(local._process)
            with self.assertRaisesRegex(Exception, "1 MiB"):
                tool.board_file_write("/too-big.txt", content + "Z")
        finally:
            tool.close()

    def test_file_mutation_rejects_epoch_changed_by_other_client(self):
        config = self.backend_config(profile="generic", scenario="fileops")
        first = self.make_client(backend_config=config)
        second = self.make_client(backend_config=config)
        first.connect()
        second.connect()
        old_epoch = first.request("status")["status"]["_broker_control_epoch"]
        second.request("control", {
            "command": "stop", "arguments": {}, "expected_epoch": old_epoch,
        })
        with self.assertRaisesRegex(BrokerUnavailable, "state changed"):
            first.request("file", {
                "command": "writefile", "arguments": {
                    "path": "/blocked.txt", "content": "must not write", "strictBackup": True,
                }, "expected_epoch": old_epoch,
            })
        files = second.request("file", {
            "command": "listfiles", "arguments": {},
            "expected_epoch": second.request("status")["status"]["_broker_control_epoch"],
        })
        self.assertNotIn("blocked.txt", {row["name"] for row in files["data"]["files"]})

    def test_workspace_claim_select_policy_with_multiple_client_leases(self):
        from bridge_client import BridgeClient
        from broker.adapter import BrokerBridgeClient

        old_root = Path(self.temp.name, "selection-old")
        new_root = Path(self.temp.name, "selection-new")
        old_root.mkdir()
        new_root.mkdir()
        config_dir = old_root / "esp32-ide"
        config_dir.mkdir()
        (config_dir / "board.json").write_text(
            '{"type":"generic","entry":"/main.py","label":"Original"}', encoding="utf-8"
        )
        config = self.backend_config(workspace=str(old_root.resolve()), profile="generic", scenario="control")
        local = BridgeClient(mode="mock", workspace=str(old_root.resolve()),
                             profile="generic", bridge_script=None,
                             mock_script=config["mock_script"], mock_scenario="control")
        facade = BrokerBridgeClient(local, self.make_client(backend_config=config))
        facade.start()
        from server import Esp32McpTools
        tool = Esp32McpTools(facade, entry="/main.py", allow_mock_elicitation=False)
        self.assertEqual(tool.entry, "/main.py")
        try:
            old = facade.workspace_current()
            self.assertEqual(old["info"]["profileLabel"], "Original")
            other = self.make_client(backend_config=config)
            other.connect()
            claim = facade.workspace_claim(str(new_root), "hiwonder", "Second", "/corex.py")
            self.assertTrue(claim["claimed"])
            with self.assertRaisesRegex(Exception, "not be overwritten"):
                facade.workspace_claim(str(new_root), "generic")
            self.assertEqual(facade.workspace_current()["workspacePath"], str(old_root.resolve()))
            selected = facade.workspace_select(str(new_root))
            self.assertEqual(selected["profile"], "hiwonder")
            self.assertEqual(facade.workspace, str(new_root.resolve()))
            self.assertEqual(tool.entry, "/corex.py")
            self.assertEqual(tool.policy_info()["policy"], "confirm-write")
            shared_status = other.request("status")["status"]
            self.assertEqual(shared_status["workspace"], str(new_root.resolve()))
            self.assertEqual(shared_status["_broker_workspace_context"]["entry"], "/corex.py")
            snapshot = facade.workspace_policy()
            self.assertEqual(snapshot["revision"], "missing")
            changed = facade.workspace_policy_set("confirm-all", snapshot["revision"])
            self.assertEqual(changed["policy"], "confirm-all")
            self.assertEqual(facade.workspace_policy()["policy"], "confirm-all")
            with self.assertRaisesRegex(Exception, "changed"):
                facade.workspace_policy_set("auto", snapshot["revision"])
            self.assertEqual(facade.workspace_current()["info"]["entry"], "/corex.py")
        finally:
            facade.close()
            if 'other' in locals():
                other.close()

    def test_agent_confirmation_is_context_bound_expiring_and_consumed_once(self):
        client = self.make_client(backend_config=self.backend_config(profile="generic"))
        peer = self.make_client(backend_config=self.backend_config(profile="generic"))
        client.connect()
        peer.connect()
        current = client.request("workspace", {"action": "current"})
        request = {
            "action": "create", "operation": "write /main.py", "effect": "write",
            "title": "写入板载文件 /main.py", "target": "FAKE0 /main.py",
            "impact": "写入 3 bytes；SHA-256 由测试夹具提供。",
            "workspacePath": None, "profile": "generic", "entry": "/main.py",
            "control_epoch": current["control_epoch"], "policy_revision": "missing",
            "request_digest": "a" * 64, "ttl_ms": 120000,
        }
        created = client.request("confirmation", request)
        self.assertEqual(created["state"], "pending")
        self.assertNotIn("request_digest", created)
        self.assertEqual(client.request("confirmation", {"action": "list"})["items"][0]["id"], created["id"])
        self.assertEqual(client.request("confirmation", {"action": "agent_list"})["items"][0]["id"], created["id"])
        self.assertEqual(peer.request("confirmation", {"action": "agent_list"})["items"], [])
        other_lease_decision = peer.request("confirmation", {
            "action": "agent_resolve", "id": created["id"], "decision": "approve",
        })
        self.assertFalse(other_lease_decision["ok"])
        self.assertEqual(other_lease_decision["state"], "not_owner")
        with self.assertRaisesRegex(BrokerUnavailable, "another confirmation"):
            client.request("confirmation", request)

        approved = client.request("confirmation", {
            "action": "agent_resolve", "id": created["id"], "decision": "approve",
        })
        self.assertTrue(approved["ok"])
        self.assertEqual(approved["decision_source"], "agent_delegated")
        wrong_digest = client.request("confirmation", {
            "action": "consume", "id": created["id"], "request_digest": "b" * 64,
        })
        self.assertEqual(wrong_digest["state"], "request_mismatch")
        consumed = client.request("confirmation", {
            "action": "consume", "id": created["id"], "request_digest": "a" * 64,
        })
        self.assertEqual(consumed["state"], "consumed")
        self.assertEqual(consumed["decision_source"], "agent_delegated")
        self.assertEqual(client.request("confirmation", {"action": "status", "id": created["id"]})["state"], "unknown")

        expiring = client.request("confirmation", {**request, "ttl_ms": 1000, "request_digest": "c" * 64})
        time.sleep(1.05)
        expired = client.request("confirmation", {"action": "status", "id": expiring["id"]})
        self.assertEqual(expired["state"], "expired")

    def test_workspace_switch_synchronizes_peer_facade_and_rejects_old_epoch(self):
        from bridge_client import BridgeClient, BridgeFailure
        from broker.adapter import BrokerBridgeClient

        old_root = Path(self.temp.name, "shared-old")
        new_root = Path(self.temp.name, "shared-new")
        for root, profile, entry in (
            (old_root, "generic", "/custom_main.py"),
            (new_root, "hiwonder", "/robot_start.py"),
        ):
            config_dir = root / "esp32-ide"
            config_dir.mkdir(parents=True)
            (config_dir / "board.json").write_text(
                json.dumps({"type": profile, "entry": entry, "label": root.name}),
                encoding="utf-8",
            )
        script = str((SERVER_DIR.parent / "tests" / "mock_bridge.py").resolve())
        first_config = self.backend_config(
            workspace=str(old_root.resolve()), profile="generic", scenario="control"
        )
        stale_peer_config = self.backend_config(
            workspace=str(new_root.resolve()), profile="hiwonder", scenario="control"
        )

        def facade(config):
            local = BridgeClient(
                mode="mock", workspace=config["workspace"], profile=config["profile"],
                bridge_script=None, mock_script=script, mock_scenario="control",
            )
            return BrokerBridgeClient(local, self.make_client(backend_config=config))

        first = facade(first_config)
        peer = facade(stale_peer_config)
        try:
            self.assertEqual(first.workspace_current()["workspacePath"], str(old_root.resolve()))
            peer_current = peer.workspace_current()
            self.assertEqual(peer_current["workspacePath"], str(old_root.resolve()))
            self.assertEqual(peer.profile, "generic")
            self.assertEqual(peer.entry, "/custom_main.py")
            old_epoch = peer.control_epoch

            selected = first.workspace_select(str(new_root))
            self.assertEqual(selected["entry"], "/robot_start.py")
            peer_status = peer.call_shared("status")
            self.assertEqual(peer_status["workspace"], str(new_root.resolve()))
            self.assertEqual(peer.workspace, str(new_root.resolve()))
            self.assertEqual(peer.profile, "hiwonder")
            self.assertEqual(peer.entry, "/robot_start.py")
            with self.assertRaisesRegex(BridgeFailure, "state changed"):
                peer.call_control("stop", {}, expected_epoch=old_epoch)

            late_joiner = facade(stale_peer_config)
            try:
                current = late_joiner.workspace_current()
                self.assertEqual(current["workspacePath"], str(new_root.resolve()))
                self.assertEqual(late_joiner.profile, "hiwonder")
                self.assertEqual(late_joiner.entry, "/robot_start.py")
            finally:
                late_joiner.close()
        finally:
            first.close()
            peer.close()

    def test_claiming_active_workspace_updates_profile_entry_epoch_for_all_clients(self):
        from bridge_client import BridgeClient, BridgeFailure
        from broker.adapter import BrokerBridgeClient

        root = Path(self.temp.name, "claim-active")
        root.mkdir()
        (root / "四路巡线.py").write_text("print('local fixture')\n", encoding="utf-8")
        config = self.backend_config(
            workspace=str(root.resolve()), profile="generic", scenario="control"
        )
        script = str((SERVER_DIR.parent / "tests" / "mock_bridge.py").resolve())

        def facade():
            local = BridgeClient(
                mode="mock", workspace=config["workspace"], profile="generic",
                bridge_script=None, mock_script=script, mock_scenario="control",
            )
            return BrokerBridgeClient(local, self.make_client(backend_config=config))

        first, peer = facade(), facade()
        try:
            current = first.workspace_current()
            peer.workspace_current()
            old_epoch = peer.control_epoch
            self.assertEqual(current["profile"], "generic")
            self.assertEqual(current["entry"], "/main.py")

            claimed = first.workspace_claim(str(root), "hiwonder", "active claim", "/robot.py")
            self.assertEqual(claimed["active_context"]["profile"], "hiwonder")
            self.assertEqual(first.profile, "hiwonder")
            self.assertEqual(first.entry, "/robot.py")
            peer.call_shared("status")
            self.assertEqual(peer.profile, "hiwonder")
            self.assertEqual(peer.entry, "/robot.py")
            self.assertEqual(peer.control_epoch, old_epoch + 1)
            with self.assertRaisesRegex(BridgeFailure, "state changed"):
                peer.call_control("stop", {}, expected_epoch=old_epoch)
        finally:
            first.close()
            peer.close()

    def test_competing_workspace_switches_commit_only_one_context(self):
        from bridge_client import BridgeClient
        from broker.adapter import BrokerBridgeClient

        roots = []
        for name, profile, entry in (
            ("race-a", "generic", "/a.py"), ("race-b", "hiwonder", "/b.py"),
        ):
            root = Path(self.temp.name, name)
            config_dir = root / "esp32-ide"
            config_dir.mkdir(parents=True)
            (config_dir / "board.json").write_text(
                json.dumps({"type": profile, "entry": entry, "label": name}),
                encoding="utf-8",
            )
            roots.append((root, profile, entry))
        initial = Path(self.temp.name, "race-initial")
        (initial / "esp32-ide").mkdir(parents=True)
        (initial / "esp32-ide" / "board.json").write_text(
            '{"type":"generic","entry":"/initial.py","label":"initial"}', encoding="utf-8"
        )
        config = self.backend_config(
            workspace=str(initial.resolve()), profile="generic", scenario="control"
        )
        script = str((SERVER_DIR.parent / "tests" / "mock_bridge.py").resolve())

        def make_facade():
            local = BridgeClient(
                mode="mock", workspace=config["workspace"], profile="generic",
                bridge_script=None, mock_script=script, mock_scenario="control",
            )
            return BrokerBridgeClient(local, self.make_client(backend_config=config))

        first, second = make_facade(), make_facade()
        results = []
        barrier = threading.Barrier(3)

        def choose(client, target):
            barrier.wait(timeout=5)
            try:
                results.append(("ok", client.workspace_select(str(target[0]))))
            except Exception as exc:
                results.append(("error", str(exc)))

        try:
            first.workspace_current()
            second.workspace_current()
            threads = [
                threading.Thread(target=choose, args=(first, roots[0]), daemon=True),
                threading.Thread(target=choose, args=(second, roots[1]), daemon=True),
            ]
            for thread in threads:
                thread.start()
            barrier.wait(timeout=5)
            for thread in threads:
                thread.join(timeout=10)
                self.assertFalse(thread.is_alive(), "workspace switch request did not finish")

            self.assertEqual(sum(result[0] == "ok" for result in results), 1)
            self.assertEqual(sum(result[0] == "error" for result in results), 1)
            current = first.workspace_current()
            committed = next(root for root, _profile, _entry in roots
                             if str(root.resolve()) == current["workspacePath"])
            expected_profile, expected_entry = next(
                (profile, entry) for root, profile, entry in roots if root == committed
            )
            self.assertEqual(current["profile"], expected_profile)
            self.assertEqual(current["entry"], expected_entry)
            self.assertEqual(current["info"]["profile"], expected_profile)
            self.assertEqual(current["info"]["entry"], expected_entry)
        finally:
            first.close()
            second.close()

    def test_connected_workspace_switch_and_policy_change_fail_closed(self):
        root = Path(self.temp.name).resolve()
        target = root / "blocked-claim"
        target.mkdir()
        config = self.backend_config(workspace=str(root), profile="generic", scenario="fileops")
        client = self.make_client(backend_config=config)
        client.connect()
        connected = client.request("status")["status"]
        self.assertTrue(connected["connected"])
        epoch = connected["_broker_control_epoch"]
        for args in (
            {"action": "claim", "path": str(target), "profile": "generic",
             "label": None, "entry": None, "expected_epoch": epoch},
            {"action": "select", "path": str(target), "expected_epoch": epoch},
            {"action": "policy_set", "policy": "auto", "revision": "missing",
             "expected_epoch": epoch},
        ):
            with self.subTest(action=args["action"]):
                with self.assertRaisesRegex(BrokerUnavailable, "disconnect"):
                    client.request("workspace", args)
        self.assertFalse((target / "esp32-ide" / "board.json").exists())
        self.assertFalse((root / "esp32-ide" / "workbench-policy.json").exists())
        self.assertEqual(client.request("workspace", {"action": "current"})["workspacePath"], str(root))

    def test_busy_workspace_switch_fails_without_changing_context(self):
        from broker.host import BrokerHost

        old_root = Path(self.temp.name, "busy-old")
        new_root = Path(self.temp.name, "busy-new")
        for root, profile in ((old_root, "generic"), (new_root, "hiwonder")):
            config_dir = root / "esp32-ide"
            config_dir.mkdir(parents=True)
            (config_dir / "board.json").write_text(
                json.dumps({"type": profile, "entry": "/main.py", "label": root.name}),
                encoding="utf-8",
            )

        class BusyBridge:
            def call(self, command):
                if command != "status":
                    raise AssertionError("busy guard should only query bridge status")
                return {"connected": False, "busy": True}

        host = BrokerHost("0" * 64, test_identity_sid="S-1-5-21-busy-test")
        host._backend_config = host._normalize_backend_config(
            self.backend_config(workspace=str(old_root.resolve()), profile="generic")
        )
        host._bridge = BusyBridge()
        with self.assertRaisesRegex(RuntimeError, "busy"):
            host._workspace_dispatch({
                "action": "select", "path": str(new_root), "expected_epoch": 0,
            })
        self.assertEqual(host._backend_config["workspace"], str(old_root.resolve()))
        self.assertEqual(host._backend_config["profile"], "generic")

    def test_control_rpcs_reject_stale_epoch(self):
        client = self.make_client()
        client.connect()
        client.request("control", {
            "command": "connect", "arguments": {"port": "MOCK0"}, "expected_epoch": 0,
        })
        with self.assertRaisesRegex(BrokerUnavailable, "state changed"):
            client.request("control", {
                "command": "stop", "arguments": {}, "expected_epoch": 0,
            })

    def test_real_control_rpc_flag_is_checked_before_bridge_initialization(self):
        from broker.host import BrokerHost

        host = BrokerHost("0" * 64, test_identity_sid="S-1-5-21-test")
        host._backend_config = {
            **self.backend_config(), "mode": "bridge", "workspace": str(Path(self.temp.name).resolve()),
            "bridge_script": str(Path(self.temp.name, "bridge.py").resolve()),
            "allow_real_controls": False,
        }
        with self.assertRaisesRegex(PermissionError, "enable-control-tools"):
            host._dispatch("control", {
                "command": "connect", "arguments": {"port": "COM1"}, "expected_epoch": 0,
            })
        self.assertIsNone(host._bridge)

    def test_runtime_configuration_conflict_is_rejected_while_leases_overlap(self):
        first = self.make_client(backend_config=self.backend_config(scenario="fileops"))
        first.connect()
        different = self.make_client(backend_config=self.backend_config(scenario="normal"))
        with self.assertRaisesRegex(BrokerUnavailable, "conflicts with an active"):
            different.connect()
        self.assertIsNotNone(first.request("status")["backend_pid"])

    def test_local_fileops_bridge_reservation_prevents_second_bridge_owner(self):
        owner_config = {**self.backend_config(), "local_bridge_owner": True}
        owner = self.make_client(backend_config=owner_config)
        owner.connect()
        with self.assertRaisesRegex(BrokerUnavailable, "broker RPC is unavailable"):
            owner.request("status")

        another = self.make_client(backend_config=self.backend_config())
        with self.assertRaisesRegex(BrokerUnavailable, "conflicts with an active"):
            another.connect()

    def test_broker_rejects_control_and_mutation_rpc_operations(self):
        client = self.make_client(backend_config=self.backend_config(scenario="fileops"))
        client.connect()
        for operation in ("connect", "run", "writefile", "deletefile"):
            with self.subTest(operation=operation):
                with self.assertRaisesRegex(BrokerUnavailable, "unsupported broker operation"):
                    client.request(operation, {"path": "/main.py"})
        self.assertTrue(client.request("status")["status"]["connected"])

    def test_repeated_startup_does_not_replace_active_broker(self):
        first = self.make_client()
        original = first.connect()["broker_pid"]
        status = first.request("status")
        duplicate = subprocess.run(
            [
                sys.executable, "-B", "-m", "broker.host", "--auth-file", str(self.auth_path),
                "--test-identity-sid", self.test_sid,
            ],
            cwd=SERVER_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self.assertEqual(duplicate.returncode, 0)
        self.assertEqual(first.request("status")["broker_pid"], original)
        self.assertEqual(status["backend_pid"], first.request("status")["backend_pid"])

    def test_protocol_version_and_authentication_mismatch_are_rejected(self):
        good = self.make_client()
        good.connect()
        wrong = self.make_client(auth_token="0" * 64)
        with self.assertRaises(BrokerUnavailable):
            wrong.connect()
        self.assertIsNone(wrong._connection)

        from broker.winpipe import open_client_pipe

        raw = open_client_pipe(pipe_address(self.test_sid), timeout_ms=1000)
        raw.send_json({
            "type": "hello",
            "protocol_version": PROTOCOL_VERSION + 1,
            "auth_token": self.auth_path.read_text(encoding="ascii").strip(),
            "idle_timeout": 60.0,
        })
        reply = raw.recv_json()
        raw.close()
        self.assertFalse(reply["ok"])
        self.assertIn("version", reply["error"])

        raw = open_client_pipe(pipe_address(self.test_sid), timeout_ms=1000)
        token = self.auth_path.read_text(encoding="ascii").strip()
        raw.send_json({
            "type": "hello",
            "protocol_version": PROTOCOL_VERSION,
            "auth_token": token,
            "idle_timeout": 60.0,
            "backend_config": self.backend_config(),
            "unexpected": True,
        })
        reply = raw.recv_json()
        raw.close()
        self.assertFalse(reply["ok"])
        self.assertIn("schema", reply["error"])

        raw = open_client_pipe(pipe_address(self.test_sid), timeout_ms=1000)
        raw.send_json({
            "type": "hello", "protocol_version": PROTOCOL_VERSION,
            "auth_token": token, "idle_timeout": 60.0,
            "backend_config": self.backend_config(),
        })
        self.assertTrue(raw.recv_json()["ok"])
        raw.send_json({"type": "request", "operation": "status", "unexpected": True})
        reply = raw.recv_json()
        self.assertFalse(reply["ok"])
        self.assertIn("schema", reply["error"])
        raw.send_json({"type": "release"})
        self.assertTrue(raw.recv_json()["ok"])
        raw.close()

    def test_client_crash_releases_last_lease_and_owned_mock_bridge(self):
        client = self.make_client()
        client.connect()
        old_backend = client.request("status")["backend_pid"]
        connection, client._connection = client._connection, None
        connection.close()  # simulate process death without a release message
        self._wait_process_exit(old_backend)
        restarted = self.make_client()
        restarted.connect()
        self.assertNotEqual(restarted.request("status")["backend_pid"], old_backend)

    def test_raw_silent_authenticated_client_expires_and_releases_its_bridge_lease(self):
        from broker.winpipe import open_client_pipe

        bootstrap = self.make_client()
        bootstrap.connect()
        bootstrap.close()
        raw = open_client_pipe(pipe_address(self.test_sid), timeout_ms=1000)
        token = self.auth_path.read_text(encoding="ascii").strip()
        raw.send_json({
            "type": "hello", "protocol_version": PROTOCOL_VERSION,
            "auth_token": token, "idle_timeout": 0.5,
            "backend_config": self.backend_config(),
        })
        self.assertEqual(raw.recv_json()["idle_timeout"], 0.5)
        raw.send_json({"type": "request", "operation": "status", "arguments": {}})
        response = raw.recv_json()
        self.assertTrue(response["ok"])
        backend = response["result"]["backend_pid"]
        self._wait_process_exit(backend, timeout=5)
        raw.close()
        reopened = self.make_client()
        reopened.connect()
        self.assertNotEqual(reopened.request("status")["backend_pid"], backend)

    def test_client_heartbeat_keeps_lease_and_backend_alive_past_idle_timeout(self):
        client = self.make_client(idle_timeout=0.5)
        hello = client.connect()
        self.assertEqual(hello["idle_timeout"], 0.5)
        first = client.request("status")
        time.sleep(1.2)
        second = client.request("status")
        self.assertEqual(first["backend_pid"], second["backend_pid"])
        self.assertEqual(first["broker_pid"], second["broker_pid"])
        self.assertEqual(first["status"], second["status"])
        backend = first["backend_pid"]
        broker = first["broker_pid"]
        client.close()
        self._wait_process_exit(backend, timeout=5)
        self._wait_process_exit(broker, timeout=5)

    def test_lost_lease_recovers_for_reads_without_replaying_control(self):
        client = self.make_client(backend_config=self.backend_config())
        peer = self.make_client(backend_config=self.backend_config())
        client.connect()
        peer.connect()
        initial = client.request("status")
        self.assertFalse(initial["status"]["connected"])

        connection = client._connection
        self.assertIsNotNone(connection)
        connection.close()
        with self.assertRaisesRegex(BrokerUnavailable, "transport failed"):
            client.request("status")
        self.assertIsNone(client._connection)

        with self.assertRaisesRegex(BrokerUnavailable, "no broker lease"):
            client.request("control", {
                "command": "connect", "arguments": {"port": "COM4"},
                "expected_epoch": 0,
            })
        self.assertIsNone(client._connection)

        recovered = client.request("status")
        peer_status = peer.request("status")
        self.assertFalse(recovered["status"]["connected"])
        self.assertIsNotNone(client._connection)
        self.assertEqual(recovered["source"], "mock_bridge")
        self.assertEqual(recovered["broker_pid"], peer_status["broker_pid"])
        self.assertEqual(recovered["backend_pid"], peer_status["backend_pid"])

        client.close()
        with self.assertRaisesRegex(BrokerUnavailable, "no broker lease"):
            client.request("status")

    def test_broker_exit_kills_owned_bridge_and_allows_clean_restart(self):
        first = self.make_client()
        original = first.connect()["broker_pid"]
        backend = first.request("status")["backend_pid"]
        process = first._owned_process or next(
            (item for item in self.launched_processes if item is not None and item.pid == original),
            None,
        )
        self.assertIsNotNone(process, "test broker must be owned before exercising crash cleanup")
        process.terminate()
        process.wait(timeout=5)
        self._wait_process_exit(backend)
        first._connection.close()
        first._connection = None
        restarted = self.make_client()
        hello = restarted.connect(timeout=8)
        self.assertNotEqual(hello["broker_pid"], original)
        self.assertNotEqual(restarted.request("status")["backend_pid"], backend)

    @staticmethod
    def _wait_process_exit(pid: int, timeout: float = 5.0):
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if not handle:
            return
        try:
            result = kernel.WaitForSingleObject(handle, int(timeout * 1000))
            if result != 0:
                raise AssertionError(f"process {pid} did not exit within {timeout}s")
        finally:
            kernel.CloseHandle(handle)


if __name__ == "__main__":
    unittest.main()
