"""Read-only sharing and operator revocation through the current API wiring."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request

from backend.api.share import stream_shared_events
from backend.services.auth import cf_access
from backend.services.auth import middleware as auth
from backend.services.sharing.share_service import ShareService


@pytest.fixture(autouse=True)
def isolate_auth():
    auth._reset_for_testing()
    cf_access.reset()
    yield
    auth._reset_for_testing()
    cf_access.reset()


async def test_create_and_read_existing_routes(client, seed_job):
    job_id = await seed_job()
    response = await client.post(f"/api/jobs/{job_id}/share")
    assert response.status_code == 200
    link = response.json()
    assert link["jobId"] == job_id
    assert link["url"] == f"http://test/shared/{link['token']}"
    for endpoint in ("job", "snapshot", "telemetry"):
        response = await client.get(f"/api/share/{link['token']}/{endpoint}")
        assert response.status_code == 200, response.text


async def test_create_missing_job_does_not_issue_link(client):
    assert (await client.post("/api/jobs/missing/share")).status_code == 404


@pytest.mark.parametrize("endpoint", ["job", "snapshot", "telemetry", "events"])
@pytest.mark.parametrize("state", ["unknown", "expired", "revoked"])
async def test_invalid_credentials_rejected_on_every_read_route(client, seed_job, session_factory, endpoint, state):
    job_id = await seed_job()
    svc = ShareService(session_factory, ttl=-1 if state == "expired" else 60)
    token = "unknown"
    if state != "unknown":
        token = (await svc.create_token(job_id)).token
    if state == "revoked":
        assert (await client.delete(f"/api/jobs/{job_id}/share/{token}")).status_code == 204
    response = await client.get(f"/api/share/{token}/{endpoint}")
    assert response.status_code == 404
    assert response.json() == {"detail": "Invalid or expired share link"}


async def test_revoke_is_idempotent_and_job_scoped(client, seed_job):
    job_id = await seed_job()
    token = (await client.post(f"/api/jobs/{job_id}/share")).json()["token"]
    assert (await client.delete(f"/api/jobs/other/share/{token}")).status_code == 204
    assert (await client.get(f"/api/share/{token}/job")).status_code == 200
    for _ in range(2):
        response = await client.delete(f"/api/jobs/{job_id}/share/{token}")
        assert response.status_code == 204
        assert response.content == b""
    assert (await client.get(f"/api/share/{token}/job")).status_code == 404


async def test_password_bypass_is_read_only_and_revocation_requires_operator(app, seed_job, session_factory):
    job_id = await seed_job()
    token = (await ShareService(session_factory).create_token(job_id)).token
    app.state.auth_policy = auth.configure_auth(remote=True, access_only=False, password="operator")
    app.middleware("http")(auth.auth_middleware)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get(f"/api/share/{token}/job")).status_code == 200
        for method, path in (
            ("POST", f"/api/jobs/{job_id}/share"),
            ("DELETE", f"/api/jobs/{job_id}/share/{token}"),
            ("POST", f"/api/jobs/{job_id}/cancel"),
            ("POST", f"/api/share/{token}/job"),
            ("GET", f"/api/jobs/{job_id}"),
            ("GET", "/api/preview/job-1/3000"),
        ):
            response = await client.request(method, path, headers={"Authorization": f"Bearer {token}"})
            assert response.status_code == 401
        client.cookies.set(auth.COOKIE_NAME, auth._create_session_token())
        assert (await client.delete(f"/api/jobs/{job_id}/share/{token}")).status_code == 204
        assert (await client.get(f"/api/share/{token}/job")).status_code == 404


@pytest.mark.parametrize("jwt_valid", [False, True])
async def test_access_only_share_reads_still_require_identity(app, seed_job, session_factory, jwt_valid):
    job_id = await seed_job()
    token = (await ShareService(session_factory).create_token(job_id)).token
    cf_access.configure(team="example", aud="audience", eager=False)
    app.state.auth_policy = auth.configure_auth(remote=True, access_only=True, password=None)
    app.middleware("http")(auth.auth_middleware)
    with patch.object(cf_access, "verify_token", return_value=jwt_valid):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get(f"/api/share/{token}/job")).status_code == 401
            assert (await client.get(f"/shared/{token}")).status_code == 401
            response = await client.get(
                f"/api/share/{token}/job", headers={"cf-access-jwt-assertion": "identity-fixture"}
            )
            assert response.status_code == (200 if jwt_valid else 401)


@pytest.mark.parametrize("invalidation", ["revoke", "expire"])
async def test_open_stream_stops_before_delivering_queued_events(seed_job, session_factory, sse_manager, invalidation):
    job_id = await seed_job()
    svc = ShareService(session_factory)
    entry = await svc.create_token(job_id)
    request = Request({"type": "http", "headers": [(b"last-event-id", b"42")]})
    replay = AsyncMock(side_effect=lambda conn, *_: conn.send("event: replay\ndata: {}\n\n"))
    with patch.object(sse_manager, "replay_from_factory", replay):
        response = await stream_shared_events(entry.token, request, svc, sse_manager, session_factory)
        iterator = aiter(response.body_iterator)
        assert await anext(iterator) == "event: session_heartbeat\ndata: {}\n\n"
        replay.assert_awaited_once()
        if invalidation == "revoke":
            await ShareService(session_factory).revoke(entry.token, job_id)
        with patch("backend.services.sharing.share_service.datetime") as clock:
            clock.now.return_value = (
                entry.expires_at + timedelta(seconds=1) if invalidation == "expire" else datetime.now(UTC)
            )
            with pytest.raises(StopAsyncIteration):
                await anext(iterator)
        assert sse_manager.connection_count == 0


async def test_idle_stream_rechecks_revocation_before_heartbeat(seed_job, session_factory, sse_manager):
    job_id = await seed_job()
    svc = ShareService(session_factory)
    entry = await svc.create_token(job_id)
    request = Request({"type": "http", "headers": []})
    response = await stream_shared_events(entry.token, request, svc, sse_manager, session_factory)
    iterator = aiter(response.body_iterator)
    assert await anext(iterator) == "event: session_heartbeat\ndata: {}\n\n"
    connection = sse_manager._connections[0]
    assert connection.job_id == job_id
    connection.send("event: progress\ndata: {}\n\n")
    assert await anext(iterator) == "event: progress\ndata: {}\n\n"
    await ShareService(session_factory).revoke(entry.token, job_id)
    with (
        patch.object(connection.queue, "get", AsyncMock(side_effect=TimeoutError)),
        pytest.raises(StopAsyncIteration),
    ):
        await anext(iterator)
    assert sse_manager.connection_count == 0
