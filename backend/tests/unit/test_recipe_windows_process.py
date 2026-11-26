"""Windows ownership protocol checks; native containment needs a Windows host."""

from __future__ import annotations

import asyncio
import ctypes
import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.services.recipe import command_runner, windows_process


@pytest.fixture
def kernel(monkeypatch):
    api = MagicMock()
    api.CreateJobObjectW.return_value = 101
    api.OpenJobObjectW.return_value = 101
    api.AssignProcessToJobObject.return_value = True
    api.SetInformationJobObject.return_value = True
    api.TerminateJobObject.return_value = True
    api.CloseHandle.return_value = True
    api.GetCurrentProcess.return_value = -1
    monkeypatch.setattr(windows_process, "_kernel32", lambda: api)
    monkeypatch.setattr(windows_process, "_last_error", lambda: 0)
    return api


def test_bootstrap_enrolls_itself_before_dispatch_and_fails_closed(kernel, monkeypatch):
    order = []

    def enroll(*_args):
        order.append("enrolled")
        return True

    def dispatch_result(*_args, **_kwargs):
        order.append("dispatch")
        return 7

    kernel.AssignProcessToJobObject.side_effect = enroll
    dispatch = MagicMock(side_effect=dispatch_result)
    monkeypatch.setattr(subprocess, "call", dispatch)
    assert windows_process._run_enrolled(["owned-job", "program.exe", "argument with spaces"]) == 7
    assert order == ["enrolled", "dispatch"]
    dispatch.assert_called_once_with(["program.exe", "argument with spaces"], stdin=subprocess.DEVNULL)
    kernel.AssignProcessToJobObject.assert_called_once_with(101, -1)
    kernel.CloseHandle.assert_called_once_with(101)
    dispatch.reset_mock()
    kernel.AssignProcessToJobObject.side_effect = None
    kernel.AssignProcessToJobObject.return_value = False
    with pytest.raises(OSError, match="AssignProcessToJobObject"):
        windows_process._run_enrolled(["owned-job", "program.exe"])
    dispatch.assert_not_called()


def test_owned_bootstrap_inherits_stdin_only_when_explicitly_requested(kernel, monkeypatch):
    owner = windows_process.WindowsCommandJob()
    dispatch = MagicMock(return_value=0)
    monkeypatch.setattr(subprocess, "call", dispatch)
    try:
        argv = owner.launch_argv(["python.exe", "-m", "owned.worker"], inherit_stdin=True)
        assert argv[-5:] == [owner.name, "--inherit-stdin", "python.exe", "-m", "owned.worker"]
        assert windows_process._run_enrolled(argv[4:]) == 0
        kernel.AssignProcessToJobObject.assert_called_once_with(101, -1)
        dispatch.assert_called_once_with(["python.exe", "-m", "owned.worker"], stdin=None)
    finally:
        owner.close()


async def test_story_worker_checks_owned_windows_descendants_after_launcher_exit(kernel, monkeypatch):
    from backend.services.story import tokenizer

    active = iter([1, 0])
    order = []

    def terminate(*_args):
        order.append("terminate owned tree")
        return True

    def accounting(_handle, _kind, pointer, _size, _returned):
        info = ctypes.cast(pointer, ctypes.POINTER(windows_process._Accounting)).contents
        info.ActiveProcesses = next(active)
        order.append(f"active={info.ActiveProcesses}")
        return True

    kernel.TerminateJobObject.side_effect = terminate
    kernel.QueryInformationJobObject.side_effect = accounting
    process = MagicMock(returncode=0)
    process.communicate = AsyncMock(return_value=(b"[3]", b""))
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(tokenizer, "sys", SimpleNamespace(platform="win32", executable=sys.executable))
    monkeypatch.setattr(tokenizer.asyncio, "create_subprocess_exec", spawn)
    assert await tokenizer.count_tokens(["abc"]) == [3]
    assert "--python-module" in spawn.call_args.args
    assert spawn.call_args.args[-1] == "backend.services.story.tokenizer"
    assert spawn.call_args.kwargs["stdin"] == asyncio.subprocess.PIPE
    assert order == ["terminate owned tree", "active=1", "active=0"]
    process.kill.assert_not_called()
    kernel.CloseHandle.assert_called_once_with(101)


async def test_exited_leader_still_terminates_and_checks_its_owned_job(kernel, monkeypatch):
    monkeypatch.setattr(command_runner, "os", SimpleNamespace(name="nt"))
    active_counts = iter([1, 0])

    def accounting(_handle, _kind, pointer, _size, _returned):
        info = ctypes.cast(pointer, ctypes.POINTER(windows_process._Accounting)).contents
        info.TotalProcesses = 3
        info.ActiveProcesses = next(active_counts)
        return True

    kernel.QueryInformationJobObject.side_effect = accounting
    owner = windows_process.WindowsCommandJob()
    process = MagicMock(returncode=0)
    process.wait = AsyncMock(return_value=0)
    await command_runner._stop_owned_process(process, owner)
    kernel.TerminateJobObject.assert_called_once_with(101, 1)
    assert kernel.QueryInformationJobObject.call_count == 2
    assert owner.command_started is True
    process.kill.assert_not_called()
    owner.close()
    kernel.CloseHandle.assert_called_once_with(101)


async def test_unowned_or_unconfirmed_windows_tree_cannot_report_confirmed_stop(kernel, monkeypatch):
    monkeypatch.setattr(command_runner, "os", SimpleNamespace(name="nt"))
    process = MagicMock(returncode=0)
    process.wait = AsyncMock(return_value=0)
    with pytest.raises(RuntimeError, match="No owned Windows process tree"):
        await command_runner._stop_owned_process(process)
    owner = windows_process.WindowsCommandJob()
    kernel.QueryInformationJobObject.return_value = False
    with pytest.raises(OSError, match="QueryInformationJobObject"):
        await command_runner._stop_owned_process(process, owner)
    assert owner.command_started is None
    owner.close()


async def test_active_stop_terminates_descendants_before_waiting_for_inherited_pipes(kernel, monkeypatch):
    monkeypatch.setattr(command_runner, "os", SimpleNamespace(name="nt"))
    pipes_closed = asyncio.Event()
    order = []
    process = MagicMock(returncode=None)

    def exited():
        process.returncode = 1
        order.append("leader exited")

    def kill():
        order.append("kill leader")
        asyncio.get_running_loop().call_soon(exited)

    def terminate(*_args):
        assert process.returncode == 1
        order.append("terminate job")
        pipes_closed.set()
        return True

    async def wait():
        await pipes_closed.wait()
        order.append("pipes drained")
        return process.returncode

    def accounting(_handle, _kind, pointer, _size, _returned):
        info = ctypes.cast(pointer, ctypes.POINTER(windows_process._Accounting)).contents
        info.TotalProcesses = 3
        info.ActiveProcesses = 0
        return True

    process.kill.side_effect = kill
    process.wait = AsyncMock(side_effect=wait)
    kernel.TerminateJobObject.side_effect = terminate
    kernel.QueryInformationJobObject.side_effect = accounting
    owner = windows_process.WindowsCommandJob()
    try:
        await asyncio.wait_for(command_runner._stop(process, owner), timeout=1)
        assert order == ["kill leader", "leader exited", "terminate job", "pipes drained"]
    finally:
        owner.close()


def test_windows_job_records_positive_no_command_start_and_isolates_bootstrap(kernel):
    def accounting(_handle, _kind, pointer, _size, _returned):
        info = ctypes.cast(pointer, ctypes.POINTER(windows_process._Accounting)).contents
        info.TotalProcesses = 1
        info.ActiveProcesses = 0
        return True

    kernel.QueryInformationJobObject.side_effect = accounting
    owner = windows_process.WindowsCommandJob()
    assert owner.active_processes() == 0 and owner.command_started is False
    launch = owner.launch_argv(["program.exe", "literal; argument"])
    assert launch[:3] == [getattr(sys, "_base_executable", sys.executable), "-I", "-S"]
    assert launch[-3:] == [owner.name, "program.exe", "literal; argument"]
    owner.close()


@pytest.mark.skipif(os.name != "nt", reason="Native Windows Job Object containment requires Windows.")
@pytest.mark.parametrize("active_leader", [False, True])
async def test_native_windows_child_is_contained_after_exited_command_leader(tmp_path, active_leader):
    import psutil

    heartbeat = tmp_path / "heartbeat.txt"
    child = (
        "import pathlib,time; p=pathlib.Path(" + repr(str(heartbeat)) + "); "
        "p.write_text('started');\nwhile True:\n p.write_text(str(time.time())); time.sleep(.05)"
    )
    streams = "None" if active_leader else "subprocess.DEVNULL"
    interpreter = getattr(sys, "_base_executable", sys.executable)
    leader = (
        "import subprocess,sys,time,pathlib; "
        f"p=subprocess.Popen([{interpreter!r},'-I','-S','-c',{child!r}],"
        f"stdin=subprocess.DEVNULL,stdout={streams},stderr={streams}); "
        f"heartbeat=pathlib.Path({str(heartbeat)!r}); "
        "\nfor _ in range(100):\n"
        " if heartbeat.exists(): break\n"
        " if p.poll() is not None: raise RuntimeError('Child exited before readiness')\n"
        " time.sleep(.02)\n"
        "assert heartbeat.exists(), 'Child failed to become ready'\n"
        "print(p.pid,flush=True)\n"
    )
    if active_leader:
        leader += "time.sleep(60)"
    owner = windows_process.WindowsCommandJob()
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            *owner.launch_argv([interpreter, "-I", "-S", "-c", leader]),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        if active_leader:
            stdout = await asyncio.wait_for(process.stdout.readline(), timeout=10)
            assert process.returncode is None
        else:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10)
            assert process.returncode == 0, stderr.decode(errors="replace")
        child_pid = int(stdout.decode().strip())
        assert psutil.pid_exists(child_pid)
        child_process = psutil.Process(child_pid)
        await command_runner._stop(process, owner)
        assert owner.active_processes() == 0 and owner.command_started is True
        # Windows can retain a terminated process object/PID while handles exist.
        # Wait on the actual process, then prove its file-writing effects stopped.
        assert child_process.wait(timeout=5) == 1
        stopped_contents = heartbeat.read_bytes()
        await asyncio.sleep(0.15)
        assert heartbeat.read_bytes() == stopped_contents
    finally:
        if process is not None:
            await command_runner._stop(process, owner)
        owner.close()
