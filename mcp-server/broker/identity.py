"""Current Windows user SID helpers for per-user broker naming."""

from __future__ import annotations

import ctypes
import hashlib
import os
from ctypes import wintypes


def current_user_sid() -> str:
    if os.name != "nt":
        raise RuntimeError("the control broker requires Windows")
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.LocalFree.argtypes = (ctypes.c_void_p,)
    advapi.OpenProcessToken.argtypes = (wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE))
    advapi.OpenProcessToken.restype = wintypes.BOOL
    advapi.GetTokenInformation.argtypes = (
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)
    )
    advapi.GetTokenInformation.restype = wintypes.BOOL
    advapi.ConvertSidToStringSidW.argtypes = (ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR))
    advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise OSError(ctypes.get_last_error(), "OpenProcessToken failed")
    try:
        needed = wintypes.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
        buffer = ctypes.create_string_buffer(needed.value)
        if not advapi.GetTokenInformation(token, 1, buffer, needed, ctypes.byref(needed)):
            raise OSError(ctypes.get_last_error(), "GetTokenInformation failed")
        sid_pointer = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        string_sid = wintypes.LPWSTR()
        if not advapi.ConvertSidToStringSidW(sid_pointer, ctypes.byref(string_sid)):
            raise OSError(ctypes.get_last_error(), "ConvertSidToStringSidW failed")
        try:
            return string_sid.value
        finally:
            kernel.LocalFree(string_sid)
    finally:
        kernel.CloseHandle(token)


def user_key(sid: str | None = None) -> str:
    value = sid or current_user_sid()
    return hashlib.sha256(value.encode("ascii", errors="strict")).hexdigest()[:24]
