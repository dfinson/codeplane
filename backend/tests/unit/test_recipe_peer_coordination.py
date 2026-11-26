import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.config import CPLConfig
from backend.models.api_schemas import PeerMessageRequest
from backend.models.db import JobRow
from backend.models.domain import SessionConfig, StateConflictError
from backend.models.schemas.recipes import RecipeDefinition
from backend.persistence.job_repo import JobRepository
from backend.services.copilot_adapter import CopilotAdapter
from backend.services.events.event_bus import EventBus
from backend.services.job.approval_service import ApprovalService
from backend.services.runtime import RuntimeService
from backend.services.session_coordination import SessionCoordination
from backend.tests.unit.test_recipe_definitions import agent
from backend.tests.unit.test_recipe_runs import create_run, ready_attempt
from backend.tests.unit.test_recipe_runs import sessions as sessions


@pytest.fixture
async def peers(sessions):
    run = await create_run(
        sessions,
        RecipeDefinition(
            name="Parallel peers", entry_steps=["review", "implement"], steps=[agent("review"), agent("implement")]
        ),
    )
    async with sessions() as session:
        attempts = [await ready_attempt(session, run, name) for name in ("review", "implement")]
    adapter = CopilotAdapter(session_factory=sessions)
    registry = MagicMock()
    registry.get_adapter.return_value = adapter
    runtime = RuntimeService(
        session_factory=sessions,
        event_bus=EventBus(),
        adapter_registry=registry,
        config=CPLConfig(),
        approval_service=ApprovalService(sessions),
    )
    tasks = [asyncio.create_task(asyncio.Event().wait()) for _ in range(3)]
    runtime._tasks[run.job_id] = tasks[0]
    owners = {attempt.id: SimpleNamespace(task=task) for attempt, task in zip(attempts, tasks[1:], strict=True)}
    runtime._recipe_controllers[run.job_id] = SimpleNamespace(owned_attempt=owners.get)
    bindings = []
    for attempt in attempts:
        session_id = f"sdk-{attempt.id}"
        adapter._sessions[session_id] = MagicMock(send=AsyncMock())
        adapter.set_job_id(session_id, run.job_id)
        bindings.append(runtime._attempt_bindings.register(run.job_id, attempt.id, adapter, session_id, "copilot"))
    try:
        yield run, runtime, adapter, bindings, SessionCoordination(sessions, runtime)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_recipe_menu_and_native_sender_select_exact_sibling_without_resuming(peers):
    run, runtime, adapter, bindings, service = peers
    source, target = bindings
    config = runtime._with_peer_callbacks(
        SessionConfig(workspace_path="/repo", prompt="Review"),
        run.job_id,
        lambda: source,
    )
    menu = await config.peer_directory_handler(0, run.job_id, "")
    assert {card["name"] for card in menu["items"]} == {"Review", "Implement"}
    assert all(card["acceptsPeerMessages"] for card in menu["items"])
    card = next(card for card in menu["items"] if card["attemptId"] == target.attempt_id)
    result = await config.peer_message_handler(
        "selected-sibling",
        run.job_id,
        "Review complete",
        None,
        card["attemptId"],
        card["executionToken"],
    )
    assert result["status"] == "accepted"
    assert result["senderAttemptId"] == source.attempt_id
    assert result["recipientAttemptId"] == target.attempt_id
    adapter._sessions[target.session_id].send.assert_awaited_once()
    adapter._sessions[source.session_id].send.assert_not_awaited()
    retry = await config.peer_message_handler(
        "selected-sibling",
        run.job_id,
        "Review complete",
        None,
        card["attemptId"],
        card["executionToken"],
    )
    assert retry["status"] == result["status"]
    assert retry["messageId"] == result["messageId"]
    assert retry["recipientExecutionToken"] == result["recipientExecutionToken"]
    adapter._sessions[target.session_id].send.assert_awaited_once()
    with pytest.raises(StateConflictError, match="different request"):
        await service.send(
            run.job_id,
            PeerMessageRequest(
                message_id="selected-sibling",
                recipient_job_id=run.job_id,
                content="Review complete",
                recipient_attempt_id=target.attempt_id,
                recipient_execution_token="different-token",
            ),
            origin="agent",
            sender_execution=source,
        )


@pytest.mark.parametrize("change", ["cancel", "replace", "approval"])
async def test_selected_attempt_changed_during_validation_is_rejected_without_rerouting(peers, change):
    run, runtime, adapter, bindings, service = peers
    source, target = bindings
    entered, release = asyncio.Event(), asyncio.Event()
    validate = adapter._validate_session_account
    original = adapter._sessions[target.session_id]

    async def blocked(session_id):
        entered.set()
        await release.wait()
        return await validate(session_id)

    adapter._validate_session_account = blocked
    sending = asyncio.create_task(
        service.send(
            run.job_id,
            PeerMessageRequest(
                message_id="selected",
                recipient_job_id=run.job_id,
                content="Note",
                recipient_attempt_id=target.attempt_id,
                recipient_execution_token=target.token,
            ),
            origin="agent",
            sender_execution=source,
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), 5)
        if change == "cancel":
            runtime._attempt_bindings.request_cancel(run.job_id, target.attempt_id, target.token)
        elif change == "replace":
            runtime._attempt_bindings.unregister(run.job_id, target.attempt_id, target.token)
            runtime._attempt_bindings.register(run.job_id, target.attempt_id, adapter, target.session_id, "copilot")
        else:
            await runtime._approval_service.create_request(run.job_id, "Pending unscoped approval")
    finally:
        release.set()
        result = await sending
    assert result.status == "rejected"
    assert (await service.messages(run.job_id)).items[0].status == "rejected"
    original.send.assert_not_awaited()
    adapter._sessions[source.session_id].send.assert_not_awaited()
    menu = await service.agents(run.job_id, run.job_id)
    card = next(card for card in menu.items if card.attempt_id == target.attempt_id)
    if change == "replace":
        assert card.execution_token != target.token
    else:
        assert not card.accepts_peer_messages


async def test_recipe_requires_explicit_selection_and_does_not_target_itself(peers):
    run, _, adapter, bindings, service = peers
    source, target = bindings
    result = await service.send(
        run.job_id,
        PeerMessageRequest(
            message_id="missing-token",
            recipient_job_id=run.job_id,
            content="Note",
            recipient_attempt_id=target.attempt_id,
        ),
        origin="agent",
        sender_execution=source,
    )
    assert result.status == "rejected"
    with pytest.raises(StateConflictError, match="different agent"):
        await service.send(
            run.job_id,
            PeerMessageRequest(
                message_id="self",
                recipient_job_id=run.job_id,
                content="Note",
                recipient_attempt_id=source.attempt_id,
                recipient_execution_token=source.token,
            ),
            origin="agent",
            sender_execution=source,
        )
    for binding in bindings:
        adapter._sessions[binding.session_id].send.assert_not_awaited()


async def test_job_directory_keeps_mixed_provider_recipes_discoverable(peers, sessions):
    run, _, _, _, service = peers
    async with sessions() as session:
        row = await session.get(JobRow, run.job_id)
        row.sdk = "claude"
        repository = JobRepository(session)
        job = await repository.get(run.job_id)
        await repository.create(replace(job, id="observer", sdk="copilot"))
        await session.commit()
    directory = await service.directory("observer")
    assert [peer.id for peer in directory.peers] == [run.job_id]
    cards = await service.agents("observer", run.job_id)
    assert len(cards.items) == 2
    assert all(card.sdk == "copilot" for card in cards.items)
