"""Actual Runtime/authority, HTTP loopback and stdin single-use gate conformance."""

import asyncio
import json
import os
import socket
import sys
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest
import uvicorn
from dishka import Provider, Scope, from_context, make_async_container
from dishka.integrations.fastapi import setup_dishka
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from backend.api.external_gate import router
from backend.app_factory import _configure_middleware
from backend.models.domain import ApprovalResolution, StateConflictError
from backend.models.schemas.external_gate import (
    ExternalGateConsume,
    ExternalGatePhase,
    ExternalGatePreflight,
    ExternalGateSourceRequest,
    ExternalGateSourceResponse,
)
from backend.services.runtime.external_gate import ExternalGateAuthenticationError
from backend.services.runtime.service import RuntimeService
from backend.tests.native_gates.qualify_runtime_integration import composed_runtime as composed_runtime
from backend.tests.native_gates.qualify_runtime_integration import events_for
from backend.tests.unit.test_native_bridge_runtime import local_flowise as local_flowise
from backend.tests.unit.test_native_bridge_runtime import target_for

if TYPE_CHECKING:
    from backend.models.events import SessionEvent


class GateProvider(Provider):
    scope = Scope.APP
    runtime = from_context(provides=RuntimeService)


@pytest.fixture
async def gate_server(composed_runtime):
    host = composed_runtime[0]
    app = FastAPI()
    _configure_middleware(app, dev=False, tunnel_origin=None, password="qualification-only", remote=True)
    app.include_router(router, prefix="/api")

    @app.exception_handler(StateConflictError)
    async def conflict(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    container = make_async_container(GateProvider(), context={RuntimeService: host})
    setup_dishka(container, app)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    async with asyncio.timeout(5):
        while not server.started:
            await asyncio.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)
        listener.close()
        await container.close()


def action(request_id="request", value="local"):
    return ExternalGatePreflight(
        request_id=request_id,
        tool_name="external.local",
        tool_input={"value": value},
        session_id="native-hook-session",
        tool_call_id="native-hook-call",
    )


async def run_owner(composed_runtime, local_flowise, scenario, *, auto_external=True):
    host, approvals, _, create = composed_runtime
    job = await create()
    target = target_for(local_flowise[0])

    async def approve(event):
        if event.kind == "permission.requested" and (auto_external or not event.metadata.correlation_id):
            await approvals.resolve(event.payload["approval_id"], ApprovalResolution.approved)

    host._event_bus.subscribe(approve)

    async def invoke():
        (execution,) = host._native_executions.for_job(job.id)
        issued = await host.external_gates.issue(
            job.id,
            ExternalGateSourceRequest(
                execution_token=execution.identity.execution_token,
                external_session_id="native-hook-session",
            ),
        )
        result = await scenario(job, issued)
        if result:
            await asyncio.to_thread(target.client.predict, "flow", "local")
        return result

    return job, await host.start_native(job.id, family="flowise", target=target, invoke=invoke)


async def test_same_request_in_process_http_and_stdin_consumes_only_once(composed_runtime, local_flowise, gate_server):
    host, approvals, governance, _ = composed_runtime
    pending: asyncio.Queue[SessionEvent] = asyncio.Queue()
    ready = asyncio.Event()
    outcomes: list[str] = []
    issued_source = None

    async def capture(event):
        if event.kind == "permission.requested" and event.metadata.correlation_id:
            await pending.put(event)

    host._event_bus.subscribe(capture)

    async def scenario(job, issued):
        nonlocal issued_source
        issued_source = issued
        request = action("same-request")
        auth = {"Authorization": f"Bearer {issued.credential}"}
        source = await host.external_gates.authenticate(issued.source_id, auth["Authorization"])
        await host.external_gates.preflight(source, request)
        async with httpx.AsyncClient(base_url=gate_server, headers=auth, trust_env=False, timeout=10) as client:
            status = await client.get(f"/api/gate/{issued.source_id}/requests/same-request")
            assert status.status_code == 200 and status.json()["phase"] == "pending"
            direct = asyncio.create_task(host.external_gates.before_call(issued.source_id, issued.credential, request))
            http = asyncio.create_task(
                client.post(
                    f"/api/gate/{issued.source_id}/invoke",
                    json={
                        "request_id": "same-request",
                        "tool_name": request.tool_name,
                        "tool_input": request.tool_input,
                        "session_id": request.session_id,
                        "tool_call_id": request.tool_call_id,
                    },
                )
            )
            process = await asyncio.create_subprocess_exec(
                str(Path(sys.executable).with_name("cpl")),
                "gate",
                "--stdin",
                "--format",
                "json",
                "--url",
                gate_server,
                "--source",
                issued.source_id,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "CPL_GATE_TOKEN": issued.credential},
            )
            stdin = json.dumps(
                {
                    "request_id": "same-request",
                    "tool": request.tool_name,
                    "input": request.tool_input,
                    "session_id": request.session_id,
                    "tool_call_id": request.tool_call_id,
                }
            ).encode()
            cli = asyncio.create_task(process.communicate(stdin))
            ready.set()
            direct_result, http_result, (stdout, stderr) = await asyncio.gather(direct, http, cli)
            assert http_result.status_code == 200, http_result.text
            assert process.returncode == 0, stderr.decode()
            cli_result = json.loads(stdout)
            if cli_result["decision"] == "deny":
                assert cli_result["reason"] == "single_use", stderr.decode()
            outcomes.extend((direct_result.decision, http_result.json()["decision"], cli_result["decision"]))
        return outcomes.count("allow") == 1

    job, owner = await run_owner(composed_runtime, local_flowise, scenario, auto_external=False)
    event = await asyncio.wait_for(pending.get(), 5)
    await asyncio.wait_for(ready.wait(), 5)
    assert not owner.done() and local_flowise[1] == []
    assert issued_source is not None
    state = await host.external_gates.repo.state(issued_source.source_id, "same-request")
    assert state.approval_id == event.payload["approval_id"]
    await approvals.resolve(event.payload["approval_id"], ApprovalResolution.approved)
    assert await asyncio.wait_for(owner, 15)
    assert outcomes.count("allow") == 1 and len(local_flowise[1]) == 1
    assert (await governance._ledger.job_totals(job.id)).executed_calls == 1
    consumed = [event for event in await events_for(host, job.id) if event.kind == "external.gate.consumed"]
    assert len(consumed) == 1


async def test_real_stdin_relay_waits_then_allows_exact_action(composed_runtime, local_flowise, gate_server):
    host, approvals, _, _ = composed_runtime
    requested: asyncio.Queue[SessionEvent] = asyncio.Queue()
    process = None

    async def capture(event):
        if event.kind == "permission.requested" and event.metadata.correlation_id:
            await requested.put(event)

    host._event_bus.subscribe(capture)

    async def scenario(job, issued):
        nonlocal process
        process = await asyncio.create_subprocess_exec(
            str(Path(sys.executable).with_name("cpl")),
            "gate",
            "--stdin",
            "--format",
            "json",
            "--url",
            gate_server,
            "--source",
            issued.source_id,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "CPL_GATE_TOKEN": issued.credential},
        )
        stdout, stderr = await process.communicate(
            json.dumps(
                {
                    "request_id": "stdin-allow",
                    "tool_name": "external.local",
                    "tool_input": {"value": "local"},
                    "session_id": "native-hook-session",
                }
            ).encode()
        )
        assert process.returncode == 0, stderr.decode()
        assert json.loads(stdout) == {"decision": "allow"}, stderr.decode()
        return True

    _, owner = await run_owner(composed_runtime, local_flowise, scenario, auto_external=False)
    event = await asyncio.wait_for(requested.get(), 10)
    assert process is not None and process.returncode is None and not owner.done()
    assert local_flowise[1] == []
    await approvals.resolve(event.payload["approval_id"], ApprovalResolution.approved)
    assert await asyncio.wait_for(owner, 10)
    assert len(local_flowise[1]) == 1


async def test_gate_credentials_do_not_grant_operator_or_sibling_authority(
    composed_runtime, local_flowise, gate_server
):
    host, _, _, _ = composed_runtime

    async def scenario(job, issued):
        (execution,) = host._native_executions.for_job(job.id)
        async with httpx.AsyncClient(base_url=gate_server, trust_env=False) as operator:
            denied = await operator.post(
                f"/api/jobs/{job.id}/external-gates",
                json={"executionToken": execution.identity.execution_token},
            )
            assert denied.status_code == 401
            login = await operator.post("/api/auth/login", json={"password": "qualification-only"})
            assert login.status_code == 200
            invalid = await operator.post(
                f"/api/jobs/{job.id}/external-gates",
                json={"executionToken": "not-an-owner"},
            )
            assert invalid.status_code == 409
            other = await operator.post(
                f"/api/jobs/{job.id}/external-gates",
                json={"executionToken": execution.identity.execution_token, "externalSessionId": "native-hook-session"},
            )
            assert other.status_code == 200, other.text
        async with httpx.AsyncClient(base_url=gate_server, trust_env=False) as external:
            for headers in ({}, {"Authorization": f"Bearer {issued.credential}"}):
                denied = await external.post(
                    f"/api/gate/{other.json()['sourceId']}/requests",
                    headers=headers,
                    content=b"not even JSON",
                )
                assert denied.status_code == 401
            denied = await external.post(
                f"/api/jobs/{job.id}/external-gates",
                json={"executionToken": execution.identity.execution_token},
                headers={"Authorization": f"Bearer {issued.credential}"},
            )
            assert denied.status_code == 401
            allowed = await external.post(
                f"/api/gate/{issued.source_id}/invoke",
                headers={"Authorization": f"Bearer {issued.credential}"},
                json={"tool": "external.local", "input": {"value": "local"}, "session_id": "native-hook-session"},
            )
            assert allowed.status_code == 200 and allowed.json()["decision"] == "allow"
        return True

    _, owner = await run_owner(composed_runtime, local_flowise, scenario)
    assert await asyncio.wait_for(owner, 10)
    assert len(local_flowise[1]) == 1


@pytest.mark.parametrize("change", ["reject", "mutate", "cancel", "cancel-api", "detach", "disconnect"])
async def test_external_pending_decisions_retire_without_execution(
    change, composed_runtime, local_flowise, gate_server
):
    host, approvals, _, _ = composed_runtime
    requested: asyncio.Queue[SessionEvent] = asyncio.Queue()
    issued_queue: asyncio.Queue[ExternalGateSourceResponse] = asyncio.Queue()

    async def capture(event):
        if event.kind == "permission.requested" and event.metadata.correlation_id:
            await requested.put(event)

    host._event_bus.subscribe(capture)

    async def scenario(job, issued):
        await issued_queue.put(issued)
        async with httpx.AsyncClient(base_url=gate_server, trust_env=False, timeout=10) as client:
            auth = {"Authorization": f"Bearer {issued.credential}"}
            response = await client.post(
                f"/api/gate/{issued.source_id}/requests", headers=auth, json=action().model_dump(by_alias=True)
            )
            assert response.status_code == 200
            if change == "disconnect":
                endpoint = httpx.URL(gate_server)
                reader, writer = await asyncio.open_connection(endpoint.host, endpoint.port)
                writer.write(
                    (
                        f"GET /api/gate/{issued.source_id}/requests/request?waitSeconds=25 HTTP/1.1\r\n"
                        f"Host: {endpoint.host}:{endpoint.port}\r\nAuthorization: Bearer {issued.credential}\r\n\r\n"
                    ).encode()
                )
                await writer.drain()
                await asyncio.sleep(0.1)
                writer.close()
                await writer.wait_closed()
                async with asyncio.timeout(5):
                    while (
                        await host.external_gates.repo.state(issued.source_id, "request")
                    ).phase == ExternalGatePhase.pending:
                        await asyncio.sleep(0.01)
                return False
            while True:
                status = await client.get(
                    f"/api/gate/{issued.source_id}/requests/request", headers=auth, params={"waitSeconds": 1}
                )
                if status.status_code != 200 or status.json()["phase"] != "pending":
                    return False

    job, owner = await run_owner(composed_runtime, local_flowise, scenario, auto_external=False)
    issued = await asyncio.wait_for(issued_queue.get(), 5)
    event = await asyncio.wait_for(requested.get(), 5)
    if change == "reject":
        await approvals.resolve(event.payload["approval_id"], ApprovalResolution.rejected)
    elif change == "mutate":
        source = await host.external_gates.authenticate(issued.source_id, f"Bearer {issued.credential}")
        with pytest.raises(StateConflictError, match="action changed"):
            await host.external_gates.preflight(source, action(value="changed"))
    elif change == "cancel":
        owner.cancel()
    elif change == "cancel-api":
        await host.cancel_job(job.id)
    elif change == "detach":
        (execution,) = host._native_executions.for_job(job.id)
        assert await host.detach_native(execution.identity) == "cancel-requested"
    await asyncio.wait_for(asyncio.gather(owner, return_exceptions=True), 10)
    assert local_flowise[1] == []
    approval = await approvals.get(event.payload["approval_id"])
    assert approval.resolution == ApprovalResolution.rejected
    state = await host.external_gates.repo.state(issued.source_id, "request")
    assert state.phase in {ExternalGatePhase.denied, ExternalGatePhase.retired}


@pytest.mark.parametrize(
    "dialect,code,key",
    [
        ("json", 0, "decision"),
        ("claude-code", 0, "hookSpecificOutput"),
        ("copilot-cli", 1, "permissionDecision"),
        ("codex", 2, "hookSpecificOutput"),
        ("gemini", 2, "decision"),
        ("cline", 0, "cancel"),
        ("cursor", 2, "permission"),
        ("amazon-q", 2, None),
        ("opencode", 2, "decision"),
        ("openhands", 2, "decision"),
    ],
)
def test_stdin_dialects_fail_closed_without_binding(dialect, code, key, monkeypatch):
    from click.testing import CliRunner

    from backend.services.external_gate_cli import gate

    monkeypatch.delenv("CPL_GATE_TOKEN", raising=False)
    result = CliRunner().invoke(gate, ["--stdin", "--agent", dialect], input='{"tool_name":"local","tool_input":{}}')
    assert result.exit_code == code
    if key is not None:
        payload = json.loads(result.stdout)
        assert key in payload
        assert payload != {} and payload.get("decision") != "allow"
    assert "failed closed" in result.stderr


async def test_sync_callback_uses_worker_and_reports_are_observational(composed_runtime, local_flowise):
    host, _, governance, _ = composed_runtime

    async def scenario(job, issued):
        with pytest.raises(RuntimeError, match="worker"):
            host.external_gates.before_call_sync(issued.source_id, issued.credential, action())
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="external-gate-caller") as worker:
            decision = await asyncio.get_running_loop().run_in_executor(
                worker, partial(host.external_gates.before_call_sync, issued.source_id, issued.credential, action())
            )
        assert decision.decision == "allow"
        from backend.models.schemas.external_gate import ExternalGateReport

        source = await host.external_gates.authenticate(issued.source_id, f"Bearer {issued.credential}")
        await host.external_gates.reported(
            source,
            "request",
            ExternalGateReport(
                report_id="reported-result",
                phase="completed",
                details={"claimed_calls": 999},
            ),
        )
        assert (await governance._ledger.job_totals(job.id)).executed_calls == 0
        return True

    job, owner = await run_owner(composed_runtime, local_flowise, scenario)
    assert await asyncio.wait_for(owner, 10)
    events = await events_for(host, job.id)
    reported = next(event for event in events if event.kind == "external.gate.execution_reported")
    assert reported.metadata.namespace[0] == "observation-source"
    assert reported.metadata.integration_binding_id.startswith("gate-")
    assert (await governance._ledger.job_totals(job.id)).executed_calls == 1


async def test_allowed_decision_mutation_and_sibling_source_cannot_release_it(composed_runtime, local_flowise):
    host, _, _, _ = composed_runtime

    async def scenario(job, issued):
        source = await host.external_gates.authenticate(issued.source_id, f"Bearer {issued.credential}")
        state = await host.external_gates.preflight(source, action())
        while state.phase == ExternalGatePhase.pending:
            state = await host.external_gates.wait(source, "request", 1)
        assert state.phase == ExternalGatePhase.allowed
        changed = ExternalGateConsume(**action(value="changed").model_dump(), action_digest=state.action_digest)
        denied = await host.external_gates.consume(source, "request", changed)
        assert denied.phase == ExternalGatePhase.retired and denied.decision == "deny"
        original = ExternalGateConsume(**action().model_dump(), action_digest=state.action_digest)
        again = await host.external_gates.consume(source, "request", original)
        assert again.decision == "deny"
        assert (await host.external_gates.preflight(source, action())).phase == ExternalGatePhase.retired
        return False

    _, owner = await run_owner(composed_runtime, local_flowise, scenario)
    assert await owner is False
    assert local_flowise[1] == []


async def test_stale_epoch_retires_durable_request_and_exact_approval(composed_runtime, local_flowise):
    from backend.services.adapters.adapter_registry import AdapterRegistry
    from backend.services.events.event_bus import EventBus
    from backend.services.git.git_service import GitService
    from backend.services.job.approval_service import ApprovalService

    host, approvals, _, _ = composed_runtime
    issued_queue: asyncio.Queue[ExternalGateSourceResponse] = asyncio.Queue()
    pending: asyncio.Queue[SessionEvent] = asyncio.Queue()

    async def capture(event):
        if event.kind == "permission.requested" and event.metadata.correlation_id:
            await pending.put(event)

    host._event_bus.subscribe(capture)

    async def scenario(job, issued):
        await issued_queue.put(issued)
        decision = await host.external_gates.before_call(issued.source_id, issued.credential, action())
        return decision.decision == "allow"

    job, owner = await run_owner(composed_runtime, local_flowise, scenario, auto_external=False)
    issued = await asyncio.wait_for(issued_queue.get(), 5)
    approval_event = await asyncio.wait_for(pending.get(), 5)
    ordinary = await approvals.create_request(job.id, "Unrelated operator decision", "not-json")
    restarted = RuntimeService(
        host._session_factory,
        EventBus(),
        AdapterRegistry(config=host._config),
        host._config,
        approval_service=ApprovalService(host._session_factory),
        git_service=GitService(host._config),
    )
    try:
        assert restarted.execution_epoch != host.execution_epoch
        await restarted.external_gates.reconcile_stale()
        state = await restarted.external_gates.repo.state(issued.source_id, "request")
        assert state.phase == ExternalGatePhase.retired
        assert (await approvals.get(approval_event.payload["approval_id"])).resolution == ApprovalResolution.rejected
        assert (await approvals.get(ordinary.id)).resolution is None
        with pytest.raises(ExternalGateAuthenticationError):
            await restarted.external_gates.authenticate(issued.source_id, f"Bearer {issued.credential}")
    finally:
        owner.cancel()
        await asyncio.gather(owner, return_exceptions=True)
        await restarted.shutdown()
    assert local_flowise[1] == []


async def test_disabled_profile_and_observation_ingest_cannot_acquire_gate_authority(composed_runtime, local_flowise):
    from backend.models.schemas.integrations import ObservationRecordBatch
    from backend.persistence.integration_repo import IntegrationRepository
    from backend.persistence.job_repo import JobRepository
    from backend.services.adapters.integration_policy import IntegrationDisabledError
    from backend.services.ingest.record_service import ObservationRecords

    host, _, _, _ = composed_runtime

    async def scenario(job, issued):
        async with host._session_factory() as session:
            records = ObservationRecords(IntegrationRepository(session), JobRepository(session), host._event_bus)
            with pytest.raises(StateConflictError):
                await records.append(
                    issued.source_id,
                    ObservationRecordBatch(
                        source_key="foreign-ingest", start_index=0, records=[{"type": "permission.allowed"}]
                    ),
                )
        host._config.runtime.copilot_only = True
        try:
            with pytest.raises(IntegrationDisabledError):
                await host.external_gates.issue(
                    job.id,
                    ExternalGateSourceRequest(
                        execution_token=issued.execution_token,
                    ),
                )
            with pytest.raises(ExternalGateAuthenticationError):
                await host.external_gates.authenticate(issued.source_id, f"Bearer {issued.credential}")
        finally:
            host._config.runtime.copilot_only = False
        with pytest.raises(ExternalGateAuthenticationError):
            await host.external_gates.authenticate(issued.source_id, f"Bearer {issued.credential}")
        return False

    _, owner = await run_owner(composed_runtime, local_flowise, scenario)
    assert await owner is False
    assert local_flowise[1] == []
