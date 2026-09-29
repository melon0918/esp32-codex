"""ESP32 MCP server with read-only tools and policy-gated control tools."""

from __future__ import annotations

import argparse
import asyncio
import atexit
import base64
import hashlib
import json
import logging
import math
import os
import tempfile
import re
import sys
import threading
import zlib
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field

from bridge_client import BridgeClient, BridgeFailure, explain_bridge_error, is_outcome_unknown
from broker.adapter import BrokerBridgeClient
from broker.client import BrokerClient
from broker.identity import current_user_sid
from policy import DEFAULT_POLICY, POLICIES, confirmation_required, read_workbench_policy
from workspace_control import selectable_workspace
from workspaces import (
    PROFILE_META, WorkspaceError, absolute_directory, detect_workspace,
    list_workspaces, validate_board_path,
)


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MOCK_SCRIPT = PLUGIN_ROOT / "tests" / "mock_bridge.py"
LOGGER = logging.getLogger("esp32_codex_mcp.server")
MAX_MUTATION_BYTES = 1024 * 1024
CRC_PATTERN = re.compile(r"^[0-9A-Fa-f]{8}$")
PID_PATTERN = {
    name: re.compile(
        rf"(?m)^([ \t]*{name}[ \t]*=[ \t]*)"
        r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
    )
    for name in ("Kp", "Ki", "Kd")
}
SENSITIVE_FILE_NAMES = {"wifi.json", "config.json", "credentials.json", "secrets.json", ".env"}
SENSITIVE_KEY_PATTERN = re.compile(
    r"(?:pass(?:word|wd)?|pwd|api[-_ ]?key|secret|token|credential|authorization|ssid)",
    re.IGNORECASE,
)
SENSITIVE_TEXT_PATTERN = re.compile(
    r"(?i)(?P<key>[\"']?(?:password|passwd|pwd|api[-_ ]?key|secret|token|credential|authorization|ssid)[\"']?\s*[:=]\s*)"
    r"(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^,\r\n;#}]+)"
)
BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")


def _sanitize_output(value: Any) -> tuple[Any, list[str]]:
    """Redact common credential fields in bridge output without logging values."""
    fields: set[str] = set()

    def visit(item: Any) -> Any:
        if isinstance(item, dict):
            clean: dict[str, Any] = {}
            for key, child in item.items():
                label = str(key)
                if SENSITIVE_KEY_PATTERN.search(label):
                    fields.add(label)
                    clean[key] = "[已隐藏]"
                else:
                    clean[key] = visit(child)
            return clean
        if isinstance(item, list):
            return [visit(child) for child in item]
        if not isinstance(item, str):
            return item

        stripped = item.lstrip()
        if stripped.startswith(("{", "[")):
            try:
                parsed = json.loads(item)
            except (json.JSONDecodeError, ValueError):
                pass
            else:
                cleaned = visit(parsed)
                return json.dumps(cleaned, ensure_ascii=False, separators=(",", ":"))

        def replace_sensitive(match: re.Match[str]) -> str:
            key = match.group("key").strip(" \t\"'=: ")
            fields.add(key or "sensitive_value")
            return match.group("key") + "[已隐藏]"

        cleaned_text = SENSITIVE_TEXT_PATTERN.sub(replace_sensitive, item)
        if BEARER_PATTERN.search(cleaned_text):
            fields.add("authorization")
            cleaned_text = BEARER_PATTERN.sub("Bearer [已隐藏]", cleaned_text)
        return cleaned_text

    return visit(value), sorted(fields, key=str.casefold)


class OperationApproval(BaseModel):
    decision: str = Field(
        description="Return exactly approve to authorize this operation, or decline. Other values are rejected."
    )


class Esp32McpTools:
    def __init__(
        self,
        client: BridgeClient,
        *,
        entry: str,
        allow_mock_elicitation: bool,
        control_tools_enabled: bool = False,
        write_tools_enabled: bool = False,
    ) -> None:
        self.client = client
        self._initial_entry = entry
        set_entry = getattr(client, "set_entry", None)
        if set_entry is not None:
            set_entry(entry)
        self.allow_mock_elicitation = allow_mock_elicitation
        self.control_tools_enabled = control_tools_enabled
        self.write_tools_enabled = write_tools_enabled
        self._control_lock = threading.RLock()
        self._file_lock = self._control_lock
        self._control_epoch = 0

    @property
    def entry(self) -> str:
        # A workspace selected through the broker can change the active entry.
        current_workspace = getattr(self.client, "workspace_current", None)
        if callable(current_workspace):
            current = current_workspace()
            current_entry = current.get("entry") if isinstance(current, dict) else None
            if isinstance(current_entry, str) and current_entry:
                return current_entry
        return getattr(self.client, "entry", None) or self._initial_entry

    def _tag_bridge(self, value: dict[str, Any]) -> dict[str, Any]:
        sanitized, redacted_fields = _sanitize_output(value)
        tagged = {
            **sanitized,
            "source": self.client.source,
            "simulated": self.client.simulated,
        }
        if redacted_fields:
            tagged["redacted"] = True
            tagged["redactedFields"] = redacted_fields
        return tagged


    def _current_panel_launch_context(self) -> tuple[str | None, str]:
        """Use the broker's active workspace rather than MCP startup arguments."""
        current = self.client.workspace_current()
        workspace = current.get("workspacePath")
        profile = current.get("profile")
        if workspace is not None and not isinstance(workspace, str):
            raise BridgeFailure("共享工作区路径无效；未启动面板")
        if profile not in {"generic", "hiwonder"}:
            raise BridgeFailure("共享工作区机型无效；未启动面板")
        return workspace, profile

    def _bridge_error(self, error: Exception, *, port: str = "") -> dict[str, Any]:
        message = str(error)
        if is_outcome_unknown(error):
            return self._tag_bridge({
                "ok": False,
                "errorCode": "outcome_unknown",
                "retryable": False,
                "error": "桥命令响应未确认，操作结果未知。请先查询状态，不要重放可能已执行的命令。",
            })
        return self._tag_bridge({
            "ok": False,
            "errorCode": "bridge_error",
            "retryable": False,
            "error": explain_bridge_error(message, port=port),
        })

    def capabilities(self) -> dict[str, Any]:
        """Report registered tool groups and explicit runtime safety gates."""
        local = getattr(self.client, "_local", self.client)
        mode = getattr(local, "mode", "unknown")
        profile = getattr(local, "profile", "unknown")
        simulated = getattr(self.client, "simulated", mode == "mock")
        control_enabled = self.control_tools_enabled and (
            simulated or getattr(local, "allow_real_controls", False)
        )
        write_enabled = self.write_tools_enabled and (
            simulated or getattr(local, "allow_real_writes", False)
        )
        real_file_access = simulated or write_enabled
        return self._tag_bridge({
            "ok": True,
            "mode": mode,
            "profile": profile,
            "toolGroups": {
                "readOnly": {"registered": True, "enabled": True},
                "control": {
                    "registered": self.control_tools_enabled,
                    "enabled": control_enabled,
                    "reason": "" if control_enabled else (
                        "需要 --enable-control-tools" if not self.control_tools_enabled
                        else "真实桥命令白名单未启用控制"
                    ),
                },
                "write": {
                    "registered": self.write_tools_enabled,
                    "enabled": write_enabled,
                    "reason": "" if write_enabled else (
                        "需要 --enable-write-tools" if not self.write_tools_enabled
                        else "真实桥命令白名单未启用写入"
                    ),
                },
            },
            "profileConstraints": {
                "serialPorts": "由当前 profile 过滤",
                "pidDefaultEntry": "/corex.py" if profile == "hiwonder" else "/main.py",
                "stopCommand": (
                    "hiwonder 桥尝试双电机零速；未确认物理停转"
                    if profile == "hiwonder"
                    else "generic 仅中断程序；电机或舵机可能保持当前位置"
                ),
            },
            "runtimeGates": {
                "realControlCommandsEnabled": control_enabled,
                "boardFileAccessEnabled": real_file_access,
                "strictBackupRequiredBeforeMutation": True,
                "capabilityCheckedBeforeMutation": True,
                "rawReadCapabilityRequiredForPid": True,
                "realConfirmationsFailClosed": True,
                "unknownCommandOutcomeRetryable": False,
            },
        })

    def snapshot(self, since: int | None, max_chars: int) -> dict[str, Any]:
        """Read a bounded, epoch-checked view of workspace and bridge state."""
        if since is not None and (type(since) is not int or since < 0):
            return self._tag_bridge({
                "ok": False, "errorCode": "invalid_request", "retryable": False,
                "error": "since 必须是非负整数。",
            })
        if type(max_chars) is not int or not 1 <= max_chars <= 20000:
            return self._tag_bridge({
                "ok": False, "errorCode": "invalid_request", "retryable": False,
                "error": "max_chars 必须在 1 到 20000 之间。",
            })
        try:
            before = self.client.workspace_current()
        except (BridgeFailure, WorkspaceError) as exc:
            return self._bridge_error(exc)

        state = {
            "workspace": before,
            "status": self.status(),
            "ports": self.serial_ports(),
            "console": self.console_read(since, max_chars),
        }
        try:
            after = self.client.workspace_current()
        except (BridgeFailure, WorkspaceError) as exc:
            return self._bridge_error(exc)

        before_epoch = before.get("control_epoch")
        after_epoch = after.get("control_epoch")
        if type(before_epoch) is not int or before_epoch != after_epoch:
            return self._tag_bridge({
                "ok": False,
                "errorCode": "snapshot_conflict",
                "retryable": True,
                "error": "读取期间共享设备状态发生变化；请重新读取快照。",
                "control_epoch": after_epoch,
            })
        ok = all(result.get("ok") is not False for result in state.values())
        return self._tag_bridge({
            "ok": ok,
            "control_epoch": before_epoch,
            "state": state,
        })

    def _require_file_tools(self) -> None:
        if not self.client.simulated and not self.write_tools_enabled:
            raise BridgeFailure(
                "真实桥文件工具默认关闭；需要显式 --enable-write-tools 配置。板载文件访问可能中断程序"
            )
        if not self.client.simulated and not self.client.allow_real_writes:
            raise BridgeFailure("真实桥文件命令默认关闭；需要显式 --enable-write-tools 配置")

    def _require_connected(self) -> dict[str, Any]:
        status = self.client.call("status")
        if status.get("connected") is not True:
            raise BridgeFailure("not connected")
        return status

    def _require_strict_capabilities(self, *, raw_file_read: bool = False) -> dict[str, Any]:
        capabilities = self.client.call("capabilities")
        strict_backup = capabilities.get("codexStrictBackup")
        if type(strict_backup) is not int or strict_backup != 1:
            raise BridgeFailure(
                "桥未声明 codexStrictBackup capability；在发送任何写入或删除命令前已拒绝操作"
            )
        if raw_file_read:
            raw_read = capabilities.get("codexRawFileRead")
            if type(raw_read) is not int or raw_read != 1:
                raise BridgeFailure(
                    "桥未声明 codexRawFileRead capability；在读取 PID 或发送修改命令前已拒绝操作"
                )
        return capabilities

    def _check_strict_backup(self, data: dict[str, Any], *, require_existing: bool = False) -> dict[str, Any]:
        existed = data.get("targetExisted")
        verified = data.get("backupVerified")
        backup_path = data.get("backupPath")
        backup_size = data.get("backupSize")
        backup_crc = data.get("backupCrc")
        if type(existed) is not bool or type(verified) is not bool:
            raise BridgeFailure("严格桥响应缺少 targetExisted/backupVerified 状态；结果不可信")
        if existed:
            if (
                verified is not True
                or not isinstance(backup_path, str)
                or not backup_path
                or not isinstance(backup_size, int)
                or isinstance(backup_size, bool)
                or backup_size < 0
                or not isinstance(backup_crc, str)
                or not CRC_PATTERN.fullmatch(backup_crc)
            ):
                raise BridgeFailure("严格桥未证明现存目标的原始备份已保存并校验")
            if not self.client.simulated:
                if not self.client.workspace:
                    raise BridgeFailure("无法验证严格备份：真实桥没有工作区路径")
                root = Path(self.client.workspace).resolve(strict=True)
                saved_path = Path(backup_path)
                if saved_path.is_symlink() or not saved_path.is_absolute():
                    raise BridgeFailure("严格备份路径无效")
                saved_path = saved_path.resolve(strict=True)
                if saved_path.parent != root or not saved_path.is_file():
                    raise BridgeFailure("严格备份未保存在工作区根目录")
                try:
                    saved = saved_path.read_bytes()
                except OSError as exc:
                    raise BridgeFailure(f"无法读取严格备份文件: {exc}") from exc
                actual_crc = "%08X" % (zlib.crc32(saved) & 0xFFFFFFFF)
                if len(saved) != backup_size or actual_crc != backup_crc.upper():
                    raise BridgeFailure("严格备份文件的长度或 CRC 校验失败")
        elif (
            verified is not False
            or backup_path != ""
            or backup_size != 0
            or backup_crc != ""
            or require_existing
        ):
            raise BridgeFailure("严格桥未证明首次创建/目标存在状态；操作结果不可信")
        return {
            "targetExisted": existed,
            "backupVerified": verified,
            "backupPath": backup_path,
            "backupSize": backup_size,
            "backupCrc": backup_crc,
            "backupStatus": "verified" if existed else "target_absent",
        }

    @staticmethod
    def _check_write_response(
        data: dict[str, Any], *, expected: bytes, require_compile: bool = False
    ) -> dict[str, Any]:
        expected_crc = "%08X" % (zlib.crc32(expected) & 0xFFFFFFFF)
        if data.get("ok") is not True:
            raise BridgeFailure(str(data.get("error") or "桥未确认写入成功"))
        size = data.get("size")
        crc = data.get("crc")
        if (
            not isinstance(size, int)
            or isinstance(size, bool)
            or size != len(expected)
            or not isinstance(crc, str)
            or crc.upper() != expected_crc
        ):
            raise BridgeFailure("桥写入响应的 size/CRC 与请求内容不符")
        if require_compile and data.get("compileOk") is not True:
            raise BridgeFailure("桥未确认 MicroPython 语法校验成功")
        return {"size": size, "crc": crc.upper()}

    @staticmethod
    def _decode_raw_read(data: dict[str, Any], path: str) -> tuple[bytes, str]:
        if data.get("path") != path:
            raise BridgeFailure("原始文件读取响应路径与请求不符")
        size = data.get("size")
        crc = data.get("crc")
        encoded = data.get("contentBase64")
        if (
            not isinstance(size, int)
            or isinstance(size, bool)
            or not 0 <= size <= MAX_MUTATION_BYTES
            or not isinstance(crc, str)
            or not CRC_PATTERN.fullmatch(crc)
            or not isinstance(encoded, str)
        ):
            raise BridgeFailure("桥原始文件响应缺少有效的长度、CRC 或 Base64 内容")
        try:
            content = base64.b64decode(encoded, validate=True)
        except (ValueError, base64.binascii.Error) as exc:
            raise BridgeFailure("桥原始文件响应 Base64 无效") from exc
        local_crc = "%08X" % (zlib.crc32(content) & 0xFFFFFFFF)
        if len(content) != size or local_crc != crc.upper():
            raise BridgeFailure("桥原始文件响应的字节数或 CRC 校验失败")
        return content, local_crc

    def _raw_board_file(self, path: str) -> tuple[bytes, str]:
        self._require_connected()
        result = self.client.call(
            "readfile", {"path": path, "raw": True, "maxBytes": MAX_MUTATION_BYTES}
        )
        return self._decode_raw_read(result, path)

    def board_file_write(self, path: str, content: str) -> dict[str, Any]:
        self._require_file_tools()
        safe_path = validate_board_path(path)
        if not isinstance(content, str) or not content:
            raise BridgeFailure("内容不能为空")
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_MUTATION_BYTES:
            raise BridgeFailure("板载文件内容超过 1 MiB 上限")
        with self._file_lock:
            self._require_connected()
            self._require_strict_capabilities()
            self._control_epoch += 1
            result = self.client.call(
                "writefile", {"path": safe_path, "content": content, "strictBackup": True}
            )
            if result.get("path") != safe_path:
                raise BridgeFailure("桥写入响应路径与请求不符")
            written = self._check_write_response(result, expected=encoded)
            backup = self._check_strict_backup(result)
        return self._tag_bridge({"ok": True, "path": safe_path, **written, **backup})

    def board_file_delete(self, path: str) -> dict[str, Any]:
        self._require_file_tools()
        safe_path = validate_board_path(path)
        with self._file_lock:
            self._require_connected()
            self._require_strict_capabilities()
            self._control_epoch += 1
            result = self.client.call("deletefile", {"path": safe_path, "strictBackup": True})
            if result.get("ok") is not True:
                raise BridgeFailure(str(result.get("error") or "桥未确认删除成功"))
            if result.get("path") != safe_path:
                raise BridgeFailure("桥删除响应路径与请求不符")
            backup = self._check_strict_backup(result, require_existing=True)
        return self._tag_bridge({"ok": True, "path": safe_path, **backup})

    def _workspace_python_payload(self, filename: str) -> tuple[bytes, str, str]:
        if (
            not isinstance(filename, str)
            or not filename
            or filename in {".", ".."}
            or Path(filename).name != filename
            or Path(filename).suffix.lower() != ".py"
            or any(char in filename for char in ("/", "\\", ":"))
        ):
            raise BridgeFailure("下载只接受工作区根目录的单个 .py 文件名")
        if not self.client.workspace:
            raise BridgeFailure("下载前必须通过 --workspace 指定工作区")
        try:
            root = Path(self.client.workspace).resolve(strict=True)
            source = root / filename
            resolved_source = source.resolve(strict=True)
        except OSError as exc:
            raise BridgeFailure(f"下载源不存在或无法读取: {exc}") from exc
        if source.is_symlink() or resolved_source.parent != root:
            raise BridgeFailure("下载文件必须是工作区根目录的普通文件，拒绝符号链接或路径穿越")
        if not source.is_file():
            raise BridgeFailure("下载源不是普通文件")
        try:
            payload = source.read_bytes()
        except OSError as exc:
            raise BridgeFailure(f"无法读取下载源: {exc}") from exc
        if not payload or len(payload) > MAX_MUTATION_BYTES:
            raise BridgeFailure("下载文件必须为 1 字节到 1 MiB")
        try:
            content = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise BridgeFailure("下载源必须是有效 UTF-8 Python 文本") from exc
        return payload, content, hashlib.sha256(payload).hexdigest()

    def download_source_summary(self, filename: str) -> dict[str, Any]:
        payload, _content, digest = self._workspace_python_payload(filename)
        return {"filename": filename, "size": len(payload), "sha256": digest}

    def download(
        self, filename: str, *, run: bool = False, expected_sha256: str | None = None
    ) -> dict[str, Any]:
        self._require_file_tools()
        payload, content, source_sha = self._workspace_python_payload(filename)
        if expected_sha256 is not None and source_sha != expected_sha256:
            raise BridgeFailure(
                "授权确认后下载源文件发生变化；在发送桥命令前已拒绝操作，请重新发起并确认"
            )
        if run is not True and run is not False:
            raise BridgeFailure("run 必须是布尔值")
        with self._file_lock:
            self._require_connected()
            target = validate_board_path(self.entry)
            self._require_strict_capabilities()
            self._control_epoch += 1
            result = self.client.call(
                "download",
                {"content": content, "target": target, "run": run, "strictBackup": True},
            )
            written = self._check_write_response(result, expected=payload, require_compile=True)
            backup = self._check_strict_backup(result)
            if run and result.get("ran") is not True:
                raise BridgeFailure("桥未确认写入校验后运行目标程序")
        return self._tag_bridge({
            "ok": True,
            "sourceFile": filename,
            "sourceSha256": source_sha,
            "target": target,
            "compileOk": True,
            "ran": run,
            **written,
            **backup,
        })

    def pid_get(self) -> dict[str, Any]:
        self._require_file_tools()
        with self._file_lock:
            self._require_connected()
            target = validate_board_path(self.entry)
            self._require_strict_capabilities(raw_file_read=True)
            content, crc = self._raw_board_file(target)
            self._control_epoch += 1
        try:
            source = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise BridgeFailure("主程序不是有效 UTF-8，不能安全解析 PID") from exc
        values: dict[str, float] = {}
        missing: list[str] = []
        for name, pattern in PID_PATTERN.items():
            match = pattern.search(source)
            if not match:
                missing.append(name)
                continue
            value = float(match.group(2))
            if not math.isfinite(value):
                missing.append(name)
                continue
            values[name] = value
        return self._tag_bridge({
            "ok": True, "path": target, "size": len(content), "crc": crc,
            "values": values, "missing": missing,
        })

    def pid_set(
        self, updates: dict[str, float], *, run: bool = False
    ) -> dict[str, Any]:
        self._require_file_tools()
        if not updates or any(name not in PID_PATTERN for name in updates):
            raise BridgeFailure("请至少提供一个 Kp/Ki/Kd 数值")
        checked: dict[str, float] = {}
        for name, value in updates.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise BridgeFailure(f"{name} 必须是有限数值")
            try:
                numeric = float(value)
            except (OverflowError, ValueError) as exc:
                raise BridgeFailure(f"{name} 必须是有限数值") from exc
            if not math.isfinite(numeric):
                raise BridgeFailure(f"{name} 必须是有限数值")
            checked[name] = numeric
        with self._file_lock:
            self._require_connected()
            target = validate_board_path(self.entry)
            self._require_strict_capabilities(raw_file_read=True)
            original, old_crc = self._raw_board_file(target)
            self._control_epoch += 1
            try:
                source = original.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise BridgeFailure("主程序不是有效 UTF-8，已拒绝 PID 修改") from exc
            updated_source = source
            for name, value in checked.items():
                pattern = PID_PATTERN[name]
                if not pattern.search(updated_source):
                    raise BridgeFailure(f"主程序 {target} 中未找到 {name} 数值赋值行")
                rendered = repr(value)
                updated_source, count = pattern.subn(rf"\g<1>{rendered}", updated_source, count=1)
                if count != 1:
                    raise BridgeFailure(f"修改 {name} 失败；未发送写入命令")
            payload = updated_source.encode("utf-8")
            if len(payload) > MAX_MUTATION_BYTES:
                raise BridgeFailure("PID 修改后的主程序超过 1 MiB")
            self._require_strict_capabilities()
            self._control_epoch += 1
            result = self.client.call(
                "writefile",
                {
                    "path": target,
                    "content": updated_source,
                    "strictBackup": True,
                    "expectedOldCrc": old_crc,
                },
            )
            if result.get("path") != target:
                raise BridgeFailure("桥 PID 写入响应路径与目标不符")
            written = self._check_write_response(result, expected=payload)
            backup = self._check_strict_backup(result, require_existing=True)
            ran = False
            if run:
                run_result = self.client.call("run")
                if run_result.get("ran") is not True:
                    raise BridgeFailure("PID 已写入并校验，但桥未确认运行入口")
                ran = True
        return self._tag_bridge({
            "ok": True,
            "path": target,
            "applied": {name: value for name, value in checked.items()},
            "ran": ran,
            **written,
            **backup,
        })

    def workspaces(self, root: str) -> dict[str, Any]:
        try:
            return list_workspaces(root)
        except WorkspaceError as exc:
            return {"ok": False, "error": str(exc), "source": "local_workspace", "simulated": False}

    def workspace_info(self, path: str) -> dict[str, Any]:
        try:
            info = detect_workspace(path)
            return {**info, "source": "local_workspace", "simulated": False}
        except WorkspaceError as exc:
            return {"ok": False, "error": str(exc), "source": "local_workspace", "simulated": False}

    def policy_info(self) -> dict[str, Any]:
        if not self.client.simulated:
            return self._bridge_error(BridgeFailure("mock policy test tool cannot run in bridge mode"))
        policy = read_workbench_policy(self.client.workspace)
        return {
            "ok": True,
            **policy,
            "dataSource": "local_workspace",
            "simulated": False,
        }

    def _policy_error(self, message: str, policy: str) -> dict[str, Any]:
        return {
            "ok": False,
            "errorCode": "confirmation_required",
            "error": message,
            "policy": policy,
            "source": "local_policy",
            "simulated": self.client.simulated,
        }

    async def _authorization_context(self) -> tuple[dict[str, Any], dict[str, Any]]:
        current = await asyncio.to_thread(self.client.workspace_current)
        if current.get("workspacePath"):
            policy = await asyncio.to_thread(self.client.workspace_policy)
        else:
            policy = {"policy": DEFAULT_POLICY, "revision": "missing"}
        return current, policy

    async def _panel_confirmation(
        self, *, action: str, effect: str, target: str, impact: str,
        current: dict[str, Any], policy: dict[str, Any],
    ) -> dict[str, Any] | None:
        revision = policy.get("revision", "missing")
        material = {
            "action": action, "effect": effect, "target": target, "impact": impact,
            "workspacePath": current.get("workspacePath"),
            "profile": current.get("profile"), "entry": current.get("entry"),
            "control_epoch": current.get("control_epoch"), "policy_revision": revision,
        }
        encoded = json.dumps(
            material, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        try:
            pending = await asyncio.to_thread(
                self.client.confirmation_request,
                operation=action[:80], effect=effect, title=action[:256],
                target=target[:2048], impact=impact[:4096],
                workspacePath=current.get("workspacePath"),
                profile=current.get("profile"), entry=current.get("entry"),
                control_epoch=current.get("control_epoch"),
                policy_revision=revision, request_digest=digest, ttl_ms=120000,
            )
        except (BridgeFailure, OSError, ValueError) as exc:
            message = str(exc)
            error_code = (
                "busy" if "another confirmation" in message else
                "epoch_conflict" if "confirmation context changed" in message else
                "broker_unavailable"
            )
            return self._tag_bridge({
                "ok": False, "errorCode": error_code,
                "error": f"无法创建面板确认请求；未执行：{message[:240]}",
            })
        confirmation_id = pending.get("id")
        if not isinstance(confirmation_id, str):
            return self._tag_bridge({
                "ok": False, "errorCode": "confirmation_required",
                "error": "面板确认请求未能建立；未执行。",
            })

        deadline = asyncio.get_running_loop().time() + 120
        terminal_errors = {
            "rejected": ("confirmation_rejected", "面板用户拒绝了本次操作；未执行。"),
            "expired": ("confirmation_expired", "面板确认已超时；未执行。"),
            "stale": ("epoch_conflict", "等待确认期间工作区或策略上下文已变化；未执行。"),
            "cancelled": ("confirmation_expired", "面板确认请求已取消；未执行。"),
            "unknown": ("confirmation_expired", "确认请求已失效或 broker 已重启；未执行。"),
        }
        try:
            while asyncio.get_running_loop().time() < deadline:
                state = await asyncio.to_thread(self.client.confirmation_status, confirmation_id)
                status = state.get("state")
                if status == "approved":
                    consumed = await asyncio.to_thread(
                        self.client.confirmation_consume, confirmation_id, digest
                    )
                    if consumed.get("ok") is True and consumed.get("state") == "consumed":
                        return None
                    code, message = terminal_errors.get(
                        str(consumed.get("state")),
                        ("confirmation_expired", "确认已失效；未执行。"),
                    )
                    return self._tag_bridge({"ok": False, "errorCode": code, "error": message})
                if status in terminal_errors:
                    code, message = terminal_errors[status]
                    return self._tag_bridge({"ok": False, "errorCode": code, "error": message})
                if status != "pending":
                    return self._tag_bridge({
                        "ok": False, "errorCode": "confirmation_expired",
                        "error": "确认状态未知；未执行。",
                    })
                await asyncio.sleep(0.4)
            return self._tag_bridge({
                "ok": False, "errorCode": "confirmation_expired",
                "error": "等待面板用户确认超时；未执行。",
            })
        except (BridgeFailure, OSError) as exc:
            return self._tag_bridge({
                "ok": False, "errorCode": "broker_unavailable",
                "error": f"确认状态不可用；未执行：{str(exc)[:200]}",
            })
        finally:
            try:
                await asyncio.to_thread(self.client.confirmation_cancel, confirmation_id)
            except Exception:
                pass

    async def authorize(
        self,
        ctx: Context,
        *,
        action: str,
        effect: str,
        impact: str,
    ) -> dict[str, Any] | None:
        try:
            current, policy_state = await self._authorization_context()
        except (BridgeFailure, WorkspaceError) as exc:
            return self._tag_bridge({
                "ok": False, "errorCode": "broker_unavailable",
                "error": f"无法读取授权上下文；未执行：{str(exc)[:240]}",
            })
        policy = policy_state.get("policy", DEFAULT_POLICY)
        if not confirmation_required(policy, effect):
            return None
        if not self.client.simulated:
            return await self._panel_confirmation(
                action=action, effect=effect,
                target=str(current.get("workspacePath") or "当前设备"),
                impact=impact, current=current, policy=policy_state,
            )
        if not self.allow_mock_elicitation:
            return self._policy_error(
                "当前 mock 测试未启用 MCP elicitation；操作未执行。"
                "不要通过工具参数、confirmed、token 或 nonce 代替用户确认。",
                policy,
            )
        workspace = self.client.workspace or "(未指定工作区)"
        prompt = (
            f"确认 ESP32 操作：{action}\n"
            f"工作区：{workspace}\n机型：{current.get('profile')}\n影响：{impact}\n"
            "只有你明确同意本次所列操作时才选择 approve；否则 decline 或取消。"
        )
        try:
            result = await ctx.elicit(prompt, schema=OperationApproval)
        except Exception:
            LOGGER.warning("MCP elicitation unavailable; denied action=%s", action, exc_info=True)
            return self._policy_error("MCP 客户端未能提供用户确认；操作未执行。", policy)
        answer = getattr(result, "data", None)
        if getattr(result, "action", None) != "accept" or getattr(answer, "decision", None) != "approve":
            return self._policy_error("用户未批准本次操作；未执行。", policy)
        return None

    @staticmethod
    def _workspace_error_code(exc: Exception) -> str:
        message = str(exc).casefold()
        if isinstance(exc, WorkspaceError) and any(
            token in message for token in ("profile", "label", "entry")
        ):
            return "invalid_request"
        if "not recognized" in message or "claim it first" in message:
            return "workspace_not_recognized"
        if "not be overwritten" in message or "already exists" in message:
            return "workspace_exists"
        if "revision" in message or "policy changed" in message:
            return "revision_conflict"
        if "epoch" in message or "state changed" in message or "selection was pending" in message:
            return "epoch_conflict"
        if "busy" in message:
            return "busy"
        if "disconnect" in message or "connected" in message:
            return "disconnect_required"
        if "broker" in message or "transport" in message or "lease" in message:
            return "broker_unavailable"
        if isinstance(exc, WorkspaceError):
            return "invalid_path"
        if "path" in message or "directory" in message or "workspace root" in message:
            return "invalid_path"
        return "invalid_request"

    def workspace_api_error(self, exc: Exception) -> dict[str, Any]:
        message = str(exc)[:512] if isinstance(exc, (BridgeFailure, WorkspaceError)) else "工作区操作失败"
        return self._tag_bridge({
            "ok": False,
            "errorCode": self._workspace_error_code(exc),
            "error": message,
        })

    def explicit_workspace_error(self, error: str, *, policy: str = DEFAULT_POLICY) -> dict[str, Any]:
        return self._tag_bridge({
            "ok": False,
            "errorCode": "confirmation_required",
            "confirmationRequired": True,
            "error": error,
            "policy": policy,
        })

    async def authorize_explicit_workspace_change(
        self, ctx: Context, *, action: str, target: str, impact: str,
    ) -> dict[str, Any] | None:
        """Workspace and policy writes always require explicit approval, even under auto."""
        try:
            current, snapshot = await self._authorization_context()
            policy = snapshot.get("policy", DEFAULT_POLICY)
        except BridgeFailure as exc:
            if "active workspace is not set" in str(exc):
                policy = DEFAULT_POLICY
                try:
                    current = await asyncio.to_thread(self.client.workspace_current)
                    snapshot = {"policy": policy, "revision": "missing"}
                except BridgeFailure as current_exc:
                    return self.workspace_api_error(current_exc)
            else:
                return self.workspace_api_error(exc)
        except WorkspaceError as exc:
            return self.workspace_api_error(exc)
        if not self.client.simulated:
            return await self._panel_confirmation(
                action=action, effect="workspace", target=target, impact=impact,
                current=current, policy=snapshot,
            )
        if not self.allow_mock_elicitation:
            return self.explicit_workspace_error(
                "当前 MCP 会话未启用模拟确认；操作未执行。", policy=policy
            )
        prompt = (
            f"确认 ESP32 工作区配置操作：{action}\n"
            f"目标：{target}\n工作区策略：{policy}\n影响：{impact}\n"
            "只有你明确同意本次所列配置操作时才选择 approve；否则 decline 或取消。"
        )
        try:
            result = await ctx.elicit(prompt, schema=OperationApproval)
        except Exception:
            return self.explicit_workspace_error(
                "MCP 客户端未提供可用的模拟确认；操作未执行。", policy=policy
            )
        answer = getattr(result, "data", None)
        if getattr(result, "action", None) != "accept" or getattr(answer, "decision", None) != "approve":
            return self.explicit_workspace_error("本次配置操作未获批准；操作未执行。", policy=policy)
        return None

    def _require_mock_control(self) -> None:
        if not self.client.simulated:
            raise BridgeFailure("mock-only control tool refused: real bridge mode is never called")
        if self.client.mock_scenario != "control":
            raise BridgeFailure("mock-only control tool requires --mock-scenario control")

    def _require_control_entry(self, *, mock_only: bool) -> None:
        if mock_only:
            self._require_mock_control()
            return
        if not self.control_tools_enabled:
            raise BridgeFailure("设备控制工具默认关闭；需要显式 --enable-control-tools 配置")
        if self.client.simulated:
            if self.client.mock_scenario != "control":
                raise BridgeFailure("模拟设备控制工具需要 --mock-scenario control")
            return
        if not self.client.allow_real_controls:
            raise BridgeFailure("真实设备控制命令默认关闭；需要显式 --enable-control-tools 配置")

    def prepare_connect(self, requested_port: str, *, mock_only: bool = True) -> dict[str, Any]:
        self._require_control_entry(mock_only=mock_only)
        if not self.client.workspace:
            return {"ok": False, "error": "连接前必须通过 --workspace 指定工作区绝对路径"}
        with self._control_lock:
            status = self.client.call("status")
            current_port = str(status.get("port") or "")
            if status.get("connected") is True:
                if requested_port and requested_port != current_port:
                    return {
                        "ok": False,
                        "error": f"桥已连接 {current_port}；请先显式断开，再连接其他端口。",
                    }
                return {
                    "ok": True,
                    "alreadyConnected": True,
                    "status": status,
                    "epoch": getattr(self.client, "control_epoch", self._control_epoch),
                }
            port_data = self.client.call("ports")
            port_rows = port_data.get("ports", [])
            devices = [row.get("device") for row in port_rows if isinstance(row, dict)]
            selected = requested_port or (devices[0] if devices else "")
            if not selected:
                return {"ok": False, "error": "没有可用串口；未尝试连接"}
            if selected not in devices:
                return {"ok": False, "error": f"串口 {selected} 不在桥当前可用列表中；未尝试打开"}
            return {
                "ok": True,
                "port": selected,
                "profile": self.client.profile,
                "epoch": getattr(self.client, "control_epoch", self._control_epoch),
            }

    def prepare_connected_action(self, action: str, *, mock_only: bool = True) -> dict[str, Any]:
        self._require_control_entry(mock_only=mock_only)
        if not self.client.workspace:
            return {"ok": False, "error": f"{action} 前必须通过 --workspace 指定工作区绝对路径"}
        with self._control_lock:
            status = self.client.call("status")
            if status.get("connected") is not True:
                return {"ok": False, "error": f"{action} 需要先连接设备；当前桥未连接"}
            return {
                "ok": True,
                "status": status,
                "epoch": getattr(self.client, "control_epoch", self._control_epoch),
            }

    def execute_control(
        self,
        command: str,
        arguments: dict[str, Any] | None = None,
        *,
        expected_epoch: int | None = None,
        mock_only: bool = True,
    ) -> dict[str, Any]:
        self._require_control_entry(mock_only=mock_only)
        with self._control_lock:
            broker_control = getattr(self.client, "call_control", None)
            if broker_control is None and expected_epoch is not None and expected_epoch != self._control_epoch:
                raise BridgeFailure("设备控制状态在确认等待期间发生变化；请重新检查状态并发起操作")
            status = self.client.call("status")
            connected = status.get("connected") is True
            if command == "connect":
                if connected:
                    raise BridgeFailure("等待确认期间桥已连接；为避免覆盖现有串口连接，连接请求已取消")
                port_data = self.client.call("ports")
                devices = [
                    row.get("device")
                    for row in port_data.get("ports", [])
                    if isinstance(row, dict)
                ]
                if not arguments or arguments.get("port") not in devices:
                    raise BridgeFailure("等待确认期间串口列表发生变化；未尝试打开端口")
            elif command == "disconnect":
                if not connected:
                    return {"connected": False, "alreadyDisconnected": True}
            elif not connected:
                raise BridgeFailure(f"{command} 需要已连接的设备")
            self._control_epoch += 1
            if broker_control is not None:
                return broker_control(
                    command,
                    arguments,
                    expected_epoch=(
                        expected_epoch if expected_epoch is not None
                        else getattr(self.client, "control_epoch", 0)
                    ),
                )
            return self.client.call(command, arguments)

    def stop_result(self, data: dict[str, Any], *, operation: str) -> dict[str, Any]:
        if self.client.simulated and self.client.profile == "hiwonder":
            meaning = "模拟器只复现旧桥响应；真实 hiwonder 桥会尝试双电机零速，但没有实际停转确认。"
        elif self.client.simulated:
            meaning = "模拟器只复现旧桥响应；generic 停止仅中断程序，电机或舵机可能保持当前位置。"
        elif self.client.profile == "hiwonder":
            meaning = "真实桥已返回停止响应；旧桥尝试双电机零速，但没有实际停转确认。"
        else:
            meaning = "真实 generic 停止仅中断程序；电机或舵机可能保持当前位置。"
        return self._tag_bridge({
            "ok": True,
            "profile": self.client.profile,
            "operation": operation,
            "bridgeReportedStopped": data.get("stopped") is True,
            "bridgeReportedInterrupted": data.get("interrupted") is True,
            "physicalStopConfirmed": False,
            "stopMeaning": meaning,
        })

    def disconnect_result(self, data: dict[str, Any]) -> dict[str, Any]:
        if self.client.simulated and self.client.profile == "hiwonder":
            meaning = "仅模拟断开响应；真实 hiwonder 桥会尝试双电机零速，但没有实际停转确认。"
        elif self.client.simulated:
            meaning = "仅模拟断开响应；generic 桥只中断程序，电机或舵机可能保持当前位置。"
        elif self.client.profile == "hiwonder":
            meaning = "真实 hiwonder 桥在关闭串口前会尝试双电机零速，但没有实际停转确认。"
        else:
            meaning = "真实 generic 桥断开会调用 stop_motors；程序中断不代表电机或舵机停转。"
        return self._tag_bridge({
            "ok": True,
            "connected": data.get("connected") is True,
            "alreadyDisconnected": data.get("alreadyDisconnected") is True,
            "profile": self.client.profile,
            "physicalStopConfirmed": False,
            "stopMeaning": meaning,
        })

    def interrupt_result(self, data: dict[str, Any], *, operation: str) -> dict[str, Any]:
        return self._tag_bridge({
            "ok": True,
            "profile": self.client.profile,
            "operation": operation,
            "bridgeReportedInterrupted": data.get("interrupted") is True,
            "physicalStopConfirmed": False,
            "interruptMeaning": "桥返回了中断响应；此字段不确认电机或舵机已安全停下。",
        })

    def serial_ports(self) -> dict[str, Any]:
        try:
            call = getattr(self.client, "call_shared", self.client.call)
            return self._tag_bridge(call("ports"))
        except BridgeFailure as exc:
            return self._bridge_error(exc)

    def status(self) -> dict[str, Any]:
        try:
            call = getattr(self.client, "call_shared", self.client.call)
            return self._tag_bridge(call("status"))
        except BridgeFailure as exc:
            return self._bridge_error(exc)

    def console_read(self, since: int | None, max_chars: int) -> dict[str, Any]:
        try:
            self.client.start()
            snapshot = self.client.read_console(since=since, max_chars=max_chars)
            return self._tag_bridge(snapshot)
        except (BridgeFailure, ValueError) as exc:
            return self._bridge_error(exc)

    def board_files_list(self) -> dict[str, Any]:
        try:
            self._require_file_tools()
            with self._file_lock:
                status = self.client.call("status")
                if status.get("connected") is not True:
                    raise BridgeFailure("not connected")
                result = self.client.call("listfiles")
                if not self.client.simulated:
                    self._control_epoch += 1
                return self._tag_bridge({
                    **result,
                    **({"warning": "真实 bridge listfiles 通过 REPL 访问板载目录，可能影响当前程序运行。"} if not self.client.simulated else {}),
                })
        except BridgeFailure as exc:
            raise exc

    def board_file_read(self, path: str) -> dict[str, Any]:
        self._require_file_tools()
        try:
            safe_path = validate_board_path(path)
        except WorkspaceError as exc:
            raise BridgeFailure(str(exc)) from exc
        try:
            with self._file_lock:
                status = self.client.call("status")
                if status.get("connected") is not True:
                    raise BridgeFailure("not connected")
                result = self.client.call("readfile", {"path": safe_path})
                warning = (
                    "真实 bridge readfile 会中断板上程序并调用机型停止动作；该响应不证明电机实际停转。"
                    if not self.client.simulated else ""
                )
                if not self.client.simulated:
                    self._control_epoch += 1
                if safe_path.rsplit("/", 1)[-1].casefold() in SENSITIVE_FILE_NAMES:
                    withheld = {key: value for key, value in result.items() if key != "content"}
                    withheld.update({
                        "content": "敏感配置文件内容已隐藏。",
                        "redacted": True,
                        "redactedFields": ["content"],
                    })
                    result = withheld
                return self._tag_bridge({**result, **({"warning": warning} if warning else {})})
        except BridgeFailure:
            raise

    def close(self) -> None:
        self.client.close()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ESP32 MCP 服务；设备控制工具默认未注册，显式启用后仍受策略门禁"
    )
    parser.add_argument("--mode", choices=("mock", "bridge"), default="mock")
    parser.add_argument("--test-broker-seed", help=argparse.SUPPRESS)
    parser.add_argument("--workspace", help="当前工作区绝对路径；桥模式必填，不扫描其他路径")
    parser.add_argument("--profile", choices=("hiwonder", "generic"), help="未能从工作区识别时使用的机型")
    parser.add_argument("--bridge-script", help="真实 bridge.py 的绝对路径；仅 --mode bridge 使用")
    parser.add_argument(
        "--mock-scenario",
        choices=("readonly", "normal", "busy", "control", "fileops", "fileops-no-capability"),
        default="readonly",
        help="无硬件模拟桥场景；control/fileops 均只返回模拟数据，不操作设备",
    )
    parser.add_argument(
        "--allow-mock-elicitation",
        action="store_true",
        help="允许 mock-only 工具请求 MCP elicitation，仅用于客户端授权分支测试",
    )
    parser.add_argument(
        "--enable-control-tools",
        action="store_true",
        help=(
            "显式注册连接、断开、停止、运行、中断和 REPL 工具；bridge 模式同时开放对应桥命令，"
            "仍受工作区策略约束。mock 模式只用于 control 场景测试"
        ),
    )
    parser.add_argument(
        "--enable-write-tools",
        action="store_true",
        help=(
            "显式注册工作区下载、板载文件写删和 PID 修改工具；bridge 模式同时要求 "
            "--enable-control-tools，并先验证严格备份 capability"
        ),
    )
    args = parser.parse_args(argv)

    if args.workspace:
        try:
            args.workspace = str(absolute_directory(args.workspace))
        except WorkspaceError as exc:
            parser.error(str(exc))
    if args.mode == "bridge":
        if args.allow_mock_elicitation:
            parser.error("--allow-mock-elicitation 只适用于 --mode mock")
        if not args.workspace:
            parser.error("--mode bridge 必须同时提供 --workspace")
        if not args.bridge_script:
            parser.error("--mode bridge 必须同时提供 --bridge-script")
        if args.enable_write_tools and not args.enable_control_tools:
            parser.error("真实桥 --enable-write-tools 必须同时启用 --enable-control-tools")
        bridge_path = Path(args.bridge_script).expanduser()
        if not bridge_path.is_absolute() or not bridge_path.is_file():
            parser.error("--bridge-script 必须是存在的绝对路径")
        args.bridge_script = str(bridge_path.resolve())

    detected_info: dict[str, Any] | None = None
    if args.profile:
        profile = args.profile
    elif args.workspace:
        detected_info = detect_workspace(args.workspace)
        profile = detected_info["profile"] if detected_info["detected"] else "generic"
    else:
        profile = "generic"
    args.profile = profile
    if detected_info and detected_info.get("detected"):
        args.entry = detected_info["entry"]
    else:
        args.entry = "/corex.py" if profile == "hiwonder" else "/main.py"
    return args


def create_server(argv: list[str] | None = None) -> tuple[FastMCP, Esp32McpTools]:
    args = _parse_args(argv)
    local_client = BridgeClient(
        mode=args.mode,
        workspace=args.workspace,
        profile=args.profile,
        bridge_script=args.bridge_script,
        mock_script=str(DEFAULT_MOCK_SCRIPT),
        mock_scenario=args.mock_scenario,
        allow_real_controls=args.mode == "bridge" and args.enable_control_tools,
        allow_real_writes=args.mode == "bridge" and args.enable_write_tools,
    )
    test_identity = None
    test_seed = args.test_broker_seed or os.environ.get("ESP32_CODEX_TEST_ISOLATION_SEED", "")
    if test_seed:
        if len(test_seed) > 64 or not test_seed.isascii() or not test_seed.isalnum():
            raise ValueError("invalid test isolation seed")
        if args.mode == "bridge":
            script = Path(args.bridge_script).resolve(strict=True)
            temp_root = Path(tempfile.gettempdir()).resolve(strict=True)
            if (not script.is_relative_to(temp_root) or
                    script.name not in {"fake-bridge.py", "must-not-run.py", "bridge-that-must-not-run.py"}):
                raise ValueError("test isolation may only start a temporary canned bridge fixture")
        test_tuple = (test_seed, args.mode, args.workspace or "", args.profile,
                      args.mock_scenario, args.bridge_script or "",
                      str(args.enable_control_tools), str(args.enable_write_tools))
        test_identity = current_user_sid() + "-test-" + hashlib.sha256(
            "|".join(test_tuple).encode("utf-8")
        ).hexdigest()[:24]
    broker = BrokerClient(backend_config={
        "mode": local_client.mode,
        "workspace": local_client.workspace,
        "profile": local_client.profile,
        "bridge_script": local_client.bridge_script,
        "mock_script": local_client.mock_script,
        "mock_scenario": local_client.mock_scenario,
        "allow_real_controls": local_client.allow_real_controls,
        "allow_real_writes": local_client.allow_real_writes,
        "local_bridge_owner": False,
    }, _identity_sid_for_testing=test_identity)
    client = BrokerBridgeClient(local_client, broker)
    tools = Esp32McpTools(
        client,
        entry=args.entry,
        allow_mock_elicitation=args.allow_mock_elicitation,
        control_tools_enabled=args.enable_control_tools,
        write_tools_enabled=args.enable_write_tools,
    )
    atexit.register(tools.close)
    server_name = (
        "esp32-codex-write-enabled" if args.enable_write_tools else
        "esp32-codex-controls" if args.enable_control_tools else "esp32-codex-readonly"
    )
    server = FastMCP(server_name)

    @server.tool()
    def esp32_workspace_list(root: str) -> dict:
        """识别 root 目录本身及其直接子目录中的 ESP32 工作区。只读取本地文件；搜索有界，不遍历符号链接。"""
        return tools.workspaces(root)

    @server.tool()
    def esp32_workspace_info(path: str) -> dict:
        """检查一个工作区的机型、显示名、板载入口和根目录 Python 文件；不会写入 board.json。"""
        return tools.workspace_info(path)

    @server.tool()
    def esp32_workspace_current() -> dict:
        """查询 broker 中当前共享工作区；不会自动连接串口或改变配置。"""
        try:
            return {**tools.client.workspace_current(), "source": "broker_workspace",
                    "simulated": tools.client.simulated}
        except BridgeFailure as exc:
            return tools._bridge_error(exc)

    async def workspace_change_preflight(expected_epoch: int) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        if type(expected_epoch) is not int or expected_epoch < 0:
            return None, tools._tag_bridge({
                "ok": False, "errorCode": "invalid_request",
                "error": "expected_epoch 必须是非负整数；请先读取 esp32_workspace_current。",
            })
        try:
            await asyncio.to_thread(tools.client.workspace_current)
            status = await asyncio.to_thread(tools.client.call_shared, "status")
            current = await asyncio.to_thread(tools.client.workspace_current)
        except (BridgeFailure, WorkspaceError) as exc:
            return None, tools.workspace_api_error(exc)
        if current.get("control_epoch") != expected_epoch:
            return None, tools._tag_bridge({
                "ok": False, "errorCode": "epoch_conflict",
                "error": "共享工作区状态已变化；请重新读取快照后发起操作。",
                "current": current,
            })
        if status.get("busy") is True:
            return None, tools._tag_bridge({
                "ok": False, "errorCode": "busy", "error": "设备桥忙碌；未更改工作区。",
            })
        if status.get("connected") is not False:
            return None, tools._tag_bridge({
                "ok": False, "errorCode": "disconnect_required",
                "error": "请先显式断开当前设备，再更改工作区或策略。",
            })
        return current, None

    @server.tool()
    def esp32_policy_get() -> dict:
        """读取 broker 当前工作区的策略及 revision；只读，不连接串口。"""
        try:
            snapshot = tools.client.workspace_policy()
            return tools._tag_bridge({"ok": True, **snapshot, "dataSource": "broker_policy"})
        except (BridgeFailure, WorkspaceError) as exc:
            return tools.workspace_api_error(exc)

    @server.tool()
    def esp32_confirmation_status() -> dict:
        """查询当前 MCP 会话自己发起、仍等待面板决策的请求；不返回代码正文。"""
        try:
            rows = tools.client.agent_pending_confirmations()
            visible = [{
                key: row.get(key) for key in (
                    "id", "state", "action", "effect", "title", "target",
                    "workspacePath", "profile", "entry", "control_epoch",
                    "policy_revision", "decision_source", "expires_in_ms",
                )
            } for row in rows[:4] if isinstance(row, dict)]
            return tools._tag_bridge({"ok": True, "pending": visible})
        except BridgeFailure as exc:
            return tools._tag_bridge({
                "ok": False, "errorCode": "broker_unavailable",
                "error": f"确认状态暂不可用：{str(exc)[:200]}",
            })

    @server.tool()
    def esp32_panel_agent_decide(confirmation_id: str, decision: str) -> dict:
        """按用户已授权范围代办当前 MCP 会话自己的面板待办；decision 为 approve 或 reject。"""
        if not isinstance(confirmation_id, str) or not 20 <= len(confirmation_id) <= 128:
            return tools._tag_bridge({
                "ok": False, "errorCode": "invalid_request", "error": "确认 ID 无效。",
            })
        if decision not in {"approve", "reject"}:
            return tools._tag_bridge({
                "ok": False, "errorCode": "invalid_request", "error": "decision 必须为 approve 或 reject。",
            })
        try:
            result = tools.client.agent_decide_confirmation(confirmation_id, decision=decision)
            source = result.get("decision_source")
            return tools._tag_bridge({
                "ok": result.get("ok") is True,
                "state": result.get("state"),
                "decisionSource": source,
            })
        except BridgeFailure as exc:
            return tools._tag_bridge({
                "ok": False, "errorCode": "broker_unavailable",
                "error": f"无法处理面板待办；原操作尚未获准执行：{str(exc)[:200]}",
            })

    @server.tool()
    def esp32_confirmation_cancel(confirmation_id: str) -> dict:
        """取消当前 MCP 会话自己发起的待确认请求；取消只会阻止原操作执行。"""
        if not isinstance(confirmation_id, str) or not 20 <= len(confirmation_id) <= 128:
            return tools._tag_bridge({
                "ok": False, "errorCode": "invalid_request", "error": "确认 ID 无效。",
            })
        try:
            result = tools.client.confirmation_cancel(confirmation_id)
            return tools._tag_bridge({"ok": result.get("ok") is True, "state": result.get("state")})
        except BridgeFailure as exc:
            return tools._tag_bridge({
                "ok": False, "errorCode": "broker_unavailable",
                "error": f"无法取消确认；未执行：{str(exc)[:200]}",
            })

    @server.tool()
    async def esp32_workspace_select(ctx: Context, path: str, expected_epoch: int) -> dict:
        """选择已识别工作区；不自动连接。始终要求逐次明确确认，并以 expected_epoch 防止过期切换。"""
        try:
            target = await asyncio.to_thread(selectable_workspace, path)
        except (WorkspaceError, OSError) as exc:
            return tools.workspace_api_error(exc)
        current, error = await workspace_change_preflight(expected_epoch)
        if error:
            return error
        target_path = target["workspacePath"]
        denied = await tools.authorize_explicit_workspace_change(
            ctx,
            action="选择工作区",
            target=target_path,
            impact=f"把共享 ESP32 broker 切换到 {target['profile']}，入口为 {target['entry']}；不会自动连接串口。",
        )
        if denied:
            return denied
        _latest, error = await workspace_change_preflight(expected_epoch)
        if error:
            return error
        try:
            selected = await asyncio.to_thread(tools.client.workspace_select, target_path)
            return tools._tag_bridge({"ok": True, **selected, "dataSource": "broker_workspace"})
        except (BridgeFailure, WorkspaceError, OSError) as exc:
            return tools.workspace_api_error(exc)

    @server.tool()
    async def esp32_workspace_claim(
        ctx: Context, path: str, profile: str, expected_epoch: int,
        label: str | None = None, entry: str | None = None,
    ) -> dict:
        """原子认领无 board.json 的工作区；绝不覆盖既有配置，始终要求逐次明确确认。"""
        try:
            if profile not in PROFILE_META:
                raise WorkspaceError("profile must be hiwonder or generic")
            root = await asyncio.to_thread(absolute_directory, path)
            chosen_label = PROFILE_META[profile]["label"] if label is None else label
            if label is not None and (
                not isinstance(label, str) or not label.strip() or len(label) > 100
                or any(ord(ch) < 32 for ch in label)
            ):
                raise WorkspaceError("invalid workspace label")
            chosen_entry = PROFILE_META[profile]["entry"] if entry is None else entry
            validate_board_path(chosen_entry)
            if not chosen_entry.lower().endswith(".py"):
                raise WorkspaceError("workspace entry must be a Python file")
            config_dir = root / "esp32-ide"
            board_file = config_dir / "board.json"
            if config_dir.is_symlink():
                raise WorkspaceError("workspace configuration directory may not be a symlink")
            if board_file.exists() or board_file.is_symlink():
                raise WorkspaceError("existing board.json must not be overwritten")
        except (WorkspaceError, OSError, TypeError) as exc:
            return tools.workspace_api_error(exc)
        current, error = await workspace_change_preflight(expected_epoch)
        if error:
            return error
        denied = await tools.authorize_explicit_workspace_change(
            ctx,
            action="认领工作区",
            target=str(root),
            impact=f"原子创建 board.json，设置 profile={profile}、label={chosen_label!r}、entry={chosen_entry}；不会覆盖既有文件。",
        )
        if denied:
            return denied
        _latest, error = await workspace_change_preflight(expected_epoch)
        if error:
            return error
        try:
            claimed = await asyncio.to_thread(
                tools.client.workspace_claim, str(root), profile, label, chosen_entry
            )
            return tools._tag_bridge({"ok": True, **claimed, "dataSource": "broker_workspace"})
        except (BridgeFailure, WorkspaceError, OSError) as exc:
            return tools.workspace_api_error(exc)

    @server.tool()
    async def esp32_policy_set(
        ctx: Context, policy: str, expected_revision: str, expected_epoch: int,
    ) -> dict:
        """更新当前工作区策略；使用 revision/epoch 比较并交换，始终要求逐次明确确认。"""
        if policy not in POLICIES:
            return tools._tag_bridge({
                "ok": False, "errorCode": "invalid_request", "error": "未知策略。",
            })
        if (not isinstance(expected_revision, str)
                or (expected_revision != "missing" and
                    (len(expected_revision) != 64 or any(c not in "0123456789abcdef" for c in expected_revision.lower())))):
            return tools._tag_bridge({
                "ok": False, "errorCode": "invalid_request",
                "error": "expected_revision 必须来自 esp32_policy_get。",
            })
        current, error = await workspace_change_preflight(expected_epoch)
        if error:
            return error
        try:
            before = await asyncio.to_thread(tools.client.workspace_policy)
        except (BridgeFailure, WorkspaceError) as exc:
            return tools.workspace_api_error(exc)
        if before.get("revision") != expected_revision:
            return tools._tag_bridge({
                "ok": False, "errorCode": "revision_conflict",
                "error": "工作区策略已变化；请重新读取策略后发起操作。",
                "current": before,
            })
        denied = await tools.authorize_explicit_workspace_change(
            ctx,
            action="修改工作区策略",
            target=before.get("workspacePath", ""),
            impact=f"把策略从 {before['policy']} 更改为 {policy}；auto 可能减少后续提示。",
        )
        if denied:
            return denied
        _latest, error = await workspace_change_preflight(expected_epoch)
        if error:
            return error
        try:
            after = await asyncio.to_thread(tools.client.workspace_policy)
        except (BridgeFailure, WorkspaceError) as exc:
            return tools.workspace_api_error(exc)
        if after.get("revision") != expected_revision:
            return tools._tag_bridge({
                "ok": False, "errorCode": "revision_conflict",
                "error": "等待确认期间工作区策略已变化；未写入。",
                "current": after,
            })
        if after.get("control_epoch") != expected_epoch:
            return tools._tag_bridge({
                "ok": False, "errorCode": "epoch_conflict",
                "error": "等待确认期间共享工作区状态已变化；未写入。",
            })
        try:
            changed = await asyncio.to_thread(
                tools.client.workspace_policy_set, policy, expected_revision
            )
            return tools._tag_bridge({"ok": True, **changed, "dataSource": "broker_policy"})
        except (BridgeFailure, WorkspaceError, OSError) as exc:
            return tools.workspace_api_error(exc)

    @server.tool()
    def esp32_open_panel(ui: str = "web") -> dict:
        """请求打开 Windows 紧凑网页面板；真机桥随 MCP 配置共享，启动不连接串口。ui=tk 可打开 Tk 回退。"""
        if ui not in {"tk", "web"}:
            return tools._tag_bridge({"ok": False, "error": "ui 必须为 tk 或 web"})
        if args.test_broker_seed or os.environ.get("ESP32_CODEX_TEST_ISOLATION_SEED"):
            return tools._tag_bridge({"ok": False, "error": "isolated MCP test fixtures cannot launch desktop windows"})
        try:
            from panel.launcher import launch_panel_process
            workspace, profile = tools._current_panel_launch_context()
            return launch_panel_process(
                mode=tools.client.mode, workspace=workspace, ui=ui,
                profile=profile, mock_scenario=tools.client.mock_scenario,
                bridge_script=tools.client.bridge_script,
                allow_real_controls=tools.client.allow_real_controls,
                allow_real_writes=tools.client.allow_real_writes,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            return tools._tag_bridge({"ok": False, "error": f"面板启动请求失败：{str(exc)[:240]}"})

    @server.tool()
    def esp32_panel_status() -> dict:
        """查询本用户 ESP32 面板是否打开、前台或最小化；不启动 bridge。"""
        from panel.lifecycle import panel_window_status
        return {**panel_window_status(), "source": "local_panel_window", "simulated": False}

    @server.tool()
    def esp32_panel_control(action: str) -> dict:
        """控制本用户面板窗口：focus/minimize/restore/close；close 经正常窗口事件释放本面板租约。"""
        from panel.lifecycle import control_panel_window
        return {**control_panel_window(action), "source": "local_panel_window", "simulated": False}

    @server.tool()
    def esp32_capabilities() -> dict:
        """报告当前 MCP 工具组注册状态及运行时安全门槛；不启动桥或连接串口。"""
        return tools.capabilities()

    @server.tool()
    def esp32_snapshot(since: int | None = None, max_chars: int = 12000) -> dict:
        """读取带共享 epoch 的工作区、桥状态、端口和有界控制台快照；不会连接串口。"""
        return tools.snapshot(since, max_chars)

    @server.tool()
    def esp32_serial_ports() -> dict:
        """列出桥当前机型允许显示的串口。source 和 simulated 字段标明结果来自模拟桥还是真实桥。"""
        return tools.serial_ports()

    @server.tool()
    def esp32_status() -> dict:
        """读取桥的连接、端口、忙碌状态、板信息与桥工作区；不会打开串口。"""
        return tools.status()

    @server.tool()
    def esp32_console_read(since: int | None = None, max_chars: int = 12000) -> dict:
        """读取 MCP 桥已缓冲的控制台事件。since 可用于增量读取；max_chars 范围为 1–20000。"""
        return tools.console_read(since, max_chars)

    @server.tool()
    async def esp32_board_files_list(ctx: Context) -> dict:
        """列出板载文件。真实 bridge 使用 REPL 查询；调用可能影响程序运行，需 confirm-all 确认。"""
        denied = await tools.authorize(
            ctx,
            action="列出 ESP32 板载文件",
            effect="control",
            impact="通过串口 REPL 列出当前目录；桥状态不会因此关闭连接。",
        )
        if denied:
            return denied
        try:
            return await asyncio.to_thread(tools.board_files_list)
        except BridgeFailure as exc:
            return tools._bridge_error(exc)

    @server.tool()
    async def esp32_board_file_read(ctx: Context, path: str) -> dict:
        """读取板载文件。真实 bridge 会中断程序并调用机型停止动作；confirm-all 下需确认。"""
        denied = await tools.authorize(
            ctx,
            action=f"读取板载文件 {path}",
            effect="control",
            impact="真实旧桥 readfile 会中断正在运行的程序并调用 stop_motors；不确认实际电机停转。",
        )
        if denied:
            return denied
        try:
            return await asyncio.to_thread(tools.board_file_read, path)
        except BridgeFailure as exc:
            return tools._bridge_error(exc)

    if args.enable_write_tools:
        @server.tool()
        async def esp32_download(ctx: Context, filename: str, run: bool = False) -> dict:
            """将工作区根目录 .py 下载到当前机型入口；覆盖前保存并校验原始备份。"""
            try:
                summary = tools.download_source_summary(filename)
            except (BridgeFailure, WorkspaceError, OSError) as exc:
                return tools._bridge_error(exc)
            impact = (
                f"把工作区根目录文件 {summary['filename']!r} 写到板载 {tools.entry}，先严格备份现有目标，"
                f"再校验 size/CRC/语法；大小 {summary['size']} bytes，SHA-256 {summary['sha256']}。"
                + ("校验成功后还会运行入口。" if run else "")
            )
            denied = await tools.authorize(
                ctx, action=f"下载 {filename!r} 到 {tools.entry}", effect="write", impact=impact
            )
            if denied:
                return denied
            try:
                return await asyncio.to_thread(
                    tools.download,
                    filename,
                    run=run,
                    expected_sha256=summary["sha256"],
                )
            except (BridgeFailure, WorkspaceError, OSError) as exc:
                return tools._bridge_error(exc)

        @server.tool()
        async def esp32_board_file_write(ctx: Context, path: str, content: str) -> dict:
            """写入板载文本文件；严格路径会在覆盖前验证原始备份，备份失败则拒绝写入。"""
            try:
                path = validate_board_path(path)
            except WorkspaceError as exc:
                return tools._bridge_error(exc)
            if not isinstance(content, str) or not content:
                return tools._tag_bridge({"ok": False, "error": "内容不能为空"})
            raw = content.encode("utf-8")
            if len(raw) > MAX_MUTATION_BYTES:
                return tools._tag_bridge({"ok": False, "error": "板载文件内容超过 1 MiB 上限"})
            digest = hashlib.sha256(raw).hexdigest()
            denied = await tools.authorize(
                ctx,
                action=f"写入板载文件 {path}",
                effect="write",
                impact=f"写入 {len(raw)} bytes，SHA-256 {digest}；现存目标会先保存并验证备份。",
            )
            if denied:
                return denied
            try:
                return await asyncio.to_thread(tools.board_file_write, path, content)
            except (BridgeFailure, WorkspaceError) as exc:
                return tools._bridge_error(exc)

        @server.tool()
        async def esp32_board_file_delete(ctx: Context, path: str) -> dict:
            """删除板载文件；只在严格备份现存原始内容成功后删除。"""
            try:
                path = validate_board_path(path)
            except WorkspaceError as exc:
                return tools._bridge_error(exc)
            denied = await tools.authorize(
                ctx,
                action=f"删除板载文件 {path}",
                effect="write",
                impact="删除目标前会把原始 bytes 保存到工作区根目录并校验；目标不存在或备份失败时拒绝。",
            )
            if denied:
                return denied
            try:
                return await asyncio.to_thread(tools.board_file_delete, path)
            except (BridgeFailure, WorkspaceError) as exc:
                return tools._bridge_error(exc)

        @server.tool()
        async def esp32_pid(
            ctx: Context,
            action: str,
            kp: float | None = None,
            ki: float | None = None,
            kd: float | None = None,
            run: bool = False,
        ) -> dict:
            """读取主程序 Kp/Ki/Kd，或严格备份后修改指定值；run 仅在写入校验后生效。"""
            if action == "get":
                denied = await tools.authorize(
                    ctx,
                    action=f"读取 PID 参数 {tools.entry}",
                    effect="control",
                    impact="读取操作会走 bridge readfile，并可能中断程序、执行机型停止动作。",
                )
                if denied:
                    return denied
                try:
                    return await asyncio.to_thread(tools.pid_get)
                except (BridgeFailure, WorkspaceError) as exc:
                    return tools._bridge_error(exc)
            if action != "set":
                return tools._tag_bridge({"ok": False, "error": "action 必须是 get 或 set"})
            updates = {name: value for name, value in (("Kp", kp), ("Ki", ki), ("Kd", kd)) if value is not None}
            if not updates:
                return tools._tag_bridge({"ok": False, "error": "set 至少需要提供 kp、ki 或 kd"})
            for name, value in list(updates.items()):
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    return tools._tag_bridge({"ok": False, "error": f"{name} 必须是有限数值"})
                try:
                    numeric = float(value)
                except (OverflowError, ValueError):
                    return tools._tag_bridge({"ok": False, "error": f"{name} 必须是有限数值"})
                if not math.isfinite(numeric):
                    return tools._tag_bridge({"ok": False, "error": f"{name} 必须是有限数值"})
                updates[name] = numeric
            denied = await tools.authorize(
                ctx,
                action=f"修改 PID 参数 {tools.entry}",
                effect="write",
                impact=(
                    f"修改 {', '.join(f'{name}={value:g}' for name, value in updates.items())}；"
                    "写回前会复读目标并保存、校验原始备份。"
                    + ("校验成功后还会运行入口。" if run else "")
                ),
            )
            if denied:
                return denied
            try:
                return await asyncio.to_thread(tools.pid_set, updates, run=run)
            except (BridgeFailure, WorkspaceError) as exc:
                return tools._bridge_error(exc)

    @server.tool()
    def esp32_mock_policy_get() -> dict:
        """读取本地工作区策略；source 表示策略来源，dataSource=local_workspace，simulated=false。"""
        return tools.policy_info()

    @server.tool()
    async def esp32_mock_connect(ctx: Context, port: str = "") -> dict:
        """仅无硬件 control 模拟：选择 MOCK0 并返回连接状态，不访问真实端口。confirm-all 下通过 MCP elicitation 测试授权。"""
        try:
            plan = await asyncio.to_thread(tools.prepare_connect, port)
        except BridgeFailure as exc:
            return tools._bridge_error(exc)
        if not plan.get("ok"):
            return tools._tag_bridge(plan)
        if plan.get("alreadyConnected"):
            return tools._tag_bridge({**plan["status"], "ok": True, "alreadyConnected": True})
        selected_port = plan["port"]
        denied = await tools.authorize(
            ctx,
            action=f"模拟连接 {selected_port}",
            effect="control",
            impact="只更新模拟桥状态；不打开串口、不连接设备。",
        )
        if denied:
            return denied
        try:
            data = await asyncio.to_thread(
                tools.execute_control,
                "connect",
                {"port": selected_port},
                expected_epoch=plan["epoch"],
            )
        except BridgeFailure as exc:
            return tools._bridge_error(exc, port=selected_port)
        return tools._tag_bridge({**data, "mockAction": "connect", "profile": tools.client.profile})

    @server.tool()
    def esp32_mock_disconnect() -> dict:
        """仅无硬件 control 模拟：清除模拟连接状态；不操作真实串口或设备。"""
        try:
            data = tools.execute_control("disconnect")
        except BridgeFailure as exc:
            return tools._bridge_error(exc)
        return tools._tag_bridge({
            "ok": True,
            **data,
            "mockAction": "disconnect",
            "profile": tools.client.profile,
            "physicalStopConfirmed": False,
        })

    @server.tool()
    def esp32_mock_stop() -> dict:
        """仅无硬件 control 模拟：返回桥 stop 响应形状；不会停止电机或其他设备。"""
        try:
            plan = tools.prepare_connected_action("模拟停止")
            if not plan.get("ok"):
                return tools._tag_bridge(plan)
            data = tools.execute_control("stop", expected_epoch=plan["epoch"])
        except BridgeFailure as exc:
            return tools._bridge_error(exc)
        result = tools.stop_result(data, operation="mock_stop")
        return {**result, "mockAction": "stop", "simulated": True}

    @server.tool()
    async def esp32_mock_run(ctx: Context) -> dict:
        """仅无硬件 control 模拟：记录 run 响应形状，不重启设备；confirm-all 下测试 MCP elicitation。"""
        try:
            plan = await asyncio.to_thread(tools.prepare_connected_action, "模拟运行")
        except BridgeFailure as exc:
            return tools._bridge_error(exc)
        if not plan.get("ok"):
            return tools._tag_bridge(plan)
        denied = await tools.authorize(
            ctx,
            action="模拟 run",
            effect="control",
            impact=f"仅记录模拟请求；不会重启设备或运行 {tools.entry}。",
        )
        if denied:
            return denied
        try:
            data = await asyncio.to_thread(
                tools.execute_control, "run", expected_epoch=plan["epoch"]
            )
        except BridgeFailure as exc:
            return tools._bridge_error(exc)
        return tools._tag_bridge({
            **data,
            "mockAction": "run",
            "entry": tools.entry,
            "simulated": True,
        })

    @server.tool()
    async def esp32_mock_interrupt(ctx: Context) -> dict:
        """仅无硬件 control 模拟：记录 interrupt 响应形状，不中断设备。confirm-all 下测试 MCP elicitation。"""
        try:
            plan = await asyncio.to_thread(tools.prepare_connected_action, "模拟中断")
        except BridgeFailure as exc:
            return tools._bridge_error(exc)
        if not plan.get("ok"):
            return tools._tag_bridge(plan)
        denied = await tools.authorize(
            ctx,
            action="模拟 interrupt",
            effect="control",
            impact="仅记录模拟请求；不会向设备发送 Ctrl-C。",
        )
        if denied:
            return denied
        try:
            data = await asyncio.to_thread(
                tools.execute_control, "interrupt", expected_epoch=plan["epoch"]
            )
        except BridgeFailure as exc:
            return tools._bridge_error(exc)
        result = tools.interrupt_result(data, operation="mock_interrupt")
        return {**result, "mockAction": "interrupt", "simulated": True}

    @server.tool()
    async def esp32_mock_repl_send(ctx: Context, line: str) -> dict:
        """仅无硬件 control 模拟：校验并记录一行代码但不执行；confirm-write/confirm-all 下测试 MCP elicitation。"""
        if not isinstance(line, str) or not line.strip():
            return tools._tag_bridge({"ok": False, "error": "模拟 REPL 行不能为空"})
        if len(line) > 512 or any(ord(char) < 32 or ord(char) == 127 for char in line):
            return tools._tag_bridge({
                "ok": False,
                "error": "模拟 REPL 只接受不超过 512 字符且不含控制字符的单行文本",
            })
        try:
            plan = await asyncio.to_thread(tools.prepare_connected_action, "模拟 REPL")
        except BridgeFailure as exc:
            return tools._bridge_error(exc)
        if not plan.get("ok"):
            return tools._tag_bridge(plan)
        denied = await tools.authorize(
            ctx,
            action="模拟执行一行 REPL 代码",
            effect="write",
            impact=f"只记录以下代码文本，不执行；生产 REPL 可写文件或控制硬件：\n{line}",
        )
        if denied:
            return denied
        try:
            data = await asyncio.to_thread(
                tools.execute_control,
                "send",
                {"line": line},
                expected_epoch=plan["epoch"],
            )
        except BridgeFailure as exc:
            return tools._bridge_error(exc)
        return tools._tag_bridge({
            "ok": data.get("sent") is True,
            "sent": data.get("sent") is True,
            "mockAction": "repl_send",
            "executed": False,
            "simulated": True,
        })

    if args.enable_control_tools:
        @server.tool()
        async def esp32_connect(ctx: Context, port: str = "") -> dict:
            """仅在显式启用控制工具后注册。连接指定端口；真实模式仍按工作区策略执行。"""
            try:
                plan = await asyncio.to_thread(tools.prepare_connect, port, mock_only=False)
            except BridgeFailure as exc:
                return tools._bridge_error(exc, port=port)
            if not plan.get("ok"):
                return tools._tag_bridge(plan)
            if plan.get("alreadyConnected"):
                return tools._tag_bridge({**plan["status"], "ok": True, "alreadyConnected": True})
            selected_port = plan["port"]
            denied = await tools.authorize(
                ctx,
                action=f"连接串口 {selected_port}",
                effect="control",
                impact=f"打开 {selected_port} 并连接当前 ESP32 工作区配置的机型。",
            )
            if denied:
                return denied
            try:
                data = await asyncio.to_thread(
                    tools.execute_control,
                    "connect",
                    {"port": selected_port},
                    expected_epoch=plan["epoch"],
                    mock_only=False,
                )
            except BridgeFailure as exc:
                return tools._bridge_error(exc, port=selected_port)
            return tools._tag_bridge({**data, "profile": tools.client.profile})

        @server.tool()
        def esp32_disconnect() -> dict:
            """关闭此 MCP 桥打开的串口；不会管理或断开 DSH 等其他进程的连接。"""
            try:
                data = tools.execute_control("disconnect", mock_only=False)
            except BridgeFailure as exc:
                return tools._bridge_error(exc)
            return tools.disconnect_result(data)

        @server.tool()
        def esp32_stop() -> dict:
            """执行机型对应的桥停止动作；generic 只中断程序，结果不证明电机已安全停转。"""
            try:
                plan = tools.prepare_connected_action("停止", mock_only=False)
                if not plan.get("ok"):
                    return tools._tag_bridge(plan)
                data = tools.execute_control(
                    "stop", expected_epoch=plan["epoch"], mock_only=False
                )
            except BridgeFailure as exc:
                return tools._bridge_error(exc)
            return tools.stop_result(data, operation="stop")

        @server.tool()
        async def esp32_run(ctx: Context) -> dict:
            """软重启板载 MicroPython 程序；confirm-all 下需经服务端确认门禁。"""
            try:
                plan = await asyncio.to_thread(
                    tools.prepare_connected_action, "运行", mock_only=False
                )
            except BridgeFailure as exc:
                return tools._bridge_error(exc)
            if not plan.get("ok"):
                return tools._tag_bridge(plan)
            denied = await tools.authorize(
                ctx,
                action=f"运行 {tools.entry}",
                effect="control",
                impact=f"软重启 MicroPython 板并运行入口 {tools.entry}。",
            )
            if denied:
                return denied
            try:
                data = await asyncio.to_thread(
                    tools.execute_control,
                    "run",
                    expected_epoch=plan["epoch"],
                    mock_only=False,
                )
            except BridgeFailure as exc:
                return tools._bridge_error(exc)
            return tools._tag_bridge({**data, "entry": tools.entry})

        @server.tool()
        async def esp32_interrupt(ctx: Context) -> dict:
            """向 REPL 发送中断并执行桥机型停止动作；confirm-all 下需经服务端确认门禁。"""
            try:
                plan = await asyncio.to_thread(
                    tools.prepare_connected_action, "中断", mock_only=False
                )
            except BridgeFailure as exc:
                return tools._bridge_error(exc)
            if not plan.get("ok"):
                return tools._tag_bridge(plan)
            denied = await tools.authorize(
                ctx,
                action="中断板上运行程序",
                effect="control",
                impact="向 REPL 发送 Ctrl-C 并执行 bridge.py 当前机型的 stop_motors；不提供实际运动状态确认。",
            )
            if denied:
                return denied
            try:
                data = await asyncio.to_thread(
                    tools.execute_control,
                    "interrupt",
                    expected_epoch=plan["epoch"],
                    mock_only=False,
                )
            except BridgeFailure as exc:
                return tools._bridge_error(exc)
            return tools.interrupt_result(data, operation="interrupt")

        @server.tool()
        async def esp32_repl_send(ctx: Context, line: str) -> dict:
            """提交一行 MicroPython REPL 文本；代码可改写文件或控制硬件，按写操作策略门禁。"""
            if not isinstance(line, str) or not line.strip():
                return tools._tag_bridge({"ok": False, "error": "REPL 行不能为空"})
            if len(line) > 512 or any(ord(char) < 32 or ord(char) == 127 for char in line):
                return tools._tag_bridge({
                    "ok": False,
                    "error": "REPL 只接受不超过 512 字符且不含控制字符的单行文本",
                })
            try:
                plan = await asyncio.to_thread(
                    tools.prepare_connected_action, "REPL", mock_only=False
                )
            except BridgeFailure as exc:
                return tools._bridge_error(exc)
            if not plan.get("ok"):
                return tools._tag_bridge(plan)
            denied = await tools.authorize(
                ctx,
                action="发送一行 MicroPython REPL 代码",
                effect="write",
                impact=f"向设备 REPL 发送以下代码行；可能写文件或控制硬件：\n{line}",
            )
            if denied:
                return denied
            try:
                data = await asyncio.to_thread(
                    tools.execute_control,
                    "send",
                    {"line": line},
                    expected_epoch=plan["epoch"],
                    mock_only=False,
                )
            except BridgeFailure as exc:
                return tools._bridge_error(exc)
            return tools._tag_bridge({
                "ok": data.get("sent") is True,
                "sent": data.get("sent") is True,
                "profile": tools.client.profile,
                "bridgeReportedSent": data.get("sent") is True,
                "warning": "REPL 输入已发送；代码可能修改文件或控制硬件。",
            })

    return server, tools


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    server, _tools = create_server()
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
