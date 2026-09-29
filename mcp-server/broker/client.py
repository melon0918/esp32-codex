"""Client and launcher for the per-user, single-instance mock control broker."""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
import time
import threading
from pathlib import Path
from typing import Any

from .identity import current_user_sid, user_key
from .protocol import PROTOCOL_VERSION, ProtocolError
from .security import create_private_token_file
from .winpipe import PipeConnection, PipeError, open_client_pipe


class BrokerUnavailable(RuntimeError):
    """The broker is absent, unhealthy, or rejected the client contract."""


def pipe_address(sid: str | None = None) -> str:
    return rf"\\.\pipe\esp32-codex-control-{user_key(sid)}"


def token_path() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        raise BrokerUnavailable("LOCALAPPDATA is unavailable; broker startup is denied")
    return Path(base) / "Esp32Codex" / "broker.token"


def _read_or_create_token(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = create_private_token_file(path)
    except FileExistsError:
        last_error: Exception | None = None
        for attempt in range(11):
            try:
                value = path.read_text(encoding="ascii").strip()
            except (OSError, UnicodeError) as exc:
                last_error = exc
                value = ""
            if len(value) == 64 and all(char in "0123456789abcdef" for char in value):
                return value
            if value and any(char not in "0123456789abcdef" for char in value):
                raise BrokerUnavailable("broker authentication material is invalid")
            if attempt < 10:
                time.sleep(0.05)
        if last_error is not None:
            raise BrokerUnavailable("broker authentication material cannot be read") from last_error
        raise BrokerUnavailable("broker authentication material is invalid")
    value = secrets.token_hex(32)
    try:
        with os.fdopen(descriptor, "w", encoding="ascii", newline="") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise BrokerUnavailable("broker authentication material could not be created") from exc
    return value


class BrokerClient:
    """One leased connection to the user's broker; starts it in mock mode by default."""

    def __init__(
        self, *, auth_token: str | None = None, auth_path: Path | None = None,
        idle_timeout: float = 60.0, backend_config: dict[str, Any] | None = None,
        _identity_sid_for_testing: str | None = None,
    ):
        self._identity_sid_for_testing = _identity_sid_for_testing
        self._sid = _identity_sid_for_testing or current_user_sid()
        self._pipe_name = pipe_address(self._sid)
        self._auth_path = auth_path or token_path()
        self._token = auth_token or _read_or_create_token(self._auth_path)
        if not 0.1 <= idle_timeout <= 600:
            raise ValueError("broker idle timeout must be between 0.1 and 600 seconds")
        self._idle_timeout = idle_timeout
        self._backend_config = dict(backend_config or {
            "mode": "mock",
            "workspace": None,
            "profile": "hiwonder",
            "bridge_script": None,
            "mock_script": str(Path(__file__).resolve().parents[2] / "tests" / "mock_bridge.py"),
            "mock_scenario": "control",
            "allow_real_controls": False,
            "allow_real_writes": False,
            "local_bridge_owner": False,
        })
        # Isolate mock-only automated suites from the user's live broker. Never
        # apply this opt-in namespace to real bridge mode or override a test's
        # explicit per-case identity. The child host receives this identity.
        namespace = os.environ.get("ESP32_CODEX_TEST_NAMESPACE", "")
        if (_identity_sid_for_testing is None and self._backend_config["mode"] == "mock"
                and len(namespace) == 32 and all(ch in "0123456789abcdef" for ch in namespace)):
            self._identity_sid_for_testing = f"{current_user_sid()}-test-{namespace}"
            self._sid = self._identity_sid_for_testing
            self._pipe_name = pipe_address(self._sid)

        self._connection: PipeConnection | None = None
        self._owned_process: subprocess.Popen[bytes] | None = None
        self._request_seq = 0
        self._request_lock = threading.Lock()
        self._connection_lock = threading.Lock()
        self._connect_lock = threading.Lock()
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None
        self._negotiated_idle_timeout: float | None = None
        self._lease_lost = False

    @property
    def broker_process_id(self) -> int | None:
        return self._owned_process.pid if self._owned_process is not None else None

    def connect(self, *, timeout: float = 5.0) -> dict[str, Any]:
        with self._connect_lock:
            with self._connection_lock:
                if self._connection is not None:
                    raise BrokerUnavailable("client already has an active broker lease")
            return self._open_connection(timeout)

    def _open_connection(self, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            try:
                connection = open_client_pipe(self._pipe_name, timeout_ms=250)
                break
            except (PipeError, OSError):
                if self._owned_process is None and time.monotonic() < deadline:
                    self._start_broker()
                if time.monotonic() >= deadline:
                    raise BrokerUnavailable("single-instance broker is unavailable")
                time.sleep(0.05)
        with self._connection_lock:
            self._connection = connection
        try:
            response = self._exchange(
                {
                    "type": "hello",
                    "protocol_version": PROTOCOL_VERSION,
                    "auth_token": self._token,
                    "idle_timeout": self._idle_timeout,
                    "backend_config": self._backend_config,
                }
            )
            if response.get("ok") is not True:
                raise BrokerUnavailable(str(response.get("error", "broker rejected client")))
            if response.get("protocol_version") != PROTOCOL_VERSION:
                raise BrokerUnavailable("broker protocol version mismatch")
            negotiated = response.get("idle_timeout")
            if isinstance(negotiated, bool) or not isinstance(negotiated, (int, float)):
                raise BrokerUnavailable("broker returned an invalid lease timeout")
            if not 0 < negotiated <= self._idle_timeout:
                raise BrokerUnavailable("broker returned an invalid lease timeout")
            self._negotiated_idle_timeout = float(negotiated)
            with self._connection_lock:
                self._lease_lost = False
            self._heartbeat_stop = threading.Event()
            self._heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop,
                name="esp32-broker-heartbeat",
                daemon=True,
            )
            self._heartbeat_thread.start()
            return response
        except Exception:
            self.close()
            raise

    def request(
        self, operation: str, arguments: dict[str, Any] | None = None,
        *, timeout: float | None = None,
    ) -> dict[str, Any]:
        with self._connection_lock:
            connection = self._connection
        if connection is None:
            if not self._is_safe_reconnect_read(operation, arguments):
                raise BrokerUnavailable("client has no broker lease")
            connection = self._reconnect_lost_lease()
        try:
            response = self._exchange(
                {"type": "request", "operation": operation, "arguments": arguments or {}},
                connection=connection, read_timeout=timeout,
            )
        except (OSError, PipeError, ProtocolError) as exc:
            with self._connection_lock:
                if self._connection is connection:
                    self._connection = None
                    self._lease_lost = True
            connection.close()
            raise BrokerUnavailable("broker request transport failed; lease was released") from exc
        if response.get("ok") is not True:
            raise BrokerUnavailable(str(response.get("error", "broker request failed")))
        result = response.get("result")
        if not isinstance(result, dict):
            raise BrokerUnavailable("broker returned an invalid result")
        return result

    @staticmethod
    def _is_safe_reconnect_read(operation: str, arguments: dict[str, Any] | None) -> bool:
        args = arguments or {}
        if operation in {"ports", "status", "console"}:
            return True
        if operation == "workspace":
            return args.get("action") in {"current", "policy_get"}
        if operation == "confirmation":
            return args.get("action") in {"status", "list"}
        return False

    def _reconnect_lost_lease(self) -> PipeConnection:
        with self._connect_lock:
            with self._connection_lock:
                if self._connection is not None:
                    return self._connection
                if not self._lease_lost:
                    raise BrokerUnavailable("client has no broker lease")
            self._open_connection(timeout=5.0)
            with self._connection_lock:
                connection = self._connection
            if connection is None:
                raise BrokerUnavailable("broker reconnect did not establish a lease")
            return connection

    def close(self) -> None:
        self._heartbeat_stop.set()
        with self._request_lock:
            with self._connection_lock:
                connection, self._connection = self._connection, None
                self._lease_lost = False
            if connection is not None:
                try:
                    connection.send_json({"type": "release"})
                    connection.recv_json()
                except (OSError, PipeError, ProtocolError, BrokerUnavailable):
                    pass
                finally:
                    connection.close()
        heartbeat = self._heartbeat_thread
        self._heartbeat_thread = None
        if heartbeat is not None and heartbeat is not threading.current_thread():
            heartbeat.join(timeout=1.0)

    def _exchange(
        self, value: dict[str, Any], *, connection: PipeConnection | None = None,
        read_timeout: float | None = None,
    ) -> dict[str, Any]:
        if connection is not None:
            target = connection
        else:
            with self._connection_lock:
                target = self._connection
        if target is None:
            raise BrokerUnavailable("named pipe is not connected")
        try:
            with self._request_lock:
                previous_timeout = target._read_timeout
                if read_timeout is not None:
                    target.set_read_timeout(read_timeout)
                try:
                    target.send_json(value)
                    return target.recv_json()
                finally:
                    if read_timeout is not None:
                        target.set_read_timeout(previous_timeout)
        except (OSError, PipeError, ProtocolError):
            with self._connection_lock:
                if self._connection is target:
                    self._connection = None
                    self._lease_lost = True
            target.close()
            raise

    def _heartbeat_loop(self) -> None:
        interval = max(0.025, min((self._negotiated_idle_timeout or 60.0) / 3.0, 20.0))
        while not self._heartbeat_stop.wait(interval):
            with self._connection_lock:
                connection = self._connection
            if connection is None:
                return
            try:
                response = self._exchange({"type": "ping"}, connection=connection)
            except (OSError, PipeError, ProtocolError, BrokerUnavailable):
                return
            if response.get("ok") is not True or response.get("type") != "pong":
                with self._connection_lock:
                    if self._connection is connection:
                        self._connection = None
                        self._lease_lost = True
                connection.close()
                return

    def _start_broker(self) -> None:
        if os.name != "nt":
            raise BrokerUnavailable("the control broker requires Windows")
        host_script = Path(__file__).with_name("host.py")
        if not host_script.is_file():
            raise BrokerUnavailable("broker host module is missing")
        try:
            argv = [
                    sys.executable, "-B", "-X", "utf8", "-u", "-m", "broker.host",
                    "--auth-file", str(self._auth_path), "--idle-timeout", str(self._idle_timeout),
                ]
            if self._identity_sid_for_testing is not None:
                argv.extend(["--test-identity-sid", self._identity_sid_for_testing])
            self._owned_process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=str(host_script.parent.parent),
                close_fds=True,
                creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
            )
        except (OSError, ValueError) as exc:
            raise BrokerUnavailable("cannot start broker host") from exc


def connect_two_clients_for_test(path: Path) -> tuple[BrokerClient, BrokerClient]:
    """Test helper is intentionally private to test modules; each client shares the same token."""
    return BrokerClient(auth_path=path), BrokerClient(auth_path=path)
