"""Actual native gates must not starve the ledger on their own executor."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import partial

import pytest

from backend.services.adapters.native_gates._optional import load_sdk
from backend.tests.native_gates.qualify_runtime_integration import composed_runtime as composed_runtime
from backend.tests.native_gates.qualify_runtime_integration import events_for


@pytest.mark.timeout(12)
@pytest.mark.parametrize("synchronous", [True, False], ids=["explicit-sync", "async-fallback"])
@pytest.mark.parametrize("cancel", [False, True], ids=["complete", "cancel"])
async def test_real_native_gate_progress_with_one_default_executor_worker(synchronous, cancel, composed_runtime):
    host, approvals, governance, create = composed_runtime
    job = await create()
    tools = load_sdk("langchain_core.tools")
    loop = asyncio.get_running_loop()
    await asyncio.to_thread(lambda: None)
    previous = vars(loop)["_default_executor"]
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="qualified-native-default")
    loop.set_default_executor(pool)
    entered, release, finished = asyncio.Event(), threading.Event(), threading.Event()
    calls = []

    def body() -> str:
        """A real native synchronous function under an actual persisted policy."""
        calls.append("body")
        loop.call_soon_threadsafe(entered.set)
        try:
            assert release.wait(5)
            return "actual-result"
        finally:
            finished.set()

    async def approve(event):
        if event.kind == "permission.requested":
            await approvals.resolve(event.payload["approval_id"], "approved")

    host._event_bus.subscribe(approve)
    tool = tools.StructuredTool.from_function(body)
    task = None
    try:
        await host.require_governance_admission()
        if not cancel:
            release.set()
        task = await host.start_native(
            job.id,
            family="langchain",
            target=tool,
            invoke=partial(tool.invoke if synchronous else tool.ainvoke, {}),
            synchronous=synchronous,
        )
        await asyncio.wait_for(entered.wait(), 3)
        if cancel:
            task.cancel()
            task.cancel()
            await asyncio.wait({task}, timeout=0.1)
            assert not task.done() and not finished.is_set()
            assert host._native_executions.for_job(job.id)
            assert job.id in host._legacy_runner_slots
            assert not any(event.kind == "execution.native.released" for event in await events_for(host, job.id))
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            assert await task == "actual-result"
        assert calls == ["body"] and finished.is_set()
        assert (await governance._ledger.job_totals(job.id)).executed_calls == 1
        assert host._native_executions.for_job(job.id) == ()
    finally:
        release.set()
        if task is not None and not task.done():
            task.cancel()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        loop.set_default_executor(previous)
        pool.shutdown(wait=True)
