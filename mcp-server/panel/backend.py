"""Read-only snapshots for an ESP32 panel sharing the MCP broker-owned bridge."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bridge_client import BridgeClient
from bridge_client import BridgeFailure
from policy import confirmation_required
from broker.adapter import BrokerBridgeClient
from broker.client import BrokerClient


class PanelBackend:
    def __init__(self, bridge: BrokerBridgeClient):
        self.bridge = bridge
        self._console_cursor: int | None = None

    @classmethod
    def default_mock(
        cls, *, workspace: str | None = None, profile: str = "generic",
        scenario: str = "fileops",
    ) -> "PanelBackend":
        """Never requests a real bridge or the physical serial port."""
        script = Path(__file__).resolve().parents[2] / "tests" / "mock_bridge.py"
        local = BridgeClient(
            mode="mock", workspace=workspace, profile=profile,
            bridge_script=None, mock_script=str(script), mock_scenario=scenario,
        )
        return cls(BrokerBridgeClient(local, BrokerClient()))

    @property
    def simulated(self) -> bool:
        return self.bridge.simulated

    def snapshot(self) -> dict[str, Any]:
        """Observe only; never call connect, control, write or open a real port."""
        current = self.bridge.workspace_current()
        status = self.bridge.call_shared("status")
        ports = self.bridge.call_shared("ports")
        policy = (
            self.bridge.workspace_policy() if current.get("workspacePath") else
            {"policy": "confirm-write", "source": "default", "reason": "no_workspace"}
        )
        console = self.bridge.read_console(since=self._console_cursor, max_chars=12000)
        cursor = console.get("cursor")
        if type(cursor) is int and cursor >= 0:
            self._console_cursor = cursor
        return {
            "workspace": current,
            "status": status,
            "ports": ports,
            "policy": policy,
            "console": console,
            "agentConfirmations": self.bridge.panel_pending_confirmations(),
            "source": self.bridge.source,
            "simulated": self.bridge.simulated,
        }

    def resolve_agent_confirmation(self, confirmation_id: str, *, approve: bool) -> dict[str, Any]:
        """Resolve one broker-issued Agent request from a panel UI event."""
        return self.bridge.panel_resolve_confirmation(confirmation_id, approve=approve)

    def discover_workspaces(self, root: str) -> dict[str, Any]:
        """Read-only bounded candidate discovery; never claims or selects a workspace."""
        from workspaces import list_workspaces
        return list_workspaces(root)

    def plan_control(self, command: str, *, port: str = "", line: str = "") -> dict[str, Any]:
        if command not in {"connect", "disconnect", "stop", "run", "interrupt", "send"}:
            raise ValueError("unsupported panel control action")
        status = self.bridge.call_shared("status")
        workspace = self.bridge.workspace_current().get("workspacePath")
        if not workspace and command not in {"stop", "disconnect"}:
            raise BridgeFailure("select or claim a workspace before device control")
        connected = status.get("connected") is True
        arguments: dict[str, Any] = {}
        if command == "connect":
            if not port or not isinstance(port, str):
                raise ValueError("select an explicit serial port before connecting")
            if connected:
                if status.get("port") == port:
                    return {"command": command, "noop": True, "result": {"connected": True, "alreadyConnected": True}}
                raise BridgeFailure("disconnect the existing port before connecting another")
            ports = self.bridge.call_shared("ports").get("ports", [])
            if port not in {r.get("device") for r in ports if isinstance(r, dict)}:
                raise BridgeFailure("selected port is not in the current bridge port list")
            arguments = {"port": port}
        elif command == "disconnect":
            if not connected:
                return {"command": command, "noop": True, "result": {"connected": False, "alreadyDisconnected": True}}
        elif not connected:
            raise BridgeFailure("connect a device before this operation")
        if command == "send":
            if (not isinstance(line, str) or not line.strip() or len(line) > 512
                    or any(ord(ch) < 32 or ord(ch) == 127 for ch in line)):
                raise ValueError("REPL requires one nonempty line of at most 512 characters")
            arguments = {"line": line}
        effects = {"send": "write", "connect": "control", "disconnect": "control",
                   "stop": "control", "run": "control", "interrupt": "control"}
        impacts = {
            "connect": f"Connect to the explicitly selected port {port}; may initialize a physical board.",
            "disconnect": "Release this app-owned connection; does not prove physical motors stopped.",
            "stop": "Request bridge stop; generic only interrupts code, no physical stop confirmation.",
            "run": "Run the configured board program.",
            "interrupt": "Interrupt the board's running program; motors may remain active.",
            "send": f"Execute one REPL line (may control hardware/write files): {line}",
        }
        return {"command": command, "arguments": arguments, "effect": effects[command],
                "target": f"{workspace or '(no workspace)'} / {status.get('port') or port or 'no port'}",
                "impact": impacts[command], "epoch": self.bridge.control_epoch,
                "noop": False}

    def perform_control(self, plan: dict[str, Any]) -> dict[str, Any]:
        if plan.get("noop"):
            return dict(plan["result"])
        return self.bridge.call_control(
            plan["command"], plan["arguments"], expected_epoch=plan["epoch"]
        )

    def current_policy(self) -> str:
        if not self.bridge.workspace:
            return "confirm-write"
        return self.bridge.workspace_policy()["policy"]

    def plan_download(self, filename: str, *, run: bool = False) -> dict[str, Any]:
        from server import Esp32McpTools
        current = self.bridge.workspace_current()
        info = current.get("info") or {}
        entry = info.get("entry") or ("/corex.py" if self.bridge.profile == "hiwonder" else "/main.py")
        tools = Esp32McpTools(self.bridge, entry=entry, allow_mock_elicitation=False,
                              write_tools_enabled=True)
        summary = tools.download_source_summary(filename)
        return {"command": "download", "effect": "write", "filename": filename,
                "entry": entry, "sha256": summary["sha256"], "size": summary["size"],
                "run": run, "target": f"{current.get('workspacePath')} -> {entry}",
                "impact": (f"Strict-backup then download {summary['size']} bytes, SHA-256 "
                           f"{summary['sha256']} to {entry}; verify size/CRC/syntax. "
                           + ("Run only after validation." if run else "Do not run after download.")),
                "epoch": self.bridge.control_epoch}

    def perform_download(self, plan: dict[str, Any]) -> dict[str, Any]:
        from server import Esp32McpTools
        self.bridge.call_shared("status")
        if self.bridge.control_epoch != plan["epoch"]:
            raise BridgeFailure("device changed after download approval; start again")
        tools = Esp32McpTools(self.bridge, entry=plan["entry"],
                              allow_mock_elicitation=False, write_tools_enabled=True)
        return tools.download(plan["filename"], run=plan["run"],
                              expected_sha256=plan["sha256"])

    def plan_workspace(self, action: str, path: str, *, profile: str = "generic",
                       label: str | None = None, entry: str | None = None) -> dict[str, Any]:
        if action not in {"select", "claim"}:
            raise ValueError("unsupported workspace action")
        status = self.bridge.call_shared("status")
        if status.get("connected") is not False:
            raise BridgeFailure("disconnect before changing or claiming workspaces")
        return {"command": action, "path": path, "profile": profile,
                "label": label, "entry": entry, "effect": "write", "epoch": self.bridge.control_epoch,
                "target": path,
                "impact": ("Select a new shared workspace without overwriting files." if action == "select"
                           else "Exclusively create board.json; never overwrite an existing file.")}

    def perform_workspace(self, plan: dict[str, Any]) -> dict[str, Any]:
        self.bridge.call_shared("status")
        if self.bridge.control_epoch != plan["epoch"]:
            raise BridgeFailure("workspace state changed after approval")
        if plan["command"] == "select":
            return self.bridge.workspace_select(plan["path"])
        return self.bridge.workspace_claim(plan["path"], plan["profile"],
                                           plan["label"], plan["entry"])

    def plan_policy(self, policy: str) -> dict[str, Any]:
        snapshot = self.bridge.workspace_policy()
        return {"command": "policy_set", "target": snapshot["workspacePath"],
                "policy": policy, "revision": snapshot["revision"],
                "effect": "write", "epoch": self.bridge.control_epoch,
                "impact": f"Change active policy from {snapshot['policy']} to {policy}. "
                          "auto can run sensitive actions without repeated prompts."}

    def perform_policy(self, plan: dict[str, Any]) -> dict[str, Any]:
        self.bridge.call_shared("status")
        if self.bridge.control_epoch != plan["epoch"]:
            raise BridgeFailure("policy state changed after approval")
        return self.bridge.workspace_policy_set(plan["policy"], plan["revision"])

    @staticmethod
    def needs_confirmation(policy: str, effect: str, command: str) -> bool:
        if command in {"stop", "disconnect"}:
            return False  # safety / release actions remain immediately available
        if command in {"claim", "select", "policy_set"}:
            return True  # explicit local configuration changes always prompt
        return confirmation_required(policy, effect)

    def close(self) -> None:
        """Release only this panel's lease; never terminate other clients."""
        self.bridge.close()
