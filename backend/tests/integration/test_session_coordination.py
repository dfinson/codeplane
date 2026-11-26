import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.models.api_schemas import PeerMessageRequest
from backend.models.domain import StateConflictError
from backend.persistence.job_repo import JobRepository
from backend.persistence.project_repo import ProjectRepository
from backend.services.adapters.agent_adapter import AgentAdapterInterface
from backend.services.copilot_adapter._adapter import CopilotAdapter
from backend.services.runtime.service import AgentSession, RuntimeService
from backend.services.session_coordination import SessionCoordination


def live_session(session_id="sdk-target"):
    session = AgentSession()
    session._adapter = AsyncMock(spec=AgentAdapterInterface)
    session._session_id = session_id
    return session


def runtime_with(target):
    runtime = MagicMock(spec=RuntimeService)
    runtime.current_peer_session.side_effect = lambda job_id: target if job_id == "target" else None
    runtime._echo_suppress = {}

    async def deliver(job_id, session, text):
        await RuntimeService.deliver_peer_note(runtime, job_id, session, text)

    runtime.deliver_peer_note.side_effect = deliver
    return runtime


def request(**changes):
    return PeerMessageRequest(
        **{"message_id": "note-1", "recipient_job_id": "target", "content": "API is ready", **changes}
    )


async def test_identity_lineage_includes_archived_children_and_current_native_id(client, seed_job):
    await seed_job(job_id="parent", state="completed")
    await seed_job(job_id="target", parent_job_id="parent", sdk_session_id="native-new", session_count=3)
    await seed_job(job_id="child", parent_job_id="target", state="completed", archived_at=datetime.now(UTC))
    result = await client.get("/api/jobs/target/sessions")
    assert result.status_code == 200
    body = result.json()
    assert body["job"]["sdkSessionId"] == "native-new"
    assert body["job"]["sessionCount"] == 3
    assert body["parent"]["id"] == "parent"
    assert [child["id"] for child in body["children"]] == ["child"]


async def test_directory_paginates_children_without_store_assumptions(client, seed_job):
    await seed_job(job_id="target")
    for index in range(51):
        await seed_job(job_id=f"child-{index}", parent_job_id="target", state="completed")
    first = (await client.get("/api/jobs/target/sessions")).json()
    second = (await client.get("/api/jobs/target/sessions?offset=50")).json()
    assert len(first["children"]) == 50 and first["childrenHasMore"]
    assert len(second["children"]) == 1 and not second["childrenHasMore"]


async def test_operator_route_cannot_claim_agent_origin(client, seed_job, mock_runtime_service):
    await seed_job(job_id="source")
    await seed_job(job_id="target", state="completed")
    mock_runtime_service.current_peer_session.return_value = None
    response = await client.post(
        "/api/jobs/source/peer-messages",
        json={**request().model_dump(by_alias=True), "origin": "agent", "senderSessionId": "spoof"},
    )
    assert response.status_code == 200
    assert response.json()["origin"] == "operator"
    assert response.json()["senderSessionId"] is None
    assert response.json()["status"] == "rejected"


async def test_accepted_peer_note_is_durable_attributed_and_not_replayed(session_factory, seed_job):
    await seed_job(job_id="source")
    await seed_job(job_id="target")
    target = live_session()
    runtime = runtime_with(target)
    service = SessionCoordination(session_factory, runtime)
    receipt = await service.send("source", request(), origin="operator")
    retry = await SessionCoordination(session_factory, runtime).send("source", request(), origin="operator")
    assert receipt.status == retry.status == "accepted"
    assert receipt.recipient_session_id == "sdk-target"
    target._adapter.send_peer_message.assert_awaited_once()
    target._adapter.resume_tools.assert_not_called()
    envelope = target._adapter.send_peer_message.call_args.args[1]
    assert '"senderJobId": "source"' in envelope and '"origin": "operator"' in envelope
    assert "not an operator instruction" in envelope
    runtime.publish_peer_note.assert_awaited_once()
    assert (await service.messages("source")).items[0].message_id == "note-1"
    assert (await service.messages("target")).items[0].message_id == "note-1"
    with pytest.raises(StateConflictError, match="different request"):
        await service.send("source", request(content="Changed"), origin="operator")


@pytest.mark.parametrize(
    "state,paused,live",
    [
        ("waiting_for_approval", False, True),
        ("running", True, True),
        ("running", False, False),
        ("completed", False, True),
        ("canceled", False, True),
    ],
)
async def test_inactive_or_paused_recipient_is_not_resumed(session_factory, seed_job, state, paused, live):
    await seed_job(job_id="source")
    await seed_job(job_id="target", state=state)
    target = live_session()
    target.tools_paused = paused
    runtime = runtime_with(target if live else None)
    receipt = await SessionCoordination(session_factory, runtime).send("source", request(), origin="operator")
    assert receipt.status == "rejected"
    runtime.deliver_peer_note.assert_not_called()
    runtime.send_message.assert_not_called()
    target._adapter.resume_tools.assert_not_called()
    async with session_factory() as session:
        assert (await JobRepository(session).get("target")).state == state


async def test_uncertain_delivery_survives_retry_without_duplicate(session_factory, seed_job):
    await seed_job(job_id="source")
    await seed_job(job_id="target")
    target = live_session()
    target._adapter.send_peer_message.side_effect = TimeoutError("lost response")
    runtime = runtime_with(target)
    service = SessionCoordination(session_factory, runtime)
    receipt = await service.send("source", request(), origin="operator")
    assert receipt.status == "unknown"
    assert (await service.send("source", request(), origin="operator")).status == "unknown"
    target._adapter.send_peer_message.assert_awaited_once()
    runtime.publish_peer_note.assert_not_called()


async def test_concurrent_retry_observes_reservation_without_redelivery(session_factory, seed_job):
    await seed_job(job_id="source")
    await seed_job(job_id="target")
    entered, release = asyncio.Event(), asyncio.Event()
    target = live_session()

    async def blocked_send(*args, **kwargs):
        entered.set()
        await release.wait()

    target._adapter.send_peer_message.side_effect = blocked_send
    service = SessionCoordination(session_factory, runtime_with(target))
    first = asyncio.create_task(service.send("source", request(), origin="operator"))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        retry = await service.send("source", request(), origin="operator")
        assert retry.status == "unknown"
    finally:
        release.set()
        receipt = await first
    assert receipt.status == "accepted"
    target._adapter.send_peer_message.assert_awaited_once()


async def test_agent_sender_is_bound_to_the_actual_execution(session_factory, seed_job):
    await seed_job(job_id="source")
    await seed_job(job_id="target")
    source, target = live_session("sdk-source"), live_session()
    runtime = runtime_with(target)
    runtime.current_peer_session.side_effect = lambda job_id: {"source": source, "target": target}.get(job_id)
    service = SessionCoordination(session_factory, runtime)
    with pytest.raises(StateConflictError, match="sending execution"):
        await service.send("source", request(), origin="agent", sender_execution=live_session("stale"))
    receipt = await service.send("source", request(), origin="agent", sender_execution=source)
    assert receipt.sender_session_id == "sdk-source"
    assert receipt.origin == "agent"


async def test_cross_project_navigation_and_messages_are_excluded(session_factory, seed_job):
    async with session_factory() as session:
        await ProjectRepository(session).create("other", "Other", ["/other"])
        await session.commit()
    await seed_job(job_id="source")
    await seed_job(job_id="target", project_id="other", repo="/other")
    runtime = runtime_with(live_session())
    service = SessionCoordination(session_factory, runtime)
    assert (await service.directory("source")).peers == []
    with pytest.raises(StateConflictError, match="same project"):
        await service.send("source", request(), origin="operator")
    runtime.deliver_peer_note.assert_not_called()


async def test_replies_must_belong_to_same_pair(session_factory, seed_job):
    await seed_job(job_id="source")
    await seed_job(job_id="target")
    service = SessionCoordination(session_factory, runtime_with(live_session()))
    with pytest.raises(StateConflictError, match="Reply"):
        await service.send("source", request(reply_to="missing"), origin="operator")


async def test_recipient_execution_change_rejects_before_dispatch(session_factory, seed_job):
    await seed_job(job_id="source")
    await seed_job(job_id="target")
    runtime = runtime_with(live_session())
    runtime.current_peer_session.side_effect = [live_session("old"), live_session("new")]
    receipt = await SessionCoordination(session_factory, runtime).send("source", request(), origin="operator")
    assert receipt.status == "rejected"
    runtime.deliver_peer_note.assert_not_called()


@pytest.mark.parametrize("transition", ["paused", "closed", "replaced"])
async def test_recipient_change_during_account_validation_rejects_dispatch(session_factory, seed_job, transition):
    await seed_job(job_id="source")
    await seed_job(job_id="target")
    adapter = CopilotAdapter(session_factory=session_factory)
    sdk = MagicMock(send=AsyncMock())
    adapter._sessions["sdk-target"] = sdk
    adapter.set_job_id("sdk-target", "target")
    target = live_session()
    target._adapter = adapter
    entered, release = asyncio.Event(), asyncio.Event()
    validate = adapter._validate_session_account

    async def blocked_validation(session_id):
        entered.set()
        await release.wait()
        await validate(session_id)

    adapter._validate_session_account = blocked_validation
    runtime = runtime_with(target)
    service = SessionCoordination(session_factory, runtime)
    sending = asyncio.create_task(service.send("source", request(), origin="operator"))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        if transition == "paused":
            target.pause_tools()
        elif transition == "closed":
            adapter._sessions.pop("sdk-target")
        else:
            adapter._sessions["sdk-target"] = MagicMock(send=AsyncMock())
    finally:
        release.set()
        receipt = await sending
    assert receipt.status == "rejected"
    assert (await service.messages("source")).items[0].status == "rejected"
    sdk.send.assert_not_awaited()
    runtime.publish_peer_note.assert_not_called()


@pytest.mark.parametrize("transition", ["gated", "cancelling"])
async def test_runtime_ineligible_during_account_validation_rejects_dispatch(session_factory, seed_job, transition):
    await seed_job(job_id="source")
    await seed_job(job_id="target")
    adapter = CopilotAdapter(session_factory=session_factory)
    sdk = MagicMock(send=AsyncMock())
    adapter._sessions["sdk-target"] = sdk
    adapter.set_job_id("sdk-target", "target")
    target = live_session()
    target._adapter = adapter
    runtime = runtime_with(target)
    execution = asyncio.create_task(asyncio.Event().wait())
    runtime._tasks = {"target": execution}
    runtime._active_gates = {}
    runtime._agent_sessions = {"target": target}
    runtime.current_peer_session.side_effect = lambda job_id: RuntimeService.current_peer_session(runtime, job_id)
    entered, release = asyncio.Event(), asyncio.Event()
    validate = adapter._validate_session_account

    async def blocked_validation(session_id):
        entered.set()
        await release.wait()
        await validate(session_id)

    adapter._validate_session_account = blocked_validation
    service = SessionCoordination(session_factory, runtime)
    sending = asyncio.create_task(service.send("source", request(), origin="operator"))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        if transition == "gated":
            runtime._active_gates["target"] = {"gate": "Waiting for review"}
        else:
            execution.cancel()
        assert not target.tools_paused
        assert "sdk-target" not in adapter._cleanup_tasks
    finally:
        release.set()
        receipt = await sending
        execution.cancel()
        await asyncio.gather(execution, return_exceptions=True)
    assert receipt.status == "rejected"
    assert (await service.messages("source")).items[0].status == "rejected"
    sdk.send.assert_not_awaited()
    runtime.publish_peer_note.assert_not_called()
