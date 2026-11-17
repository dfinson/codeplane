import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.config import CPLConfig
from backend.models.domain import SessionConfig
from backend.models.events import EventKind, new_event
from backend.services.action_policy.classifier import Preset, RepoPolicy
from backend.services.auth.permission_policy import PermissionRequest
from backend.services.claude_adapter import ClaudeAdapter
from backend.services.copilot_adapter import CopilotAdapter
from backend.services.events.activity_projection import ActivityProjection
from backend.services.events.event_bus import EventBus
from backend.services.events.event_processor import EventProcessor
from backend.services.job.approval_service import ApprovalService
from backend.services.runtime import RuntimeService
from backend.services.runtime.attempt_bindings import AttemptBindingConflictError, AttemptBindings
from backend.services.steps.tracker import StepTracker
from backend.tests.unit.test_approval_service import session_factory as session_factory


def test_binding_replacement_fences_old_token_and_scopes_by_job() -> None:
    bindings = AttemptBindings("epoch")
    adapter = AsyncMock()
    first = bindings.register("job-1", "attempt-1", adapter, "real-session", "copilot")
    second = bindings.register("job-2", "attempt-1", adapter, "other-session", "copilot")
    bindings.unregister("job-1", "attempt-1", first.token)
    replacement = bindings.register("job-1", "attempt-1", adapter, "real-session", "copilot")
    assert replacement.token != first.token
    with pytest.raises(AttemptBindingConflictError):
        bindings.unregister("job-1", "attempt-1", first.token)
    assert bindings.get("job-1", "attempt-1") == replacement
    assert bindings.get("job-2", "attempt-1") == second
    assert bindings.get("job-2", "missing") is None


def test_two_attempts_cannot_own_the_same_actual_session() -> None:
    bindings = AttemptBindings("epoch")
    adapter = AsyncMock()
    bindings.register("job-1", "attempt-1", adapter, "real-session", "copilot")
    with pytest.raises(AttemptBindingConflictError):
        bindings.register("job-2", "attempt-2", adapter, "real-session", "copilot")


def test_register_does_not_replace_existing_attempt_or_launch_anything() -> None:
    bindings = AttemptBindings("epoch")
    adapter = AsyncMock()
    bindings.register("job-1", "attempt-1", adapter, "real-session", "copilot")
    with pytest.raises(AttemptBindingConflictError):
        bindings.register("job-1", "attempt-1", adapter, "replacement-session", "copilot")
    adapter.create_session.assert_not_called()


def _runtime(session_factory):
    adapter = MagicMock()
    adapter.send_message = AsyncMock()
    adapter.abort_session = AsyncMock()
    registry = MagicMock()
    registry.get_adapter.return_value = adapter
    approvals = ApprovalService(session_factory)
    runtime = RuntimeService(
        session_factory=session_factory,
        event_bus=EventBus(),
        adapter_registry=registry,
        config=CPLConfig(),
        approval_service=approvals,
    )
    primary = SimpleNamespace(session_id="primary", send_message=AsyncMock())
    runtime._agent_sessions["job-1"] = primary
    return runtime, adapter, approvals, primary


async def test_runtime_targets_exact_attempt_not_primary_or_sibling(session_factory):
    runtime, adapter, _, primary = _runtime(session_factory)
    first = await runtime.register_attempt_execution("job-1", "first", adapter, "session-first")
    second = await runtime.register_attempt_execution("job-1", "second", adapter, "session-second")
    stale = await runtime.dispatch_current_execution("job-1", first.execution_token, "hi", attempt_id="second")
    assert stale.reason == "stale_execution"
    missing = await runtime.dispatch_current_execution("job-1", first.execution_token, "hi", attempt_id="missing")
    assert missing.reason == "no_live_execution"
    result = await runtime.dispatch_current_execution("job-1", second.execution_token, "hi", attempt_id="second")
    assert result.status == "accepted"
    adapter.send_message.assert_awaited_once_with("session-second", "hi")
    primary.send_message.assert_not_awaited()
    await runtime.cancel_attempt_execution("job-1", "first", first.execution_token)
    adapter.abort_session.assert_awaited_once_with("session-first")
    assert (await runtime.get_current_execution_context("job-1", attempt_id="first")).execution_token is None
    assert (await runtime.get_current_execution_context("job-1", attempt_id="second")).execution_token == (
        second.execution_token
    )


async def test_accepted_dispatch_is_a_receipt_not_a_second_operator_boundary(session_factory):
    runtime, _, _, _ = _runtime(session_factory)
    received = []
    runtime._event_bus.subscribe(AsyncMock(side_effect=received.append))
    context = await runtime.get_current_execution_context("job-1")
    result = await runtime.dispatch_current_execution("job-1", context.execution_token, "Operator instruction")
    assert result.status == "accepted"
    receipt = received[0]
    assert receipt.payload["admission_status"] == "accepted"
    assert receipt.payload["execution_status"] == "unconfirmed"
    assert receipt.payload["operator_content"] == "Operator instruction"
    assert receipt.metadata.execution_token == context.execution_token
    assert receipt.metadata.run_id == "primary"
    assert receipt.metadata.source_framework == "codeplane"
    assert receipt.metadata.namespace == ("control", "admission-receipt")
    assert "Operator instruction" not in runtime._echo_suppress.get("job-1", set())
    bus = EventBus()
    output = []
    bus.subscribe(AsyncMock(side_effect=output.append))
    tracker, activities = StepTracker(bus), ActivityProjection(bus)
    titles = SimpleNamespace(push=AsyncMock())
    processor = EventProcessor(bus, step_tracker=tracker, activity_projection=activities, title_pipeline=titles)
    await processor.process_event(
        "job-1",
        new_event(
            "job-1",
            EventKind.message_user,
            {"content": "Actual input", "turn_id": "observed-turn"},
        ),
    )
    step = tracker.current_step("job-1")
    activity = activities.snapshot()
    titles.push.reset_mock()
    output.clear()
    await processor.process_event("job-1", receipt)
    assert output == [receipt]
    assert tracker.current_step("job-1") is step
    assert activities.snapshot() == activity
    assert "turn_id" not in receipt.payload and "step_id" not in receipt.payload
    titles.push.assert_not_awaited()


async def test_runtime_pending_approval_blocks_attempt_dispatch_and_registration(session_factory):
    runtime, adapter, approvals, _ = _runtime(session_factory)
    context = await runtime.register_attempt_execution("job-1", "first", adapter, "session-first")
    await approvals.create_request("job-1", "Await explicit operator decision", requires_explicit_approval=True)
    result = await runtime.dispatch_current_execution("job-1", context.execution_token, "hi", attempt_id="first")
    assert result.reason == "approval_pending"
    adapter.send_message.assert_not_awaited()
    with pytest.raises(AttemptBindingConflictError, match="pending approval"):
        await runtime.register_attempt_execution("job-1", "second", adapter, "session-second")


async def test_unconfirmed_stop_keeps_handle_but_cannot_be_steered(session_factory):
    runtime, adapter, _, _ = _runtime(session_factory)
    context = await runtime.register_attempt_execution("job-1", "first", adapter, "session-first")
    adapter.abort_session.side_effect = ConnectionError("Transport lost")
    with pytest.raises(ConnectionError):
        await runtime.cancel_attempt_execution("job-1", "first", context.execution_token)
    binding = runtime._attempt_bindings.get("job-1", "first")
    assert binding is not None and binding.cancel_requested
    result = await runtime.dispatch_current_execution("job-1", context.execution_token, "hi", attempt_id="first")
    assert result.reason == "no_live_execution"
    adapter.send_message.assert_not_awaited()


async def test_completed_owner_can_unregister_during_real_stop_without_deadlock(session_factory):
    runtime, adapter, _, _ = _runtime(session_factory)
    context = await runtime.register_attempt_execution("job-1", "first", adapter, "session-first")

    async def finish(session_id):
        await runtime.unregister_attempt_execution("job-1", "first", context.execution_token)

    adapter.abort_session.side_effect = finish
    await runtime.cancel_attempt_execution("job-1", "first", context.execution_token)
    assert runtime._attempt_bindings.get("job-1", "first") is None


async def test_owned_deferred_session_is_closed_when_registration_fails(session_factory):
    runtime, adapter, approvals, _ = _runtime(session_factory)

    async def create_session(config):
        await approvals.create_request("job-1", "New gate during SDK creation", requires_explicit_approval=True)
        return "inert-session"

    adapter.create_session = AsyncMock(side_effect=create_session)
    config = SessionConfig(workspace_path="/test", prompt="must not execute", job_id="job-1")
    with pytest.raises(AttemptBindingConflictError):
        async with runtime.open_attempt_execution("job-1", "attempt", config):
            pytest.fail("A pending approval must prevent an attempt becoming dispatchable")
    assert adapter.create_session.call_args.args[0].defer_start
    adapter.send_message.assert_not_awaited()
    adapter.abort_session.assert_awaited_once_with("inert-session")
    assert runtime._attempt_bindings.get("job-1", "attempt") is None


async def test_attempt_policy_reuses_job_router_across_providers_without_workspace_or_budget_reset(session_factory):
    runtime, _, _, _ = _runtime(session_factory)
    runtime._config.runtime.copilot_only = False
    copilot, claude = CopilotAdapter(), ClaudeAdapter()
    runtime._adapter_registry.get_adapter.side_effect = lambda sdk: copilot if str(sdk) == "copilot" else claude
    router = SimpleNamespace(route=AsyncMock(return_value=SimpleNamespace(proceed=True, classification=None)))
    policy = RepoPolicy(preset=Preset.supervised)
    runtime._policy_routers["job-1"] = router
    runtime._repo_policies["job-1"] = policy
    runtime._governance_decider = MagicMock()
    copilot.set_policy_router(router, policy, "job-1", "/primary")
    for provider, adapter, attempt, workspace in (
        ("copilot", copilot, "first", "/worktree-first"),
        ("claude", claude, "second", "/worktree-second"),
    ):
        sid = f"session-{attempt}"
        adapter.set_job_id(sid, "job-1")
        adapter._queues[sid] = asyncio.Queue()
        adapter.set_session_metadata(
            sid,
            SessionConfig(
                workspace_path=workspace,
                prompt="",
                job_id="job-1",
                attempt_id=attempt,
                execution_epoch=runtime.execution_epoch,
            ),
        )
        context = await runtime.register_attempt_execution("job-1", attempt, adapter, sid, sdk=provider)
        await runtime.attach_attempt_policy("job-1", attempt, context.execution_token)
        assert adapter._policy_router["job-1"] is router
        assert adapter._repo_policies["job-1"] is policy
        adapter._verify_and_set_model(sid, "job-1", f"actual-{attempt}", "")
        observed = adapter._queues[sid].get_nowait()
        assert observed.metadata.run_id == sid
        assert observed.metadata.execution_token == context.execution_token
        await adapter._evaluate_with_policy_router(
            sid,
            "job-1",
            PermissionRequest(kind="write", workspace_path="/untrusted", file_name="file"),
            tool_name="write",
            tool_input={"path": "file"},
        )
        action = router.route.call_args.args[0]
        assert action.workspace_path == workspace
        assert router.route.call_args.kwargs["cwd"] == workspace
    assert copilot._worktree_paths["job-1"] == "/primary"
    assert "job-1" not in claude._worktree_paths
    runtime._governance_decider.register_job.assert_not_called()


async def test_attempt_policy_attachment_rejects_stale_tokens_and_missing_job_policy(session_factory):
    runtime, adapter, _, _ = _runtime(session_factory)
    context = await runtime.register_attempt_execution("job-1", "attempt", adapter, "session")
    with pytest.raises(AttemptBindingConflictError, match="current execution token"):
        await runtime.attach_attempt_policy("job-1", "attempt", "stale")
    with pytest.raises(AttemptBindingConflictError, match="not been configured"):
        await runtime.attach_attempt_policy("job-1", "attempt", context.execution_token)
    adapter.attach_attempt_policy.assert_not_called()


@pytest.mark.parametrize("hard_gate", [False, True])
async def test_pending_permission_cannot_outlive_cancel_fence_while_native_abort_waits(session_factory, hard_gate):
    from backend.models.domain import ApprovalResolution
    from backend.services.adapters.base_adapter import PermissionDecision

    runtime, _, _, _ = _runtime(session_factory)
    adapter = CopilotAdapter()
    runtime._adapter_registry.get_adapter.return_value = adapter
    sid = "controlled-session"
    adapter.set_job_id(sid, "job-1")
    adapter.set_session_metadata(
        sid,
        SessionConfig(
            workspace_path="/test",
            prompt="",
            job_id="job-1",
            attempt_id="attempt",
            execution_epoch=runtime.execution_epoch,
        ),
    )
    adapter._queues[sid] = asyncio.Queue()
    deciding, decide, aborting, finish_abort = (asyncio.Event() for _ in range(4))

    async def pending_decision(*args, **kwargs):
        deciding.set()
        await decide.wait()
        return ApprovalResolution.approved if hard_gate else SimpleNamespace(proceed=True, classification=None)

    if hard_gate:
        adapter._hard_block_approval = AsyncMock(side_effect=pending_decision)
    else:
        adapter._policy_router["job-1"] = SimpleNamespace(route=AsyncMock(side_effect=pending_decision))

    async def slow_abort():
        aborting.set()
        await finish_abort.wait()

    adapter._sessions[sid] = SimpleNamespace(abort=slow_abort)
    context = await runtime.register_attempt_execution("job-1", "attempt", adapter, sid)
    permission = asyncio.create_task(
        adapter._evaluate_permission(
            sid,
            "job-1",
            PermissionRequest(
                kind="shell" if hard_gate else "read",
                workspace_path="/test",
                full_command_text="git reset --hard HEAD" if hard_gate else None,
            ),
            tool_name="Bash" if hard_gate else "Read",
        )
    )
    await asyncio.wait_for(deciding.wait(), timeout=2)
    cancellation = asyncio.create_task(runtime.cancel_attempt_execution("job-1", "attempt", context.execution_token))
    try:
        await asyncio.wait_for(aborting.wait(), timeout=2)
        assert not cancellation.done()
        decide.set()
        assert await permission == PermissionDecision.deny
    finally:
        decide.set()
        finish_abort.set()
        await asyncio.gather(permission, cancellation, return_exceptions=True)


@pytest.mark.parametrize("hard_gate", [False, True])
@pytest.mark.parametrize("transition", ["replacement", "pause_resume", "new_token"])
async def test_reused_session_identity_does_not_inherit_a_pending_permission(hard_gate, transition):
    from backend.models.domain import ApprovalResolution
    from backend.services.adapters.base_adapter import PermissionDecision

    adapter = CopilotAdapter()
    config = SessionConfig(
        workspace_path="/test",
        prompt="",
        job_id="job",
        attempt_id="attempt",
        execution_epoch="epoch",
    )
    adapter.set_job_id("same-id", "job")
    adapter.set_session_metadata("same-id", config)
    entered, release = asyncio.Event(), asyncio.Event()

    async def wait_for_decision(*args, **kwargs):
        entered.set()
        await release.wait()
        return ApprovalResolution.approved if hard_gate else SimpleNamespace(proceed=True, classification=None)

    if hard_gate:
        adapter._hard_block_approval = AsyncMock(side_effect=wait_for_decision)
    else:
        adapter._policy_router["job"] = SimpleNamespace(route=AsyncMock(side_effect=wait_for_decision))
    request = PermissionRequest(
        kind="shell" if hard_gate else "read",
        workspace_path="/test",
        full_command_text="git reset --hard HEAD" if hard_gate else None,
    )
    pending = asyncio.create_task(adapter._evaluate_permission("same-id", "job", request, tool_name="Read"))
    await asyncio.wait_for(entered.wait(), timeout=2)
    if transition == "replacement":
        adapter._cleanup_session_state("same-id")
        adapter.set_job_id("same-id", "job")
        adapter.set_session_metadata("same-id", config)
    elif transition == "pause_resume":
        adapter.pause_tools("same-id")
        adapter.resume_tools("same-id")
    else:
        adapter.attach_attempt_policy(
            "same-id",
            "job",
            "attempt",
            "epoch",
            adapter._policy_router.get("job", MagicMock()),
            RepoPolicy(),
            execution_token="new-execution",
        )
    release.set()
    assert await pending == PermissionDecision.deny
    assert await adapter._evaluate_permission("same-id", "job", request, tool_name="Read") == PermissionDecision.allow


@pytest.mark.parametrize("provider", ["copilot", "claude"])
@pytest.mark.parametrize("boundary", ["permission", "hard_gate", "pre_tool"])
@pytest.mark.parametrize("transition", ["pause_resume", "replacement", "new_token"])
async def test_sdk_callback_ownership_spans_capability_parser_wait(
    tmp_path,
    monkeypatch,
    provider,
    boundary,
    transition,
):
    from backend.models.domain import ApprovalResolution
    from backend.services.action_policy.syntax import analyze_syntax
    from backend.services.adapters import capability_guard

    adapter = CopilotAdapter() if provider == "copilot" else ClaudeAdapter()
    command = "git reset --hard HEAD" if boundary == "hard_gate" else "echo test"
    adapter._hard_block_approval = AsyncMock(return_value=ApprovalResolution.approved)
    config = SessionConfig(
        workspace_path=str(tmp_path),
        prompt="",
        job_id="job",
        attempt_id="attempt",
        execution_epoch="epoch",
        defer_start=True,
        execution_capabilities=frozenset({"read", "write", "shell", "external_access"}),
    )
    client = MagicMock()
    client.stop = AsyncMock()
    client.force_stop = AsyncMock()
    client.interrupt = AsyncMock()
    client._transport = None
    if provider == "copilot":
        session = MagicMock(session_id="same-id")
        session.abort = AsyncMock()
        client.create_session = AsyncMock(return_value=session)
        sid = await adapter._create_owned_session(client, "lease", config)
        callbacks = client.create_session.call_args.kwargs

        async def invoke():
            if boundary == "pre_tool":
                return await callbacks["hooks"]["on_pre_tool_use"](
                    {"toolName": "bash", "toolArgs": {"command": command}, "cwd": str(tmp_path), "timestamp": 0},
                    {"session_id": sid},
                )
            return await callbacks["on_permission_request"](
                SimpleNamespace(
                    kind="shell",
                    tool_name="bash",
                    full_command_text=command,
                    file_name=None,
                    path=None,
                    possible_paths=None,
                    read_only=False,
                ),
                {"session_id": sid},
            )

        def denied(result):
            return result is not None and (
                result.get("permissionDecision") == "deny" if boundary == "pre_tool" else result.kind == "reject"
            )
    else:
        import claude_code_sdk

        client.connect = AsyncMock()
        factory = MagicMock(return_value=client)
        monkeypatch.setattr(claude_code_sdk, "ClaudeSDKClient", factory)
        monkeypatch.setattr(adapter, "_consume_messages", AsyncMock())
        sid = await adapter.create_session(config)
        options = factory.call_args.args[0]

        async def invoke():
            if boundary == "pre_tool":
                return await options.hooks["PreToolUse"][0].hooks[0](
                    {"tool_name": "Bash", "tool_input": {"command": command}},
                    "call",
                    {},
                )
            return await options.can_use_tool("Bash", {"command": command}, None)

        def denied(result):
            if boundary == "pre_tool":
                return result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
            return isinstance(result, claude_code_sdk.PermissionResultDeny)

    entered, release = asyncio.Event(), asyncio.Event()

    async def pending_parser(command, dialect):
        entered.set()
        await release.wait()
        return analyze_syntax(command, dialect)

    monkeypatch.setattr(capability_guard, "analyze_syntax_async", pending_parser)
    router = SimpleNamespace(route=AsyncMock(return_value=SimpleNamespace(proceed=True, classification=None)))
    adapter._policy_router["job"] = router
    pending = asyncio.create_task(invoke())
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        if transition == "pause_resume":
            adapter.pause_tools(sid)
            adapter.resume_tools(sid)
        elif transition == "replacement":
            adapter.set_session_metadata(sid, config)
        else:
            adapter.attach_attempt_policy(
                sid,
                "job",
                "attempt",
                "epoch",
                router,
                RepoPolicy(),
                execution_token="replacement",
            )
        release.set()
        assert denied(await asyncio.wait_for(pending, timeout=2))
        router.route.assert_not_awaited()
        adapter._hard_block_approval.assert_not_awaited()
        assert not denied(await invoke()), "A new callback on the current execution must still proceed"
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)
        await adapter.abort_session(sid)


def _scoped_permission_adapter(session_factory, *, event_bus=None):
    approvals = ApprovalService(session_factory)
    adapter = CopilotAdapter(approval_service=approvals, event_bus=event_bus)
    config = SessionConfig(
        workspace_path="/test",
        prompt="",
        job_id="job-1",
        recipe_run_id="recipe-run",
        recipe_revision=1,
        step_instance_id="step-instance",
        attempt_id="attempt",
        execution_epoch="epoch",
    )
    adapter.set_job_id("same-session", "job-1")
    adapter.set_session_metadata("same-session", config)
    adapter.attach_attempt_policy(
        "same-session",
        "job-1",
        "attempt",
        "epoch",
        MagicMock(),
        RepoPolicy(),
        execution_token="original-token",
    )
    adapter._queues["same-session"] = asyncio.Queue()
    return adapter, approvals, config


@pytest.mark.parametrize("hard_gate", [False, True])
async def test_permission_request_event_retains_originating_attempt_metadata(session_factory, hard_gate):
    from backend.models.domain import ApprovalResolution

    adapter, approvals, _ = _scoped_permission_adapter(session_factory)
    call = (
        adapter._hard_block_approval("same-session", "job-1", "git reset --hard HEAD")
        if hard_gate
        else adapter._route_to_operator("same-session", "job-1", "Review this action", "action")
    )
    pending = asyncio.create_task(call)
    try:
        event = await asyncio.wait_for(adapter._queues["same-session"].get(), timeout=2)
        assert event.kind == EventKind.approval_requested
        assert event.metadata.recipe_run_id == "recipe-run"
        assert event.metadata.step_instance_id == "step-instance"
        assert event.metadata.attempt_id == "attempt"
        assert event.metadata.execution_epoch == "epoch"
        assert event.metadata.execution_token == "original-token"
        assert event.metadata.run_id == "same-session"
        await approvals.resolve(event.payload["approval_id"], ApprovalResolution.approved)
        assert await asyncio.wait_for(pending, timeout=2) == ApprovalResolution.approved
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.parametrize("hard_gate", [False, True])
@pytest.mark.parametrize("transition", ["replacement", "pause_resume"])
async def test_stale_approval_creation_is_retired_without_publishing_to_replacement(
    session_factory,
    monkeypatch,
    hard_gate,
    transition,
):
    from backend.models.domain import ApprovalResolution

    adapter, approvals, config = _scoped_permission_adapter(session_factory)
    create = approvals.create_request
    entered, release = asyncio.Event(), asyncio.Event()
    created = []

    async def delayed(*args, **kwargs):
        approval = await create(*args, **kwargs)
        created.append(approval.id)
        entered.set()
        await release.wait()
        return approval

    monkeypatch.setattr(approvals, "create_request", delayed)
    call = (
        adapter._hard_block_approval("same-session", "job-1", "git reset --hard HEAD")
        if hard_gate
        else adapter._route_to_operator("same-session", "job-1", "Review this action", "action")
    )
    pending = asyncio.create_task(call)
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        if transition == "replacement":
            adapter.set_session_metadata("same-session", config)
        else:
            adapter.pause_tools("same-session")
            adapter.resume_tools("same-session")
        release.set()
        assert await asyncio.wait_for(pending, timeout=2) == ApprovalResolution.rejected
        assert adapter._queues["same-session"].empty()
        assert await approvals.list_pending("job-1") == []
        retired = await approvals.get(created[0])
        assert retired.resolution == ApprovalResolution.rejected
        assert "Originating execution changed" in retired.notes
    finally:
        release.set()
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


async def test_classification_keeps_original_scope_across_policy_wait_and_replacement(session_factory):
    from backend.services.action_policy.classifier import Classification, RecommendedAction
    from backend.services.adapters.base_adapter import PermissionDecision

    events = EventBus()
    observed = []

    async def record(event):
        observed.append(event)

    events.subscribe(record)
    adapter, _, config = _scoped_permission_adapter(session_factory, event_bus=events)
    entered, release = asyncio.Event(), asyncio.Event()
    classification = Classification(
        RecommendedAction.ALLOW,
        "known_read",
        0,
        "unknown",
        "read_only",
        "file",
        "Read",
    )

    async def delayed(*args, **kwargs):
        entered.set()
        await release.wait()
        return SimpleNamespace(
            proceed=True, classification=classification, recommended_action=RecommendedAction.ALLOW, checkpoint_ref=None
        )

    adapter._policy_router["job-1"] = SimpleNamespace(route=AsyncMock(side_effect=delayed))
    pending = asyncio.create_task(
        adapter._evaluate_permission(
            "same-session",
            "job-1",
            PermissionRequest(kind="read", workspace_path="/test"),
            tool_name="Read",
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        adapter.set_session_metadata("same-session", config)
        adapter.attach_attempt_policy(
            "same-session",
            "job-1",
            "attempt",
            "epoch",
            adapter._policy_router["job-1"],
            RepoPolicy(),
            execution_token="replacement-token",
        )
        release.set()
        assert await asyncio.wait_for(pending, timeout=2) == PermissionDecision.deny
        (event,) = observed
        assert event.kind == EventKind.action_classified
        assert event.metadata.execution_token == "original-token"
        assert event.metadata.recipe_run_id == "recipe-run"
        assert event.metadata.attempt_id == "attempt"
        assert event.metadata.run_id == "same-session"
    finally:
        release.set()
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


async def test_cancellation_after_registration_cleans_handle_and_refuses_late_dispatch(session_factory):
    runtime, adapter, _, _ = _runtime(session_factory)
    adapter.create_session = AsyncMock(return_value="inert-session")
    config = SessionConfig(workspace_path="/test", prompt="must not execute", job_id="job-1")
    registered = asyncio.Event()
    token = None

    async def open_and_wait():
        nonlocal token
        async with runtime.open_attempt_execution("job-1", "attempt", config) as execution:
            token = execution.execution_token
            registered.set()
            await asyncio.Event().wait()
            await runtime.dispatch_current_execution("job-1", token, config.prompt, attempt_id="attempt")

    task = asyncio.create_task(open_and_wait())
    await registered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    adapter.abort_session.assert_awaited_once_with("inert-session")
    adapter.send_message.assert_not_awaited()
    receipt = await runtime.dispatch_current_execution("job-1", token, config.prompt, attempt_id="attempt")
    assert receipt.reason == "no_live_execution"


async def test_hung_abort_cannot_delay_sibling_fences_or_primary_cancellation(session_factory):
    runtime, adapter, _, _ = _runtime(session_factory)
    first = await runtime.register_attempt_execution("job-1", "first", adapter, "session-first")
    second = await runtime.register_attempt_execution("job-1", "second", adapter, "session-second")
    primary_started, primary_cancelled = asyncio.Event(), asyncio.Event()
    abort_entered, release_abort = asyncio.Event(), asyncio.Event()

    async def primary():
        primary_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            primary_cancelled.set()

    async def abort(session_id):
        if session_id == "session-first":
            abort_entered.set()
            await release_abort.wait()

    task = asyncio.create_task(primary())
    runtime._tasks["job-1"] = task
    adapter.abort_session.side_effect = abort
    await primary_started.wait()
    cancel = asyncio.create_task(runtime.cancel("job-1"))
    try:
        await abort_entered.wait()
        await asyncio.wait_for(primary_cancelled.wait(), timeout=1)
        for attempt, token in (("first", first.execution_token), ("second", second.execution_token)):
            binding = runtime._attempt_bindings.get("job-1", attempt)
            assert binding is not None and binding.cancel_requested
            receipt = await runtime.dispatch_current_execution("job-1", token, "late", attempt_id=attempt)
            assert receipt.reason == "no_live_execution"
        assert any(call.args == ("session-second",) for call in adapter.pause_tools.call_args_list)
        assert not cancel.done()
    finally:
        release_abort.set()
        await cancel
        with pytest.raises(asyncio.CancelledError):
            await task
