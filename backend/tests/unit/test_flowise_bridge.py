import asyncio
import contextlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from backend.models.session_event import EventMetadata
from backend.services.adapters.contracts import ExecutionIdentity
from backend.services.adapters.native_gates import GateAttachment, NativeBinding
from backend.services.adapters.native_gates.binding import NativeGateDeniedError
from backend.services.adapters.profile_bridges.flowise import FlowiseDeliveryUnknownError, FlowiseTarget, install


@pytest.fixture
def flowise_peer():
    calls = []
    behavior = {"loss": False, "oversize": False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            calls.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            if behavior["loss"]:
                self.connection.shutdown(2)
                self.connection.close()
                return
            body = json.dumps(
                {
                    "sessionId": "remote-session",
                    "text": "x" * (1024 * 1024) if behavior["oversize"] else "local result",
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls, behavior
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


async def test_real_upstream_predict_upsert_are_gated_and_report_native_response(flowise_peer):
    from agentmesh.integrations.flowise import FlowiseNodeIdentity, FlowiseTrustPolicy, TrustGatedFlowiseClient

    url, calls, behavior = flowise_peer
    entered, release = asyncio.Event(), asyncio.Event()

    class Policy:
        execution_epoch = "epoch"
        allowed = False

        async def authorize_permission(self, invocation):
            entered.set()
            await release.wait()
            return self.allowed

    policy = Policy()
    observations, receipts = [], []

    async def current():
        return True

    async def report(call, started, succeeded):
        receipts.append((call, started, succeeded))

    client = TrustGatedFlowiseClient(
        url,
        FlowiseNodeIdentity.generate("fixture"),
        policy=FlowiseTrustPolicy(require_https=False),
    )
    target = FlowiseTarget(client, observations.append)
    binding = NativeBinding(
        family="flowise",
        execution=ExecutionIdentity("job", "binding", "epoch", "token"),
        policy=policy,
        metadata=EventMetadata(execution_epoch="epoch", execution_token="token"),
        workspace="/fixture",
        protected_paths=(),
        is_current=current,
        on_result=report,
    )
    attachment = GateAttachment(binding, target)
    original = client._make_request
    install(attachment, target)
    try:
        pending = asyncio.create_task(asyncio.to_thread(client.predict, "flow", "question"))
        await asyncio.wait_for(entered.wait(), 2)
        assert calls == []
        release.set()
        with pytest.raises(NativeGateDeniedError):
            await pending
        assert calls == [] and receipts == []
        policy.allowed = True
        result = await asyncio.to_thread(client.predict, "flow", "question", {"sessionId": "remote-session"})
        assert result["sessionId"] == "remote-session"
        await asyncio.to_thread(client.upsert, "flow", [{"text": "document"}])
        assert [path for path, _ in calls] == ["/api/v1/prediction/flow", "/api/v1/vector/upsert/flow"]
        assert calls[0][1]["overrideConfig"]["sessionId"] == "remote-session"
        assert [observation.operation for observation in observations] == ["predict", "upsert"]
        assert all(observation.remote_session_id == "remote-session" for observation in observations)
        assert all(started is True and succeeded is True for _, started, succeeded in receipts)
        assert len(client.get_audit_log()) == 2
        behavior["loss"] = True
        with pytest.raises(FlowiseDeliveryUnknownError):
            await asyncio.to_thread(client.predict, "flow", "lost response")
        assert len(calls) == 3, "Response loss must not retry the operation"
        assert receipts[-1][1:] == (None, None)
        assert observations[-1].outcome == "delivery_unknown"
        behavior.update(loss=False, oversize=True)
        with pytest.raises(FlowiseDeliveryUnknownError):
            await asyncio.to_thread(client.predict, "flow", "oversized response")
        assert len(calls) == 4 and receipts[-1][1:] == (None, None)
    finally:
        attachment.detach()
    assert client._make_request == original
