"""Per-command Windows Job ownership, established before user code can run."""

from __future__ import annotations

import ctypes
import json
import os
import runpy
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path
from uuid import uuid4


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimits),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _Accounting(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_int64),
        ("TotalKernelTime", ctypes.c_int64),
        ("ThisPeriodTotalUserTime", ctypes.c_int64),
        ("ThisPeriodTotalKernelTime", ctypes.c_int64),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
    ]


def _kernel32() -> ctypes.CDLL:
    if sys.platform != "win32":
        raise OSError("Windows command ownership requires Windows.")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.OpenJobObjectW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.OpenJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    kernel.QueryInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateJobObject.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    ctypes.set_last_error(0)
    return kernel


def _last_error() -> int:
    if sys.platform != "win32":
        raise OSError("Windows error reporting requires Windows.")
    return ctypes.get_last_error()


def _require(success: object, operation: str) -> None:
    if not success:
        raise OSError(f"Windows command ownership: {operation} failed (error {_last_error()}).")


class WindowsCommandJob:
    def __init__(self) -> None:
        self.name = f"CodePlaneRecipe-{uuid4().hex}"
        self.command_started: bool | None = None
        self._kernel = _kernel32()
        self._handle = self._kernel.CreateJobObjectW(None, self.name)
        _require(self._handle, "CreateJobObjectW")
        try:
            if _last_error() == 183:
                raise OSError("The Windows command ownership name already exists.")
            limits = _ExtendedLimits()
            limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            _require(
                self._kernel.SetInformationJobObject(self._handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)),
                "SetInformationJobObject",
            )
        except BaseException:
            self.close()
            raise

    def launch_argv(self, argv: list[str], *, inherit_stdin: bool = False) -> list[str]:
        # Isolated Python starts no site/custom startup code before self-enrollment.
        # A Windows venv launcher adds another process and its own descendant
        # lifetime. Use the base interpreter for this stdlib-only bootstrap.
        interpreter = getattr(sys, "_base_executable", sys.executable)
        options = ["--inherit-stdin"] if inherit_stdin else []
        return [interpreter, "-I", "-S", str(Path(__file__).resolve()), self.name, *options, *argv]

    def launch_python_module(self, module: str) -> list[str]:
        interpreter = getattr(sys, "_base_executable", sys.executable)
        python_path = [entry for entry in os.environ.get("PYTHONPATH", "").split(os.pathsep) if entry]
        return [
            interpreter,
            "-I",
            "-S",
            str(Path(__file__).resolve()),
            self.name,
            "--python-module",
            json.dumps([*python_path, *sys.path]),
            module,
        ]

    def terminate(self) -> None:
        _require(self._kernel.TerminateJobObject(self._handle, 1), "TerminateJobObject")

    def active_processes(self) -> int:
        info = _Accounting()
        _require(
            self._kernel.QueryInformationJobObject(self._handle, 1, ctypes.byref(info), ctypes.sizeof(info), None),
            "QueryInformationJobObject",
        )
        self.command_started = int(info.TotalProcesses) >= 2
        return int(info.ActiveProcesses)

    def close(self) -> None:
        if self._handle is not None:
            _require(self._kernel.CloseHandle(self._handle), "CloseHandle")
            self._handle = None


def _run_enrolled(argv: list[str]) -> int:
    if len(argv) < 2:
        raise OSError("Command bootstrap requires an ownership name and executable.")
    inherit_stdin = argv[1] == "--inherit-stdin"
    command = argv[2:] if inherit_stdin else argv[1:]
    if not command:
        raise OSError("Command bootstrap requires an executable.")
    kernel = _kernel32()
    job = kernel.OpenJobObjectW(0x0001, False, argv[0])  # JOB_OBJECT_ASSIGN_PROCESS
    _require(job, "OpenJobObjectW")
    try:
        _require(kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()), "AssignProcessToJobObject")
        if argv[1] == "--python-module":
            # Run inside the enrolled process, not a venv redirector's child.
            # The caller can then wait on the actual worker's process handle.
            sys.path[:] = json.loads(argv[2])
            sys.argv = [argv[3]]
            import site

            site.execsitecustomize()
            runpy.run_module(argv[3], run_name="__main__")
            return 0
        return subprocess.call(command, stdin=None if inherit_stdin else subprocess.DEVNULL)
    finally:
        _require(kernel.CloseHandle(job), "CloseHandle")


if __name__ == "__main__":
    try:
        status = _run_enrolled(sys.argv[1:])
    except OSError as exc:
        print(f"CodePlane command bootstrap failed: {exc}", file=sys.stderr)
        status = 125
    raise SystemExit(status)
