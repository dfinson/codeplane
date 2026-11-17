"""Definition API wiring and nonexecution guarantees."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import func, select

from backend.config import CPLConfig
from backend.models.db import JobRow, RecipeRunRow
from backend.models.domain import JobState
from backend.persistence.job_repo import JobRepository
from backend.persistence.recipe_run_repo import RecipeRunRepository
from backend.services.adapters.adapter_registry import AdapterRegistry
from backend.services.events.event_bus import EventBus
from backend.services.runtime import RuntimeService
from backend.tests.unit.test_recipe_definitions import simple_recipe
from backend.tests.unit.test_recipe_runs import agent_result, create_run, ready_attempt


def definition():
    return {
        "name": "Context and check",
        "entrySteps": ["context"],
        "steps": [
            {
                "id": "context",
                "title": "Context",
                "instructions": "Return the exact context brief.",
                "runner": {"kind": "agent"},
                "capabilities": ["read"],
                "outputs": {"brief": {"type": "string"}},
                "routes": {"succeeded": {"nextSteps": ["check"]}, "failed": {"terminal": "failed"}},
            },
            {
                "id": "check",
                "title": "Command check",
                "runner": {"kind": "command", "argv": ["git", "status", "--porcelain"]},
                "capabilities": ["read", "shell"],
                "inputs": {
                    "brief": {"type": "string", "binding": {"source": "output", "stepId": "context", "output": "brief"}}
                },
                "dependsOn": [{"stepId": "context"}],
                "routes": {"succeeded": {"terminal": "succeeded"}, "failed": {"terminal": "failed"}},
            },
        ],
    }


async def test_draft_validate_publish_copy_never_launches(client, session_factory, mock_runtime_service):
    body = definition()
    created = await client.post("/api/recipes", json=body)
    assert created.status_code == 201, created.text
    recipe_id = created.json()["id"]
    validation = await client.post("/api/recipes/validate", json=body)
    assert validation.status_code == 200 and validation.json() == {"valid": True, "issues": []}
    published = await client.post(f"/api/recipes/{recipe_id}/publish", json={"expectedVersion": 1})
    assert published.status_code == 201, published.text
    revision = published.json()
    assert revision["definition"]["steps"][1]["runner"]["kind"] == "command"
    fetched = await client.get(f"/api/recipes/revisions/{revision['id']}")
    assert fetched.json() == revision
    copied = await client.post(f"/api/recipes/revisions/{revision['id']}/copy", json={"name": "Independent copy"})
    assert copied.status_code == 201 and copied.json()["id"] != recipe_id
    assert (await client.get(f"/api/recipes/{copied.json()['id']}/revisions")).json()["items"] == []
    mock_runtime_service.start_or_enqueue.assert_not_awaited()
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(JobRow)) == 0
        assert await session.scalar(select(func.count()).select_from(RecipeRunRow)) == 0


async def test_invalid_graph_and_stale_version_return_actionable_errors(client):
    body = definition()
    body["steps"][1]["inputs"]["brief"]["binding"]["output"] = "missing"
    created = await client.post("/api/recipes", json=body)
    recipe_id = created.json()["id"]
    published = await client.post(f"/api/recipes/{recipe_id}/publish", json={"expectedVersion": 1})
    assert published.status_code == 422
    assert any(issue["code"] == "unknown_output" for issue in published.json()["detail"]["issues"])
    saved = await client.put(f"/api/recipes/{recipe_id}", json={"expectedVersion": 1, "definition": definition()})
    assert saved.status_code == 200 and saved.json()["version"] == 2
    stale = await client.post(f"/api/recipes/{recipe_id}/publish", json={"expectedVersion": 1})
    assert stale.status_code == 409
    missing = await client.get("/api/recipes/revisions/not-present")
    assert missing.status_code == 404


async def test_list_queries_are_bounded_and_responses_keep_frozen_highwater(client):
    for index in range(3):
        await client.post("/api/recipes", json={"name": f"Draft {index}"})
    first = await client.get("/api/recipes", params={"limit": 1})
    assert first.status_code == 200
    page = first.json()
    await client.post("/api/recipes", json={"name": "Later"})
    second = await client.get(
        "/api/recipes", params={"after": page["nextCursor"], "highWater": page["highWater"], "limit": 100}
    )
    assert [row["definition"]["name"] for row in page["items"] + second.json()["items"]] == [
        "Draft 0",
        "Draft 1",
        "Draft 2",
    ]
    assert (await client.get("/api/recipes", params={"limit": 101})).status_code == 422


async def test_default_recipe_resolution_is_published_idempotent_and_never_launches(
    client, session_factory, mock_runtime_service
):
    first = await client.post("/api/recipes/resolve-default", json={"planFirst": False})
    again = await client.post("/api/recipes/resolve-default", json={"planFirst": False})
    planned = await client.post("/api/recipes/resolve-default", json={"planFirst": True})
    assert first.status_code == again.status_code == planned.status_code == 200
    assert again.json() == first.json()
    assert planned.json()["id"] != first.json()["id"]
    assert any(step["runner"]["kind"] == "human" for step in planned.json()["definition"]["steps"])
    revision = first.json()
    assert (await client.get(f"/api/recipes/revisions/{revision['id']}")).json() == revision
    context = revision["definition"]["steps"][0]
    assert context["capabilities"] == ["read"]
    assert context["instructions"] == (
        "Read the relevant code and instructions. Return a concise, source-backed brief. "
        "Do not edit files or run commands."
    )
    mock_runtime_service.start_or_enqueue.assert_not_awaited()
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(JobRow)) == 0


async def test_changed_default_configuration_creates_a_new_pin_without_rewriting_old_revision(session_factory):
    from backend.persistence.recipe_repo import RecipeRepository
    from backend.services.recipe.definition_service import RecipeDefinitionService

    config = CPLConfig()
    async with session_factory() as session:
        service = RecipeDefinitionService(RecipeRepository(session))
        old = await service.resolve_default(config, plan_first=False)
        await session.commit()
        config.verification.verify = True
        config.verification.verify_prompt = "Run the exact configured verifier."
        new = await service.resolve_default(config, plan_first=False)
        await session.commit()
        assert old.id != new.id
        assert (await service.get_revision(old.id)).model_dump() == old.model_dump()
        assert new.definition.steps[-1].instructions == "Run the exact configured verifier."


async def test_inspection_reads_exact_historical_attempt_without_dispatch(
    client, session_factory, mock_runtime_service
):
    from backend.models.schemas.recipes import RecipeCodeReference

    run = await create_run(session_factory, simple_recipe(), job_id="inspect-history")
    async with session_factory() as session:
        repo = RecipeRunRepository(session)
        first = (await repo.list_steps(run.id))[0]
        attempt = await ready_attempt(session, run, first.step_path)
        await repo.finish_attempt(attempt.id, agent_result(), {"result": "Original evidence"})
        await repo.resolve_run(run.id, "succeeded")
        await session.commit()
    base = f"/api/jobs/{run.job_id}/recipe"
    old_detail = await client.get(f"{base}/runs/{run.id}/steps/{first.id}/attempts/{attempt.id}")
    assert old_detail.status_code == 200, old_detail.text
    history = (await client.get(f"{base}/runs", params={"limit": 1})).json()
    async with session_factory() as session:
        successor = await RecipeRunRepository(session).create(
            run.job_id,
            run.snapshot,
            run.inputs,
            start_request_key="inspect-successor",
            continuation_of_id=run.id,
            instruction="New work",
            code_ref=RecipeCodeReference(workspace="/repo", commit="a" * 40),
        )
        await session.commit()
    current = await client.get(f"{base}/current")
    assert current.json()["run"]["id"] == successor.id
    assert (
        await client.get(f"{base}/runs/{run.id}/steps/{first.id}/attempts/{attempt.id}")
    ).json() == old_detail.json()
    step = (await client.get(f"{base}/runs/{run.id}/steps/{first.id}")).json()
    assert step["attempts"][0]["id"] == attempt.id
    assert step["attempts"][0]["outcome"] == "succeeded"
    assert "outputs" not in step["attempts"][0]
    frozen = (await client.get(f"{base}/runs", params={"highWater": history["highWater"]})).json()
    assert [item["id"] for item in frozen["items"]] == [run.id]
    mock_runtime_service.start_or_enqueue.assert_not_awaited()
    mock_runtime_service.resume_job.assert_not_awaited()


async def test_recipe_inspection_rejects_cross_job_run_step_and_attempt_identity(client, session_factory):
    first = await create_run(session_factory, simple_recipe(), job_id="inspect-one")
    second = await create_run(session_factory, simple_recipe(), job_id="inspect-two")
    async with session_factory() as session:
        repo = RecipeRunRepository(session)
        first_step = (await repo.list_steps(first.id))[0]
        second_step = (await repo.list_steps(second.id))[0]
        first_attempt = await ready_attempt(session, first, first_step.step_path)
        second_attempt = await ready_attempt(session, second, second_step.step_path)
    root = f"/api/jobs/{first.job_id}/recipe"
    for path in (
        f"{root}/runs/{second.id}",
        f"{root}/runs/{first.id}/steps/{second_step.id}",
        f"{root}/runs/{first.id}/steps/{first_step.id}/attempts/{second_attempt.id}",
        f"{root}/runs/{second.id}/steps/{second_step.id}/attempts/{first_attempt.id}",
        "/api/jobs/missing/recipe/current",
    ):
        response = await client.get(path)
        assert response.status_code == 404, response.text


async def test_recipe_step_pages_bound_results_without_loading_attempt_evidence(client, session_factory):
    from backend.models.schemas.recipes import RecipeDefinition
    from backend.tests.unit.test_recipe_definitions import agent

    run = await create_run(
        session_factory,
        RecipeDefinition(
            name="Fanout",
            entry_steps=["alpha", "beta", "gamma"],
            steps=[
                agent("alpha"),
                agent("beta"),
                agent("gamma"),
            ],
        ),
        job_id="inspect-pages",
    )
    root = f"/api/jobs/{run.job_id}/recipe/runs/{run.id}/steps"
    first = (await client.get(root, params={"limit": 1})).json()
    second = (await client.get(root, params={"limit": 100, "after": first["nextCursor"]})).json()
    assert [step["stepPath"] for step in first["items"] + second["items"]] == ["alpha", "beta", "gamma"]
    assert second["nextCursor"] is None
    assert (await client.get(root, params={"limit": 101})).status_code == 422
    assert (await client.get(root, params={"limit": 0})).status_code == 422


async def test_step_inspection_links_pinned_dependencies_and_consumers_without_inventing_receipts(
    client, session_factory
):
    from backend.models.schemas.recipes import RecipeDefinition

    run = await create_run(session_factory, RecipeDefinition.model_validate(definition()), job_id="lineage-job")
    async with session_factory() as session:
        repository = RecipeRunRepository(session)
        producer = await repository.get_step_by_path(run.id, "context")
        consumer = await repository.get_step_by_path(run.id, "check")
    root = f"/api/jobs/{run.job_id}/recipe/runs/{run.id}/steps"
    waiting = (await client.get(f"{root}/{consumer.id}")).json()
    assert waiting["attempts"] == []
    assert waiting["predecessors"][0]["stepId"] == producer.id
    assert waiting["predecessors"][0]["attemptId"] is None
    assert waiting["predecessors"][0]["requiredOutcomes"] == ["succeeded"]
    assert waiting["predecessors"][0]["inputNames"] == ["brief"]
    async with session_factory() as session:
        attempt = await ready_attempt(session, run, "context")
        await RecipeRunRepository(session).finish_attempt(attempt.id, agent_result(), {"brief": "Exact source brief"})
        await session.commit()
    producer_detail = (await client.get(f"{root}/{producer.id}")).json()
    assert producer_detail["consumers"][0]["stepId"] == consumer.id
    assert producer_detail["consumers"][0]["attemptId"] is None
    after = (await client.get(f"{root}/{consumer.id}")).json()
    assert after["predecessors"][0]["attemptId"] == attempt.id
    assert after["predecessors"][0]["state"] == "succeeded"
    assert after["attempts"] == [], "Reading a ready input cannot claim or dispatch the waiting consumer."


async def test_job_creation_pins_selected_revision_and_request_before_background_setup(
    client,
    session_factory,
    mock_runtime_service,
):
    from backend.persistence.project_repo import ProjectRepository

    async with session_factory() as session:
        await ProjectRepository(session).create("project", "Recipe project", ["/test/repo"])
        await session.commit()
    body = {
        "name": "Pinned launch",
        "parameters": {"request": {"type": "string"}},
        "entrySteps": ["work"],
        "steps": [
            {
                "id": "work",
                "title": "Work",
                "instructions": "Original pinned instructions",
                "runner": {"kind": "agent"},
                "capabilities": ["read"],
                "inputs": {"request": {"type": "string", "binding": {"source": "parameter", "parameter": "request"}}},
                "routes": {"succeeded": {"terminal": "succeeded"}, "failed": {"terminal": "failed"}},
            }
        ],
    }
    draft = (await client.post("/api/recipes", json=body)).json()
    publication = await client.post(f"/api/recipes/{draft['id']}/publish", json={"expectedVersion": 1})
    assert publication.status_code == 201, publication.text
    revision = publication.json()
    response = await client.post(
        "/api/jobs",
        json={
            "repo": "/test/repo",
            "projectId": "project",
            "prompt": "Actual launch request",
            "title": "Pinned launch",
            "branch": "recipe-launch",
            "worktreeName": "recipe-launch",
            "recipe": {"revisionId": revision["id"]},
        },
    )
    assert response.status_code == 201, response.text
    job_id = response.json()["id"]
    async with session_factory() as session:
        run = await RecipeRunRepository(session).get_for_job(job_id)
        assert run.revision_id == revision["id"]
        assert run.inputs == {"request": "Actual launch request"}
        assert run.snapshot.definition.steps[0].instructions == "Original pinned instructions"
        assert await session.scalar(select(func.count()).select_from(RecipeRunRow)) == 1
    body["steps"][0]["instructions"] = "Later draft edit must not affect the run"
    saved = await client.put(f"/api/recipes/{draft['id']}", json={"expectedVersion": 2, "definition": body})
    assert saved.status_code == 200, saved.text
    async with session_factory() as session:
        assert (await RecipeRunRepository(session).get_for_job(job_id)).snapshot.definition.steps[0].instructions == (
            "Original pinned instructions"
        )


async def test_invalid_recipe_launch_inputs_leave_no_job_or_run(client, session_factory, mock_runtime_service):
    body = {
        "name": "Typed launch",
        "parameters": {"count": {"type": "integer"}},
        "entrySteps": ["work"],
        "steps": [
            {
                "id": "work",
                "title": "Work",
                "instructions": "Read the supplied count and return the requested work.",
                "runner": {"kind": "agent"},
                "capabilities": ["read"],
                "inputs": {
                    "count": {"type": "integer", "binding": {"source": "parameter", "parameter": "count"}},
                },
                "routes": {"succeeded": {"terminal": "succeeded"}, "failed": {"terminal": "failed"}},
            }
        ],
    }
    draft = (await client.post("/api/recipes", json=body)).json()
    publication = await client.post(f"/api/recipes/{draft['id']}/publish", json={"expectedVersion": 1})
    assert publication.status_code == 201, publication.text
    revision = publication.json()
    response = await client.post(
        "/api/jobs",
        json={
            "repo": "/test/repo",
            "prompt": "Must not start",
            "recipe": {"revisionId": revision["id"], "parameters": {"count": True}},
        },
    )
    assert response.status_code == 422, response.text
    assert any(issue["code"] == "type_mismatch" for issue in response.json()["detail"]["issues"])
    mock_runtime_service.setup_and_start.assert_not_awaited()
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(JobRow)) == 0
        assert await session.scalar(select(func.count()).select_from(RecipeRunRow)) == 0


async def test_recipe_naming_failure_keeps_explicit_recipe_ownership_without_starting(
    client,
    session_factory,
    mock_runtime_service,
    monkeypatch,
):
    from backend.persistence.project_repo import ProjectRepository
    from backend.services.completers.naming_service import NamingError
    from backend.services.job.job_service import JobService

    async with session_factory() as session:
        await ProjectRepository(session).create("project", "Recipe project", ["/test/repo"])
        await session.commit()
    draft = (await client.post("/api/recipes", json=definition())).json()
    publication = await client.post(f"/api/recipes/{draft['id']}/publish", json={"expectedVersion": 1})
    assert publication.status_code == 201, publication.text
    ownership = []
    persist = JobService.persist_prepared_job

    async def record_owner(service, prepared, **kwargs):
        ownership.append(kwargs.get("recipe_admission"))
        return await persist(service, prepared, **kwargs)

    monkeypatch.setattr(JobService, "persist_prepared_job", record_owner)
    monkeypatch.setattr(JobService, "_resolve_job_name", AsyncMock(side_effect=NamingError("Naming unavailable")))
    response = await client.post(
        "/api/jobs",
        json={
            "repo": "/test/repo",
            "projectId": "project",
            "prompt": "Retain recipe ownership",
            "recipe": {"revisionId": publication.json()["id"]},
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["state"] == "failed"
    assert ownership == [True]
    async with session_factory() as session:
        repository = RecipeRunRepository(session)
        run = await repository.get_for_job(response.json()["id"])
        assert run is not None and run.revision_id == publication.json()["id"]
        assert run.code_ref is None and not await repository.has_attempts(run.id)
    mock_runtime_service.setup_and_start.assert_not_awaited()


async def test_explicit_resume_response_retains_the_exact_run_receipt(client, session_factory, mock_runtime_service):
    run = await create_run(session_factory, simple_recipe())
    async with session_factory() as session:
        job = await JobRepository(session).get(run.job_id)
    mock_runtime_service.resume_recipe_run.return_value = (job, run)
    response = await client.post(
        f"/api/jobs/{job.id}/resume",
        json={
            "instruction": "Explicit next instruction",
            "expectedRunId": run.id,
            "startRequestKey": "request-key",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["recipeRun"]["id"] == run.id
    mock_runtime_service.resume_recipe_run.assert_awaited_once_with(
        job.id,
        "Explicit next instruction",
        expected_run_id=run.id,
        start_request_key="request-key",
        selection=None,
    )
    mock_runtime_service.resume_job.assert_not_awaited()


@pytest.mark.parametrize("case", ["accepted", "pending", "replaced", "uncertain", "failed"])
async def test_attempt_message_api_uses_only_exact_live_execution(
    client, session_factory, mock_runtime_service, approval_service, case
):
    from backend.models.schemas.recipes import RecipeAttemptExecution
    from backend.persistence.event_repo import EventRepository
    from backend.services.completers.copilot_steer import NativeDeliveryError

    run = await create_run(session_factory, simple_recipe(), job_id="controlled-job")
    async with session_factory() as session:
        attempt = await ready_attempt(session, run, "context")
    adapter = MagicMock()
    adapter.send_message = AsyncMock()
    registry = MagicMock()
    registry.get_adapter.return_value = adapter
    actual = RuntimeService(
        session_factory=session_factory,
        event_bus=EventBus(),
        adapter_registry=registry,
        config=CPLConfig(),
        approval_service=approval_service,
    )
    live = await actual.register_attempt_execution(run.job_id, attempt.id, adapter, "actual-session")
    async with session_factory() as session:
        await RecipeRunRepository(session).record_execution(
            attempt.id,
            RecipeAttemptExecution(
                execution_token=live.execution_token,
                session_id="actual-session",
                provider="copilot",
                model=None,
                started_at=datetime.now(UTC),
            ),
        )
        await session.commit()
    mock_runtime_service._execution_control = actual._execution_control
    mock_runtime_service._attempt_bindings = actual._attempt_bindings
    mock_runtime_service._recipe_controllers = actual._recipe_controllers
    mock_runtime_service._config = actual._config
    mock_runtime_service.execution_epoch = actual.execution_epoch
    mock_runtime_service._require_attempt_job = actual._require_attempt_job
    mock_runtime_service.dispatch_current_execution = actual.dispatch_current_execution
    actual._resume_orphaned = AsyncMock()
    root = f"/api/jobs/{run.job_id}/recipe/runs/{run.id}/steps/{attempt.step_instance_id}/attempts/{attempt.id}"
    preview = await client.get(f"{root}/control")
    assert preview.status_code == 200 and preview.json()["canMessage"]
    assert preview.json()["executionToken"] == live.execution_token
    approval = None
    if case == "pending":
        approval = await approval_service.create_request(
            run.job_id, "Current hard gate", requires_explicit_approval=True
        )
    elif case == "replaced":
        await actual.unregister_attempt_execution(run.job_id, attempt.id, live.execution_token)
        await actual.register_attempt_execution(run.job_id, attempt.id, adapter, "actual-session")
    elif case in {"uncertain", "failed"}:
        adapter.send_message.side_effect = NativeDeliveryError(
            "Provider delivery result", uncertain=case == "uncertain"
        )
    response = await client.post(
        f"{root}/messages",
        json={
            "expectedExecution": live.execution_token,
            "content": "Only this exact attempt",
        },
    )
    assert response.status_code == (409 if case in {"pending", "replaced"} else 200), response.text
    assert response.json()["status"] == ("conflict" if case in {"pending", "replaced"} else case)
    if case in {"pending", "replaced"}:
        adapter.send_message.assert_not_awaited()
    else:
        adapter.send_message.assert_awaited_once_with("actual-session", "Only this exact attempt")
    if approval:
        assert (await approval_service.get(approval.id)).resolution is None
    if case == "accepted":
        receipt_id = response.json()["messageEventId"]
        async with session_factory() as session:
            event = await EventRepository(session).get(receipt_id)
        assert event.metadata.recipe_run_id == run.id and event.metadata.step_instance_id == attempt.step_instance_id
        assert event.metadata.attempt_id == attempt.id and event.metadata.execution_token == live.execution_token
        assert event.metadata.run_id == "actual-session" and event.payload["execution_status"] == "unconfirmed"
    actual._resume_orphaned.assert_not_awaited()
    adapter.resume_tools.assert_not_called()
    mock_runtime_service.resume_job.assert_not_awaited()


@pytest.mark.parametrize("channel", ["rest", "mcp"])
@pytest.mark.parametrize("case", ["active", "completed", "unexpected_primary"])
async def test_legacy_messages_cannot_start_or_unpause_a_recipe_primary(
    client,
    session_factory,
    mock_runtime_service,
    approval_service,
    mock_merge_service,
    channel,
    case,
):
    run = await create_run(session_factory, simple_recipe())
    if case == "completed":
        async with session_factory() as session:
            repo = RecipeRunRepository(session)
            attempt = await ready_attempt(session, run, "context")
            await repo.finish_attempt(attempt.id, agent_result(), {"result": "Retained completion"})
            await repo.resolve_run(run.id, "succeeded")
            await JobRepository(session).update_state(run.job_id, JobState.completed, datetime.now(UTC))
            await session.commit()
    actual = RuntimeService(
        session_factory=session_factory,
        event_bus=EventBus(),
        adapter_registry=AdapterRegistry(),
        config=CPLConfig(),
    )
    actual._resume_orphaned = AsyncMock(return_value=False)
    primary = MagicMock()
    primary.send_message = AsyncMock()
    if case == "unexpected_primary":
        actual._agent_sessions[run.job_id] = primary
    mock_runtime_service.send_operator_message.side_effect = actual.send_operator_message
    if channel == "rest":
        response = await client.post(f"/api/jobs/{run.job_id}/messages", json={"content": "Do not resume implicitly"})
        assert response.status_code == 409, response.text
        assert "selected attempt" in response.text
    else:
        from backend.mcp.server import create_mcp_server

        server = create_mcp_server(
            session_factory=session_factory,
            runtime_service=actual,
            approval_service=approval_service,
            merge_service=mock_merge_service,
        )
        result = await server._tool_manager._tools["codeplane_job"].fn(
            action="message",
            job_id=run.job_id,
            content="Do not resume implicitly",
        )
        assert "selected attempt" in result["error"]
    actual._resume_orphaned.assert_not_awaited()
    primary.send_message.assert_not_awaited()
    primary.resume_tools.assert_not_called()
    assert not actual._tasks
