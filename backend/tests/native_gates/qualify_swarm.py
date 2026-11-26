"""Original Swarm SDK execution with inert functions and a controlled model transport."""

from __future__ import annotations

import asyncio
import json
import threading

import httpx
import pytest

from backend.services.adapters.native_gates._optional import load_sdk
from backend.services.adapters.native_gates.binding import NativeGateDeniedError
from backend.services.adapters.profile_bridges.swarm import SwarmTarget
from backend.tests.native_gates.qualify_runtime_integration import composed_runtime as composed_runtime
from backend.tests.native_gates.qualify_runtime_integration import events_for

swarm = load_sdk("swarm")
openai = load_sdk("openai")


@pytest.mark.parametrize(
    "outcome",
    ["allow", "deny", "untrusted-handoff", "same-name-handoff", "result-handoff", "cancel", "stream", "stream-usage"],
)
async def test_real_swarm_function_handoff_policy_and_scoped_observation(composed_runtime, outcome):
    from agentmesh.integrations.swarm import HandoffVerifier, TrustPolicy, TrustViolationError

    host, approvals, governance, create = composed_runtime
    job = await create()
    entered, finish = threading.Event(), threading.Event()
    called = []
    requests = []
    peer = swarm.Agent(name="target", instructions="Inert target", model="fixture-model")
    clone = swarm.Agent(name="target", instructions="Not the registered target", model="fixture-model")
    result_type = load_sdk("swarm.types").Result

    def transfer(value: str, context_variables: dict):
        """Return the configured peer after a local inert action."""
        called.append((value, dict(context_variables)))
        entered.set()
        assert finish.wait(10)
        return (
            clone
            if outcome == "same-name-handoff"
            else result_type(value="handoff", agent=peer, context_variables={"next": "value"})
            if outcome == "result-handoff"
            else peer
        )

    source = swarm.Agent(name="source", instructions="Inert source", functions=[transfer], model="fixture-model")
    verifier = HandoffVerifier(TrustPolicy(min_trust_score=0.5))
    verifier.register_agent(source, trust_score=0.9)
    verifier.register_agent(peer, trust_score=0.1 if outcome == "untrusted-handoff" else 0.9)

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            result = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "native-call",
                        "type": "function",
                        "function": {"name": "transfer", "arguments": '{"value":"real-body"}'},
                    }
                ],
            }
        else:
            result = {"role": "assistant", "content": "actual target result"}
        if body["stream"]:
            if result.get("tool_calls"):
                result["tool_calls"][0]["index"] = 0
            chunk = {
                "id": f"native-response-{len(requests)}",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "fixture-model",
                "choices": [
                    {"index": 0, "delta": result, "finish_reason": "tool_calls" if len(requests) == 1 else "stop"}
                ],
            }
            chunks = [chunk]
            if outcome == "stream-usage":
                chunks.append(
                    {
                        **chunk,
                        "choices": [],
                        "usage": {"prompt_tokens": 0, "completion_tokens": 2, "total_tokens": 2},
                    }
                )
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=b"".join(b"data: " + json.dumps(item).encode() + b"\n\n" for item in chunks)
                + b"data: [DONE]\n\n",
            )
        return httpx.Response(
            200,
            json={
                "id": f"native-response-{len(requests)}",
                "object": "chat.completion",
                "created": 1,
                "model": "fixture-model",
                "choices": [{"index": 0, "message": result, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 2, "total_tokens": 2},
            },
        )

    client = openai.OpenAI(
        api_key="fixture-not-a-cloud-credential",
        http_client=httpx.Client(transport=httpx.MockTransport(respond)),
        max_retries=0,
    )
    runtime = swarm.Swarm(client=client)
    original_functions = source.functions
    original_dispatch = runtime.handle_tool_calls
    target = SwarmTarget(runtime, (source, peer), verifier)

    async def approve(event):
        if event.kind == "permission.requested":
            await approvals.resolve(event.payload["approval_id"], "rejected" if outcome == "deny" else "approved")

    host._event_bus.subscribe(approve)
    if outcome != "cancel":
        finish.set()

    def invoke():
        result = runtime.run(
            source,
            [{"role": "user", "content": "inert fixture"}],
            context_variables={"caller": "original-context"},
            max_turns=4,
            stream=outcome in {"stream", "stream-usage"},
        )
        return list(result) if outcome in {"stream", "stream-usage"} else result

    task = await host.start_native(
        job.id,
        family="swarm",
        target=target,
        invoke=invoke,
        synchronous=True,
    )
    try:
        if outcome == "cancel":
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            task.cancel()
            await asyncio.wait({task}, timeout=0.05)
            assert not task.done()
            finish.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        elif outcome in {"deny", "same-name-handoff", "untrusted-handoff"}:
            with pytest.raises((NativeGateDeniedError, TrustViolationError)):
                await task
        else:
            result = await task
            if outcome in {"stream", "stream-usage"}:
                result = result[-1]["response"]
            assert result.agent is peer and result.messages[-1]["content"] == "actual target result"
        assert len(called) == int(outcome != "deny")
        if called:
            assert called[0] == ("real-body", {"caller": "original-context"})
        assert len(requests) == (2 if outcome in {"allow", "result-handoff", "stream", "stream-usage"} else 1)
        assert source.functions is original_functions and runtime.handle_tool_calls == original_dispatch
        stored = await events_for(host, job.id)
        usage = [event for event in stored if event.kind == "execution.native.usage"]
        assert len(usage) == (0 if outcome == "stream" else len(requests))
        for event in usage:
            assert {amount["unit"]: amount["amount"] for amount in event.payload["usage"]} == {
                "input_tokens": "0",
                "output_tokens": "2",
            }
            assert all(
                amount["producer"] == "swarm" and amount["role"] == "primary" and amount["model"] == "fixture-model"
                for amount in event.payload["usage"]
            )
        observed = [event for event in stored if event.metadata.integration_binding_id is not None]
        model_kind = "llm.stream.chunk" if outcome in {"stream", "stream-usage"} else "llm.call.completed"
        assert any(event.kind == model_kind for event in observed)
        if outcome != "deny":
            tool = next(event for event in observed if event.kind in {"tool.call.completed", "tool.call.failed"})
            assert tool.payload["tool_call_id"] == "native-call"
            assert tool.payload["agent_id"] == "source"
        handoffs = [event for event in observed if event.kind == "delegation.verified"]
        assert bool(handoffs) == (outcome in {"allow", "result-handoff", "cancel", "stream", "stream-usage"})
        if handoffs:
            assert handoffs[0].payload["source_did"] == verifier._identities["source"].did
            assert handoffs[0].payload["target_did"] == verifier._identities["target"].did
        assert (await governance._ledger.job_totals(job.id)).executed_calls == int(outcome != "deny")
    finally:
        finish.set()
        await asyncio.gather(task, return_exceptions=True)
        client.close()
