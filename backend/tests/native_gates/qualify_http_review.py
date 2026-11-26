"""Actual framework response streaming and pre-existing host execution contexts."""

import asyncio
import io
import threading
from importlib.metadata import PackageNotFoundError, version
from typing import Any
from wsgiref.util import setup_testing_defaults

import pytest

from backend.services.adapters.native_gates import selection
from backend.services.adapters.native_gates.observation_scope import native_execution_context
from backend.services.adapters.native_gates.readiness import UnsupportedNativeHookError
from backend.services.adapters.profile_bridges import http as bridge
from backend.services.auth import middleware as auth
from backend.tests.native_gates.qualify_http_profile import FRAMEWORKS, application, approvals, request
from backend.tests.native_gates.qualify_http_profile import isolate_sessions as isolate_sessions
from backend.tests.native_gates.qualify_runtime_integration import composed_runtime as composed_runtime
from backend.tests.native_gates.qualify_runtime_integration import events_for


def headers(target):
    token = auth._create_session_token()
    return {
        **target.request_headers(),
        "cookie": f"{auth.COOKIE_NAME}={token}",
        "x-request-id": "request-stream",
        "content-type": "application/json",
    }


@pytest.mark.parametrize("framework", FRAMEWORKS)
@pytest.mark.parametrize("drift", [False, True])
async def test_only_the_selected_http_framework_needs_its_qualified_release(
    framework, drift, composed_runtime, monkeypatch
):
    host, service, _, create = composed_runtime
    job = await create()
    approvals(host, service)

    def selected(package):
        if package in FRAMEWORKS:
            if package != framework:
                raise PackageNotFoundError(package)
            if drift:
                return "0.0"
        return version(package)

    monkeypatch.setattr(selection, "version", selected)
    monkeypatch.setattr(bridge, "version", selected)
    async with application(framework) as (target, token, _, calls, *_):
        task = await host.start_native(job.id, family="http", target=target, invoke=lambda: request(target, token))
        if drift:
            with pytest.raises(UnsupportedNativeHookError, match="qualified HTTP release"):
                await task
            assert calls == []
        else:
            assert (await task).status_code == 200
            assert calls == ["body"]


@pytest.mark.parametrize("framework", FRAMEWORKS)
async def test_actual_response_larger_than_telemetry_limit_is_preserved(framework, composed_runtime):
    host, service, _, create = composed_runtime
    job = await create()
    approvals(host, service)
    size = 1024 * 1024 + 1
    async with application(framework, response_size=size) as (target, token, _, calls, *_):
        task = await host.start_native(job.id, family="http", target=target, invoke=lambda: request(target, token))
        response = await task
        assert response.status_code == 200 and response.content == b"x" * size
        assert calls == ["body"]
    completed = next(event for event in await events_for(host, job.id) if event.kind == "task.completed")
    assert completed.payload["response_bytes"] == size
    assert len(completed.model_dump_json()) < 4096


@pytest.mark.parametrize("framework", FRAMEWORKS)
async def test_authenticated_request_on_host_task_created_before_attachment(framework, composed_runtime):
    host, service, _, create = composed_runtime
    job = await create()
    approvals(host, service)
    start = asyncio.Event()
    async with application(framework) as (target, token, _, calls, *_):

        async def host_request():
            original = native_execution_context.get()
            await start.wait()
            try:
                return await request(target, token)
            finally:
                assert native_execution_context.get() == original

        external = asyncio.create_task(host_request())

        async def invoke():
            start.set()
            return await external

        task = await host.start_native(job.id, family="http", target=target, invoke=invoke)
        try:
            assert (await task).status_code == 200
            assert calls == ["body"]
        finally:
            start.set()
            await asyncio.gather(task, external, return_exceptions=True)
    events = await events_for(host, job.id)
    assert any(event.kind == "task.completed" for event in events)
    assert any(event.kind == "execution.native.tool_result" for event in events)


@pytest.mark.parametrize("framework", ["flask", "django"])
@pytest.mark.parametrize("mode", ["complete", "close", "cancel"])
async def test_wsgi_first_chunk_and_lifetime_through_exhaustion_or_close(framework, mode, composed_runtime):
    host, service, _, create = composed_runtime
    job = await create()
    approvals(host, service)
    loop = asyncio.get_running_loop()
    start, consume_rest = threading.Event(), threading.Event()
    first_chunk, owner_finish = asyncio.Event(), asyncio.Event()
    async with application(framework, stream=True, block=True) as (target, _, _, calls, _, release, thread_release):

        def server_request():
            original = native_execution_context.get()
            assert start.wait(10)
            environ: dict[str, Any] = {}
            setup_testing_defaults(environ)
            environ.update(
                REQUEST_METHOD="POST",
                PATH_INFO="/run",
                CONTENT_LENGTH="2",
                CONTENT_TYPE="application/json",
                **{"wsgi.input": io.BytesIO(b"{}")},
            )
            for name, value in headers(target).items():
                environ["HTTP_" + name.upper().replace("-", "_")] = value
            statuses = []
            response = target.wsgi(environ, lambda status, values, exc_info=None: statuses.append(status))
            try:
                iterator = iter(response)
                first = next(iterator)
                loop.call_soon_threadsafe(first_chunk.set)
                assert first == b"first"
                assert consume_rest.wait(10)
                if mode == "complete":
                    assert list(iterator) == [b"last"]
                assert statuses == ["200 OK"]
            finally:
                response.close()
                assert native_execution_context.get() == original

        external = asyncio.create_task(asyncio.to_thread(server_request))

        async def invoke():
            start.set()
            await owner_finish.wait()

        task = await host.start_native(job.id, family="http", target=target, invoke=invoke)
        try:
            await asyncio.wait_for(first_chunk.wait(), 2)
            assert "stream_closed" not in calls and not external.done()
            if mode == "complete":
                thread_release.set()
                consume_rest.set()
                await external
                owner_finish.set()
            else:
                if mode == "cancel":
                    (execution,) = host._native_executions.for_job(job.id)
                    await host.detach_native(execution.identity)
                owner_finish.set()
                await asyncio.wait({task}, timeout=0.05)
                assert not task.done(), "Runtime released an unclosed caller-owned response"
                consume_rest.set()
                await external
            await task
            assert calls == ["body", "stream_closed"]
        finally:
            start.set()
            release.set()
            thread_release.set()
            consume_rest.set()
            owner_finish.set()
            await asyncio.gather(task, external, return_exceptions=True)
    events = await events_for(host, job.id)
    receipt = next(event for event in events if event.kind == "execution.native.tool_result")
    assert receipt.payload["started"] is True
    assert receipt.payload["succeeded"] is (mode == "complete")
    assert not host._native_executions.for_job(job.id)


@pytest.mark.parametrize("cancel", [False, True])
async def test_asgi_first_chunk_is_delivered_before_completion_or_cancellation(cancel, composed_runtime):
    host, service, _, create = composed_runtime
    job = await create()
    approvals(host, service)
    first_chunk = asyncio.Event()
    sent = []
    async with application("fastapi", stream=True, block=True) as (target, _, _, calls, _, release, _):

        async def receive():
            await asyncio.Event().wait()

        async def send(message):
            sent.append(message)
            if message.get("body") == b"first":
                first_chunk.set()

        async def invoke():
            incoming = True

            async def request_body():
                nonlocal incoming
                if incoming:
                    incoming = False
                    return {"type": "http.request", "body": b"{}", "more_body": False}
                return await receive()

            scope = {
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": "2.4"},
                "method": "POST",
                "path": "/run",
                "raw_path": b"/run",
                "query_string": b"",
                "scheme": "http",
                "server": ("testserver", 80),
                "client": ("127.0.0.1", 1),
                "headers": [(name.encode(), value.encode()) for name, value in headers(target).items()],
            }
            return await target.asgi(scope, request_body, send)

        task = await host.start_native(job.id, family="http", target=target, invoke=invoke)
        try:
            await asyncio.wait_for(first_chunk.wait(), 2)
            assert not task.done() and "stream_closed" not in calls
            if cancel:
                (execution,) = host._native_executions.for_job(job.id)
                await host.detach_native(execution.identity)
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                release.set()
                await task
            assert calls == ["body", "stream_closed"]
            assert sent[0]["status"] == 200
            assert b"".join(item.get("body", b"") for item in sent) == (b"first" if cancel else b"firstlast")
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
