"""Integration tests for the approvals and messages API endpoints.

Exercises:
  GET  /api/jobs/{job_id}/approvals
  POST /api/approvals/{approval_id}/resolve
  POST /api/jobs/{job_id}/approvals/trust
  POST /api/jobs/{job_id}/messages
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from unittest.mock import AsyncMock

    from httpx import AsyncClient

    from backend.services.approval_service import ApprovalService

    from .conftest import SeedJobFn


# ---------------------------------------------------------------------------
# List Approvals
# ---------------------------------------------------------------------------


class TestListApprovals:
    """GET /api/jobs/{job_id}/approvals"""

    @pytest.mark.asyncio
    async def test_empty_when_no_approvals_exist(self, client: AsyncClient, seed_job: SeedJobFn) -> None:
        job_id = await seed_job()
        resp = await client.get(f"/api/jobs/{job_id}/approvals")
        assert resp.status_code == 200
        assert resp.json() == []

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
        data = resp.json()
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
        item = resp.json()[0]
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
        data = resp.json()
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
        mock_runtime_service.send_message.return_value = True

        resp = await client.post(
            f"/api/jobs/{job_id}/messages",
            json={"content": "Try a different approach"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "seq" in body
        assert "timestamp" in body
        mock_runtime_service.send_message.assert_called_once_with(job_id, "Try a different approach")

    @pytest.mark.asyncio
    async def test_non_running_job_returns_409(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
        mock_runtime_service: AsyncMock,
    ) -> None:
        job_id = await seed_job(state="succeeded")
        mock_runtime_service.send_message.return_value = False

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
