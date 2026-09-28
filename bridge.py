#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DSH 幻尔 ESP32 工作台 - Python 串口桥

由 DSH 插件 (Host) 以子进程方式启动:
    python -X utf8 -u bridge.py --workspace <工作区路径>

协议 (stdin/stdout, 每行一个 JSON, UTF-8):
    Host -> 桥:  {"id": N, "cmd": "...", ...参数}
    桥 -> Host:  {"id": N, "ok": true, "data": {...}}          命令响应
                 {"event": "console", "text": "..."}           控制台输出(随时推送)
                 {"event": "log", "text": "..."}               桥自身日志

命令:
    ports                 列出 CH340 串口
    connect  {port}       打开串口, 打断运行中的程序, 停电机, 探测板子信息
    disconnect            停电机并关闭串口
    status                当前状态
    stop                  停车 (幻尔: 电机停转; 其他板: 打断程序, 舵机保持姿态)
    run                   软重启板子 (machine.reset, 固件自动跑主程序 /corex.py 或 /main.py)
    interrupt             Ctrl-C 打断程序并自动停电机
    send     {line}       向 REPL 发送一行 (回显与输出走控制台事件)
    download {content, run, target}  停电机 -> 备份板载 target -> 分段写入 -> CRC32+语法复核 -> (可选)重启运行
    listfiles             列出板载根目录文件 (名称+大小, json 单行)
    readfile {path}       读板载文件 (分段十六进制回读, size+CRC 隐式校验)
    writefile {path, content}  分段写入任意板载文件 (打断程序+停电机, size+CRC32 校验)
    deletefile {path}     删除板载文件 (os.remove)

板载路径规则: 必须以 / 开头, 禁止 .. 引号反斜杠 (写入主程序请走 download, 它带备份)。

写入链路与 写入ESP32.py 相同: 普通 REPL 单行命令 + 回显校验 + 分段传输, 不用 raw REPL。

机型 (--profile):
    hiwonder  幻尔小车: 停车 = 打断 + EncoderMotor 停转命令; 主程序 /corex.py
    generic   通用 ESP32 MicroPython 板 (四足等预设均属此型): 停车 = 打断程序,
              电机/舵机保持当前状态; 主程序默认 /main.py (MicroPython 标准自启,
              可由 host 经 download.target 指定); 串口不再限定 CH340
"""
import argparse
import base64
import binascii
import hashlib
import json
import os
import sys
import threading
import time
import zlib
from collections import deque

import serial
from serial.tools import list_ports

CH340_VID = 0x1A86
TARGET = "/corex.py"
CODEX_MAX_FILE_BYTES = 1024 * 1024
CODEX_CAPABILITIES = {
    "codexStrictBackup": 1,
    "codexRawFileRead": 1,
}

_io_lock = threading.Lock()


def out(obj):
    line = json.dumps(obj, ensure_ascii=False)
    with _io_lock:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


def log(text):
    out({"event": "log", "text": text})


# ---------------- 控制台缓冲 (备查) ----------------
_console = deque()
_console_len = 0
_console_lock = threading.Lock()
CONSOLE_CAP = 120_000


def console_write(text):
    global _console_len
    if not text:
        return
    with _console_lock:
        _console.append(text)
        _console_len += len(text)
        while _console_len > CONSOLE_CAP and _console:
            _console_len -= len(_console.popleft())
    out({"event": "console", "text": text})


# ---------------- 板子 (REPL 会话) ----------------
class Board:
    def __init__(self, workspace, profile="hiwonder"):
        self.workspace = workspace
        self.profile = profile if profile in ("hiwonder", "generic") else "hiwonder"
        self.ser = None
        self.port = ""
        self.board_info = ""
        self.lock = threading.RLock()
        self._deadline = None
        self._reader = None
        self._reader_stop = False
        self._decoder = None

    # ---- 串口基础 ----
    def open(self, port):
        self.close_quiet()
        self.ser = serial.Serial(port, 115200, timeout=0.05)
        self.port = port
        self._reader_stop = False
        self._decoder = None
        self._reader = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader.start()
        time.sleep(0.5)

    def close_quiet(self):
        self._deadline = time.time() + 6.0
        try:
            self.stop_motors(mute=True)
        except Exception:
            pass
        self._deadline = None
        self._reader_stop = True
        if self._reader:
            self._reader.join(timeout=1.0)
            self._reader = None
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None
        self.port = ""
        self.board_info = ""

    def _reader_loop(self):
        pending = b""
        while not self._reader_stop:
            try:
                with self.lock:
                    if self.ser is None:
                        break
                    chunk = self.ser.read(512)
                if not chunk:
                    time.sleep(0.02)
                    continue
                data = pending + chunk
                try:
                    text = data.decode("utf-8")
                    pending = b""
                except UnicodeDecodeError:
                    # 找到最长可解码前缀, 余下字节留到下一块
                    cut = 0
                    for i in range(len(data), 0, -1):
                        try:
                            text = data[:i].decode("utf-8")
                            cut = i
                            break
                        except UnicodeDecodeError:
                            continue
                    else:
                        text = ""
                    pending = data[cut:]
                console_write(text)
            except Exception as e:
                console_write("\n[桥] 串口读取异常: %r\n" % (e,))
                break

    def _raw_read(self, wait):
        out = b""
        t0 = time.time()
        while time.time() - t0 < wait:
            if self.ser is None:
                break
            chunk = self.ser.read(4096)
            if chunk:
                out += chunk
                t0 = time.time()
        return out

    def _emit(self, data, mute):
        text = data.decode("utf-8", "replace")
        if mute:
            return ""
        console_write(text)
        return text

    # ---- REPL 命令 ----
    def cmd(self, line, timeout=8.0, mute=False, attempts=2):
        """执行一行 REPL 命令, 回显校验, 返回输出文本。须持有 lock。"""
        for _attempt in range(attempts):
            if self._deadline and time.time() > self._deadline:
                raise RuntimeError("板子无响应, 超出时间预算")
            self.ser.reset_input_buffer()
            self.ser.write(line.encode("utf-8") + b"\r\n")
            got = b""
            t0 = time.time()
            while time.time() - t0 < timeout:
                chunk = self.ser.read(2048)
                if chunk:
                    got += chunk
                    t0 = time.time()
                    if got.rstrip().endswith(b">>>"):
                        break
            text = got.decode("utf-8", "replace")
            if got.rstrip().endswith(b">>>") and line[:24] in text and line[-24:] in text:
                if not mute:
                    console_write(text)
                return text
            time.sleep(0.3)
        raise RuntimeError("命令无响应或回显不完整: " + line[:60])

    def value(self, line, timeout=8.0, mute=False):
        """执行一行并取最后一条纯数据行。报错时携带完整输出行, 不只第一行。"""
        text = self.cmd(line, timeout=timeout, mute=mute)
        rows = []
        for ln in text.splitlines():
            s = ln.strip()
            if not s or s.endswith(">>>") or s == line.strip() or s == "...":
                continue
            rows.append(s)
        bad = [s for s in rows if ("Traceback" in s or "Error" in s or "memory allocation failed" in s)]
        if bad:
            raise RuntimeError("板子报错: " + " | ".join(rows[-8:]))
        if not rows:
            raise RuntimeError("命令无输出: " + line[:60])
        return rows[-1]

    # ---- 动作 ----
    def interrupt(self, mute=False):
        self.ser.write(b"\x03\x03")
        time.sleep(0.4)
        self._emit(self._raw_read(0.4), mute)
        self.ser.write(b"\x03\r\n")
        time.sleep(0.3)
        self._emit(self._raw_read(0.4), mute)

    def stop_motors(self, mute=True):
        with self.lock:
            if not self.ser:
                return
            self.interrupt(mute=True)
            if self.profile != "hiwonder":
                # 通用机型: 打断即停, 电机/舵机保持当前状态 (幻尔停转命令不存在, 跳过省时)
                return
            for line in (
                "import Hiwonder as _h",
                "_em = _h.EncoderMotor; _em.setType(_em.TT_MOTOR)",
                "_em.setSpeed(_em.Motor1, 0); _em.setSpeed(_em.Motor2, 0)",
                "print('MOTORS_STOPPED')",
            ):
                try:
                    self.cmd(line, timeout=5.0, mute=True)
                except Exception:
                    pass

    def run(self):
        with self.lock:
            if not self.ser:
                raise RuntimeError("未连接")
            console_write("\n[运行] 软重启板子...\n")
            try:
                self.cmd("import machine; machine.reset()", timeout=4.0)
            except Exception:
                self.ser.write(b"\x04")

    def probe(self):
        with self.lock:
            v = self.value(
                "import sys; print('MPYINFO', sys.implementation.name, sys.version, sys.platform)",
                timeout=5.0, mute=True,
            )
            if "MPYINFO" in v:
                self.board_info = " ".join(v.split()[1:])

    # ---- 文件读写 (与 写入ESP32.py 同链路) ----
    def read_board_file(self, path, verify=False, max_bytes=None):
        with self.lock:
            # 自给自足: 不依赖调用方先导入 binascii (fresh REPL 会话里不存在)
            self.cmd("import binascii", timeout=5.0, mute=True)
            size = int(self.value("print(len(open(%r,'rb').read()))" % path, mute=True))
            if max_bytes is not None and size > max_bytes:
                raise RuntimeError("板载文件超过读取上限: %d > %d" % (size, max_bytes))
            pieces = []
            for k in range(0, size, 128):
                hx = self.value(
                    "print(binascii.hexlify(open(%r,'rb').read()[%d:%d]).decode())" % (path, k, k + 128),
                    mute=True,
                ).strip()
                if not hx or len(hx) % 2:
                    raise RuntimeError("读回 %s 第 %d 段异常" % (path, k))
                piece = binascii.unhexlify(hx)
                expected = min(128, size - k)
                if verify and len(piece) != expected:
                    raise RuntimeError("读回 %s 第 %d 段长度不符" % (path, k))
                pieces.append(piece)
            data = b"".join(pieces)
            if verify:
                if len(data) != size:
                    raise RuntimeError("读回 %s 长度不符: %d != %d" % (path, len(data), size))
                remote_crc = int(self.value(
                    "print(binascii.crc32(open(%r,'rb').read()))" % path,
                    mute=True,
                )) & 0xFFFFFFFF
                local_crc = zlib.crc32(data) & 0xFFFFFFFF
                if remote_crc != local_crc:
                    raise RuntimeError("读回 %s CRC32 不符: %08X != %08X" % (path, local_crc, remote_crc))
            return data

    def file_entry_exists(self, path):
        """Return existence only after the board successfully lists the parent directory."""
        parent, _, name = path.rpartition("/")
        parent = parent or "/"
        line = (
            "import os; _n=%r; _p=%r; "
            "print('CODEX_TARGET_PRESENT' if _n in os.listdir(_p) else 'CODEX_TARGET_ABSENT')"
        ) % (name, parent)
        result = self.value(line, timeout=8.0, mute=True).strip()
        if result == "CODEX_TARGET_PRESENT":
            return True
        if result == "CODEX_TARGET_ABSENT":
            return False
        raise RuntimeError("无法可靠判断目标文件是否存在: " + path)

    def save_verified_backup(self, path, data):
        """Save an exclusive, durable, read-back-verified backup in the workspace root."""
        workspace = os.path.abspath(self.workspace)
        if not os.path.isdir(workspace):
            raise RuntimeError("工作区备份目录不可用: " + workspace)
        digest = hashlib.sha256(path.encode("utf-8")).hexdigest()[:16]
        stamp = time.strftime("%Y%m%d_%H%M%S")
        backup_path = ""
        file_handle = None
        for attempt in range(100):
            backup_name = "codex_backup_%s_%s_%02d.bin" % (digest, stamp, attempt)
            candidate = os.path.join(workspace, backup_name)
            try:
                file_handle = open(candidate, "xb")
                backup_path = candidate
                break
            except FileExistsError:
                continue
            except OSError as exc:
                raise RuntimeError(
                    "严格备份失败；未发出目标写入命令: 无法独占创建备份 (%s)" % exc
                )
        if file_handle is None:
            raise RuntimeError("严格备份失败；未发出目标写入命令: 备份文件名冲突")

        try:
            with file_handle as stream:
                written = stream.write(data)
                if written != len(data):
                    raise IOError("备份写入长度不符")
                stream.flush()
                os.fsync(stream.fileno())
            with open(backup_path, "rb") as stream:
                saved = stream.read()
        except Exception as exc:
            raise RuntimeError(
                "严格备份失败；未发出目标写入命令: 保存或回读失败 (%s); backup=%s"
                % (exc, backup_path)
            )

        expected_crc = zlib.crc32(data) & 0xFFFFFFFF
        saved_crc = zlib.crc32(saved) & 0xFFFFFFFF
        if len(saved) != len(data) or saved != data or saved_crc != expected_crc:
            raise RuntimeError(
                "严格备份失败；未发出目标写入命令: 备份回读校验失败; backup=%s" % backup_path
            )
        return {
            "backupVerified": True,
            "backupPath": backup_path,
            "backupSize": len(saved),
            "backupCrc": "%08X" % saved_crc,
        }

    def backup_before_mutation(self, path, expected_old_crc=None):
        """Prove absence or back up and verify original raw bytes before mutation."""
        existed = self.file_entry_exists(path)
        if not existed:
            if expected_old_crc is not None:
                raise RuntimeError("严格备份失败；目标在确认后已不存在，未发出目标写入命令")
            return {
                "targetExisted": False,
                "backupVerified": False,
                "backupPath": "",
                "backupSize": 0,
                "backupCrc": "",
            }

        original = self.read_board_file(
            path,
            verify=True,
            max_bytes=CODEX_MAX_FILE_BYTES,
        )
        original_crc = zlib.crc32(original) & 0xFFFFFFFF
        if expected_old_crc is not None and "%08X" % original_crc != expected_old_crc.upper():
            raise RuntimeError("严格备份失败；目标在读取后已变化，未发出目标写入命令")
        backup = self.save_verified_backup(path, original)
        return {
            "targetExisted": True,
            **backup,
        }

    def download(self, content, run_after, target=TARGET, strict_backup=False):
        data = content.encode("utf-8")
        if strict_backup and len(data) > CODEX_MAX_FILE_BYTES:
            raise RuntimeError("严格下载超过 1 MiB 上限；未发出目标写入命令")
        local_crc = zlib.crc32(data) & 0xFFFFFFFF
        base_noext = os.path.basename(target).rsplit(".", 1)[0] or "corex"
        with self.lock:
            if not self.ser:
                raise RuntimeError("未连接")
            console_write("[下载] 停电机...\n")
            self.interrupt(mute=True)
            self.stop_motors(mute=True)
            self.cmd("import binascii", mute=True)

            console_write("[下载] 备份板载 %s ...\n" % target)
            backup_name = ""
            strict_backup_info = None
            if strict_backup:
                strict_backup_info = self.backup_before_mutation(target)
            else:
                try:
                    old = self.read_board_file(target)
                    stamp = time.strftime("%Y%m%d_%H%M%S")
                    backup_name = "备份_板载%s_%s.py" % (base_noext, stamp)
                    with open(os.path.join(self.workspace, backup_name), "wb") as f:
                        f.write(old)
                except Exception as e:
                    console_write("[下载] 备份失败(继续写入): %r\n" % (e,))

            console_write("[下载] 写入 %d 字节 ...\n" % len(data))
            self.cmd("f = open('%s', 'wb')" % target, mute=True)
            chunk = 80
            total = len(data)
            for i in range(0, total, chunk):
                hx = binascii.hexlify(data[i:i + chunk]).decode()
                self.cmd("f.write(binascii.unhexlify('%s'))" % hx, mute=True)
                if (i // chunk) % 10 == 0:
                    console_write("[下载] 已写 %d/%d 字节\n" % (min(i + chunk, total), total))
            console_write("[下载] 已写 %d/%d 字节, 关闭文件\n" % (total, total))
            self.cmd("f.close()", mute=True)

            size = int(self.value("import os; print(os.stat('%s')[6])" % target, mute=True))
            crc = int(self.value("print(binascii.crc32(open('%s','rb').read()))" % target, mute=True)) & 0xFFFFFFFF
            compile_ok = "COMPILE_OK" in self.value(
                "compile(open('%s','rb').read(), '%s', 'exec'); print('COMPILE_OK')" % (target, base_noext),
                mute=True,
            )
            ok = size == len(data) and crc == local_crc and compile_ok
            if not ok:
                console_write("[下载] 校验失败! size=%s crc=%08X compile=%s\n" % (size, crc, compile_ok))
                result = {"ok": False, "size": size, "crc": "%08X" % crc, "compileOk": compile_ok,
                          "backup": backup_name}
                if strict_backup_info is not None:
                    result.update(strict_backup_info)
                return result
            console_write("[下载] 校验通过 CRC32=%08X\n" % crc)
            if run_after:
                self.run()
            result = {"ok": True, "size": size, "crc": "%08X" % crc, "compileOk": True,
                      "backup": backup_name, "ran": run_after}
            if strict_backup_info is not None:
                result.update(strict_backup_info)
            return result


# ---------------- 命令分发 ----------------
class Bridge:
    def __init__(self, workspace, profile="hiwonder"):
        self.workspace = workspace
        self.board = Board(workspace, profile)
        self.busy = False

    def handle(self, req):
        cmd = req.get("cmd")
        args = {k: v for k, v in req.items() if k not in ("id", "cmd")}
        handler = {
            "ports": self.c_ports, "connect": self.c_connect, "disconnect": self.c_disconnect,
            "status": self.c_status, "stop": self.c_stop, "run": self.c_run,
            "interrupt": self.c_interrupt, "send": self.c_send, "download": self.c_download,
            "listfiles": self.c_listfiles, "readfile": self.c_readfile,
            "writefile": self.c_writefile, "deletefile": self.c_deletefile,
            "capabilities": self.c_capabilities,
        }.get(cmd)
        if handler is None:
            raise RuntimeError("未知命令: " + str(cmd))
        if self.busy and cmd not in ("status", "ports"):
            raise RuntimeError("桥忙: 正在执行其他操作")
        self.busy = cmd not in ("status", "ports")
        try:
            return handler(args)
        finally:
            self.busy = False

    def c_capabilities(self, _a):
        return dict(CODEX_CAPABILITIES)

    def c_ports(self, _a):
        ports = []
        for p in list_ports.comports():
            is_ch340 = p.vid == CH340_VID or "CH340" in (p.description or "").upper()
            # 幻尔小车只认 CH340; 通用机型板载串口芯片多样 (CP210x 等), 列出全部
            if is_ch340 or self.board.profile == "generic":
                ports.append({"device": p.device, "description": p.description or "", "ch340": is_ch340})
        ports.sort(key=lambda x: 0 if x["ch340"] else 1)
        return {"ports": ports}

    def c_connect(self, a):
        port = a.get("port") or ""
        if not port:
            found = self.c_ports({})["ports"]
            if not found:
                raise RuntimeError("未找到可用串口" if self.board.profile == "generic" else "未找到 CH340 串口")
            port = found[0]["device"]  # 已按 CH340 优先排序
        console_write("[桥] 连接 %s ...\n" % port)
        self.board.open(port)
        self.board._deadline = time.time() + 10.0  # 连接总预算 10 秒, 防止无响应板子拖死
        try:
            with self.board.lock:
                self.board.interrupt(mute=False)
                self.board.stop_motors(mute=True)
                self.board.probe()
        except Exception as e:
            self.board.close_quiet()
            raise RuntimeError("连接失败(板子无响应或固件异常, 请确认该串口是 MicroPython 板): %r" % (e,))
        finally:
            self.board._deadline = None
        console_write("[桥] 已连接 %s  %s\n" % (port, self.board.board_info))
        return {"connected": True, "port": port, "boardInfo": self.board.board_info}

    def c_disconnect(self, _a):
        self.board.close_quiet()
        console_write("[桥] 已断开\n")
        return {"connected": False}

    def c_status(self, _a):
        return {"connected": bool(self.board.ser), "port": self.board.port,
                "busy": self.busy, "boardInfo": self.board.board_info,
                "workspace": self.workspace}

    def c_stop(self, _a):
        self.board.stop_motors(mute=True)
        console_write("[桥] 电机已停转\n")
        return {"stopped": True}

    def c_run(self, _a):
        self.board.run()
        return {"ran": True}

    def c_interrupt(self, _a):
        with self.board.lock:
            self.board.interrupt(mute=False)
        self.board.stop_motors(mute=True)
        console_write("[桥] 已打断程序并停电机\n")
        return {"interrupted": True}

    def c_send(self, a):
        line = str(a.get("line", ""))
        with self.board.lock:
            if not self.board.ser:
                raise RuntimeError("未连接")
            self.board.ser.reset_input_buffer()
            self.board.ser.write(line.encode("utf-8") + b"\r\n")
            self.board._emit(self.board._raw_read(0.4), mute=False)
        return {"sent": True}

    def c_download(self, a):
        content = a.get("content")
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("内容为空")
        target = str(a.get("target") or TARGET)
        if not target.startswith("/") or ".." in target or "'" in target or '"' in target or "\\" in target:
            raise RuntimeError("非法下载目标: %r (须以 / 开头, 禁止 .. 与引号)" % target[:80])
        result = self.board.download(
            content,
            bool(a.get("run")),
            target,
            strict_backup=a.get("strictBackup") is True,
        )
        return result

    # ---- 板载文件管理 (M2) ----
    @staticmethod
    def _safe_path(a):
        path = str(a.get("path") or "")
        if not path.startswith("/") or ".." in path or "'" in path or '"' in path or "\\" in path:
            raise RuntimeError("非法板载路径: %r (须以 / 开头, 禁止 .. 与引号)" % path[:80])
        return path

    def c_listfiles(self, _a):
        with self.board.lock:
            if not self.board.ser:
                raise RuntimeError("未连接")
            v = self.board.value(
                "import os, json; print(json.dumps([[f, os.stat(f)[6]] for f in os.listdir()]))",
                timeout=6.0, mute=True,
            )
        try:
            rows = json.loads(v)
        except Exception:
            raise RuntimeError("文件列表解析失败: " + v[-100:])
        return {"files": [{"name": str(f), "size": int(s)} for f, s in rows]}

    def c_readfile(self, a):
        path = self._safe_path(a)
        raw_read = a.get("raw") is True
        max_bytes = a.get("maxBytes", CODEX_MAX_FILE_BYTES)
        if raw_read and (
            not isinstance(max_bytes, int)
            or isinstance(max_bytes, bool)
            or max_bytes < 1
            or max_bytes > CODEX_MAX_FILE_BYTES
        ):
            raise RuntimeError("raw 文件读取 maxBytes 必须在 1 到 1 MiB 之间")
        with self.board.lock:
            if not self.board.ser:
                raise RuntimeError("未连接")
            # 先静默板子: 打断残留线程的异常输出会污染 value() 的数据行解析
            self.board.interrupt(mute=True)
            self.board.stop_motors(mute=True)
            data = self.board.read_board_file(
                path,
                verify=raw_read,
                max_bytes=max_bytes if raw_read else None,
            )
        if raw_read:
            return {
                "path": path,
                "size": len(data),
                "crc": "%08X" % (zlib.crc32(data) & 0xFFFFFFFF),
                "contentBase64": base64.b64encode(data).decode("ascii"),
            }
        return {"path": path, "size": len(data), "content": data.decode("utf-8", "replace")}

    def c_writefile(self, a):
        path = self._safe_path(a)
        content = a.get("content")
        if not isinstance(content, str) or not content:
            raise RuntimeError("内容为空")
        strict_backup = a.get("strictBackup") is True
        expected_old_crc = a.get("expectedOldCrc")
        if strict_backup and expected_old_crc is not None:
            if not isinstance(expected_old_crc, str) or len(expected_old_crc) != 8:
                raise RuntimeError("expectedOldCrc 格式错误；未发出目标写入命令")
            try:
                int(expected_old_crc, 16)
            except ValueError:
                raise RuntimeError("expectedOldCrc 格式错误；未发出目标写入命令")
        data = content.encode("utf-8")
        if strict_backup and len(data) > CODEX_MAX_FILE_BYTES:
            raise RuntimeError("严格文件写入超过 1 MiB 上限；未发出目标写入命令")
        local_crc = zlib.crc32(data) & 0xFFFFFFFF
        backup_info = None
        with self.board.lock:
            if not self.board.ser:
                raise RuntimeError("未连接")
            self.board.interrupt(mute=True)
            self.board.stop_motors(mute=True)
            self.board.cmd("import binascii", mute=True)
            if strict_backup:
                backup_info = self.board.backup_before_mutation(path, expected_old_crc)
            self.board.cmd("f = open('%s', 'wb')" % path, mute=True)
            chunk = 80
            for i in range(0, len(data), chunk):
                hx = binascii.hexlify(data[i:i + chunk]).decode()
                self.board.cmd("f.write(binascii.unhexlify('%s'))" % hx, mute=True)
            self.board.cmd("f.close()", mute=True)
            size = int(self.board.value("import os; print(os.stat('%s')[6])" % path, mute=True))
            crc = int(self.board.value("print(binascii.crc32(open('%s','rb').read()))" % path, mute=True)) & 0xFFFFFFFF
        if size != len(data) or crc != local_crc:
            raise RuntimeError("写入校验失败 size=%s crc=%08X (期望 crc=%08X)" % (size, crc, local_crc))
        console_write("[文件] 已写入 %s (%d 字节, CRC32=%08X)\n" % (path, size, crc))
        result = {"ok": True, "path": path, "size": size, "crc": "%08X" % crc}
        if backup_info is not None:
            result.update(backup_info)
        return result

    def c_deletefile(self, a):
        path = self._safe_path(a)
        strict_backup = a.get("strictBackup") is True
        backup_info = None
        with self.board.lock:
            if not self.board.ser:
                raise RuntimeError("未连接")
            if strict_backup:
                self.board.interrupt(mute=True)
                self.board.stop_motors(mute=True)
                self.board.cmd("import binascii", mute=True)
                backup_info = self.board.backup_before_mutation(path)
                if backup_info.get("targetExisted") is not True:
                    raise RuntimeError("严格备份无法证明目标存在；未发出删除命令: " + path)
            v = self.board.value(
                "import os; os.remove('%s'); print('FILE_DELETED')" % path,
                timeout=6.0, mute=True,
            )
        if "FILE_DELETED" not in v:
            raise RuntimeError("删除失败: " + v[-100:])
        console_write("[文件] 已删除 %s\n" % path)
        result = {"ok": True, "path": path}
        if backup_info is not None:
            result.update(backup_info)
        return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--profile", default="hiwonder", choices=("hiwonder", "generic"),
                    help="机型: hiwonder=幻尔小车, generic=通用 ESP32 MicroPython 板")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdin.reconfigure(encoding="utf-8")
    except Exception:
        pass

    bridge = Bridge(args.workspace, args.profile)
    log("bridge ready, workspace=" + args.workspace)
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
            rid = req.get("id")
        except Exception as e:
            out({"ok": False, "error": "bad request: %r" % (e,)})
            continue
        try:
            data = bridge.handle(req)
            out({"id": rid, "ok": True, "data": data})
        except Exception as e:
            out({"id": rid, "ok": False, "error": "%s: %s" % (type(e).__name__, e)})


if __name__ == "__main__":
    main()
