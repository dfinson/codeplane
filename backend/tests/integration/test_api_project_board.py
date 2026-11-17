"""Full-Project Board totals, combined identity and retained decision reads."""

from datetime import UTC, datetime

from backend.models.db import ApprovalRow, JobRow, TaskLinkRow


def job(job_id, project_id, *, state="running", repo="/historical/repo", archived=False):
    now = datetime.now(UTC)
    return JobRow(
        id=job_id,
        project_id=project_id,
        repo=repo,
        prompt="Exact task prompt",
        state=state,
        base_ref="main",
        created_at=now,
        updated_at=now,
        archived_at=now if archived else None,
        resolution="merged" if state == "completed" else None,
    )


def task(task_id, project_id, *, job_id=None, state="ready"):
    now = datetime.now(UTC)
    return TaskLinkRow(
        id=task_id,
        project_id=project_id,
        repo_path="/historical/repo",
        story_node_id=task_id,
        depends_on="[]",
        chain_root_id=task_id,
        state=state,
        job_id=job_id,
        output_routes="[]",
        created_at=now,
        updated_at=now,
    )


async def project(client, name):
    response = await client.post("/api/settings/projects", json={"name": name, "repoPaths": [f"/current/{name}"]})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def test_board_counts_linked_task_and_job_once_using_persisted_project_owner(client, session_factory):
    owner = await project(client, "board-owner")
    foreign = await project(client, "other-owner")
    async with session_factory() as session:
        session.add_all(
            [
                job("linked", owner, state="review"),
                job("foreign", foreign, repo="/current/board-owner"),
            ]
        )
        await session.flush()
        session.add_all(
            [
                task("ready-task", owner),
                task("blocked-task", owner, state="waiting"),
                task("linked-task", owner, job_id="linked", state="running"),
            ]
        )
        await session.commit()
    response = await client.get(f"/api/settings/projects/{owner}/board")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["totals"]["taskCount"] == 3
    assert body["totals"]["linkedJobCount"] == 1
    assert body["totals"]["standaloneJobCount"] == 0
    assert body["totals"]["reviewJobCount"] == 1
    assert body["totals"]["workCounts"] == {
        "ready_blocked": 2,
        "in_progress": 0,
        "needs_decision": 1,
        "failed": 0,
    }
    cards = [item for page in body["columns"].values() for item in page["items"]]
    assert len(cards) == 3 and len({card["id"] for card in cards}) == 3
    combined = body["columns"]["needs_decision"]["items"][0]
    assert combined["job"]["id"] == "linked"
    assert combined["tasks"][0]["id"] == "linked-task"
    assert all(card["job"] is None or card["job"]["id"] != "foreign" for card in cards)
    overview = (await client.get("/api/settings/projects/summary")).json()["items"]
    owner_summary = next(item for item in overview if item["id"] == owner)
    foreign_summary = next(item for item in overview if item["id"] == foreign)
    assert owner_summary["activeJobCount"] == 0 and owner_summary["awaitingInputCount"] == 1
    assert foreign_summary["activeJobCount"] == 1


async def test_archived_completed_jobs_keep_their_actual_pending_approvals(client, session_factory):
    owner = await project(client, "archived-attention")
    now = datetime.now(UTC)
    async with session_factory() as session:
        session.add(job("archived", owner, state="completed", archived=True))
        await session.flush()
        session.add_all(
            [
                ApprovalRow(
                    id=f"approval-{number}",
                    job_id="archived",
                    description=f"Decision {number}",
                    proposed_action="Exact retained proposal",
                    requested_at=now,
                    requires_explicit_approval=True,
                )
                for number in range(3)
            ]
        )
        await session.commit()
    root = f"/api/settings/projects/{owner}/board"
    board = (await client.get(root)).json()
    assert board["totals"]["pendingApprovalCount"] == 3
    assert board["totals"]["workCounts"]["needs_decision"] == 1
    assert board["columns"]["needs_decision"]["items"][0]["job"]["archivedAt"] is not None
    overview = (await client.get("/api/settings/projects/summary")).json()["items"]
    assert next(item for item in overview if item["id"] == owner)["awaitingInputCount"] == 1
    first = (await client.get(f"{root}/approvals", params={"limit": 1})).json()
    following = (await client.get(f"{root}/approvals", params={"after": first["nextCursor"]})).json()
    assert [entry["id"] for entry in first["items"] + following["items"]] == [
        "approval-0",
        "approval-1",
        "approval-2",
    ]


async def test_board_totals_cover_all_pages_without_fetching_other_projects(client, session_factory):
    owner = await project(client, "paged-board")
    async with session_factory() as session:
        session.add_all([job(f"job-{number:02d}", owner) for number in range(35)])
        await session.commit()
    root = f"/api/settings/projects/{owner}/board"
    board = (await client.get(root)).json()
    page = board["columns"]["in_progress"]
    assert len(page["items"]) == 20
    assert board["totals"]["workCounts"]["in_progress"] == 35
    assert board["totals"]["standaloneJobCount"] == 35
    following = (await client.get(f"{root}/columns/in_progress", params={"after": page["nextCursor"]})).json()
    assert len(following["items"]) == 15 and following["nextCursor"] is None
    assert len({item["id"] for item in page["items"] + following["items"]}) == 35
    assert (await client.get(f"{root}/columns/in_progress", params={"limit": 101})).status_code == 422
    assert (await client.get("/api/settings/projects/missing/board")).status_code == 404


async def test_inspecting_task_retains_full_prompt_and_never_creates_work(
    client, session_factory, mock_runtime_service
):
    from sqlalchemy import func, select

    owner = await project(client, "task-inspection")
    prompt = "First requirement\n" + "Keep every condition in this prompt.\n" * 80
    async with session_factory() as session:
        record = task("inspect", owner)
        record.repo_path = "/current/task-inspection"
        record.prompt_override = prompt
        session.add(record)
        await session.commit()
    response = await client.get(f"/api/settings/projects/{owner}/task-links/inspect")
    assert response.status_code == 200, response.text
    assert response.json()["prompt"] == prompt
    assert response.json()["promptSource"] == "stored_override"
    assert response.json()["canStart"] is True
    assert response.json()["dependencies"] == []
    async with session_factory() as session:
        assert await session.scalar(select(func.count(JobRow.id))) == 0
        assert await session.scalar(select(func.count(ApprovalRow.id))) == 0
    mock_runtime_service.setup_and_start.assert_not_awaited()
