"""Small synchronous Windows named-pipe byte transport with explicit framing."""

from __future__ import annotations

import ctypes
import base64
import binascii
import hashlib
import os
import time
from ctypes import wintypes

from .protocol import (CHUNK_BYTES, HEADER, MAX_LOGICAL_BYTES, MAX_MESSAGE_BYTES,
                       ProtocolError, decode_message, encode_message, serialize_message)
from .security import create_user_security_descriptor, free_security_descriptor

if os.name == "nt":
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateNamedPipeW.restype = wintypes.HANDLE
    _k32.CreateFileW.restype = wintypes.HANDLE
    _k32.GetNamedPipeClientProcessId.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG))
    _k32.GetNamedPipeClientProcessId.restype = wintypes.BOOL
    _k32.SetNamedPipeHandleState.argtypes = (
        wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p, ctypes.c_void_p
    )
    _k32.SetNamedPipeHandleState.restype = wintypes.BOOL

    PIPE_ACCESS_DUPLEX = 0x00000003
    PIPE_TYPE_BYTE = 0x00000000
    PIPE_READMODE_BYTE = 0x00000000
    PIPE_WAIT = 0x00000000
    PIPE_UNLIMITED_INSTANCES = 255
    OPEN_EXISTING = 3
    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    ERROR_PIPE_CONNECTED = 535
    ERROR_BROKEN_PIPE = 109
    ERROR_NO_DATA = 232
    ERROR_SEM_TIMEOUT = 121
    ERROR_PIPE_LISTENING = 536
    PIPE_NOWAIT = 0x00000001


class PipeError(OSError):
    pass


def _require_windows() -> None:
    if os.name != "nt":
        raise PipeError("Windows named pipes are available only on Windows")


def _handle_value(handle: wintypes.HANDLE) -> int:
    return ctypes.cast(handle, ctypes.c_void_p).value or 0


def _read_exact(handle: wintypes.HANDLE, count: int, timeout: float | None = None) -> bytes:
    parts: list[bytes] = []
    remaining = count
    deadline = None if timeout is None else time.monotonic() + timeout
    while remaining:
        buffer = ctypes.create_string_buffer(min(remaining, 65536))
        received = wintypes.DWORD()
        ok = _k32.ReadFile(handle, buffer, len(buffer), ctypes.byref(received), None)
        if not ok:
            code = ctypes.get_last_error()
            if code == ERROR_NO_DATA and deadline is not None:
                if time.monotonic() < deadline:
                    time.sleep(0.01)
                    continue
                raise PipeError(ERROR_SEM_TIMEOUT, "named pipe response timed out")
            raise PipeError(code, "named pipe read failed")
        if received.value == 0:
            raise PipeError(ERROR_BROKEN_PIPE, "named pipe closed")
        parts.append(buffer.raw[: received.value])
        remaining -= received.value
    return b"".join(parts)


def _write_all(handle: wintypes.HANDLE, data: bytes) -> None:
    # PIPE_NOWAIT can accept zero bytes when its bounded buffer is full. This is
    # backpressure, not proof that the peer closed the connection. Keep retries
    # bounded and let the reader drain while preserving each frame's ordering.
    offset = 0
    deadline = time.monotonic() + 30.0
    while offset < len(data):
        chunk = data[offset : offset + 65536]
        buffer = ctypes.create_string_buffer(chunk)
        written = wintypes.DWORD()
        if not _k32.WriteFile(handle, buffer, len(chunk), ctypes.byref(written), None):
            code = ctypes.get_last_error()
            if code in {ERROR_NO_DATA, ERROR_PIPE_LISTENING} and time.monotonic() < deadline:
                time.sleep(0.005)
                continue
            raise PipeError(code, "named pipe write failed")
        if written.value == 0:
            if time.monotonic() >= deadline:
                raise PipeError(ERROR_SEM_TIMEOUT, "named pipe write stalled")
            time.sleep(0.005)
            continue
        offset += written.value


class PipeConnection:
    """Connection API mirrors send_bytes/recv_bytes, but only carries JSON bytes."""

    def __init__(self, handle: wintypes.HANDLE, *, read_timeout: float | None = None):
        self._handle = handle
        self._closed = False
        self._read_timeout = read_timeout

    def set_read_timeout(self, seconds: float | None) -> None:
        self._read_timeout = seconds

    def send_bytes(self, payload: bytes) -> None:
        if self._closed:
            raise PipeError("pipe is closed")
        if not isinstance(payload, bytes) or not payload or len(payload) > MAX_MESSAGE_BYTES:
            raise ProtocolError("invalid framed payload length")
        _write_all(self._handle, HEADER.pack(len(payload)) + payload)

    def recv_bytes(self) -> bytes:
        if self._closed:
            raise PipeError("pipe is closed")
        (length,) = HEADER.unpack(_read_exact(self._handle, HEADER.size, self._read_timeout))
        if length < 1 or length > MAX_MESSAGE_BYTES:
            raise ProtocolError("message length is outside the allowed limit")
        return _read_exact(self._handle, length, self._read_timeout)

    def send_json(self, value: dict) -> None:
        payload = serialize_message(value, limit=MAX_LOGICAL_BYTES)
        if len(payload) <= MAX_MESSAGE_BYTES:
            self.send_bytes(payload)
            return
        digest = hashlib.sha256(payload).hexdigest()
        parts = [payload[offset:offset + CHUNK_BYTES]
                 for offset in range(0, len(payload), CHUNK_BYTES)]
        for index, part in enumerate(parts):
            framed = encode_message({
                "type": "_broker_chunk", "index": index, "total": len(parts),
                "sha256": digest, "data": base64.b64encode(part).decode("ascii"),
            })
            self.send_bytes(framed[HEADER.size:])

    def recv_json(self) -> dict:
        message = decode_message(self.recv_bytes())
        if message.get("type") != "_broker_chunk":
            return message
        total = message.get("total")
        digest = message.get("sha256")
        if (type(total) is not int or not 2 <= total <=
                (MAX_LOGICAL_BYTES + CHUNK_BYTES - 1) // CHUNK_BYTES or
                not isinstance(digest, str) or len(digest) != 64 or
                any(ch not in "0123456789abcdef" for ch in digest)):
            raise ProtocolError("invalid chunk envelope")
        pieces: list[bytes] = []
        for index in range(total):
            frame = message if index == 0 else decode_message(self.recv_bytes())
            if (set(frame) != {"type", "index", "total", "sha256", "data"} or
                    frame.get("type") != "_broker_chunk" or
                    type(frame.get("index")) is not int or frame["index"] != index or
                    frame.get("total") != total or frame.get("sha256") != digest or
                    not isinstance(frame.get("data"), str)):
                raise ProtocolError("invalid chunk order or metadata")
            try:
                part = base64.b64decode(frame["data"], validate=True)
            except (ValueError, binascii.Error) as exc:
                raise ProtocolError("invalid chunk data") from exc
            if not part or len(part) > CHUNK_BYTES:
                raise ProtocolError("invalid chunk size")
            pieces.append(part)
            if sum(map(len, pieces)) > MAX_LOGICAL_BYTES:
                raise ProtocolError("reassembled message exceeds limit")
        payload = b"".join(pieces)
        if hashlib.sha256(payload).hexdigest() != digest:
            raise ProtocolError("chunk integrity check failed")
        return decode_message(payload, limit=MAX_LOGICAL_BYTES)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                _k32.DisconnectNamedPipe(self._handle)
            finally:
                _k32.CloseHandle(self._handle)


def create_server_pipe(name: str) -> wintypes.HANDLE:
    _require_windows()
    class SecurityAttributes(ctypes.Structure):
        _fields_ = [
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", ctypes.c_void_p),
            ("bInheritHandle", wintypes.BOOL),
        ]

    _k32.CreateNamedPipeW.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
        wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(SecurityAttributes)
    )
    descriptor = create_user_security_descriptor()
    try:
        attributes = SecurityAttributes(ctypes.sizeof(SecurityAttributes), descriptor, False)
        handle = _k32.CreateNamedPipeW(
            name,
            PIPE_ACCESS_DUPLEX,
            PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_NOWAIT,
            PIPE_UNLIMITED_INSTANCES,
            65536,
            65536,
            0,
            ctypes.byref(attributes),
        )
    finally:
        free_security_descriptor(descriptor)
    if _handle_value(handle) == ctypes.c_void_p(-1).value:
        raise PipeError(ctypes.get_last_error(), "CreateNamedPipeW failed")
    return handle


def accept_pipe(handle: wintypes.HANDLE, *, timeout: float | None = None) -> bool:
    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        if _k32.ConnectNamedPipe(handle, None):
            return True
        code = ctypes.get_last_error()
        if code == ERROR_PIPE_CONNECTED:
            return True
        if code == ERROR_PIPE_LISTENING:
            if deadline is not None and time.monotonic() >= deadline:
                return False
            time.sleep(0.01)
            continue
        raise PipeError(code, "ConnectNamedPipe failed")


def open_client_pipe(name: str, timeout_ms: int = 3000) -> PipeConnection:
    _require_windows()
    if not _k32.WaitNamedPipeW(name, timeout_ms):
        raise PipeError(ctypes.get_last_error(), "named pipe did not become available")
    handle = _k32.CreateFileW(name, GENERIC_READ | GENERIC_WRITE, 0, None, OPEN_EXISTING, 0, None)
    if _handle_value(handle) == ctypes.c_void_p(-1).value:
        raise PipeError(ctypes.get_last_error(), "CreateFileW for named pipe failed")
    mode = wintypes.DWORD(PIPE_READMODE_BYTE | PIPE_NOWAIT)
    if not _k32.SetNamedPipeHandleState(handle, ctypes.byref(mode), None, None):
        error = ctypes.get_last_error()
        _k32.CloseHandle(handle)
        raise PipeError(error, "cannot configure bounded named pipe reads")
    return PipeConnection(handle, read_timeout=15.0)


def connected_client_pid(handle: wintypes.HANDLE) -> int:
    pid = wintypes.ULONG()
    if not _k32.GetNamedPipeClientProcessId(handle, ctypes.byref(pid)):
        raise PipeError(ctypes.get_last_error(), "cannot identify named pipe client")
    return int(pid.value)
