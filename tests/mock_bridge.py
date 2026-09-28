"""阶段 1/2 与 mock-only 阶段 3a 用 JSON-lines 桥模拟器；不触碰设备。"""

import argparse
import base64
import json
import sys
import zlib


def configure_utf8_streams():
    for stream in (sys.stdin, sys.stdout):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="strict")


def emit(message):
    sys.stdout.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def parse_request(raw):
    try:
        value = json.loads(raw)
    except Exception:
        emit({"ok": False, "error": "bad request: invalid JSON"})
        return None
    if not isinstance(value, dict):
        emit({"ok": False, "error": "bad request: expected JSON object"})
        return None
    return value


def main():
    configure_utf8_streams()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario",
        choices=(
            "normal", "error", "download-failure", "download-backup-failure",
            "download-integrity-failure", "timeout", "exit", "readonly", "busy", "control",
            "fileops", "fileops-no-capability",
        ),
        default="normal",
    )
    parser.add_argument("--profile", choices=("hiwonder", "generic"), default="hiwonder")
    parser.add_argument("--workspace", default="fixture-workspace")
    options = parser.parse_args()

    emit({"event": "log", "text": "bridge ready, workspace=" + options.workspace})
    if options.scenario == "readonly":
        emit({"event": "console", "text": "[模拟] 只读 MCP 夹具就绪；这不是实体设备输出。\n"})
    elif options.scenario in {"control", "fileops"}:
        emit({"event": "console", "text": "[模拟] 控制工具夹具就绪；这不是实体设备输出。\n"})
    is_fileops = options.scenario.startswith("fileops")
    connected = options.scenario == "readonly" or is_fileops
    current_port = "MOCK0" if connected else ""
    board_files = {
        "/main.py": b"Kp = 1.0\nKi = 0.1\nKd = 0.01\nprint('fixture')\n",
        "/corex.py": b"Kp = 2.0\nKi = 0.2\nKd = 0.02\nprint('fixture')\n",
        "/demo.bin": b"\x00fixture\xff",
        "/wifi.json": b'{"ssid":"fixture-only-ssid","password":"fixture-only-password"}',
    }

    def backup_fields(path):
        if path not in board_files:
            return {
                "targetExisted": False,
                "backupVerified": False,
                "backupPath": "",
                "backupSize": 0,
                "backupCrc": "",
            }
        previous = board_files[path]
        return {
            "targetExisted": True,
            "backupVerified": True,
            "backupPath": "mock-backup:" + path,
            "backupSize": len(previous),
            "backupCrc": "%08X" % (zlib.crc32(previous) & 0xFFFFFFFF),
        }
    for raw in sys.stdin:
        if not raw.strip():
            continue
        if options.scenario == "timeout":
            # Intentionally leave the caller waiting while this child stays alive.
            continue
        if options.scenario == "exit":
            raise SystemExit(17)

        request = parse_request(raw)
        if request is None:
            continue
        request_id = request.get("id")
        command = request.get("cmd")
        if options.scenario == "busy":
            emit({
                "id": request_id,
                "ok": False,
                "error": "SerialException: could not open port 'COM9': Access is denied",
            })
            continue
        if options.scenario == "error":
            emit({"id": request_id, "ok": False, "error": "RuntimeError: simulated operation error"})
            continue

        if command == "status":
            data = {
                "connected": connected,
                "port": current_port,
                "busy": False,
                "boardInfo": "模拟 ESP32，不是实体设备" if connected else "",
                "workspace": options.workspace,
            }
        elif command == "ports":
            data = {
                "ports": (
                    [{
                        "device": "MOCK0",
                        "description": "无硬件模拟端口",
                        "ch340": options.profile == "hiwonder",
                    }]
                    if options.scenario in {"readonly", "control"} or is_fileops
                    else []
                )
            }
        elif command == "capabilities":
            data = (
                {}
                if options.scenario == "fileops-no-capability"
                else {"codexStrictBackup": 1, "codexRawFileRead": 1}
            )
        elif command == "connect":
            selected_port = request.get("port") or "MOCK0"
            if (options.scenario == "control" or is_fileops) and connected:
                emit({"id": request_id, "ok": False, "error": "RuntimeError: already connected"})
                continue
            if (options.scenario == "control" or is_fileops) and selected_port != "MOCK0":
                emit({"id": request_id, "ok": False, "error": "RuntimeError: unknown mock port"})
                continue
            if (options.scenario == "control" or is_fileops):
                connected = True
                current_port = selected_port
                emit({"event": "console", "text": "[模拟] 连接/探测结果夹具；未操作设备。\n"})
            data = {
                "connected": True,
                "port": selected_port,
                "boardInfo": (
                    "模拟 ESP32，不是实体设备"
                    if options.scenario in {"readonly", "control"} or is_fileops
                    else "MicroPython fixture"
                ),
            }
        elif command == "disconnect":
            if (options.scenario == "control" or is_fileops):
                if not connected:
                    emit({"id": request_id, "ok": False, "error": "RuntimeError: 未连接"})
                    continue
                connected = False
                current_port = ""
                emit({"event": "console", "text": "[模拟] 断开状态已记录；设备侧停止动作未执行。\n"})
            data = {"connected": False}
        elif command == "run":
            if (options.scenario == "control" or is_fileops) and not connected:
                emit({"id": request_id, "ok": False, "error": "RuntimeError: 未连接"})
                continue
            if (options.scenario == "control" or is_fileops):
                emit({"event": "console", "text": "[模拟] run 请求已记录；未重启设备。\n"})
            data = {"ran": True}
        elif command == "interrupt":
            if (options.scenario == "control" or is_fileops) and not connected:
                emit({"id": request_id, "ok": False, "error": "RuntimeError: 未连接"})
                continue
            if (options.scenario == "control" or is_fileops):
                emit({
                    "event": "console",
                    "text": "[模拟] interrupt 响应夹具 interrupted=true；无法确认实体设备状态。\n",
                })
            else:
                emit({"event": "console", "text": "[桥] 已打断程序并停电机\n"})
            data = {"interrupted": True}
        elif command == "stop":
            if (options.scenario == "control" or is_fileops) and not connected:
                emit({"id": request_id, "ok": False, "error": "RuntimeError: 未连接"})
                continue
            if (options.scenario == "control" or is_fileops):
                emit({
                    "event": "console",
                    "text": "[模拟] stop 响应夹具 stopped=true；不表示电机或舵机实际停转。\n",
                })
            else:
                # Protocol fixtures preserve the legacy wire text; callers must
                # not interpret it as physical stop confirmation for generic.
                emit({"event": "console", "text": "[桥] 电机已停转\n"})
            data = {"stopped": True}
        elif command == "send":
            if (options.scenario == "control" or is_fileops) and not connected:
                emit({"id": request_id, "ok": False, "error": "RuntimeError: 未连接"})
                continue
            if (options.scenario == "control" or is_fileops):
                emit({"event": "console", "text": "[模拟] REPL 行已接收；代码未执行。\n"})
            else:
                emit({"event": "console", "text": "[模拟] REPL 输出: 1\n"})
            data = {"sent": True}
        elif command == "download":
            if options.scenario == "download-integrity-failure":
                data = {
                    "ok": False,
                    "size": 3,
                    "crc": "00000000",
                    "compileOk": True,
                    "backup": "backup_fixture.py",
                }
            elif options.scenario == "download-backup-failure":
                data = {
                    "ok": True,
                    "size": 4,
                    "crc": "D87F7E0C",
                    "compileOk": True,
                    "backup": "",
                    "ran": bool(request.get("run")),
                }
            elif options.scenario == "download-failure":
                data = {
                    "ok": False,
                    "size": 4,
                    "crc": "D87F7E0C",
                    "compileOk": False,
                    "backup": "",
                }
            else:
                data = {
                    "ok": True,
                    "size": 4,
                    "crc": "D87F7E0C",
                    "compileOk": True,
                    "backup": "backup_fixture.py",
                    "ran": bool(request.get("run")),
                }
            if is_fileops and request.get("strictBackup") is True:
                target = request.get("target") or "/main.py"
                payload = str(request.get("content") or "").encode("utf-8")
                backup = backup_fields(target)
                board_files[target] = payload
                data = {
                    "ok": True,
                    "size": len(payload),
                    "crc": "%08X" % (zlib.crc32(payload) & 0xFFFFFFFF),
                    "compileOk": True,
                    "ran": bool(request.get("run")),
                    **backup,
                }
        elif command == "listfiles":
            if is_fileops:
                data = {
                    "files": [
                        {"name": name.rsplit("/", 1)[-1], "size": len(payload)}
                        for name, payload in board_files.items()
                    ]
                }
            else:
                data = {"files": [{"name": "main.py", "size": 4}]}
        elif command == "readfile":
            path = request.get("path", "/main.py")
            content = board_files.get(path, b"test") if is_fileops else b"test"
            if request.get("raw") is True and is_fileops:
                data = {
                    "path": path,
                    "size": len(content),
                    "crc": "%08X" % (zlib.crc32(content) & 0xFFFFFFFF),
                    "contentBase64": base64.b64encode(content).decode("ascii"),
                }
            else:
                data = {
                    "path": path,
                    "size": 4 if not is_fileops else len(content),
                    "content": "test" if not is_fileops else content.decode("utf-8", "replace"),
                }
        elif command == "writefile":
            path = request.get("path", "/demo.py")
            payload = str(request.get("content", "test")).encode("utf-8")
            backup = backup_fields(path) if request.get("strictBackup") is True else {}
            board_files[path] = payload
            data = {
                "ok": True,
                "path": path,
                "size": len(payload),
                "crc": "%08X" % (zlib.crc32(payload) & 0xFFFFFFFF),
                **backup,
            }
        elif command == "deletefile":
            path = request.get("path", "/demo.py")
            backup = backup_fields(path) if request.get("strictBackup") is True else {}
            if request.get("strictBackup") is True and path not in board_files:
                emit({"id": request_id, "ok": False, "error": "strict mock refuses deletion of missing target"})
                continue
            board_files.pop(path, None)
            data = {"ok": True, "path": path, **backup}
        else:
            emit({"id": request_id, "ok": False, "error": "RuntimeError: 未知命令: " + str(command)})
            continue

        emit({"id": request_id, "ok": True, "data": data})


if __name__ == "__main__":
    main()
