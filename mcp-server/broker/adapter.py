"""BridgeClient-compatible facade for all shared broker operations."""

from __future__ import annotations

import threading
from typing import Any

from bridge_client import BridgeFailure, BridgeClient, COMMAND_TIMEOUTS, FILE_COMMANDS

from .client import BrokerClient, BrokerUnavailable


_BROKER_FIELDS = {
    "broker_pid", "backend_pid", "source", "simulated", "_broker_control_epoch",
    "_broker_workspace_context",
}


class BrokerBridgeClient:
    """Share exactly one broker-owned bridge across MCP services and panel clients."""

    def __init__(self, local: BridgeClient, broker: BrokerClient):
        self._local = local
        self._broker = broker
        self._control_epoch = 0
        self._entry: str | None = self._initial_entry(local)
        self._start_lock = threading.Lock()
        self._started = False

    @staticmethod
    def _initial_entry(local: BridgeClient) -> str:
        if local.workspace:
            try:
                from workspaces import WorkspaceError, detect_workspace
                info = detect_workspace(local.workspace)
            except (OSError, WorkspaceError):
                info = None
            if info and info.get("profile") == local.profile and isinstance(info.get("entry"), str):
                return info["entry"]
        return "/corex.py" if local.profile == "hiwonder" else "/main.py"

    @property
    def mode(self) -> str:
        return self._local.mode

    @property
    def workspace(self) -> str | None:
        self._refresh_workspace_if_started()
        return self._local.workspace

    @property
    def profile(self) -> str:
        self._refresh_workspace_if_started()
        return self._local.profile

    @property
    def entry(self) -> str | None:
        self._refresh_workspace_if_started()
        return self._entry

    def set_entry(self, value: str) -> None:
        self._entry = value

    @property
    def bridge_script(self) -> str | None:
        return self._local.bridge_script

    @property
    def mock_script(self) -> str:
        return self._local.mock_script

    @property
    def mock_scenario(self) -> str:
        return self._local.mock_scenario

    @property
    def allow_real_controls(self) -> bool:
        return self._local.allow_real_controls

    @property
    def allow_real_writes(self) -> bool:
        return self._local.allow_real_writes

    @property
    def source(self) -> str:
        return self._local.source

    @property
    def simulated(self) -> bool:
        return self._local.simulated

    @property
    def control_epoch(self) -> int:
        return self._control_epoch

    def _backend_config(self) -> dict[str, Any]:
        entry = self._entry or ("/corex.py" if self._local.profile == "hiwonder" else "/main.py")
        return {
            "mode": self._local.mode,
            "workspace": self._local.workspace,
            "profile": self._local.profile,
            "entry": entry,
            "bridge_script": self._local.bridge_script,
            "mock_script": self._local.mock_script,
            "mock_scenario": self._local.mock_scenario,
            "allow_real_controls": self._local.allow_real_controls,
            "allow_real_writes": self._local.allow_real_writes,
            "local_bridge_owner": False,
        }

    def start(self) -> None:
        if self._started:
            return
        with self._start_lock:
            if self._started:
                return
            try:
                self._broker._backend_config = self._backend_config()
                self._broker.connect()
            except BrokerUnavailable as exc:
                raise BridgeFailure(str(exc)) from exc
            self._started = True

    def _refresh_workspace_if_started(self) -> None:
        if self._started:
            self.workspace_current()

    def _adopt_workspace_context(self, value: dict[str, Any]) -> None:
        workspace = value.get("workspacePath")
        profile = value.get("profile")
        entry = value.get("entry")
        epoch = value.get("control_epoch")
        if workspace is not None and not isinstance(workspace, str):
            raise BridgeFailure("broker workspace response is invalid")
        if profile not in {"hiwonder", "generic"}:
            raise BridgeFailure("broker workspace profile is invalid")
        if not isinstance(entry, str) or type(epoch) is not int or epoch < 0:
            raise BridgeFailure("broker workspace context is invalid")
        self._local.workspace = workspace
        self._local.profile = profile
        self._entry = entry
        self._control_epoch = epoch
        self._broker._backend_config = self._backend_config()

    def call(self, command: str, arguments: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        if kwargs:
            raise BridgeFailure("broker bridge does not accept local process-only options")
        if command in {"connect", "disconnect", "stop", "run", "interrupt", "send"}:
            return self.call_control(command, arguments or {}, expected_epoch=self._control_epoch)
        if command in {"status", "ports"}:
            return self.call_shared(command, arguments)
        if command in FILE_COMMANDS:
            return self.call_file(command, arguments or {})
        raise BridgeFailure(f"broker refuses out-of-scope bridge command: {command}")

    def call_control(
        self, command: str, arguments: dict[str, Any] | None = None, *, expected_epoch: int
    ) -> dict[str, Any]:
        self.start()
        try:
            result = self._broker.request("control", {
                "command": command,
                "arguments": arguments or {},
                "expected_epoch": expected_epoch,
            }, timeout=COMMAND_TIMEOUTS[command] + 15.0)
        except BrokerUnavailable as exc:
            raise BridgeFailure(str(exc)) from exc
        data = result.get("data")
        epoch = result.get("control_epoch")
        if not isinstance(data, dict) or type(epoch) is not int:
            raise BridgeFailure("broker control response is invalid")
        self._control_epoch = epoch
        return data

    def call_file(self, command: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.start()
        try:
            response = self._broker.request("file", {
                "command": command, "arguments": arguments,
                "expected_epoch": self._control_epoch,
            }, timeout=COMMAND_TIMEOUTS[command] + 15.0)
        except BrokerUnavailable as exc:
            raise BridgeFailure(str(exc)) from exc
        data = response.get("data")
        epoch = response.get("control_epoch")
        if not isinstance(data, dict) or type(epoch) is not int:
            raise BridgeFailure("broker file response is invalid")
        self._control_epoch = epoch
        return data

    def call_shared(self, command: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        if command not in {"status", "ports"}:
            raise BridgeFailure("only ports and status are available through the read-only broker")
        self.start()
        try:
            result = self._broker.request(command, arguments)
        except BrokerUnavailable as exc:
            raise BridgeFailure(str(exc)) from exc
        if command == "status":
            status = result.get("status")
            if not isinstance(status, dict):
                raise BridgeFailure("broker status response is invalid")
            epoch = status.get("_broker_control_epoch")
            if type(epoch) is int:
                self._control_epoch = epoch
            context = status.get("_broker_workspace_context")
            if isinstance(context, dict):
                self._adopt_workspace_context(context)
            return {key: value for key, value in status.items() if key not in _BROKER_FIELDS}
        return {key: value for key, value in result.items() if key not in _BROKER_FIELDS}

    def read_console(self, *, since: int | None, max_chars: int) -> dict[str, Any]:
        if not 1 <= max_chars <= 20000:
            raise ValueError("max_chars 必须在 1 到 20000 之间")
        self.start()
        try:
            result = self._broker.request("console", {"since": since, "max_chars": max_chars})
        except BrokerUnavailable as exc:
            raise BridgeFailure(str(exc)) from exc
        return {key: value for key, value in result.items() if key not in _BROKER_FIELDS}

    def workspace_current(self) -> dict[str, Any]:
        self.start()
        try:
            result = self._broker.request("workspace", {"action": "current"})
        except BrokerUnavailable as exc:
            raise BridgeFailure(str(exc)) from exc
        self._adopt_workspace_context(result)
        return result

    def workspace_claim(
        self, path: str, profile: str, label: str | None = None,
        entry: str | None = None,
    ) -> dict[str, Any]:
        self.start()
        try:
            result = self._broker.request("workspace", {
                "action": "claim", "path": path, "profile": profile,
                "label": label, "entry": entry, "expected_epoch": self._control_epoch,
            })
        except BrokerUnavailable as exc:
            raise BridgeFailure(str(exc)) from exc
        active_context = result.get("active_context")
        if isinstance(active_context, dict):
            self._adopt_workspace_context(active_context)
            return result
        epoch = result.get("control_epoch")
        if type(epoch) is int and epoch >= 0:
            self._control_epoch = epoch
        return result

    def workspace_select(self, path: str) -> dict[str, Any]:
        self.start()
        try:
            info = self._broker.request("workspace", {
                "action": "select", "path": path,
                "expected_epoch": self._control_epoch,
            })
        except BrokerUnavailable as exc:
            raise BridgeFailure(str(exc)) from exc
        self._adopt_workspace_context(info)
        return info

    def workspace_policy(self) -> dict[str, Any]:
        self.start()
        try:
            result = self._broker.request("workspace", {"action": "policy_get"})
        except BrokerUnavailable as exc:
            raise BridgeFailure(str(exc)) from exc
        self._adopt_workspace_context(result)
        return result

    def workspace_policy_set(self, policy: str, revision: str) -> dict[str, Any]:
        """Internal UI-only endpoint; never register this as an unconfirmed MCP tool."""
        self.start()
        try:
            info = self._broker.request("workspace", {
                "action": "policy_set", "policy": policy, "revision": revision,
                "expected_epoch": self._control_epoch,
            })
        except BrokerUnavailable as exc:
            raise BridgeFailure(str(exc)) from exc
        self._adopt_workspace_context(info)
        return info

    def confirmation_request(self, **request: Any) -> dict[str, Any]:
        """Create one pending confirmation for an MCP operation; never approves it."""
        self.start()
        try:
            return self._broker.request("confirmation", {"action": "create", **request})
        except BrokerUnavailable as exc:
            raise BridgeFailure(str(exc)) from exc

    def confirmation_status(self, confirmation_id: str) -> dict[str, Any]:
        self.start()
        try:
            return self._broker.request("confirmation", {"action": "status", "id": confirmation_id})
        except BrokerUnavailable as exc:
            raise BridgeFailure(str(exc)) from exc

    def agent_pending_confirmations(self) -> list[dict[str, Any]]:
        """Return pending requests created by this MCP broker lease only."""
        self.start()
        try:
            result = self._broker.request("confirmation", {"action": "agent_list"})
        except BrokerUnavailable as exc:
            raise BridgeFailure(str(exc)) from exc
        rows = result.get("items")
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    def agent_decide_confirmation(self, confirmation_id: str, *, decision: str) -> dict[str, Any]:
        """Make an explicitly attributed Agent decision on this MCP lease's pending request."""
        self.start()
        try:
            return self._broker.request("confirmation", {
                "action": "agent_resolve", "id": confirmation_id, "decision": decision,
            })
        except BrokerUnavailable as exc:
            raise BridgeFailure(str(exc)) from exc

    def confirmation_consume(self, confirmation_id: str, request_digest: str) -> dict[str, Any]:
        """Consume a one-time approval after the server's exact request digest matches."""
        self.start()
        try:
            return self._broker.request("confirmation", {
                "action": "consume", "id": confirmation_id,
                "request_digest": request_digest,
            })
        except BrokerUnavailable as exc:
            raise BridgeFailure(str(exc)) from exc

    def confirmation_cancel(self, confirmation_id: str) -> dict[str, Any]:
        self.start()
        try:
            return self._broker.request("confirmation", {"action": "cancel", "id": confirmation_id})
        except BrokerUnavailable as exc:
            raise BridgeFailure(str(exc)) from exc

    def panel_pending_confirmations(self) -> list[dict[str, Any]]:
        """Panel-only read endpoint. This method is deliberately not registered with MCP."""
        self.start()
        try:
            result = self._broker.request("confirmation", {"action": "list"})
        except BrokerUnavailable as exc:
            raise BridgeFailure(str(exc)) from exc
        rows = result.get("items")
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    def panel_resolve_confirmation(self, confirmation_id: str, *, approve: bool) -> dict[str, Any]:
        """Panel-only UI decision endpoint; no MCP tool exposes this resolver."""
        self.start()
        try:
            return self._broker.request("confirmation", {
                "action": "resolve", "id": confirmation_id,
                "decision": "approve" if approve else "reject",
            })
        except BrokerUnavailable as exc:
            raise BridgeFailure(str(exc)) from exc

    def close(self) -> None:
        self._broker.close()
        self._local.close()
