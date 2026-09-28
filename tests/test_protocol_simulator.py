"""协议模拟器的无硬件封套、错误、超时与退出测试。"""

import json
import queue
import subprocess
import sys
import threading
import unittest
from pathlib import Path


TESTS_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = TESTS_DIR / "fixtures"
SIMULATOR = TESTS_DIR / "mock_bridge.py"


def fixture_lines(name):
    text = (FIXTURES_DIR / name).read_text(encoding="utf-8-sig")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def fixture_request(name):
    return (FIXTURES_DIR / name).read_text(encoding="utf-8-sig").strip()


class RunningSimulator:
    def __init__(self, scenario="normal", profile="hiwonder"):
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-X",
                "utf8",
                str(SIMULATOR),
                "--scenario",
                scenario,
                "--profile",
                profile,
                "--workspace",
                "fixture-workspace",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self.lines = queue.Queue()
        self.reader = threading.Thread(target=self._read_stdout, daemon=True)
        self.reader.start()

    def _read_stdout(self):
        for line in self.process.stdout:
            self.lines.put(line.rstrip("\r\n"))
        self.lines.put(None)

    def send(self, line):
        self.process.stdin.write(line + "\n")
        self.process.stdin.flush()

    def receive(self, timeout=1.0):
        line = self.lines.get(timeout=timeout)
        if line is None:
            raise AssertionError("simulator exited before sending the expected line")
        return json.loads(line)

    def assert_silent(self, timeout=0.2):
        try:
            line = self.lines.get(timeout=timeout)
        except queue.Empty:
            return
        output = "process exit" if line is None else line
        raise AssertionError("expected no output during timeout window; received " + output)

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=2)
        self.reader.join(timeout=1)
        if self.process.stdin:
            self.process.stdin.close()
        if self.process.stdout:
            self.process.stdout.close()


class ProtocolSimulatorTests(unittest.TestCase):
    def setUp(self):
        self.simulator = None

    def tearDown(self):
        if self.simulator is not None:
            self.simulator.close()

    def start(self, scenario="normal", profile="hiwonder"):
        self.simulator = RunningSimulator(scenario, profile)

    def test_status_success_matches_fixture(self):
        self.start()
        self.assertEqual(
            self.simulator.receive(),
            {"event": "log", "text": "bridge ready, workspace=fixture-workspace"},
        )
        self.simulator.send(fixture_request("status.request.jsonl"))
        self.assertEqual(self.simulator.receive(), fixture_lines("status.response.jsonl")[0])

    def test_all_thirteen_commands_have_canned_wire_fixtures(self):
        commands = (
            "status", "ports", "connect", "disconnect", "run", "interrupt", "stop", "send",
            "download", "listfiles", "readfile", "writefile", "deletefile",
        )
        self.start(profile="generic")
        self.assertEqual(
            self.simulator.receive(),
            {"event": "log", "text": "bridge ready, workspace=fixture-workspace"},
        )
        for command in commands:
            with self.subTest(command=command):
                expected = fixture_lines(command + ".response.jsonl")
                self.simulator.send(fixture_request(command + ".request.jsonl"))
                observed = [self.simulator.receive() for _ in expected]
                self.assertEqual(observed, expected)

    def test_unknown_command_returns_correlated_error(self):
        self.start()
        self.simulator.receive()
        self.simulator.send(fixture_request("command-error.request.jsonl"))
        self.assertEqual(self.simulator.receive(), fixture_lines("command-error.response.jsonl")[0])

    def test_forced_operation_error_keeps_request_id(self):
        self.start(scenario="error")
        self.simulator.receive()
        self.simulator.send(fixture_request("operation-error.request.jsonl"))
        self.assertEqual(self.simulator.receive(), fixture_lines("operation-error.response.jsonl")[0])

    def test_malformed_request_error_has_no_id(self):
        self.start()
        self.simulator.receive()
        self.simulator.send(fixture_request("malformed-request.request.jsonl"))
        expected = fixture_lines("malformed-request.response.jsonl")[0]
        self.assertEqual(self.simulator.receive(), expected)
        self.assertNotIn("id", expected)

    def test_valid_non_object_json_returns_unmatched_error(self):
        self.start()
        self.simulator.receive()
        self.simulator.send(fixture_request("non-object.request.jsonl"))
        expected = fixture_lines("non-object.response.jsonl")[0]
        self.assertEqual(self.simulator.receive(), expected)
        self.assertNotIn("id", expected)

    def test_blank_line_is_ignored(self):
        self.start()
        self.simulator.receive()
        self.simulator.send("")
        self.simulator.send(fixture_request("status.request.jsonl"))
        self.assertEqual(self.simulator.receive(), fixture_lines("status.response.jsonl")[0])

    def test_missing_id_is_echoed_as_null(self):
        self.start()
        self.simulator.receive()
        self.simulator.send(fixture_request("missing-id.request.jsonl"))
        self.assertEqual(self.simulator.receive(), fixture_lines("missing-id.response.jsonl")[0])

    def test_multiple_ids_are_answered_in_request_order(self):
        self.start()
        self.simulator.receive()
        self.simulator.send(fixture_request("status.request.jsonl"))
        self.simulator.send(fixture_request("ports.request.jsonl"))
        observed = (self.simulator.receive(), self.simulator.receive())
        expected = (
            fixture_lines("status.response.jsonl")[0],
            fixture_lines("ports.response.jsonl")[0],
        )
        self.assertEqual(observed, expected)

    def test_send_console_event_is_interleaved_before_response(self):
        self.start()
        self.simulator.receive()
        self.simulator.send(fixture_request("send.request.jsonl"))
        self.assertEqual(
            (self.simulator.receive(), self.simulator.receive()),
            tuple(fixture_lines("send.response.jsonl")),
        )

    def test_download_compile_failure_shape(self):
        self.start(scenario="download-failure")
        self.simulator.receive()
        self.simulator.send(fixture_request("download.request.jsonl"))
        self.assertEqual(
            self.simulator.receive(),
            fixture_lines("download-failure.response.jsonl")[0],
        )

    def test_download_can_succeed_without_backup(self):
        self.start(scenario="download-backup-failure")
        self.simulator.receive()
        self.simulator.send(fixture_request("download.request.jsonl"))
        self.assertEqual(
            self.simulator.receive(),
            fixture_lines("download-backup-failure.response.jsonl")[0],
        )

    def test_download_integrity_failure_can_compile_successfully(self):
        self.start(scenario="download-integrity-failure")
        self.simulator.receive()
        self.simulator.send(fixture_request("download.request.jsonl"))
        self.assertEqual(
            self.simulator.receive(),
            fixture_lines("download-integrity-failure.response.jsonl")[0],
        )

    def test_timeout_scenario_stays_alive_without_response(self):
        self.start(scenario="timeout")
        self.assertEqual(
            self.simulator.receive(),
            {"event": "log", "text": "bridge ready, workspace=fixture-workspace"},
        )
        self.simulator.send(fixture_request("timeout.request.jsonl"))
        self.simulator.assert_silent()
        self.assertIsNone(self.simulator.process.poll())

    def test_exit_scenario_exits_without_response(self):
        self.start(scenario="exit")
        self.assertEqual(
            self.simulator.receive(),
            {"event": "log", "text": "bridge ready, workspace=fixture-workspace"},
        )
        self.simulator.send(fixture_request("exit.request.jsonl"))
        self.assertEqual(self.simulator.process.wait(timeout=2), 17)
        self.assertIsNone(self.simulator.lines.get(timeout=1))


if __name__ == "__main__":
    unittest.main()
