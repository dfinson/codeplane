from backend.persistence.event_repo import EventRepository
from backend.persistence.project_repo import ProjectRepository
from backend.services.events.sse_manager import SSEConnection


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


async def test_records_use_processor_and_durable_checkpoint_without_retry_duplicates(
    client, session_factory, mock_runtime_service, mock_git_service, event_bus, sse_manager,
):
    async with session_factory() as session:
        await ProjectRepository(session).create("observations", "Observations", [])
        await session.commit()
    created = await client.post("/api/integrations/observations", json={
        "bindingId": "records", "projectId": "observations", "formatId": "copilot", "externalSessionId": "source",
    })
    job_id = created.json()["id"]
    connection = SSEConnection(job_id=job_id)
    sse_manager.register(connection)

    async def deliver(event):
        async with session_factory() as session:
            cursor = await EventRepository(session).append(event)
            await session.commit()
        await sse_manager.broadcast_domain_event(event, storage_cursor=cursor)

    event_bus.subscribe(deliver, required=True)
    first = {"sourceKey": "events", "startIndex": 0, "records": [
        {"type": "user.message", "data": {"content": "Read the project"}},
        {"type": "tool.execution_start", "data": {
            "toolCallId": "tool", "toolName": "view", "arguments": {"path": "a"},
        }},
    ]}
    accepted = await client.post("/api/integrations/records/records", json=first)
    assert accepted.status_code == 200, accepted.text
    receipt = accepted.json()
    assert receipt["nextIndex"] == 2
    first_frame_count = connection.queue.qsize()
    repeat = await client.post("/api/integrations/records/records", json=first)
    assert repeat.status_code == 200 and repeat.json()["replay"]
    assert connection.queue.qsize() == first_frame_count
    second = {"sourceKey": "events", "startIndex": 2, "prefixSha256": receipt["prefixSha256"], "records": [
        {"type": "tool.execution_complete", "data": {
            "toolCallId": "tool", "success": True, "result": {"content": "data"},
        }},
    ]}
    finished = await client.post("/api/integrations/records/records", json=second)
    assert finished.status_code == 200, finished.text
    earlier_retry = await client.post("/api/integrations/records/records", json=first)
    assert earlier_retry.status_code == 200 and earlier_retry.json()["replay"]
    response = await client.get("/api/integrations/records/events")
    assert response.status_code == 200
    events = response.json()["events"]
    completed = [event for event in events if event["kind"] == "tool.call.completed"]
    assert len(completed) == 1
    assert completed[0]["payload"]["tool_name"] == "view"
    assert completed[0]["metadata"]["integration_binding_id"] == "records"
    assert completed[0]["metadata"]["duration_ms"] is None
    assert completed[0]["session_id"] == job_id
    assert any(event["kind"] == "step.started" for event in events)
    assert any(event["kind"] == "turn.summary" for event in events)
    frames = []
    while not connection.queue.empty():
        frames.append(connection.queue.get_nowait())
    completion_frames = [frame for frame in frames if "event: tool.call.completed\n" in frame]
    assert len(completion_frames) == 1
    assert completion_frames[0].startswith("id: ")
    assert completed[0]["id"] in completion_frames[0]
    event_bus.unsubscribe(deliver)
    sse_manager.unregister(connection)
    mock_runtime_service.start_or_enqueue.assert_not_awaited()
    mock_git_service.create_worktree.assert_not_awaited()
    async with session_factory() as session:
        assert len(await EventRepository(session).list_after(0, job_id=job_id)) == len(events)


async def test_whole_invalid_batch_and_oversized_body_write_nothing(client, session_factory):
    async with session_factory() as session:
        await ProjectRepository(session).create("observations", "Observations", [])
        await session.commit()
    await client.post("/api/integrations/observations", json={
        "bindingId": "invalid", "projectId": "observations", "formatId": "copilot", "externalSessionId": "source",
    })
    invalid = await client.post("/api/integrations/invalid/records", json={
        "sourceKey": "events", "startIndex": 0, "records": [
            {"type": "user.message", "data": {"content": "Valid prefix"}},
            {"type": "assistant.message", "timestamp": "invalid-time", "data": {"content": "Invalid suffix"}},
        ],
    })
    assert invalid.status_code == 400
    assert (await client.get("/api/integrations/invalid/events")).json()["events"] == []
    oversized = await client.post(
        "/api/integrations/invalid/records", content=b" " * (1024 * 1024 + 1),
        headers={"content-type": "application/json"},
    )
    assert oversized.status_code == 413
    at_limit = await client.post(
        "/api/integrations/invalid/records", content=b" " * (1024 * 1024),
        headers={"content-type": "application/json"},
    )
    assert at_limit.status_code == 422  # Bounded, but not a JSON record batch.
    assert (await client.get("/api/integrations/invalid/events")).json()["events"] == []


async def test_binding_ids_are_addressable_without_restricting_source_identities(client, session_factory):
    async with session_factory() as session:
        await ProjectRepository(session).create("observations", "Observations", [])
        await session.commit()
    body = {
        "bindingId": "team/session", "projectId": "observations",
        "formatId": "copilot", "externalSessionId": "vendor/session",
    }
    rejected = await client.post("/api/integrations/observations", json=body)
    assert rejected.status_code == 422
    created = await client.post("/api/integrations/observations", json={**body, "bindingId": "team_session"})
    assert created.status_code == 201
    accepted = await client.post("/api/integrations/team_session/records", json={
        "sourceKey": "channel/with/slashes", "startIndex": 0,
        "records": [{"type": "assistant.message", "data": {"content": "Retained"}}],
    })
    assert accepted.status_code == 200
    retained = (await client.get("/api/integrations/team_session/events")).json()["events"]
    message = next(event for event in retained if event["kind"] == "message.assistant")
    assert message["metadata"]["run_id"] == "vendor/session"
    assert message["payload"]["_codeplane_source"]["source_key"] == "channel/with/slashes"
