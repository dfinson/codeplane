"""Real persisted Runtime admission through the fixed Flowise request bridge."""

import asyncio
import contextlib
import json
import socket
import threading
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import PackageNotFoundError

import pytest

from backend.models.domain import ApprovalResolution, JobState, StateConflictError
from backend.models.events import EventKind, SessionEvent
from backend.services.adapters.integration_policy import IntegrationDisabledError
from backend.services.adapters.native_gates import selection
from backend.services.adapters.native_gates.readiness import UnsupportedNativeHookError
from backend.services.adapters.profile_bridges.flowise import FlowiseTarget
from backend.tests.native_gates.qualify_runtime_integration import composed_runtime as composed_runtime
from backend.tests.native_gates.qualify_runtime_integration import events_for


@pytest.fixture
def local_flowise(monkeypatch):
    calls = []
    received, release = threading.Event(), threading.Event()
    release.set()
    original_connect = socket.socket.connect

    def local_only(sock, address):
        if isinstance(address, tuple) and address[0] not in {"127.0.0.1", "::1"}:
            raise AssertionError("Bridge qualification only permits loopback connections")
        return original_connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", local_only)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            calls.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            received.set()
            assert release.wait(10)
            body = json.dumps({"sessionId": "native-local-session", "text": "local result"}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls, received, release
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def target_for(url):
    from agentmesh.integrations.flowise import FlowiseNodeIdentity, FlowiseTrustPolicy, TrustGatedFlowiseClient

    return FlowiseTarget(
        TrustGatedFlowiseClient(
            url,
            FlowiseNodeIdentity.generate("runtime-fixture"),
            policy=FlowiseTrustPolicy(require_https=False),
        )
    )


async def pending_bridge(composed_runtime, target):
    host, _, _, create = composed_runtime
    job = await create()
    requested: asyncio.Queue[SessionEvent] = asyncio.Queue()

    async def on_event(event):
        if event.kind == EventKind.approval_requested:
            await requested.put(event)

    host._event_bus.subscribe(on_event)
    task = await host.start_native(
        job.id,
        family="flowise",
        target=target,
        invoke=partial(target.client.predict, "flow", "question"),
        synchronous=True,
    )
    event = await asyncio.wait_for(requested.get(), 10)
    return job, task, event


async def test_fixed_bridge_reuses_real_admission_pin_owner_and_receipts(composed_runtime, local_flowise):
    host, approvals, governance, _ = composed_runtime
    url, calls, _, _ = local_flowise
    target = target_for(url)
    original = target.client._make_request
    job, task, event = await pending_bridge(composed_runtime, target)
    (execution,) = host._native_executions.for_job(job.id)
    assert execution.owner is task
    assert host._tasks[job.id] is task
    assert calls == [] and not task.done()
    assert host._adapter_registry._adapters == {} and job.id not in host._agent_sessions
    proposed = json.loads(event.payload["proposed_action"])
    assert proposed["execution"]["execution_token"] == execution.identity.execution_token
    assert proposed["admission_id"] == execution.pin.admission_id
    await approvals.resolve(event.payload["approval_id"], ApprovalResolution.approved)
    result = await asyncio.wait_for(task, 10)
    assert result["sessionId"] == "native-local-session"
    assert len(calls) == 1
    assert target.client._make_request == original
    assert (await host._get_job(job.id)).state == JobState.review
    records = await events_for(host, job.id)
    receipt = next(record for record in records if record.kind == "execution.native.tool_result")
    assert receipt.payload["family"] == "flowise"
    assert (await governance._ledger.job_totals(job.id)).executed_calls == 1
    await governance.observe(receipt)
    await governance.observe(receipt.model_copy(update={"id": "late-unstored-bridge-receipt"}))
    assert (await governance._ledger.job_totals(job.id)).executed_calls == 1


async def test_bridge_disable_nonowner_and_nonrunnable_profiles_are_refused(composed_runtime, local_flowise):
    host, _, _, create = composed_runtime
    url, calls, _, _ = local_flowise
    job = await create()
    target = target_for(url)
    original = target.client._make_request
    arguments = dict(family="flowise", target=target, invoke=lambda: None, synchronous=True)
    host._config.runtime.copilot_only = True
    with pytest.raises(IntegrationDisabledError):
        await host.start_native(job.id, **arguments)
    host._config.runtime.copilot_only = False
    with pytest.raises(StateConflictError, match="owning Runtime task"):
        await host.invoke_native(job.id, **arguments)
    for profile in ("otel", "ai_card", "native"):
        with pytest.raises(UnsupportedNativeHookError):
            await host.start_native(job.id, **(arguments | {"family": profile}))
    assert calls == []
    assert target.client._make_request == original
    assert host._tasks == {}
    assert (await host._get_job(job.id)).state == JobState.queued


async def test_bridge_detach_retires_exact_pending_decision_before_transport(composed_runtime, local_flowise):
    host, approvals, governance, _ = composed_runtime
    url, calls, _, _ = local_flowise
    target = target_for(url)
    job, task, event = await pending_bridge(composed_runtime, target)
    (execution,) = host._native_executions.for_job(job.id)
    assert await host.detach_native(execution.identity) == "cancel-requested"
    await asyncio.gather(task, return_exceptions=True)
    assert calls == []
    assert (await approvals.get(event.payload["approval_id"])).resolution == ApprovalResolution.rejected
    assert (await governance._ledger.job_totals(job.id)).executed_calls == 0
    with pytest.raises(StateConflictError):
        await host.detach_native(execution.identity)


async def test_bridge_missing_dependency_is_not_admitted(composed_runtime, local_flowise, monkeypatch):
    host, _, _, create = composed_runtime
    job = await create()
    target = target_for(local_flowise[0])
    original = selection.version

    def missing(package):
        if package == "httpx":
            raise PackageNotFoundError(package)
        return original(package)

    monkeypatch.setattr(selection, "version", missing)
    with pytest.raises(UnsupportedNativeHookError, match="Missing optional dependency: httpx"):
        await host.start_native(
            job.id,
            family="flowise",
            target=target,
            invoke=partial(target.client.predict, "flow", "question"),
            synchronous=True,
        )
    assert host._tasks == {} and local_flowise[1] == []
    assert (await host._get_job(job.id)).state == JobState.queued


async def test_other_policy_pin_cannot_invoke_a_native_bridge(composed_runtime, local_flowise):
    host, _, _, create = composed_runtime
    current = next(iter(host._execution_contracts._available.values()))
    other = current.model_copy(
        update={
            "id": "other-admission",
            "policy": current.policy.model_copy(update={"id": "other-policy"}),
        }
    )
    host.configure_execution_contracts(
        available={current.id: current, other.id: other},
        selected_for_new=other.id,
    )
    job = await create()
    target = target_for(local_flowise[0])
    with pytest.raises(StateConflictError, match="cp-policy-v1"):
        await host.start_native(
            job.id,
            family="flowise",
            target=target,
            invoke=partial(target.client.predict, "flow", "question"),
            synchronous=True,
        )
    assert local_flowise[1] == [] and host._adapter_registry._adapters == {}


async def test_bridge_cancel_retains_actual_transport_until_response(composed_runtime, local_flowise):
    host, approvals, governance, _ = composed_runtime
    url, calls, received, release = local_flowise
    release.clear()
    target = target_for(url)
    job, task, event = await pending_bridge(composed_runtime, target)
    await approvals.resolve(event.payload["approval_id"], ApprovalResolution.approved)
    assert await asyncio.to_thread(received.wait, 5)
    try:
        await host.cancel(job.id)
        await asyncio.wait({task}, timeout=0.1)
        assert not task.done()
        assert host._native_executions.for_job(job.id)
        assert job.id in host._legacy_runner_slots
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert len(calls) == 1
    assert host._native_executions.for_job(job.id) == ()
    assert (await governance._ledger.job_totals(job.id)).executed_calls == 1
