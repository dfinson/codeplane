import asyncio
import json
import socket

import httpx
import pytest
import uvicorn

from backend.app_factory import _configure_middleware
from backend.config import CPLConfig
from backend.persistence.event_repo import EventRepository
from backend.persistence.integration_repo import IntegrationRepository
from backend.persistence.project_repo import ProjectRepository
from backend.services.auth import middleware as auth
from backend.tests.unit.test_otlp_admission import otlp


@pytest.fixture
async def otlp_http(app, session_factory):
    auth._reset_for_testing()
    _configure_middleware(app, dev=False, tunnel_origin="https://fixture.example.test", password="fixture-only")
    config = await app.state.dishka_container.get(CPLConfig)
    config.runtime.copilot_only = False
    async with session_factory() as session:
        await ProjectRepository(session).create("observations", "Observations", [])
        await session.commit()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if task.done():
                    await task
                await asyncio.sleep(0.01)
        async with (
            httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", trust_env=False) as operator,
            httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", trust_env=False) as source,
        ):
            login = await operator.post("/api/auth/login", json={"password": "fixture-only"})
            assert login.status_code == 200, login.text
            yield operator, source, config
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 5)
        listener.close()
        auth._reset_for_testing()


async def register(operator, name):
    created = await operator.post(
        "/api/integrations/observations",
        json={
            "bindingId": name,
            "projectId": "observations",
            "formatId": "otlp_json",
            "externalSessionId": f"{name}/producer/session",
        },
    )
    assert created.status_code == 201, created.text
    issued = await operator.post(f"/api/integrations/{name}/ingest-credential", json={"enabled": True})
    assert issued.status_code == 200 and issued.headers["cache-control"] == "no-store"
    return created.json(), issued.json()["token"]


async def test_real_http_otlp_scoped_admission_replay_revocation_and_history(
    otlp_http,
    session_factory,
    mock_runtime_service,
    mock_git_service,
):
    operator, source, config = otlp_http
    job, token = await register(operator, "first")
    _, other_token = await register(operator, "second")
    headers = {"x-codeplane-ingest-token": token}
    endpoint = "/api/integrations/first/traces"
    accepted = await source.post(endpoint, json=otlp(1), headers=headers)
    assert accepted.status_code == 200 and accepted.json() == {}, accepted.text
    assert (await source.post(endpoint, json=otlp(1), headers=headers)).status_code == 200
    assert (await source.post(endpoint, json=otlp(1, 2), headers=headers)).status_code == 200
    for path, method in (
        ("/api/integrations/first/events", "GET"),
        ("/api/integrations/first/traces", "GET"),
        ("/api/integrations/first/ingest-credential", "POST"),
        (f"/api/jobs/{job['id']}/pause", "POST"),
        ("/api/events", "GET"),
        ("/mcp", "POST"),
    ):
        response = await source.request(method, path, headers=headers)
        assert response.status_code == 401, (path, response.text)
    wrong = await source.post(endpoint, json=otlp(3), headers={"x-codeplane-ingest-token": other_token})
    assert wrong.status_code == 403
    config.runtime.copilot_only = True
    assert (await source.post(endpoint, json=otlp(3), headers=headers)).status_code == 403
    config.runtime.copilot_only = False
    binary = await source.post(endpoint, content=b"", headers={**headers, "content-type": "application/octet-stream"})
    assert binary.status_code == 415
    revoked = await operator.delete("/api/integrations/first/ingest-credential")
    assert revoked.status_code == 204
    assert (await source.post(endpoint, json=otlp(3), headers=headers)).status_code == 403
    history = await operator.get("/api/integrations/first/events")
    assert history.status_code == 200
    assert len([event for event in history.json()["events"] if event["kind"] == "llm.call.completed"]) == 2
    async with session_factory() as session:
        binding = await IntegrationRepository(session).get("first")
        rows = await EventRepository(session).list_after(0, job_id=job["id"])
        assert token not in json.dumps(binding.config)
        assert all(token not in row.event.model_dump_json() for row in rows)
    mock_runtime_service.start_or_enqueue.assert_not_awaited()
    mock_git_service.create_worktree.assert_not_awaited()


async def test_real_http_invalid_batch_body_limit_and_default_discovery(otlp_http):
    operator, source, config = otlp_http
    _, token = await register(operator, "bounded")
    headers = {"x-codeplane-ingest-token": token}
    malformed = otlp(1, 2)
    malformed["resourceSpans"][0]["scopeSpans"][0]["spans"][1]["traceId"] = "invalid"
    assert (await source.post("/api/integrations/bounded/traces", json=malformed, headers=headers)).status_code == 400
    response = await source.post(
        "/api/integrations/bounded/traces",
        content=b" " * (1024 * 1024 + 1),
        headers={**headers, "content-type": "application/json"},
    )
    assert response.status_code == 413
    assert (await operator.get("/api/integrations/bounded/events")).json()["events"] == []
    config.runtime.copilot_only = True
    catalog = (await operator.get("/api/integrations/profiles")).json()
    assert len(catalog["profiles"]) == 32
    assert all(not row["liveReady"] for row in catalog["profiles"])
    enabled = [row["profileId"] for row in catalog["profiles"] if row["admissionEnabled"]]
    assert enabled == ["copilot-sdk", "copilot-cli"]
