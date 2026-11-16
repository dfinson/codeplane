from backend.persistence.project_repo import ProjectRepository


async def test_observation_registration_and_readback_never_launch_workspace_or_sdk(
    client, session_factory, mock_runtime_service, mock_git_service,
):
    async with session_factory() as session:
        await ProjectRepository(session).create("observations", "Observations", [])
        await session.commit()
    body = {
        "bindingId": "source-binding", "projectId": "observations",
        "formatId": "copilot", "externalSessionId": "external-vendor-label",
    }
    response = await client.post("/api/integrations/observations", json=body)
    assert response.status_code == 201, response.text
    job = response.json()
    assert job["repo"] is None and job["baseRef"] is None and job["worktreePath"] is None
    assert job["observationOnly"] and job["source"] == "integration"
    assert job["sdk"] == "native:copilot"
    repeated = await client.post("/api/integrations/observations", json=body)
    assert repeated.json()["id"] == job["id"]
    fetched = await client.get(f"/api/jobs/{job['id']}")
    assert fetched.status_code == 200 and fetched.json()["observationOnly"]
    mock_runtime_service.start_or_enqueue.assert_not_awaited()
    mock_git_service.create_worktree.assert_not_awaited()


async def test_observation_registration_rejects_unowned_project_and_invalid_format(client):
    body = {
        "bindingId": "binding", "projectId": "missing", "formatId": "copilot",
        "externalSessionId": "external",
    }
    missing = await client.post("/api/integrations/observations", json=body)
    assert missing.status_code == 400
    invalid = await client.post("/api/integrations/observations", json={**body, "formatId": "../not-a-mapping"})
    assert invalid.status_code == 422
