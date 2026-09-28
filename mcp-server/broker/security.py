"""Creation of the per-user broker token with an explicit Windows DACL."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path

from .identity import current_user_sid


def create_user_security_descriptor() -> ctypes.c_void_p:
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD)
    )
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    descriptor = ctypes.c_void_p()
    sddl = f"D:P(A;;FA;;;{current_user_sid()})"
    if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, 1, ctypes.byref(descriptor), None
    ):
        raise OSError(ctypes.get_last_error(), "cannot create private broker ACL")
    return descriptor


def free_security_descriptor(descriptor: ctypes.c_void_p) -> None:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = (ctypes.c_void_p,)
    kernel.LocalFree(descriptor)


def create_private_token_file(path: Path) -> int:
    """Create a new file whose protected DACL grants access only to the current SID."""
    if os.name != "nt":
        raise OSError("private broker token files require Windows ACLs")
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)

    class SecurityAttributes(ctypes.Structure):
        _fields_ = [
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", ctypes.c_void_p),
            ("bInheritHandle", wintypes.BOOL),
        ]

    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD)
    )
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    kernel.CreateFileW.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(SecurityAttributes),
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE
    )
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.LocalFree.argtypes = (ctypes.c_void_p,)

    descriptor = create_user_security_descriptor()
    try:
        attributes = SecurityAttributes(ctypes.sizeof(SecurityAttributes), descriptor, False)
        handle = kernel.CreateFileW(
            str(path), 0x40000000, 0, ctypes.byref(attributes), 1, 0x80, None
        )
        if ctypes.cast(handle, ctypes.c_void_p).value == ctypes.c_void_p(-1).value:
            error = ctypes.get_last_error()
            if error in {80, 183}:
                raise FileExistsError(error, "broker token already exists", str(path))
            raise OSError(error, "cannot create private broker token file")
        import msvcrt

        try:
            return msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
        except Exception:
            kernel.CloseHandle(handle)
            raise
    finally:
        free_security_descriptor(descriptor)
