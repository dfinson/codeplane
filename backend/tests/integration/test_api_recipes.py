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
