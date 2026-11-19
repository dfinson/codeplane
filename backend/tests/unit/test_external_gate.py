"""Actual Runtime/authority, HTTP loopback and stdin single-use gate conformance."""

import asyncio
import json
import os
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
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
    async with serving_gate(composed_runtime[0]) as url:
        yield url


@asynccontextmanager
async def serving_gate(host):
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
        try:
            await host.external_gates.close()
        finally:
            server.should_exit = True
            try:
                await asyncio.wait_for(task, 10)
            finally:
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


async def test_server_teardown_retires_pending_stdin_without_touching_ordinary_approvals(
    composed_runtime, local_flowise, monkeypatch
):
    host, approvals, _, create = composed_runtime
    requested: asyncio.Queue[SessionEvent] = asyncio.Queue()
    polling = asyncio.Event()
    process = None
    original_wait = host.external_gates.wait

    async def observe_wait(*args, **kwargs):
        polling.set()
        return await original_wait(*args, **kwargs)

    monkeypatch.setattr(host.external_gates, "wait", observe_wait)

    async def capture(event):
        if event.kind == "permission.requested" and event.metadata.correlation_id:
            await requested.put(event)

    host._event_bus.subscribe(capture)
    async with serving_gate(host) as url:

        async def scenario(job, issued):
            nonlocal process
            process = await asyncio.create_subprocess_exec(
                str(Path(sys.executable).with_name("cpl")),
                "gate",
                "--stdin",
                "--format",
                "json",
                "--url",
                url,
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
                        "request_id": "stdin-server-close",
                        "tool_name": "external.local",
                        "tool_input": {"value": "local"},
                        "session_id": "native-hook-session",
                    }
                ).encode()
            )
            assert process.returncode == 0, stderr.decode()
            assert json.loads(stdout)["decision"] == "deny"
            return False

        _, owner = await run_owner(composed_runtime, local_flowise, scenario, auto_external=False)
        event = await asyncio.wait_for(requested.get(), 10)
        ordinary_job = await create("ordinary-job")
        ordinary = await approvals.create_request(ordinary_job.id, "Unrelated operator decision", "not-json")
        await asyncio.wait_for(polling.wait(), 5)
        assert process is not None and process.returncode is None and not owner.done()
    assert await asyncio.wait_for(owner, 10) is False
    assert process.returncode == 0 and not local_flowise[1]
    assert (await approvals.get(event.payload["approval_id"])).resolution == ApprovalResolution.rejected
    assert (await approvals.get(ordinary.id)).resolution is None


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


@pytest.mark.parametrize("tool", ["Write", "Bash", "external.local", "Read"])
async def test_native_hook_facts_reach_real_policy_without_trusting_read_hints(
    tool, composed_runtime, local_flowise, tmp_path, monkeypatch
):
    from backend.models.action_assessment import ActionAssessment
    from backend.services.action_policy.classifier import Preset, RepoPolicy
    from backend.services.adapters.agent_adapter import CompletionResult

    host, approvals, _, _ = composed_runtime
    (tmp_path / "tracked.txt").write_text("original", encoding="utf-8")
    (tmp_path / ".codeplane.yml").write_text("protected_paths: [tracked.txt]\n", encoding="utf-8")
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "tracked.txt"], check=True)
    assessed = []

    class LocalAssessment:
        async def complete(self, prompt, *, model, **kwargs):
            fields = json.loads(prompt)
            raw: dict[str, object] = {
                "schema_version": "cp-action-v1",
                "mechanism": "shell" if tool == "Bash" else "file" if tool in {"Write", "Read"} else "sdk",
                "effect": "mutating" if tool == "Write" else "read_only",
                "scope": "workspace",
                "network": "none",
                "reversibility": "git" if tool == "Write" else "not_applicable",
                "capabilities": [],
                "structure": [],
                "unknowns": [],
            }
            raw["evidence"] = [
                {"field": field, "value": raw[field], "source": "arguments", "start": 0, "end": 1}
                for field in ("effect", "scope", "network", "reversibility")
            ]
            text = json.dumps(raw)
            assert ActionAssessment.model_validate_json(text, context=fields).complete
            assessed.append(fields)
            return CompletionResult(text=text, model=model)

    provider = LocalAssessment()
    monkeypatch.setattr(host._adapter_registry, "get_completion_provider", lambda _: provider)
    host._config.runtime.utility_model = "inert-local-assessment"
    requested = []

    async def reject(event):
        if event.kind == "permission.requested" and event.metadata.correlation_id:
            requested.append(event)
            await approvals.resolve(event.payload["approval_id"], ApprovalResolution.rejected)

    host._event_bus.subscribe(reject)

    async def scenario(job, issued):
        host._policy_routers[job.id].update_policy(RepoPolicy(preset=Preset.autonomous))
        request = ExternalGatePreflight(
            request_id="normalized-hook",
            tool_name=tool,
            tool_input=(
                {"file_path": "tracked.txt", "content": "updated", "read_only": True}
                if tool in {"Write", "Read"}
                else {"command": "git status", "read_only": True}
            ),
            session_id="native-hook-session",
        )
        decision = await host.external_gates.before_call(issued.source_id, issued.credential, request)
        assert decision.decision == ("deny" if tool == "Write" else "allow")
        if tool == "Write":
            assert not assessed, "Configured protected writes must hard-gate before utility assessment"
            assert len(requested) == 1 and requested[0].payload["requires_explicit_approval"]
            proposed = json.loads(requested[0].payload["proposed_action"])
            assert proposed["reason_code"] == "hard_gate"
            assert "permission kind: write" in proposed["fields"]["context"]
        else:
            assert len(assessed) == 1, "External Read must not acquire the managed SDK's trusted-read bypass"
            assert assessed[0]["command"] == ("git status" if tool == "Bash" else "")
            kind = {"Bash": "shell", "Read": "read"}.get(tool, "custom-tool")
            assert f"permission kind: {kind}" in assessed[0]["context"]
            assert not requested
        return False

    _, owner = await run_owner(composed_runtime, local_flowise, scenario, auto_external=False)
    assert await asyncio.wait_for(owner, 15) is False
    assert (tmp_path / "tracked.txt").read_text(encoding="utf-8") == "original"
    assert not local_flowise[1]


async def test_policy_replacement_during_terminal_write_cannot_release_or_replay_allow(
    composed_runtime, local_flowise, monkeypatch
):
    from dataclasses import replace

    host, _, _, _ = composed_runtime

    async def scenario(job, issued):
        source = await host.external_gates.authenticate(issued.source_id, f"Bearer {issued.credential}")
        request = action("replace-policy")
        state = await host.external_gates.preflight(source, request)
        while state.phase == ExternalGatePhase.pending:
            state = await host.external_gates.wait(source, request.request_id, 1)
        assert state.phase == ExternalGatePhase.allowed
        consume = ExternalGateConsume(**request.model_dump(), action_digest=state.action_digest)
        finish = host.external_gates.repo.finish

        async def change_policy(*args, **kwargs):
            result = await finish(*args, **kwargs)
            source.owner.policy.update_policy(replace(source.owner.policy._policy))
            return result

        monkeypatch.setattr(host.external_gates.repo, "finish", change_policy)
        decision = await host.external_gates.consume(source, request.request_id, consume)
        assert decision.phase == ExternalGatePhase.consumed and decision.decision == "deny"
        assert (await host.external_gates.consume(source, request.request_id, consume)).decision == "deny"
        assert (await host.external_gates.status(source, request.request_id)).decision == "deny"
        return False

    job, owner = await run_owner(composed_runtime, local_flowise, scenario)
    assert await asyncio.wait_for(owner, 15) is False
    assert len([event for event in await events_for(host, job.id) if event.kind == "external.gate.consumed"]) == 1
    assert not local_flowise[1]


@pytest.mark.parametrize("transport", ["in-process", "http"])
async def test_exact_maximum_action_can_be_consumed_with_transport_digest(
    transport, composed_runtime, local_flowise, gate_server
):
    from pydantic import ValidationError

    host, _, _, _ = composed_runtime
    fields = action("maximum-action", "").model_dump()
    overhead = len(json.dumps(fields, allow_nan=False, ensure_ascii=False).encode())
    fields["tool_input"] = {"value": "x" * (65536 - overhead)}
    request = ExternalGatePreflight(**fields)
    assert len(json.dumps(request.model_dump(), allow_nan=False, ensure_ascii=False).encode()) == 65536
    with pytest.raises(ValidationError, match="64 KiB"):
        ExternalGatePreflight(**{**fields, "tool_input": {"value": fields["tool_input"]["value"] + "x"}})

    async def scenario(job, issued):
        if transport == "in-process":
            result = await host.external_gates.before_call(issued.source_id, issued.credential, request)
            assert result.decision == "allow"
        else:
            auth = {"Authorization": f"Bearer {issued.credential}"}
            source = await host.external_gates.authenticate(issued.source_id, auth["Authorization"])
            state = await host.external_gates.preflight(source, request)
            while state.phase == ExternalGatePhase.pending:
                state = await host.external_gates.wait(source, request.request_id, 1)
            async with httpx.AsyncClient(base_url=gate_server, headers=auth, trust_env=False) as client:
                result = await client.post(
                    f"/api/gate/{issued.source_id}/requests/{request.request_id}/consume",
                    json={**request.model_dump(by_alias=True), "actionDigest": state.action_digest},
                )
                assert result.status_code == 200, result.text
                assert result.json()["decision"] == "allow"
        return False

    _, owner = await run_owner(composed_runtime, local_flowise, scenario)
    assert await asyncio.wait_for(owner, 15) is False
