"""Single-instance broker process with shared reads and serialized control RPCs."""

from __future__ import annotations

import argparse
import ctypes
import hmac
import json
import os
import secrets
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any

from .identity import current_user_sid, user_key
from .job import OwnedProcessJob
from .protocol import PROTOCOL_VERSION, ProtocolError
from workspaces import validate_board_path
from workspace_control import claim_workspace, policy_snapshot, set_workspace_policy, selectable_workspace
from workspaces import detect_workspace, WorkspaceError

_FILE_COMMANDS = {"capabilities", "listfiles", "readfile", "download", "writefile", "deletefile"}
_FILE_EFFECT_COMMANDS = _FILE_COMMANDS - {"capabilities"}
_FILE_MUTATIONS = {"download", "writefile", "deletefile"}
from .winpipe import (
    PipeConnection,
    accept_pipe,
    connected_client_pid,
    create_server_pipe,
)


class BrokerHost:
    def __init__(
        self, auth_token: str, *, idle_timeout: float = 60.0,
        test_identity_sid: str | None = None,
    ):
        self.auth_token = auth_token
        if not 0.1 <= idle_timeout <= 600:
            raise ValueError("broker idle timeout must be between 0.1 and 600 seconds")
        self.idle_timeout = idle_timeout
        self.sid = test_identity_sid or current_user_sid()
        self.pipe_name = rf"\\.\pipe\esp32-codex-control-{user_key(self.sid)}"
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._operation_lock = threading.Lock()
        self._lease_count = 0
        self._active_connections = 0
        self._last_activity = time.monotonic()
        self._bridge = None
        self._mutex = None
        self._bridge_error: str | None = None
        self._job: OwnedProcessJob | None = None
        self._backend_config: dict[str, Any] | None = None
        self._control_epoch = 0
        self._confirmation_lock = threading.Lock()
        self._pending_confirmation: dict[str, Any] | None = None

    def run(self) -> int:
        if os.name != "nt":
            return 20
        mutex_name = rf"Global\Esp32CodexBroker-{user_key(self.sid)}"
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
        kernel.CreateMutexW.restype = wintypes.HANDLE
        kernel.ReleaseMutex.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        mutex = kernel.CreateMutexW(None, True, mutex_name)
        if not mutex:
            return 21
        if ctypes.get_last_error() == 183:
            kernel.CloseHandle(mutex)
            return 0
        self._mutex = mutex
        try:
            self._job = OwnedProcessJob()
            while not self._stop.is_set():
                with self._lock:
                    idle = self._lease_count == 0 and self._active_connections == 0
                    idle_for = time.monotonic() - self._last_activity
                if idle and idle_for >= self.idle_timeout:
                    break
                server = create_server_pipe(self.pipe_name)
                try:
                    accepted = accept_pipe(server, timeout=0.25)
                except OSError:
                    kernel.CloseHandle(server)
                    if self._stop.is_set():
                        break
                    raise
                if not accepted:
                    kernel.CloseHandle(server)
                    continue
                with self._lock:
                    self._active_connections += 1
                threading.Thread(
                    target=self._serve_connection,
                    args=(PipeConnection(server, read_timeout=15.0),),
                    name="esp32-broker-client",
                    daemon=True,
                ).start()
            return 0
        finally:
            self._close_bridge()
            if self._job is not None:
                self._job.close()
            kernel.ReleaseMutex(mutex)
            kernel.CloseHandle(mutex)

    def _serve_connection(self, connection: PipeConnection) -> None:
        leased = False

        def reject(reason: str) -> None:
            connection.send_json({"ok": False, "error": reason})
            # Keep the pipe alive long enough for the client to consume the rejection.
            # The next client frame or the bounded read timeout closes it without a lease.
            try:
                connection.recv_json()
            except (OSError, ProtocolError):
                pass

        try:
            hello = connection.recv_json()
            if hello.get("type") != "hello":
                reject("authentication required")
                return
            version = hello.get("protocol_version")
            if type(version) is not int or version != PROTOCOL_VERSION:
                reject("unsupported protocol version")
                return
            if set(hello) != {"type", "protocol_version", "auth_token", "idle_timeout", "backend_config"}:
                reject("invalid hello schema")
                return
            received_token = hello.get("auth_token")
            if not isinstance(received_token, str) or not hmac.compare_digest(received_token, self.auth_token):
                reject("authentication failed")
                return
            requested_idle = hello.get("idle_timeout")
            if isinstance(requested_idle, bool) or not isinstance(requested_idle, (int, float)):
                reject("invalid idle timeout")
                return
            if requested_idle < 0.1 or requested_idle > 600:
                reject("invalid idle timeout")
                return
            backend_config = hello.get("backend_config")
            if not self._valid_backend_config(backend_config):
                reject("invalid backend configuration")
                return
            backend_config = self._normalize_backend_config(backend_config)
            connection.set_read_timeout(min(float(requested_idle), self.idle_timeout))
            try:
                client_pid = connected_client_pid(connection._handle)
            except OSError:
                reject("client identity could not be verified")
                return
            with self._operation_lock:
                with self._lock:
                    if backend_config.get("local_bridge_owner") and self._lease_count:
                        reject("a local fileops bridge owner already holds the broker lease")
                        return
                    current_config = self._backend_config
                    runtime_matches = (
                        current_config is None
                        or self._same_runtime_config(current_config, backend_config)
                    )
                    if not runtime_matches and self._lease_count:
                        reject("backend configuration conflicts with an active broker lease")
                        return
                    if current_config is None or not runtime_matches:
                        old_bridge, self._bridge = self._bridge, None
                        self._bridge_error = None
                        self._backend_config = backend_config
                    else:
                        # workspace/profile/entry are live shared state. A new MCP or
                        # panel client may have been launched with an older snapshot;
                        # it must join the broker's current context instead of resetting it.
                        old_bridge = None
                if old_bridge is not None:
                    try:
                        old_bridge.close()
                    except Exception:
                        pass
                with self._lock:
                    self._lease_count += 1
                    self._last_activity = time.monotonic()
                    leased = True
                    lease_count = self._lease_count
            connection.send_json({
                "ok": True,
                "protocol_version": PROTOCOL_VERSION,
                "broker_pid": os.getpid(),
                "client_pid": client_pid,
                "lease_count": lease_count,
                "idle_timeout": min(float(requested_idle), self.idle_timeout),
            })
            while True:
                message = connection.recv_json()
                kind = message.get("type")
                if kind == "ping":
                    if set(message) != {"type"}:
                        connection.send_json({"ok": False, "error": "invalid ping schema"})
                        continue
                    connection.send_json({"ok": True, "type": "pong"})
                    continue
                if kind == "release":
                    if set(message) != {"type"}:
                        connection.send_json({"ok": False, "error": "invalid release schema"})
                        return
                    with self._lock:
                        self._lease_count = max(0, self._lease_count - 1)
                        self._last_activity = time.monotonic()
                        leased = False
                        close_bridge = self._lease_count == 0
                    if close_bridge:
                        self._close_bridge()
                    connection.send_json({"ok": True})
                    # Let the caller consume the acknowledgment before this endpoint closes.
                    try:
                        connection.recv_json()
                    except (OSError, ProtocolError):
                        pass
                    return
                if kind != "request":
                    connection.send_json({"ok": False, "error": "invalid message type"})
                    continue
                if set(message) != {"type", "operation", "arguments"}:
                    connection.send_json({"ok": False, "error": "invalid request schema"})
                    continue
                operation = message.get("operation")
                args = message.get("arguments")
                if not isinstance(args, dict):
                    connection.send_json({"ok": False, "error": "arguments must be an object"})
                    continue
                try:
                    result = self._dispatch(operation, args)
                    connection.send_json({"ok": True, "result": result})
                except Exception as exc:
                    connection.send_json({"ok": False, "error": str(exc)[:512]})
        except (OSError, ProtocolError):
            pass
        finally:
            connection.close()
            with self._lock:
                self._active_connections = max(0, self._active_connections - 1)
                self._last_activity = time.monotonic()
                if leased:
                    self._lease_count = max(0, self._lease_count - 1)
                    close_bridge = self._lease_count == 0
                else:
                    close_bridge = False
                if close_bridge:
                    # Defer bridge shutdown until after releasing the broker lock.
                    close_bridge_after_lock = True
                else:
                    close_bridge_after_lock = False
            if close_bridge_after_lock:
                self._close_bridge()

    @staticmethod
    def _valid_backend_config(value: Any) -> bool:
        expected = {
            "mode", "workspace", "profile", "bridge_script", "mock_script",
            "mock_scenario", "allow_real_controls", "allow_real_writes", "local_bridge_owner",
        }
        if not isinstance(value, dict) or frozenset(value) not in {
            frozenset(expected), frozenset(expected | {"entry"}),
        }:
            return False
        if value.get("mode") not in {"mock", "bridge"}:
            return False
        if value.get("profile") not in {"hiwonder", "generic"}:
            return False
        if value.get("mock_scenario") not in {
            "readonly", "normal", "busy", "control", "fileops", "fileops-no-capability",
        }:
            return False
        for key in ("workspace", "bridge_script", "mock_script"):
            if value.get(key) is not None and not isinstance(value[key], str):
                return False
            if value.get(key) and not Path(value[key]).is_absolute():
                return False
        if not value.get("mock_script"):
            return False
        if value["mode"] == "bridge" and (not value.get("workspace") or not value.get("bridge_script")):
            return False
        if type(value.get("allow_real_controls")) is not bool or type(value.get("allow_real_writes")) is not bool:
            return False
        if type(value.get("local_bridge_owner")) is not bool:
            return False
        if value["allow_real_writes"] and not value["allow_real_controls"]:
            return False
        if value["mode"] == "mock" and (value["allow_real_controls"] or value["allow_real_writes"]):
            return False
        entry = value.get("entry")
        if entry is not None:
            try:
                validate_board_path(entry)
            except (TypeError, WorkspaceError):
                return False
            if not entry.lower().endswith(".py"):
                return False
        return True

    @staticmethod
    def _normalize_backend_config(value: dict[str, Any]) -> dict[str, Any]:
        """Fill the entry for older internal clients before storing broker state."""
        if value.get("entry"):
            return dict(value)
        profile = value["profile"]
        entry = "/corex.py" if profile == "hiwonder" else "/main.py"
        workspace = value.get("workspace")
        if workspace:
            try:
                info = detect_workspace(workspace)
            except WorkspaceError:
                info = None
            if info and info.get("profile") == profile and isinstance(info.get("entry"), str):
                entry = info["entry"]
        return {**value, "entry": entry}

    @staticmethod
    def _same_runtime_config(left: dict[str, Any], right: dict[str, Any]) -> bool:
        mutable = {"workspace", "profile", "entry"}
        if left.get("mode") == right.get("mode") == "bridge":
            mutable.add("mock_scenario")
        return (
            {key: value for key, value in left.items() if key not in mutable}
            == {key: value for key, value in right.items() if key not in mutable}
        )

    def _dispatch(self, operation: Any, arguments: dict[str, Any]) -> dict[str, Any]:
        if operation not in {"status", "ports", "console", "control", "file", "workspace", "confirmation"}:
            raise ValueError("unsupported broker operation")
        if operation == "workspace":
            return self._workspace_dispatch(arguments)
        if operation == "confirmation":
            return self._confirmation_dispatch(arguments)
        if operation in {"status", "ports"} and arguments:
            raise ValueError("read operation does not accept arguments")
        if operation == "console":
            since = arguments.get("since")
            max_chars = arguments.get("max_chars", 12000)
            if set(arguments) - {"since", "max_chars"}:
                raise ValueError("invalid console arguments")
            if since is not None and (type(since) is not int or since < 0):
                raise ValueError("since must be a non-negative integer or null")
            if type(max_chars) is not int or not 1 <= max_chars <= 20000:
                raise ValueError("max_chars must be between 1 and 20000")
        if operation == "control":
            if set(arguments) != {"command", "arguments", "expected_epoch"}:
                raise ValueError("invalid control arguments")
            command = arguments.get("command")
            control_args = arguments.get("arguments")
            expected_epoch = arguments.get("expected_epoch")
            if command not in {"connect", "disconnect", "stop", "run", "interrupt", "send"}:
                raise ValueError("unsupported control command")
            if not isinstance(control_args, dict):
                raise ValueError("control command arguments must be an object")
            if type(expected_epoch) is not int or expected_epoch < 0:
                raise ValueError("control epoch is required")
            config = self._backend_config or {}
            if config.get("mode") == "bridge" and config.get("allow_real_controls") is not True:
                raise PermissionError("real control RPC requires --enable-control-tools")
        if operation == "file":
            if set(arguments) != {"command", "arguments", "expected_epoch"}:
                raise ValueError("invalid file RPC arguments")
            command = arguments.get("command")
            file_args = arguments.get("arguments")
            epoch = arguments.get("expected_epoch")
            if command not in _FILE_COMMANDS or not isinstance(file_args, dict):
                raise ValueError("unsupported file command or arguments")
            if type(epoch) is not int or epoch < 0:
                raise ValueError("file control epoch is required")
            if command in {"capabilities", "listfiles"} and file_args:
                raise ValueError("file command does not accept arguments")
            if command == "readfile":
                if set(file_args) - {"path", "raw", "maxBytes"} or "path" not in file_args:
                    raise ValueError("invalid readfile arguments")
                if "raw" in file_args and type(file_args["raw"]) is not bool:
                    raise ValueError("raw must be boolean")
                if "maxBytes" in file_args and (type(file_args["maxBytes"]) is not int or not 1 <= file_args["maxBytes"] <= 1024 * 1024):
                    raise ValueError("maxBytes must be between 1 and 1 MiB")
            if command in _FILE_MUTATIONS:
                expected_keys = {"path", "content", "strictBackup", "expectedOldCrc"} if command == "writefile" else ({"target", "content", "run", "strictBackup"} if command == "download" else {"path", "strictBackup"})
                if set(file_args) - expected_keys or file_args.get("strictBackup") is not True:
                    raise PermissionError("mutating file RPC requires strictBackup=true and valid arguments")
                if command != "deletefile":
                    content = file_args.get("content")
                    if not isinstance(content, str) or not 0 < len(content.encode("utf-8")) <= 1024 * 1024:
                        raise ValueError("file payload must be nonempty UTF-8, at most 1 MiB")
                if command == "download" and type(file_args.get("run", False)) is not bool:
                    raise ValueError("run must be boolean")
                if command == "writefile" and "expectedOldCrc" in file_args:
                    crc = file_args["expectedOldCrc"]
                    if not isinstance(crc, str) or len(crc) != 8 or any(ch not in "0123456789abcdefABCDEF" for ch in crc):
                        raise ValueError("expectedOldCrc must be eight hexadecimal characters")
            if command in {"readfile", "writefile", "deletefile"}:
                validate_board_path(file_args.get("path"))
            if command == "download":
                validate_board_path(file_args.get("target"))
            config = self._backend_config or {}
            if config.get("mode") == "bridge" and config.get("allow_real_writes") is not True:
                raise PermissionError("real file RPC requires --enable-write-tools and --enable-control-tools")
        if (self._backend_config or {}).get("local_bridge_owner"):
            raise PermissionError("broker RPC is unavailable while a local fileops bridge owns this session")
        with self._operation_lock:
            current = self._bridge
            current_process = getattr(current, "_process", None) if current is not None else None
            if current_process is not None and current_process.poll() is not None:
                try:
                    current.close()
                except Exception:
                    pass
                self._bridge = None
                self._bridge_error = None
            bridge = self._get_bridge()
            if not getattr(bridge, "_broker_job_assigned", False):
                bridge.start()
                process = getattr(bridge, "_process", None)
                if process is None or self._job is None:
                    raise RuntimeError("broker cannot contain its mock bridge process")
                try:
                    self._job.assign(process._handle)
                except OSError as exc:
                    try:
                        bridge.close()
                    finally:
                        self._bridge = None
                    self._bridge_error = "broker cannot contain its bridge process"
                    raise RuntimeError(self._bridge_error) from exc
                bridge._broker_job_assigned = True
            if operation in {"status", "ports"}:
                data = bridge.call(operation)
                if operation == "status":
                    config = self._backend_config or {}
                    context = {
                        "workspacePath": config.get("workspace"),
                        "profile": config.get("profile"),
                        "entry": config.get("entry"),
                        "control_epoch": self._control_epoch,
                    }
                    data = {
                        **data,
                        "workspace": config.get("workspace"),
                        "_broker_control_epoch": self._control_epoch,
                        "_broker_workspace_context": context,
                    }
            elif operation == "control":
                if arguments["expected_epoch"] != self._control_epoch:
                    raise RuntimeError("device control state changed while confirmation was pending")
                status = bridge.call("status")
                command = arguments["command"]
                control_args = arguments["arguments"]
                connected = status.get("connected") is True
                if command == "connect":
                    if connected:
                        raise RuntimeError("bridge is already connected; connect request refused")
                    ports = bridge.call("ports").get("ports", [])
                    devices = [row.get("device") for row in ports if isinstance(row, dict)]
                    if control_args.get("port") not in devices:
                        raise RuntimeError("requested port is not in the current bridge port list")
                elif command == "disconnect":
                    if not connected:
                        return {"data": {"connected": False, "alreadyDisconnected": True}, "control_epoch": self._control_epoch}
                elif not connected:
                    raise RuntimeError(f"{command} requires a connected device")
                data = bridge.call(command, control_args)
                self._control_epoch += 1
                return {"data": data, "control_epoch": self._control_epoch}
            elif operation == "file":
                if arguments["expected_epoch"] != self._control_epoch:
                    raise RuntimeError("device control state changed while file operation was pending")
                command = arguments["command"]
                file_args = arguments["arguments"]
                if command != "capabilities" and bridge.call("status").get("connected") is not True:
                    raise RuntimeError("file operation requires a connected device")
                if command in _FILE_MUTATIONS:
                    capabilities = bridge.call("capabilities")
                    if type(capabilities.get("codexStrictBackup")) is not int or capabilities["codexStrictBackup"] != 1:
                        raise PermissionError("strict backup capability required before mutation")
                try:
                    data = bridge.call(command, file_args)
                finally:
                    if command in _FILE_EFFECT_COMMANDS:
                        self._control_epoch += 1
                return {"data": data, "control_epoch": self._control_epoch}
            else:
                since = arguments.get("since")
                max_chars = arguments.get("max_chars", 12000)
                data = bridge.read_console(since=since, max_chars=max_chars)
            process = getattr(bridge, "_process", None)
            return {
                **({"status": data} if operation == "status" else data),
                "broker_pid": os.getpid(),
                "backend_pid": process.pid if process is not None else None,
                "source": bridge.source,
                "simulated": bridge.simulated,
            }

    def _workspace_dispatch(self, args: dict[str, Any]) -> dict[str, Any]:
        """The authenticated pipe exposes internal workspace commands, not MCP mutators."""
        action = args.get("action")
        shapes = {
            "current": {"action"}, "policy_get": {"action"},
            "select": {"action", "path", "expected_epoch"},
            "claim": {"action", "path", "profile", "label", "entry", "expected_epoch"},
            "policy_set": {"action", "policy", "revision", "expected_epoch"},
        }
        if action not in shapes or set(args) != shapes[action]:
            raise ValueError("invalid workspace RPC action or schema")
        with self._operation_lock:
            config = self._backend_config
            if config is None:
                raise RuntimeError("no active broker workspace")
            if action == "current":
                path = config.get("workspace")
                info = detect_workspace(path) if path else None
                if info is not None:
                    info = {**info, "profile": config["profile"], "entry": config["entry"]}
                return {"workspacePath": path, "profile": config["profile"],
                        "entry": config["entry"], "info": info,
                        "control_epoch": self._control_epoch}
            if action == "policy_get":
                path = config.get("workspace")
                if not path:
                    raise WorkspaceError("active workspace is not set")
                return {
                    **policy_snapshot(path), "profile": config["profile"],
                    "entry": config["entry"], "control_epoch": self._control_epoch,
                }
            if (type(args.get("expected_epoch")) is not int or
                    args["expected_epoch"] != self._control_epoch):
                raise RuntimeError("workspace state changed while selection was pending")
            if self._bridge is not None:
                status = self._bridge.call("status")
                if status.get("busy") is True:
                    raise RuntimeError("device bridge is busy; workspace or policy change refused")
                if status.get("connected") is not False:
                    raise RuntimeError("disconnect the current device before changing workspace or policy")
            if action == "select":
                info = selectable_workspace(args["path"])
                new_path = info["workspacePath"]
                previous = self._bridge
                if previous is not None:
                    previous.close()
                self._bridge = None
                self._bridge_error = None
                self._backend_config = {
                    **config, "workspace": new_path,
                    "profile": info["profile"], "entry": info["entry"],
                }
                self._control_epoch += 1
                return {**info, "control_epoch": self._control_epoch}
            if action == "claim":
                info = claim_workspace(args["path"], args["profile"], args["label"], args["entry"])
                active_context = None
                current_path = config.get("workspace")
                if (current_path and os.path.normcase(os.path.abspath(current_path))
                        == os.path.normcase(os.path.abspath(info["workspacePath"]))):
                    context_changed = (
                        config["profile"] != info["profile"] or config["entry"] != info["entry"]
                    )
                    if context_changed:
                        self._backend_config = {
                            **config, "profile": info["profile"], "entry": info["entry"],
                        }
                        self._control_epoch += 1
                    active_context = {
                        "workspacePath": current_path, "profile": info["profile"],
                        "entry": info["entry"], "control_epoch": self._control_epoch,
                    }
                return {
                    **info, "control_epoch": self._control_epoch,
                    **({"active_context": active_context} if active_context else {}),
                }
            if action == "policy_set":
                if not config.get("workspace"):
                    raise WorkspaceError("active workspace is not set")
                result = set_workspace_policy(config["workspace"], args["policy"], args["revision"])
                self._control_epoch += 1
                return {
                    **result, "profile": config["profile"], "entry": config["entry"],
                    "control_epoch": self._control_epoch,
                }
        raise ValueError("unsupported workspace action")

    def _confirmation_context(self) -> dict[str, Any]:
        config = self._backend_config
        if config is None:
            raise RuntimeError("broker backend configuration is not initialized")
        workspace = config.get("workspace")
        revision = policy_snapshot(workspace)["revision"] if workspace else "missing"
        return {
            "workspacePath": workspace,
            "profile": config["profile"],
            "entry": config["entry"],
            "control_epoch": self._control_epoch,
            "policy_revision": revision,
        }

    @staticmethod
    def _confirmation_public(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": item["id"], "state": item["state"],
            "action": item["action"], "effect": item["effect"],
            "title": item["title"], "target": item["target"],
            "impact": item["impact"], "workspacePath": item["workspacePath"],
            "profile": item["profile"], "entry": item["entry"],
            "control_epoch": item["control_epoch"],
            "policy_revision": item["policy_revision"],
            "expires_in_ms": max(0, int((item["expires_at"] - time.monotonic()) * 1000)),
        }

    def _confirmation_dispatch(self, args: dict[str, Any]) -> dict[str, Any]:
        """One-shot human decisions; the broker never exposes approval as an MCP tool."""
        action = args.get("action")
        if action == "create":
            expected = {
                "action", "operation", "effect", "title", "target", "impact",
                "workspacePath", "profile", "entry", "control_epoch",
                "policy_revision", "request_digest", "ttl_ms",
            }
            if set(args) != expected:
                raise ValueError("invalid confirmation request schema")
            for key, limit in (("operation", 80), ("effect", 24), ("title", 256),
                               ("target", 2048), ("impact", 4096)):
                value = args.get(key)
                if (not isinstance(value, str) or not value or len(value) > limit
                        or any(ord(ch) < 32 and ch not in "\n\t" for ch in value)):
                    raise ValueError(f"invalid confirmation {key}")
            if args.get("effect") not in {"control", "write", "workspace"}:
                raise ValueError("invalid confirmation effect")
            if args.get("profile") not in {"hiwonder", "generic"}:
                raise ValueError("invalid confirmation profile")
            if args.get("workspacePath") is not None and not isinstance(args.get("workspacePath"), str):
                raise ValueError("invalid confirmation workspace")
            if not isinstance(args.get("entry"), str):
                raise ValueError("invalid confirmation entry")
            epoch = args.get("control_epoch")
            revision = args.get("policy_revision")
            digest = args.get("request_digest")
            ttl_ms = args.get("ttl_ms")
            if type(epoch) is not int or epoch < 0:
                raise ValueError("invalid confirmation epoch")
            if (not isinstance(revision, str) or
                    (revision != "missing" and (len(revision) != 64 or any(ch not in "0123456789abcdef" for ch in revision.lower())))):
                raise ValueError("invalid confirmation policy revision")
            if (not isinstance(digest, str) or len(digest) != 64
                    or any(ch not in "0123456789abcdef" for ch in digest.lower())):
                raise ValueError("invalid confirmation request digest")
            if type(ttl_ms) is not int or not 1000 <= ttl_ms <= 120000:
                raise ValueError("invalid confirmation expiry")
            with self._operation_lock:
                current = self._confirmation_context()
                for key in ("workspacePath", "profile", "entry", "control_epoch", "policy_revision"):
                    if args[key] != current[key]:
                        raise RuntimeError("confirmation context changed before request")
                with self._confirmation_lock:
                    existing = self._pending_confirmation
                    if existing and existing["state"] in {"pending", "approved"}:
                        if time.monotonic() >= existing["expires_at"]:
                            existing["state"] = "expired"
                        elif self._confirmation_context() != {
                            key: existing[key] for key in current
                        }:
                            existing["state"] = "stale"
                        else:
                            raise RuntimeError("another confirmation is already pending")
                    item = {
                        **current,
                        "id": secrets.token_urlsafe(24),
                        "state": "pending",
                        "action": args["operation"],
                        "effect": args["effect"],
                        "title": args["title"],
                        "target": args["target"],
                        "impact": args["impact"],
                        "request_digest": digest.lower(),
                        "expires_at": time.monotonic() + ttl_ms / 1000,
                    }
                    self._pending_confirmation = item
                    return self._confirmation_public(item)
        if action not in {"list", "status", "resolve", "consume", "cancel"}:
            raise ValueError("unsupported confirmation action")
        if action == "list" and set(args) != {"action"}:
            raise ValueError("invalid confirmation list schema")
        if action != "list" and set(args) != {"action", "id"} | ({"decision"} if action == "resolve" else set()) | ({"request_digest"} if action == "consume" else set()):
            raise ValueError("invalid confirmation action schema")
        confirmation_id = args.get("id")
        if action != "list" and (not isinstance(confirmation_id, str) or not 20 <= len(confirmation_id) <= 128):
            raise ValueError("invalid confirmation id")
        with self._operation_lock:
            current = self._confirmation_context()
            with self._confirmation_lock:
                item = self._pending_confirmation
                if item is None:
                    return {"state": "unknown"} if action != "list" else {"items": []}
                if time.monotonic() >= item["expires_at"] and item["state"] in {"pending", "approved"}:
                    item["state"] = "expired"
                elif any(item.get(key) != current.get(key) for key in current):
                    if item["state"] in {"pending", "approved"}:
                        item["state"] = "stale"
                if action == "list":
                    visible = [self._confirmation_public(item)] if item["state"] == "pending" else []
                    return {"items": visible}
                if item["id"] != confirmation_id:
                    return {"state": "unknown"}
                if action == "status":
                    return self._confirmation_public(item)
                if action == "resolve":
                    decision = args.get("decision")
                    if decision not in {"approve", "reject"}:
                        raise ValueError("invalid confirmation decision")
                    if item["state"] != "pending":
                        return {"ok": False, "state": item["state"]}
                    item["state"] = "approved" if decision == "approve" else "rejected"
                    return {"ok": True, "state": item["state"]}
                if action == "consume":
                    digest = args.get("request_digest")
                    if not isinstance(digest, str) or not secrets.compare_digest(digest.lower(), item["request_digest"]):
                        return {"ok": False, "state": "request_mismatch"}
                    if item["state"] != "approved":
                        return {"ok": False, "state": item["state"]}
                    self._pending_confirmation = None
                    return {"ok": True, "state": "consumed"}
                if action == "cancel":
                    if item["state"] in {"pending", "approved"}:
                        item["state"] = "cancelled"
                    self._pending_confirmation = None
                    return {"ok": True, "state": item["state"]}
        raise RuntimeError("confirmation operation was not handled")

    def _get_bridge(self):
        if self._bridge is not None:
            return self._bridge
        if self._bridge_error:
            raise RuntimeError(self._bridge_error)
        config = self._backend_config
        if config is None:
            raise RuntimeError("broker backend configuration is not initialized")
        try:
            server_dir = str(Path(__file__).resolve().parents[1])
            if server_dir not in sys.path:
                sys.path.insert(0, server_dir)
            from bridge_client import BridgeClient

            self._bridge = BridgeClient(
                mode=config["mode"],
                workspace=config["workspace"],
                profile=config["profile"],
                bridge_script=config["bridge_script"],
                mock_script=config["mock_script"],
                mock_scenario=config["mock_scenario"],
                allow_real_controls=config["allow_real_controls"],
                allow_real_writes=config["allow_real_writes"],
            )
            return self._bridge
        except Exception as exc:
            self._bridge_error = "broker bridge could not be initialized"
            raise RuntimeError(self._bridge_error) from exc

    def _close_bridge(self) -> None:
        with self._operation_lock:
            bridge, self._bridge = self._bridge, None
            if bridge is not None:
                try:
                    bridge.close()
                except Exception:
                    pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--auth-file", required=True)
    parser.add_argument("--idle-timeout", type=float, default=60.0)
    parser.add_argument("--test-identity-sid", help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        token = Path(args.auth_file).read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return 22
    if len(token) != 64 or any(ch not in "0123456789abcdef" for ch in token):
        return 23
    return BrokerHost(
        token, idle_timeout=args.idle_timeout,
        test_identity_sid=args.test_identity_sid,
    ).run()


if __name__ == "__main__":
    raise SystemExit(main())
