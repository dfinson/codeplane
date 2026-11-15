"""Definition API wiring and nonexecution guarantees."""

from sqlalchemy import func, select

from backend.models.db import JobRow, RecipeRunRow


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
