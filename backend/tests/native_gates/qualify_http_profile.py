"""Real authenticated framework requests; no listener, provider call or fake identity."""

import asyncio
import threading
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import ModuleType

import httpx
import pytest

from backend.services.adapters.native_gates._optional import load_sdk
from backend.services.adapters.native_gates.lifecycle import retain_task
from backend.services.adapters.profile_bridges.http import HttpIdentity, HttpTarget, signed_request
from backend.services.auth import middleware as auth
from backend.tests.native_gates.qualify_runtime_integration import composed_runtime as composed_runtime
from backend.tests.native_gates.qualify_runtime_integration import events_for

FRAMEWORKS = ("fastapi", "flask", "django")


@pytest.fixture(autouse=True)
def isolate_sessions(monkeypatch):
    monkeypatch.setattr(auth, "_session_tokens", {})
    monkeypatch.setattr(auth, "_internal_token", None)
    monkeypatch.setattr(auth, "_auth_policy", None)


@asynccontextmanager
async def application(framework, *, block=False, actor=False, synchronous=False, response_size=None, stream=False):
    calls = []
    entered, release = asyncio.Event(), asyncio.Event()
    thread_release = threading.Event()
    loop = asyncio.get_running_loop()
    if not block:
        release.set()
        thread_release.set()
    controls = {("POST", "/run"): "run"}
    override = None

    def stream_body():
        try:
            yield b"first"
            assert thread_release.wait(10)
            yield b"last"
        finally:
            calls.append("stream_closed")

    async def async_stream_body():
        try:
            yield b"first"
            await release.wait()
            yield b"last"
        finally:
            calls.append("stream_closed")

    if framework == "fastapi":
        sdk = load_sdk("fastapi")
        app = sdk.FastAPI()

        if synchronous:

            @app.post("/run")
            def handler():
                calls.append("body")
                loop.call_soon_threadsafe(entered.set)
                assert thread_release.wait(10)
                return {"result": "local"}
        else:

            @app.post("/run")
            async def handler():
                calls.append("body")
                entered.set()
                if stream:
                    return load_sdk("starlette.responses").StreamingResponse(async_stream_body())
                await release.wait()
                if response_size is not None:
                    return load_sdk("starlette.responses").Response(b"x" * response_size)
                return {"result": "local"}
    elif framework == "flask":
        sdk = load_sdk("flask")
        app = sdk.Flask("native-http-qualification")

        @app.post("/run")
        def handler():
            calls.append("body")
            loop.call_soon_threadsafe(entered.set)
            if stream:
                return sdk.Response(stream_body())
            assert thread_release.wait(10)
            if response_size is not None:
                return sdk.Response(b"x" * response_size)
            return {"result": "local"}
    else:
        settings = load_sdk("django.conf").settings
        if not settings.configured:
            settings.configure(SECRET_KEY="inert-qualification-only", ALLOWED_HOSTS=["testserver"], MIDDLEWARE=[])
            load_sdk("django").setup()
        urls = ModuleType("native_http_qualification_urls")

        def handler(request):
            calls.append("body")
            loop.call_soon_threadsafe(entered.set)
            if stream:
                return load_sdk("django.http").StreamingHttpResponse(stream_body())
            assert thread_release.wait(10)
            if response_size is not None:
                return load_sdk("django.http").HttpResponse(b"x" * response_size)
            return load_sdk("django.http").JsonResponse({"result": "local"})

        urls.urlpatterns = [load_sdk("django.urls").path("run", handler)]
        override = load_sdk("django.test.utils").override_settings(ROOT_URLCONF=urls)
        override.enable()
        app = load_sdk("django.core.handlers.wsgi").WSGIHandler()
    identity = None
    pins = None
    if actor:
        sdk_identity = load_sdk("agentmesh.identity")
        cards = load_sdk("agentmesh.integrations.ai_card")
        identity = sdk_identity.AgentIdentity.create(
            name="local-http", sponsor="fixture@example.test", capabilities=["run"]
        )
        card = cards.AICard.from_identity(
            identity, services=[cards.AICardService(protocol="http", url="http://testserver/run")]
        )
        card.expires_at = datetime.now(UTC) + timedelta(hours=1)
        card.sign(identity)
        pins = HttpIdentity(
            card.model_dump_json().encode(), "project", str(identity.did), identity.public_key, "http://testserver/run"
        )
    target = HttpTarget(framework, app, controls, identity=pins)
    token = auth._create_session_token()
    try:
        yield target, token, identity, calls, entered, release, thread_release
    finally:
        release.set()
        thread_release.set()
        if override is not None:
            override.disable()


async def request(target, token, identity=None, *, change=None, foreign_credential=None):
    body = b'{"value":"local"}'
    headers = {
        **target.request_headers(),
        "cookie": f"{auth.COOKIE_NAME}={token}",
        "x-request-id": "request-local",
        "content-type": "application/json",
    }
    if identity is not None:
        headers["x-agent-did"] = str(identity.did)
        headers["x-agent-signature"] = identity.sign(signed_request(headers, "POST", "/run", body))
    path = "/run"
    if change == "unauthenticated":
        del headers["cookie"]
    elif change == "ingest":
        del headers["cookie"]
        headers["x-codeplane-ingest-token"] = token
    elif change == "ingest-with-operator":
        headers["x-codeplane-ingest-token"] = token
    elif change == "binding":
        headers["x-codeplane-request-binding"] = "other-binding"
    elif change == "epoch":
        headers["x-codeplane-request-epoch"] = "other-epoch"
    elif change == "credential":
        headers["x-codeplane-request-token"] = "not-this-binding"
    elif change == "self-asserted":
        headers["x-agent-did"] = "did:claimed"
        headers["x-agent-capabilities"] = "all"
    elif change == "signature":
        headers["x-agent-signature"] = "invalid"
    elif change == "body":
        body = b'{"value":"tampered"}'
    elif change == "undeclared":
        path = "/undeclared"
    elif change == "query":
        path += "?changed=true"
    if foreign_credential is not None:
        headers["x-codeplane-request-token"] = foreign_credential
    if target.framework == "fastapi":
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=target.asgi), base_url="http://testserver"
        ) as client:
            return await client.post(path, headers=headers, content=body)

    def invoke():
        with httpx.Client(transport=httpx.WSGITransport(app=target.wsgi), base_url="http://testserver") as client:
            return client.post(path, headers=headers, content=body)

    return await retain_task(asyncio.create_task(asyncio.to_thread(invoke)))


def approvals(host, service, *, allow=True):
    seen = []

    async def decide(event):
        if event.kind == "permission.requested":
            seen.append(event)
            await service.resolve(event.payload["approval_id"], "approved" if allow else "rejected")

    host._event_bus.subscribe(decide)
    return seen


@pytest.mark.parametrize("framework", FRAMEWORKS)
async def test_real_authenticated_handler_is_gated_and_persisted(framework, composed_runtime):
    host, service, governance, create = composed_runtime
    job = await create()
    decisions = approvals(host, service)
    async with application(framework, actor=True) as (target, token, identity, calls, *_):
        task = await host.start_native(
            job.id, family="http", target=target, invoke=lambda: request(target, token, identity)
        )
        response = await task
        assert response.status_code == 200 and response.json() == {"result": "local"}
        assert calls == ["body"] and len(decisions) == 1
        actor_id = str(identity.did)
    events = await events_for(host, job.id)
    completed = next(event for event in events if event.kind == "task.completed")
    assert completed.payload["native_request_id"] == "request-local"
    assert completed.payload["actor_did"] == actor_id and completed.session_id == job.id
    assert all(token not in event.model_dump_json() for event in events)
    assert not any(event.kind == "execution.native.usage" for event in events)
    assert (await governance._ledger.job_totals(job.id)).executed_calls == 1


@pytest.mark.parametrize("framework", FRAMEWORKS)
@pytest.mark.parametrize(
    "change",
    [
        "unauthenticated",
        "ingest",
        "ingest-with-operator",
        "binding",
        "epoch",
        "credential",
        "self-asserted",
        "undeclared",
    ],
)
async def test_authentication_and_binding_fail_before_preflight(framework, change, composed_runtime):
    host, service, governance, create = composed_runtime
    job = await create()
    decisions = approvals(host, service)
    async with application(framework) as (target, token, identity, calls, *_):
        task = await host.start_native(
            job.id, family="http", target=target, invoke=lambda: request(target, token, identity, change=change)
        )
        assert (await task).status_code == 403
        assert calls == [] and decisions == []
    assert (await governance._ledger.job_totals(job.id)).executed_calls == 0


@pytest.mark.parametrize("framework", FRAMEWORKS)
@pytest.mark.parametrize("change", ["signature", "body", "query"])
async def test_pinned_actor_request_signature_is_checked(framework, change, composed_runtime):
    host, service, _, create = composed_runtime
    job = await create()
    decisions = approvals(host, service)
    async with application(framework, actor=True) as (target, token, identity, calls, *_):
        task = await host.start_native(
            job.id, family="http", target=target, invoke=lambda: request(target, token, identity, change=change)
        )
        assert (await task).status_code == 403 and not calls and not decisions


@pytest.mark.parametrize("framework", FRAMEWORKS)
async def test_policy_denial_prevents_original_handler(framework, composed_runtime):
    host, service, governance, create = composed_runtime
    job = await create()
    decisions = approvals(host, service, allow=False)
    async with application(framework) as (target, token, identity, calls, *_):
        task = await host.start_native(job.id, family="http", target=target, invoke=lambda: request(target, token))
        assert (await task).status_code == 403 and calls == [] and len(decisions) == 1
    assert (await governance._ledger.job_totals(job.id)).executed_calls == 0


@pytest.mark.parametrize("framework", FRAMEWORKS)
async def test_stop_waiting_does_not_claim_unconfirmed_external_completion(framework, composed_runtime):
    host, service, _, create = composed_runtime
    job = await create()
    approvals(host, service)
    async with application(framework, block=True) as (target, token, identity, calls, entered, release, thread_release):
        task = await host.start_native(job.id, family="http", target=target, invoke=lambda: request(target, token))
        try:
            await asyncio.wait_for(entered.wait(), 10)
            (execution,) = host._native_executions.for_job(job.id)
            assert await host.detach_native(execution.identity) == "cancel-requested"
            if framework != "fastapi":
                await asyncio.wait({task}, timeout=0.1)
                assert not task.done() and host._native_executions.for_job(job.id)
            else:
                await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 5)
        finally:
            release.set()
            thread_release.set()
            await asyncio.gather(task, return_exceptions=True)
        assert calls == ["body"]
    assert not host._native_executions.for_job(job.id)
    if framework == "fastapi":
        assert any(event.payload.get("remote_outcome") == "unknown" for event in await events_for(host, job.id))


async def test_fastapi_sync_handler_stays_owned_after_local_wait_is_cancelled(composed_runtime):
    host, service, _, create = composed_runtime
    job = await create()
    approvals(host, service)
    async with application("fastapi", block=True, synchronous=True) as (
        target,
        token,
        identity,
        calls,
        entered,
        release,
        thread_release,
    ):
        task = await host.start_native(job.id, family="http", target=target, invoke=lambda: request(target, token))
        try:
            await asyncio.wait_for(entered.wait(), 10)
            (execution,) = host._native_executions.for_job(job.id)
            await host.detach_native(execution.identity)
            await asyncio.wait({task}, timeout=0.1)
            assert not task.done() and host._native_executions.for_job(job.id)
        finally:
            thread_release.set()
            release.set()
            await asyncio.gather(task, return_exceptions=True)
        assert calls == ["body"]
    assert not host._native_executions.for_job(job.id)


@pytest.mark.parametrize("framework", FRAMEWORKS)
async def test_real_credential_from_another_binding_is_rejected(framework, composed_runtime):
    host, service, governance, create = composed_runtime
    first, second = await create("first"), await create("second")
    decisions = approvals(host, service)
    ready, finish = asyncio.Event(), asyncio.Event()
    foreign = {}
    async with application(framework) as (first_target, first_token, *_):

        async def hold():
            foreign.update(first_target.request_headers())
            ready.set()
            await finish.wait()

        holder = await host.start_native(first.id, family="http", target=first_target, invoke=hold)
        try:
            await asyncio.wait_for(ready.wait(), 5)
            async with application(framework) as (target, token, identity, calls, *_):
                task = await host.start_native(
                    second.id,
                    family="http",
                    target=target,
                    invoke=lambda: request(target, token, foreign_credential=foreign["x-codeplane-request-token"]),
                )
                assert (await task).status_code == 403
                assert not calls and not decisions
        finally:
            finish.set()
            await holder
    assert (await governance._ledger.job_totals(second.id)).executed_calls == 0


async def test_invalid_pinned_card_rejects_attachment_before_handler_or_preflight(composed_runtime):
    host, service, _, create = composed_runtime
    job = await create()
    decisions = approvals(host, service)
    async with application("fastapi", actor=True) as (target, token, identity, calls, *_):
        other = load_sdk("agentmesh.identity").AgentIdentity.create(name="other", sponsor="fixture@example.test")
        target.identity = replace(target.identity, public_key=other.public_key)
        route = next(route for route in target.app.routes if route.path == "/run")
        original = route.dependant.call
        task = await host.start_native(
            job.id, family="http", target=target, invoke=lambda: request(target, token, identity)
        )
        with pytest.raises(ValueError, match="pinned signing key"):
            await task
        assert calls == [] and decisions == []
        assert route.dependant.call is original


@pytest.mark.parametrize("framework", FRAMEWORKS)
async def test_cached_transport_callback_cannot_outlive_its_binding(framework, composed_runtime):
    host, service, _, create = composed_runtime
    job = await create()
    decisions = approvals(host, service)
    retained = {}
    async with application(framework) as (target, token, identity, calls, *_):

        async def invoke():
            retained["app"] = target.asgi if framework == "fastapi" else target.wsgi
            retained["headers"] = {
                **target.request_headers(),
                "cookie": f"{auth.COOKIE_NAME}={token}",
                "x-request-id": "late-request",
            }

        await (await host.start_native(job.id, family="http", target=target, invoke=invoke))
        if framework == "fastapi":
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=retained["app"]), base_url="http://testserver"
            ) as client:
                response = await client.post("/run", headers=retained["headers"])
        else:

            def late():
                with httpx.Client(
                    transport=httpx.WSGITransport(app=retained["app"]), base_url="http://testserver"
                ) as client:
                    return client.post("/run", headers=retained["headers"])

            response = await asyncio.to_thread(late)
        assert response.status_code == 403 and not calls and not decisions
