"""JSON-lines bridge process adapter for read-only and gated control tools.

The mock backend supports canned read-only data and explicitly simulated
control actions. Real bridge mode permits ports/status by default; its legacy
file commands can interrupt a running board program and invoke the profile stop
action. Control commands require an explicit server option and policy gates.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import sys
import threading
from collections import deque
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger("esp32_codex_mcp.bridge")
PHASE2_MOCK_COMMANDS = {"ports", "status", "listfiles", "readfile"}
MOCK_ONLY_CONTROL_COMMANDS = {"connect", "disconnect", "stop", "run", "interrupt", "send"}
REAL_CONTROL_COMMANDS = {"connect", "disconnect", "stop", "run", "interrupt", "send"}
REAL_BRIDGE_SAFE_COMMANDS = {"ports", "status"}
FILE_COMMANDS = {"capabilities", "download", "writefile", "deletefile", "listfiles", "readfile"}
COMMAND_TIMEOUTS = {
    "ports": 8.0,
    "status": 15.0,
    "connect": 15.0,
    "disconnect": 15.0,
    "stop": 15.0,
    "run": 12.0,
    "interrupt": 15.0,
    "send": 8.0,
    "capabilities": 8.0,
    "download": 120.0,
    "listfiles": 20.0,
    "readfile": 60.0,
    "writefile": 120.0,
    "deletefile": 15.0,
}
CONSOLE_MAX_EVENTS = 4000
CONSOLE_MAX_CHARS = 200000


class BridgeFailure(RuntimeError):
    """The bridge process or a bridge command failed."""


OUTCOME_UNKNOWN_MARKERS = (
    "timeout", "timed out", "超时", "transport failed", "lease was released",
    "process exit", "process exited", "桥进程退出", "桥进程已退出",
    "response is invalid", "响应缺少对象 data", "returned an invalid result",
)


def is_outcome_unknown(error: Exception) -> bool:
    """Whether a failed command may have reached its consumer without a confirmed reply."""
    folded = str(error).casefold()
    return any(marker in folded for marker in OUTCOME_UNKNOWN_MARKERS)


class BridgeClient:
    def __init__(
        self,
        *,
        mode: str,
        workspace: str | None,
        profile: str,
        bridge_script: str | None,
        mock_script: str,
        mock_scenario: str = "readonly",
        allow_real_controls: bool = False,
        allow_real_writes: bool = False,
    ) -> None:
        if mode not in {"mock", "bridge"}:
            raise ValueError("bridge mode must be 'mock' or 'bridge'")
        if profile not in {"hiwonder", "generic"}:
            raise ValueError("profile must be 'hiwonder' or 'generic'")
        self.mode = mode
        self.workspace = workspace
        self.profile = profile
        self.bridge_script = bridge_script
        self.mock_script = str(Path(mock_script).resolve())
        self.mock_scenario = mock_scenario
        self.allow_real_controls = allow_real_controls
        self.allow_real_writes = allow_real_writes
        self._process: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        self._write_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._pending: dict[int, queue.Queue[dict[str, Any]]] = {}
        self._next_id = 0
        self._console_seq = 0
        self._console: deque[tuple[int, str]] = deque()
        self._console_chars = 0
        self._closed = False
        self._start_error: str | None = None

    @property
    def source(self) -> str:
        return "mock_bridge" if self.mode == "mock" else "bridge_process"

    @property
    def simulated(self) -> bool:
        return self.mode == "mock"

    def start(self) -> None:
        with self._state_lock:
            if self._closed:
                raise BridgeFailure("MCP 服务正在关闭，桥进程不可用")
            if self._start_error:
                raise BridgeFailure(self._start_error)
            process = self._process
            if process is not None:
                code = process.poll()
                if code is None:
                    return
                raise BridgeFailure(f"桥进程已退出，exit code={code}")
            if self.mode == "bridge":
                script = self._validated_bridge_script()
                if not self.workspace:
                    raise BridgeFailure("真实桥模式需要通过 --workspace 指定工作区绝对路径")
                workspace_path = Path(self.workspace)
                if not workspace_path.is_absolute() or not workspace_path.is_dir():
                    raise BridgeFailure("--workspace 必须是存在的工作区绝对路径")
                argv = [
                    sys.executable,
                    "-X",
                    "utf8",
                    "-u",
                    script,
                    "--workspace",
                    str(workspace_path.resolve()),
                    "--profile",
                    self.profile,
                ]
                cwd = str(workspace_path.resolve())
            else:
                mock_path = Path(self.mock_script)
                if not mock_path.is_file():
                    raise BridgeFailure(f"找不到无硬件模拟桥: {mock_path}")
                argv = [
                    sys.executable,
                    "-X",
                    "utf8",
                    "-u",
                    str(mock_path),
                    "--scenario",
                    self.mock_scenario,
                    "--profile",
                    self.profile,
                    "--workspace",
                    self.workspace or str(Path(__file__).resolve().parents[1]),
                ]
                cwd = self.workspace or str(Path(__file__).resolve().parents[1])

            try:
                process = subprocess.Popen(
                    argv,
                    cwd=cwd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    encoding="utf-8",
                    errors="strict",
                    bufsize=1,
                    close_fds=True,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            except (OSError, ValueError) as exc:
                self._start_error = f"无法启动桥进程: {exc}"
                raise BridgeFailure(self._start_error) from exc
            self._process = process
            self._reader = threading.Thread(
                target=self._read_stdout,
                args=(process,),
                name="esp32-bridge-reader",
                daemon=True,
            )
            self._reader.start()

    def _validated_bridge_script(self) -> str:
        if not self.bridge_script:
            raise BridgeFailure("真实桥模式需要通过 --bridge-script 指定 bridge.py")
        script = Path(self.bridge_script)
        if not script.is_absolute() or not script.is_file():
            raise BridgeFailure("--bridge-script 必须是存在的绝对路径")
        return str(script.resolve())

    def call(
        self,
        command: str,
        arguments: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        mock_commands = PHASE2_MOCK_COMMANDS | MOCK_ONLY_CONTROL_COMMANDS | FILE_COMMANDS
        if command not in mock_commands:
            raise BridgeFailure(f"桥适配器拒绝范围外命令: {command}")
        if self.mode == "bridge" and command in FILE_COMMANDS and not self.allow_real_controls:
            raise BridgeFailure(
                "真实桥写入工具要求同时显式启用 --enable-control-tools 和 --enable-write-tools"
            )
        real_allowed_commands = REAL_BRIDGE_SAFE_COMMANDS
        if self.allow_real_controls:
            real_allowed_commands = real_allowed_commands | REAL_CONTROL_COMMANDS
        if self.allow_real_writes:
            real_allowed_commands = real_allowed_commands | FILE_COMMANDS
        if self.mode == "bridge" and command not in real_allowed_commands:
            gate = "--enable-write-tools" if command in FILE_COMMANDS else "--enable-control-tools"
            raise BridgeFailure(
                f"真实桥模式拒绝 {command}：需要显式 {gate} 配置"
            )
        self.start()
        process = self._process
        if process is None or process.stdin is None:
            raise BridgeFailure("桥进程未就绪")
        with self._state_lock:
            self._next_id += 1
            request_id = self._next_id
            response_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
            self._pending[request_id] = response_queue

        payload: dict[str, Any] = {"id": request_id, "cmd": command}
        if arguments:
            payload.update(arguments)
        try:
            with self._write_lock:
                if process.poll() is not None:
                    raise BridgeFailure(f"桥进程已退出，exit code={process.returncode}")
                process.stdin.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
                process.stdin.flush()
            wait_seconds = COMMAND_TIMEOUTS[command] if timeout is None else timeout
            try:
                message = response_queue.get(timeout=wait_seconds)
            except queue.Empty as exc:
                self._stop_owned_process()
                raise BridgeFailure(f"桥命令 {command} 等待 {wait_seconds:g} 秒超时；已停止本 MCP 桥进程") from exc
        except (BrokenPipeError, OSError) as exc:
            raise BridgeFailure(f"向桥发送 {command} 命令失败: {exc}") from exc
        finally:
            with self._state_lock:
                self._pending.pop(request_id, None)

        if message.get("_process_exit"):
            raise BridgeFailure(f"桥进程退出，exit code={message.get('returncode')}")
        if message.get("ok") is not True:
            raise BridgeFailure(str(message.get("error") or f"桥命令 {command} 失败"))
        data = message.get("data")
        if not isinstance(data, dict):
            raise BridgeFailure(f"桥命令 {command} 响应缺少对象 data")
        return data

    def read_console(self, *, since: int | None, max_chars: int) -> dict[str, Any]:
        if not 1 <= max_chars <= 20000:
            raise ValueError("max_chars 必须在 1 到 20000 之间")
        with self._state_lock:
            chunks = list(self._console)
            cursor = self._console_seq
        if since is None:
            text = "".join(chunk for _, chunk in chunks)[-max_chars:]
            dropped = False
        else:
            if since < 0:
                raise ValueError("since 必须是非负整数")
            dropped = bool(chunks and since < chunks[0][0] - 1)
            text = "".join(chunk for seq, chunk in chunks if seq > since)[-max_chars:]
        return {"text": text, "cursor": cursor, "dropped": dropped}

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            process = self._process
        if process is None:
            return
        if process.poll() is None:
            try:
                if process.stdin is not None:
                    process.stdin.close()
                process.wait(timeout=2.0)
            except (OSError, subprocess.TimeoutExpired):
                self._stop_owned_process(process)
        reader = self._reader
        if reader is not None and reader.is_alive():
            reader.join(timeout=0.25)

    def _stop_owned_process(self, process: subprocess.Popen[str] | None = None) -> None:
        target = process or self._process
        if target is None or target.poll() is not None:
            return
        try:
            target.terminate()
            target.wait(timeout=2.0)
        except (OSError, subprocess.TimeoutExpired):
            try:
                target.kill()
                target.wait(timeout=2.0)
            except (OSError, subprocess.TimeoutExpired):
                LOGGER.error("无法停止本 MCP 自己启动的桥进程")

    def _read_stdout(self, process: subprocess.Popen[str]) -> None:
        stream = process.stdout
        if stream is not None:
            try:
                for line in stream:
                    try:
                        message = json.loads(line)
                    except (json.JSONDecodeError, TypeError):
                        LOGGER.warning("桥 stdout 中出现无效 JSON 行；已忽略")
                        continue
                    if not isinstance(message, dict):
                        continue
                    if message.get("event") == "console":
                        text = message.get("text")
                        if isinstance(text, str):
                            with self._state_lock:
                                self._console_seq += 1
                                self._console.append((self._console_seq, text))
                                self._console_chars += len(text)
                                while self._console and (
                                    len(self._console) > CONSOLE_MAX_EVENTS
                                    or self._console_chars > CONSOLE_MAX_CHARS
                                ):
                                    _, dropped_text = self._console.popleft()
                                    self._console_chars -= len(dropped_text)
                        continue
                    if message.get("event") == "log":
                        continue
                    response_id = message.get("id")
                    if isinstance(response_id, int):
                        with self._state_lock:
                            response_queue = self._pending.get(response_id)
                        if response_queue is not None:
                            try:
                                response_queue.put_nowait(message)
                            except queue.Full:
                                pass
            except (OSError, UnicodeError):
                LOGGER.exception("读取桥 stdout 失败")
        return_code = process.poll()
        with self._state_lock:
            pending = list(self._pending.values())
        for response_queue in pending:
            try:
                response_queue.put_nowait({"_process_exit": True, "returncode": return_code})
            except queue.Full:
                pass


def explain_bridge_error(error: str, *, port: str = "") -> str:
    message = str(error).strip() or "桥命令失败"
    folded = message.casefold()
    busy_markers = (
        "access is denied",
        "permission denied",
        "device or resource busy",
        "could not open port",
        "cannot open port",
        "resource busy",
        "port is busy",
        "port busy",
        "拒绝访问",
        "正在使用",
        "被占用",
    )
    not_connected_markers = ("not connected", "未连接", "no serial", "没有打开的串口")
    port_text = f" {port}" if port else ""
    if any(marker in folded for marker in busy_markers):
        return (
            f"串口{port_text}当前不可用，可能已由 DSH 占用。请先在 DSH 中手动断开，再重试；"
            f"Codex 不会结束或断开其他进程。原始错误: {message}"
        )
    if any(marker in folded for marker in not_connected_markers):
        return (
            "此 MCP 桥当前未连接设备。若串口正由 DSH 使用，请在 DSH 中手动断开；"
            f"Codex 不会结束或断开其他进程。原始错误: {message}"
        )
    return message
