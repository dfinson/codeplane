"""Windows user-bound DPAPI secrets and private audit directory access."""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


class _Blob(ctypes.Structure):
    _fields_ = [("length", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _libraries() -> tuple[ctypes.CDLL, ctypes.CDLL, ctypes.CDLL]:
    if sys.platform != "win32":
        raise OSError("Windows audit protection requires Windows")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.OpenProcessToken.restype = wintypes.BOOL
    advapi.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi.GetTokenInformation.restype = wintypes.BOOL
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
    ]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    advapi.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    advapi.SetFileSecurityW.restype = wintypes.BOOL
    crypt.CryptProtectData.argtypes = [
        ctypes.POINTER(_Blob),
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_Blob),
    ]
    crypt.CryptProtectData.restype = wintypes.BOOL
    crypt.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_Blob),
    ]
    crypt.CryptUnprotectData.restype = wintypes.BOOL
    return kernel, advapi, crypt


def _require(success: object, operation: str) -> None:
    if not success:
        raise OSError(f"{operation} failed for Windows audit storage")


def private_directory(path: Path) -> None:
    """Protected DACL grants only the current user and SYSTEM inherited access."""
    kernel, advapi, _ = _libraries()
    token = wintypes.HANDLE()
    _require(advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)), "OpenProcessToken")
    sid_string = wintypes.LPWSTR()
    descriptor = ctypes.c_void_p()
    try:
        needed = wintypes.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
        if not 0 < needed.value <= 65536:
            raise OSError("Invalid Windows user token size")
        info = ctypes.create_string_buffer(needed.value)
        _require(advapi.GetTokenInformation(token, 1, info, needed.value, ctypes.byref(needed)), "GetTokenInformation")
        sid = ctypes.cast(info, ctypes.POINTER(ctypes.c_void_p))[0]
        _require(advapi.ConvertSidToStringSidW(sid, ctypes.byref(sid_string)), "ConvertSidToStringSidW")
        sddl = f"D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;{sid_string.value})"
        _require(
            advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None),
            "ConvertStringSecurityDescriptor",
        )
        _require(advapi.SetFileSecurityW(str(path), 0x80000004, descriptor), "SetFileSecurityW")
    finally:
        if descriptor:
            kernel.LocalFree(descriptor)
        if sid_string:
            kernel.LocalFree(ctypes.cast(sid_string, ctypes.c_void_p))
        kernel.CloseHandle(token)


def protect(data: bytes, *, decrypt: bool = False) -> bytes:
    kernel, _, crypt = _libraries()
    source = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    incoming, outgoing = _Blob(len(data), source), _Blob()
    try:
        if decrypt:
            success = crypt.CryptUnprotectData(
                ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing)
            )
        else:
            # CRYPTPROTECT_UI_FORBIDDEN, without LOCAL_MACHINE: only this user can decrypt.
            success = crypt.CryptProtectData(
                ctypes.byref(incoming),
                "CodePlane audit key",
                None,
                None,
                None,
                1,
                ctypes.byref(outgoing),
            )
        _require(success, "CryptUnprotectData" if decrypt else "CryptProtectData")
        return ctypes.string_at(outgoing.data, outgoing.length)
    finally:
        if outgoing.data:
            kernel.LocalFree(outgoing.data)


def reject_reparse(path: Path) -> None:
    info = path.lstat()
    if getattr(info, "st_file_attributes", 0) & 0x400:
        raise OSError("Windows audit storage cannot use reparse points")
    if not os.path.isdir(path) and not os.path.isfile(path):
        raise OSError("Windows audit storage requires a regular file or directory")
