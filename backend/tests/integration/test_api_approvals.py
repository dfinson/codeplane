"""Integration tests for the approvals and messages API endpoints.

Exercises:
  GET  /api/jobs/{job_id}/approvals
  POST /api/approvals/{approval_id}/resolve
  POST /api/jobs/{job_id}/approvals/trust
  POST /api/jobs/{job_id}/messages
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from backend.models.db import JobRow
from backend.tests.unit.test_ingest_sources import _TASK_ID
from backend.tests.unit.test_ingest_sources import native_control_stack as native_control_stack

if TYPE_CHECKING:
    from unittest.mock import AsyncMock

    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from backend.models.domain import Job
    from backend.services.job.approval_service import ApprovalService

    from .conftest import SeedJobFn


# ---------------------------------------------------------------------------
# List Approvals
# ---------------------------------------------------------------------------


class TestRuntimeContract:
    async def test_native_message_uses_source_aware_runtime_command(self):
        from unittest.mock import AsyncMock

        from backend.api.approvals import send_message
        from backend.models.api_schemas import SendMessageRequest
        from backend.tests.unit.conftest import make_job

        runtime = AsyncMock()
        runtime.send_operator_message.return_value = True
        ingest = AsyncMock()
        jobs = AsyncMock()
        jobs.get_job.return_value = make_job(source="copilot_cli", external_session_id="native-1")
        await send_message("job-1", SendMessageRequest(content="fixture"), runtime, ingest, jobs)
        assert runtime.send_operator_message.await_count == 1
        ingest.send_operator_message.assert_not_awaited()


class TestListApprovals:
    """GET /api/jobs/{job_id}/approvals"""

    @pytest.mark.asyncio
    async def test_empty_when_no_approvals_exist(self, client: AsyncClient, seed_job: SeedJobFn) -> None:
        job_id = await seed_job()
        resp = await client.get(f"/api/jobs/{job_id}/approvals")
        assert resp.status_code == 200
        assert resp.json() == {"items": []}

    @pytest.mark.asyncio
    async def test_returns_created_approvals(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
        approval_service: ApprovalService,
    ) -> None:
        job_id = await seed_job()
        a1 = await approval_service.create_request(job_id, "Deploy to prod?")
        a2 = await approval_service.create_request(job_id, "Scale workers?")

        resp = await client.get(f"/api/jobs/{job_id}/approvals")
        assert resp.status_code == 200
        data = resp.json()["items"]
        assert len(data) == 2
        returned_ids = {item["id"] for item in data}
        assert {a1.id, a2.id} == returned_ids

    @pytest.mark.asyncio
    async def test_response_uses_camel_case_and_expected_shape(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
        approval_service: ApprovalService,
    ) -> None:
        job_id = await seed_job()
        await approval_service.create_request(job_id, "Check permissions?")

        resp = await client.get(f"/api/jobs/{job_id}/approvals")
        item = resp.json()["items"][0]
        assert item["jobId"] == job_id
        assert item["description"] == "Check permissions?"
        assert item["proposedAction"] is None
        assert item["requestedAt"] is not None
        assert item["resolvedAt"] is None
        assert item["resolution"] is None

    @pytest.mark.asyncio
    async def test_only_returns_approvals_for_requested_job(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
        approval_service: ApprovalService,
    ) -> None:
        job_a = await seed_job()
        job_b = await seed_job()
        await approval_service.create_request(job_a, "Job A approval")
        await approval_service.create_request(job_b, "Job B approval")

        resp = await client.get(f"/api/jobs/{job_a}/approvals")
        data = resp.json()["items"]
        assert len(data) == 1
        assert data[0]["description"] == "Job A approval"


# ---------------------------------------------------------------------------
# Resolve Approval
# ---------------------------------------------------------------------------


class TestResolveApproval:
    """POST /api/approvals/{approval_id}/resolve"""

    @pytest.mark.asyncio
    async def test_approve(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
        approval_service: ApprovalService,
    ) -> None:
        job_id = await seed_job()
        approval = await approval_service.create_request(job_id, "Proceed?")

        resp = await client.post(
            f"/api/approvals/{approval.id}/resolve",
            json={"resolution": "approved"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["resolution"] == "approved"
        assert body["resolvedAt"] is not None

    @pytest.mark.asyncio
    async def test_reject(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
        approval_service: ApprovalService,
    ) -> None:
        job_id = await seed_job()
        approval = await approval_service.create_request(job_id, "Proceed?")

        resp = await client.post(
            f"/api/approvals/{approval.id}/resolve",
            json={"resolution": "rejected"},
        )
        assert resp.status_code == 200
        assert resp.json()["resolution"] == "rejected"

    @pytest.mark.asyncio
    async def test_already_resolved_returns_409(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
        approval_service: ApprovalService,
    ) -> None:
        job_id = await seed_job()
        approval = await approval_service.create_request(job_id, "Proceed?")
        await approval_service.resolve(approval.id, "approved")

        resp = await client.post(
            f"/api/approvals/{approval.id}/resolve",
            json={"resolution": "rejected"},
        )
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_nonexistent_approval_returns_404(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/approvals/does-not-exist/resolve",
            json={"resolution": "approved"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_invalid_resolution_value_returns_422(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
        approval_service: ApprovalService,
    ) -> None:
        job_id = await seed_job()
        approval = await approval_service.create_request(job_id, "Proceed?")

        resp = await client.post(
            f"/api/approvals/{approval.id}/resolve",
            json={"resolution": "maybe"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_approving_gated_spawn_task_link_spawns_and_starts_job(
        self,
        client: AsyncClient,
        session_factory: async_sessionmaker[AsyncSession],
        approval_service: ApprovalService,
        mock_runtime_service: AsyncMock,
        tmp_path: object,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Story 5.4, AC #1: approving a `spawn_task:{task_link_id}` approval
        performs the deferred spawn and starts the new job."""
        import asyncio
        from pathlib import Path

        from backend.persistence.project_repo import ProjectRepository
        from backend.persistence.task_link_repo import TaskLinkRepository

        repo_path = str(Path(str(tmp_path)).resolve())
        import subprocess

        subprocess.run(["git", "init", "--quiet", repo_path], check=True)
        subprocess.run(["git", "-C", repo_path, "config", "user.email", "test@test.com"], check=True)
        subprocess.run(["git", "-C", repo_path, "config", "user.name", "test"], check=True)
        (Path(repo_path) / "README.md").write_text("init")
        subprocess.run(["git", "-C", repo_path, "add", "."], check=True)
        subprocess.run(["git", "-C", repo_path, "commit", "-m", "init", "--quiet"], check=True)

        job_id = f"job-{uuid4().hex[:8]}"
        async with session_factory() as session:
            project = await ProjectRepository(session).create("proj-gated", "Gated Project", [repo_path])
            session.add(
                JobRow(
                    id=job_id,
                    repo=repo_path,
                    project_id=project.id,
                    prompt="Test prompt",
                    state="completed",
                    resolution="merged",
                    base_ref="main",
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )
            await session.commit()

            task_link_repo = TaskLinkRepository(session)
            created = await task_link_repo.upsert_many(
                project.id,
                [
                    {
                        "repo_path": repo_path,
                        "story_node_id": "1-2-b",
                        "depends_on": [],
                        "epic_id": "epic-1",
                    }
                ],
            )
            await session.commit()
            dependent_link = created[0]

        approval = await approval_service.create_request(
            job_id, "Ready to spawn", proposed_action=f"spawn_task:{dependent_link.id}"
        )

        runtime_started = asyncio.Event()
        persisted_at_runtime_start: dict[str, bool] = {}

        async def observe_persisted_spawn(job: Job) -> None:
            async with session_factory() as session:
                persisted_at_runtime_start["job"] = await session.get(JobRow, job.id) is not None
                linked = await TaskLinkRepository(session).get(dependent_link.id)
                persisted_at_runtime_start["task_link"] = linked is not None and linked.job_id == job.id
            runtime_started.set()

        mock_runtime_service.setup_and_start.side_effect = observe_persisted_spawn

        resp = await client.post(
            f"/api/approvals/{approval.id}/resolve",
            json={"resolution": "approved"},
        )
        assert resp.status_code == 200
        assert resp.json()["resolution"] == "approved"
        await asyncio.wait_for(runtime_started.wait(), timeout=1)
        assert persisted_at_runtime_start == {"job": True, "task_link": True}

        async with session_factory() as session:
            refreshed = await TaskLinkRepository(session).get(dependent_link.id)
        assert refreshed is not None
        assert refreshed.job_id is not None
        mock_runtime_service.setup_and_start.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_rejecting_gated_spawn_task_link_never_spawns(
        self,
        client: AsyncClient,
        session_factory: async_sessionmaker[AsyncSession],
        approval_service: ApprovalService,
        mock_runtime_service: AsyncMock,
        seed_job: SeedJobFn,
        tmp_path: object,
    ) -> None:
        """Story 5.4, AC #3: rejecting leaves the TaskLink's job_id unset."""
        from pathlib import Path

        from backend.persistence.project_repo import ProjectRepository
        from backend.persistence.task_link_repo import TaskLinkRepository

        repo_path = str(Path(str(tmp_path)).resolve())
        job_id = await seed_job()

        async with session_factory() as session:
            project = await ProjectRepository(session).create("proj-gated-2", "Gated Project 2", [repo_path])
            task_link_repo = TaskLinkRepository(session)
            created = await task_link_repo.upsert_many(
                project.id,
                [
                    {
                        "repo_path": repo_path,
                        "story_node_id": "1-2-c",
                        "depends_on": [],
                        "epic_id": "epic-1",
                    }
                ],
            )
            await session.commit()
            dependent_link = created[0]

        approval = await approval_service.create_request(
            job_id, "Ready to spawn", proposed_action=f"spawn_task:{dependent_link.id}"
        )

        resp = await client.post(
            f"/api/approvals/{approval.id}/resolve",
            json={"resolution": "rejected"},
        )
        assert resp.status_code == 200
        assert resp.json()["resolution"] == "rejected"

        async with session_factory() as session:
            refreshed = await TaskLinkRepository(session).get(dependent_link.id)
        assert refreshed is not None
        assert refreshed.job_id is None
        mock_runtime_service.setup_and_start.assert_not_awaited()


# ---------------------------------------------------------------------------
# Trust Job
# ---------------------------------------------------------------------------


class TestTrustJob:
    """POST /api/jobs/{job_id}/approvals/trust"""

    @pytest.mark.asyncio
    async def test_resolves_all_pending_approvals(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
        approval_service: ApprovalService,
    ) -> None:
        job_id = await seed_job()
        await approval_service.create_request(job_id, "First?")
        await approval_service.create_request(job_id, "Second?")

        resp = await client.post(f"/api/jobs/{job_id}/approvals/trust")
        assert resp.status_code == 200
        assert resp.json()["resolved"] == 2

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_pending(self, client: AsyncClient, seed_job: SeedJobFn) -> None:
        job_id = await seed_job()
        resp = await client.post(f"/api/jobs/{job_id}/approvals/trust")
        assert resp.status_code == 200
        assert resp.json()["resolved"] == 0

    @pytest.mark.asyncio
    async def test_does_not_re_resolve_already_resolved(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
        approval_service: ApprovalService,
    ) -> None:
        job_id = await seed_job()
        approval = await approval_service.create_request(job_id, "Already handled")
        await approval_service.resolve(approval.id, "rejected")

        resp = await client.post(f"/api/jobs/{job_id}/approvals/trust")
        assert resp.status_code == 200
        assert resp.json()["resolved"] == 0


# ---------------------------------------------------------------------------
# Send Message
# ---------------------------------------------------------------------------


class TestSendMessage:
    """POST /api/jobs/{job_id}/messages"""

    @pytest.mark.asyncio
    async def test_send_to_running_job(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
        mock_runtime_service: AsyncMock,
    ) -> None:
        job_id = await seed_job()
        mock_runtime_service.send_operator_message.return_value = True

        resp = await client.post(
            f"/api/jobs/{job_id}/messages",
            json={"content": "Try a different approach"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "seq" in body
        assert "timestamp" in body
        mock_runtime_service.send_operator_message.assert_called_once_with(job_id, "Try a different approach")

    @pytest.mark.asyncio
    async def test_non_running_job_returns_409(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
        mock_runtime_service: AsyncMock,
    ) -> None:
        job_id = await seed_job(state="review")
        mock_runtime_service.send_operator_message.return_value = False

        resp = await client.post(
            f"/api/jobs/{job_id}/messages",
            json={"content": "Hello"},
        )
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_empty_content_returns_422(self, client: AsyncClient, seed_job: SeedJobFn) -> None:
        job_id = await seed_job()
        resp = await client.post(
            f"/api/jobs/{job_id}/messages",
            json={"content": ""},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_missing_content_field_returns_422(self, client: AsyncClient, seed_job: SeedJobFn) -> None:
        job_id = await seed_job()
        resp = await client.post(f"/api/jobs/{job_id}/messages", json={})
        assert resp.status_code == 422


class TestNativeBindingAPI:
    @pytest.mark.parametrize("case", ["accepted", "missing", "mismatch", "off", "rejected", "uncertain"])
    async def test_native_routing_and_typed_failure(self, client, mock_runtime_service, native_control_stack, case):
        import json

        import httpx

        stack = native_control_stack
        if case == "missing":
            stack.workspace.unlink()
        elif case in {"mismatch", "off"}:
            data = json.loads(stack.workspace.read_text(encoding="utf-8"))
            data["mc_session_id" if case == "mismatch" else "remote_steerable"] = (
                _TASK_ID if case == "mismatch" else False
            )
            stack.workspace.write_text(json.dumps(data), encoding="utf-8")
        elif case == "rejected":
            stack.post.return_value = httpx.Response(
                404, text="PRIVATE-BODY", request=httpx.Request("POST", "https://fixture.invalid")
            )
        elif case == "uncertain":
            stack.post.side_effect = httpx.ReadTimeout("PRIVATE-DIAGNOSTIC")
        mock_runtime_service.send_operator_message.side_effect = stack.runtime.send_operator_message
        response = await client.post(f"/api/jobs/{stack.job.id}/messages", json={"content": "fixture"})
        if case == "accepted":
            assert response.status_code == 200 and "seq" in response.json()
        else:
            assert response.status_code == 503
            assert response.json()["detail"]["reason"] == (
                "delivery_unknown" if case == "uncertain" else "delivery_failed"
            )
            assert "PRIVATE" not in response.text
        if case in {"missing", "mismatch", "off"}:
            stack.post.assert_not_awaited()
        else:
            stack.post.assert_awaited_once_with(
                f"https://api.enterprise.githubcopilot.com/agents/tasks/{_TASK_ID}/steer",
                json={"content": "fixture", "type": "user_message"},
            )
        stack.runtime.send_message.assert_not_awaited()
        stack.runtime._resume_orphaned.assert_not_awaited()
