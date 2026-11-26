import asyncio
import sys

import pytest

from backend.services.adapters.native_gates import dependency_readiness
from backend.tests.native_gates.producers import MODES, make_producer


@pytest.mark.parametrize("mode", MODES)
async def test_allow_once_deny_before_call_and_exact_teardown(mode, binding_factory):
    family = mode.split("-")[0]
    assert dependency_readiness(family).ready
    p = make_producer(mode)
    original = vars(p.target).copy()
    gates, decision, _ = binding_factory(family)
    attachment = await gates.attach(p.target)
    assert await gates.attach(p.target) is attachment
    result = await p.invoke()
    assert result is not None
    assert len(p.calls) == len(decision.invocations) == 1
    invocation = decision.invocations[0]
    assert invocation.metadata.execution_token == "token"
    assert invocation.metadata.execution_epoch == "epoch"
    assert invocation.job_id == "job-1"
    decision.allow = False
    await denied(p)
    assert len(p.calls) == 1
    assert len(decision.invocations) == 2
    gates.detach(p.target)
    gates.detach(p.target)
    assert not attachment.active
    assert "_codeplane_native_gate" not in vars(p.target)
    for name in (
        "_run",
        "_arun",
        "run",
        "arun",
        "_get_toolset",
        "execute_tool_call",
        "_wrap_tool_call",
        "_awrap_tool_call",
    ):
        if name in original:
            assert vars(p.target)[name] is original[name]
        else:
            assert name not in vars(p.target)
    await p.invoke()
    assert len(p.calls) == 2
    assert len(decision.invocations) == 2
    await gates.attach(p.target)
    await denied(p)
    assert len(p.calls) == 2
    assert len(decision.invocations) == 3


async def denied(p):
    try:
        result = await p.invoke()
    except Exception as exc:
        # Native exception type is checked separately, never a successful tool result.
        assert "Denied by CodePlane" in str(exc)
    else:
        assert "Denied by CodePlane" in str(result) or "blocked by hook" in str(result)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("invalidation", ["detach", "cancel", "ownership", "replace"])
async def test_pending_decision_is_awaited_and_late_allow_cannot_execute(mode, invalidation, binding_factory):
    family = mode.split("-")[0]
    p = make_producer(mode)
    gates, decision, live = binding_factory(family)
    decision.pending = True
    await gates.attach(p.target)
    task = asyncio.create_task(denied(p))
    try:
        await asyncio.wait_for(decision.entered.wait(), 5)
        assert not task.done()
        assert not p.calls
        if invalidation == "detach":
            gates.detach(p.target)
        elif invalidation == "cancel":
            assert gates.cancel() == "cancel-requested"
        elif invalidation == "ownership":
            live["current"] = False
        else:
            gates.detach(p.target)
            replacement, replacement_decision, _ = binding_factory(family, token="replacement")
            await replacement.attach(p.target)
            gates.detach(p.target)
        decision.release.set()
        await asyncio.wait_for(task, 5)
        assert not p.calls
        if invalidation == "replace":
            await p.invoke()
            assert len(p.calls) == len(replacement_decision.invocations) == 1
    finally:
        decision.release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("mode", MODES)
async def test_pending_allow_and_sibling_isolation(mode, binding_factory):
    family = mode.split("-")[0]
    left, right = make_producer(mode), make_producer(mode)
    a, first, _ = binding_factory(family)
    b, second, _ = binding_factory(family, token="sibling")
    await a.attach(left.target)
    await b.attach(right.target)
    first.pending = True
    task = asyncio.create_task(left.invoke())
    try:
        await asyncio.wait_for(first.entered.wait(), 5)
        await right.invoke()
        assert not left.calls and len(right.calls) == 1
        b.detach(right.target)
        first.release.set()
        await asyncio.wait_for(task, 5)
        assert len(left.calls) == len(first.invocations) == len(second.invocations) == 1
        await left.invoke()
        assert len(left.calls) == len(first.invocations) == 2
    finally:
        first.release.set()
        await asyncio.gather(task, return_exceptions=True)


def test_no_traceforge_runtime_loaded():
    assert not any(name == "traceforge" or name.startswith("traceforge.") for name in sys.modules)
    assert not {"onnxruntime", "torch", "sklearn"}.intersection(sys.modules)
