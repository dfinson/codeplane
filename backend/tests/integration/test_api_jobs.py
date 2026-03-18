"""API integration tests for Jobs and Health endpoints.

Tests exercise the full request → route → service → DB path using
the shared ``conftest.py`` fixtures (in-memory SQLite, mocked runtime
and merge services, real JobService + EventBus).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.services.job_service import JobNotFoundError, StateConflictError

if TYPE_CHECKING:
    from fastapi import FastAPI
    from httpx import AsyncClient

    from .conftest import SeedJobFn


# ── Module-wide fixture: patch GitService so create/rerun/continue ──
# ── routes use the mock instead of hitting real git ──────────────────


@pytest.fixture(autouse=True)
def _patch_for_job_creation(
    monkeypatch: pytest.MonkeyPatch,
    mock_git_service: AsyncMock,
    app: FastAPI,
) -> None:
    """Replace GitService in the jobs module and disable NamingService."""
    monkeypatch.setattr(
        "backend.api.jobs.GitService", lambda config: mock_git_service
    )
    # Disable NamingService (requires LLM); hash-based fallback is fine
    app.state.utility_session = None


# ── Helpers ──────────────────────────────────────────────────────────


def _create_body(**overrides: object) -> dict[str, object]:
    """Minimal valid POST /api/jobs body."""
    base: dict[str, object] = {"repo": "/test/repo", "prompt": "Fix bug"}
    base.update(overrides)
    return base


@dataclass
class FakeMergeResult:
    status: str = "merged"
    pr_url: str | None = None
    conflict_files: list[str] | None = None
    strategy: str | None = None
    error: str | None = None


# ── Health ───────────────────────────────────────────────────────────


class TestHealth:
    async def test_health_returns_200(self, client: AsyncClient) -> None:
        resp = await client.get("/api/health")
        assert resp.status_code == 200

    async def test_health_response_structure(self, client: AsyncClient) -> None:
        data = (await client.get("/api/health")).json()
        assert data["status"] == "healthy"
        assert data["version"] == "0.1.0"
        assert isinstance(data["uptimeSeconds"], (int, float))
        assert isinstance(data["activeJobs"], int)
        assert isinstance(data["queuedJobs"], int)


# ── Jobs CRUD ────────────────────────────────────────────────────────


class TestJobsCrud:
    # ── Create ──

    async def test_create_job_returns_201(self, client: AsyncClient) -> None:
        resp = await client.post("/api/jobs", json=_create_body())
        assert resp.status_code == 201
        data = resp.json()
        assert data["id"]
        assert data["state"] in ("queued", "running")
        assert "createdAt" in data

    async def test_create_job_response_fields(self, client: AsyncClient) -> None:
        resp = await client.post("/api/jobs", json=_create_body())
        data = resp.json()
        assert data["sdk"] == "copilot"
        assert data.get("branch") is not None or data.get("worktreePath") is not None

    async def test_create_job_invalid_repo(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/jobs", json=_create_body(repo="/not/allowed")
        )
        assert resp.status_code == 400

    async def test_create_job_missing_prompt(self, client: AsyncClient) -> None:
        resp = await client.post("/api/jobs", json={"repo": "/test/repo"})
        assert resp.status_code == 422

    async def test_create_job_with_optional_fields(self, client: AsyncClient) -> None:
        body = _create_body(
            base_ref="develop",
            model="gpt-4",
            verify=True,
            self_review=True,
            max_turns=3,
        )
        resp = await client.post("/api/jobs", json=body)
        assert resp.status_code == 201

    async def test_create_job_calls_runtime_start(
        self, client: AsyncClient, mock_runtime_service: AsyncMock
    ) -> None:
        await client.post("/api/jobs", json=_create_body())
        mock_runtime_service.start_or_enqueue.assert_called_once()

    # ── List ──

    async def test_list_jobs_empty(self, client: AsyncClient) -> None:
        resp = await client.get("/api/jobs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["hasMore"] is False

    async def test_list_jobs_returns_seeded(
        self, client: AsyncClient, seed_job: SeedJobFn
    ) -> None:
        jid = await seed_job(state="running")
        resp = await client.get("/api/jobs")
        data = resp.json()
        ids = [j["id"] for j in data["items"]]
        assert jid in ids

    async def test_list_jobs_state_filter(
        self, client: AsyncClient, seed_job: SeedJobFn
    ) -> None:
        await seed_job(state="running", job_id="run-1")
        await seed_job(state="succeeded", job_id="succ-1")

        resp = await client.get("/api/jobs", params={"state": "succeeded"})
        data = resp.json()
        assert all(j["state"] == "succeeded" for j in data["items"])

    async def test_list_jobs_pagination(
        self, client: AsyncClient, seed_job: SeedJobFn
    ) -> None:
        for i in range(3):
            await seed_job(state="running", job_id=f"page-{i}")

        resp = await client.get("/api/jobs", params={"limit": 2})
        data = resp.json()
        assert len(data["items"]) == 2
        assert data["hasMore"] is True
        assert data["cursor"] is not None

        resp2 = await client.get(
            "/api/jobs", params={"limit": 2, "cursor": data["cursor"]}
        )
        data2 = resp2.json()
        assert len(data2["items"]) >= 1

    async def test_list_jobs_archived_filter(
        self, client: AsyncClient, seed_job: SeedJobFn
    ) -> None:
        await seed_job(
            state="succeeded",
            job_id="archived-1",
            archived_at=datetime.now(UTC),
        )
        await seed_job(state="running", job_id="active-1")

        resp = await client.get("/api/jobs", params={"archived": True})
        data = resp.json()
        ids = [j["id"] for j in data["items"]]
        assert "archived-1" in ids
        assert "active-1" not in ids

    # ── Get ──

    async def test_get_job(
        self, client: AsyncClient, seed_job: SeedJobFn
    ) -> None:
        jid = await seed_job(state="running", job_id="get-1")
        resp = await client.get(f"/api/jobs/{jid}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == jid
        assert data["state"] == "running"
        assert data["repo"] == "/test/repo"

    async def test_get_job_not_found(self, client: AsyncClient) -> None:
        resp = await client.get("/api/jobs/nonexistent")
        assert resp.status_code == 404

    # ── Cancel ──

    async def test_cancel_running_job(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
        mock_runtime_service: AsyncMock,
    ) -> None:
        jid = await seed_job(state="running", job_id="cancel-1")
        resp = await client.post(f"/api/jobs/{jid}/cancel")
        assert resp.status_code == 200
        assert resp.json()["state"] == "canceled"
        mock_runtime_service.cancel.assert_called_once_with(jid)

    async def test_cancel_queued_job(
        self, client: AsyncClient, seed_job: SeedJobFn
    ) -> None:
        jid = await seed_job(state="queued", job_id="cancel-q")
        resp = await client.post(f"/api/jobs/{jid}/cancel")
        assert resp.status_code == 200
        assert resp.json()["state"] == "canceled"

    async def test_cancel_already_terminal_job(
        self, client: AsyncClient, seed_job: SeedJobFn
    ) -> None:
        jid = await seed_job(state="succeeded", job_id="cancel-term")
        resp = await client.post(f"/api/jobs/{jid}/cancel")
        assert resp.status_code == 409

    async def test_cancel_not_found(self, client: AsyncClient) -> None:
        resp = await client.post("/api/jobs/ghost/cancel")
        assert resp.status_code == 404

    # ── Rerun ──

    async def test_rerun_job(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
        mock_runtime_service: AsyncMock,
    ) -> None:
        jid = await seed_job(state="succeeded", job_id="rerun-1")
        resp = await client.post(f"/api/jobs/{jid}/rerun")
        assert resp.status_code == 201
        data = resp.json()
        # New job should have a different ID
        assert data["id"] != jid
        mock_runtime_service.start_or_enqueue.assert_called()

    async def test_rerun_not_found(self, client: AsyncClient) -> None:
        resp = await client.post("/api/jobs/ghost/rerun")
        assert resp.status_code == 404


# ── Job Control ──────────────────────────────────────────────────────


class TestJobControl:
    # ── Pause ──

    async def test_pause_running_job(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
        mock_runtime_service: AsyncMock,
    ) -> None:
        mock_runtime_service.pause_job.return_value = True
        jid = await seed_job(state="running", job_id="pause-1")
        resp = await client.post(f"/api/jobs/{jid}/pause")
        assert resp.status_code == 204
        mock_runtime_service.pause_job.assert_called_once_with(jid)

    async def test_pause_not_running_returns_409(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
        mock_runtime_service: AsyncMock,
    ) -> None:
        mock_runtime_service.pause_job.return_value = False
        jid = await seed_job(state="running", job_id="pause-nr")
        resp = await client.post(f"/api/jobs/{jid}/pause")
        assert resp.status_code == 409

    async def test_pause_not_found(self, client: AsyncClient) -> None:
        resp = await client.post("/api/jobs/ghost/pause")
        assert resp.status_code == 404

    # ── Continue ──

    async def test_continue_job(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
        mock_runtime_service: AsyncMock,
    ) -> None:
        jid = await seed_job(state="succeeded", job_id="cont-1")
        resp = await client.post(
            f"/api/jobs/{jid}/continue",
            json={"instruction": "Add tests"},
        )
        assert resp.status_code == 201
        data = resp.json()
        # New follow-up job created
        assert data["id"] != jid
        mock_runtime_service.start_or_enqueue.assert_called()

    async def test_continue_not_found(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/jobs/ghost/continue",
            json={"instruction": "Add tests"},
        )
        assert resp.status_code == 404

    async def test_continue_missing_instruction(
        self, client: AsyncClient, seed_job: SeedJobFn
    ) -> None:
        jid = await seed_job(state="succeeded", job_id="cont-missing")
        resp = await client.post(f"/api/jobs/{jid}/continue", json={})
        assert resp.status_code == 422

    # ── Resume ──

    async def test_resume_terminal_job(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
        mock_runtime_service: AsyncMock,
    ) -> None:
        jid = await seed_job(state="failed", job_id="resume-1")
        fake_job = MagicMock()
        fake_job.id = jid
        fake_job.repo = "/test/repo"
        fake_job.prompt = "Test prompt"
        fake_job.title = None
        fake_job.state = "running"
        fake_job.base_ref = "main"
        fake_job.worktree_path = "/tmp/wt"
        fake_job.branch = "fix/branch"
        fake_job.permission_mode = None
        fake_job.created_at = datetime.now(UTC)
        fake_job.updated_at = datetime.now(UTC)
        fake_job.completed_at = None
        fake_job.pr_url = None
        fake_job.merge_status = None
        fake_job.resolution = None
        fake_job.archived_at = None
        fake_job.failure_reason = None
        fake_job.model = None
        fake_job.worktree_name = None
        fake_job.verify = None
        fake_job.self_review = None
        fake_job.max_turns = None
        fake_job.verify_prompt = None
        fake_job.self_review_prompt = None
        mock_runtime_service.resume_job.return_value = fake_job

        resp = await client.post(
            f"/api/jobs/{jid}/resume",
            json={"instruction": "Continue with the fix"},
        )
        assert resp.status_code == 200
        assert resp.json()["state"] == "running"
        mock_runtime_service.resume_job.assert_called_once_with(
            jid, "Continue with the fix"
        )

    async def test_resume_not_found(
        self,
        client: AsyncClient,
        mock_runtime_service: AsyncMock,
    ) -> None:
        mock_runtime_service.resume_job.side_effect = JobNotFoundError(
            "Job ghost not found"
        )
        resp = await client.post(
            "/api/jobs/ghost/resume",
            json={"instruction": "Retry"},
        )
        assert resp.status_code == 404

    async def test_resume_state_conflict(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
        mock_runtime_service: AsyncMock,
    ) -> None:
        jid = await seed_job(state="running", job_id="resume-active")
        mock_runtime_service.resume_job.side_effect = StateConflictError(
            "Job is still running"
        )
        resp = await client.post(
            f"/api/jobs/{jid}/resume",
            json={"instruction": "Something"},
        )
        assert resp.status_code == 409

    async def test_resume_missing_instruction(
        self, client: AsyncClient, seed_job: SeedJobFn
    ) -> None:
        jid = await seed_job(state="failed", job_id="resume-bad")
        resp = await client.post(f"/api/jobs/{jid}/resume", json={})
        assert resp.status_code == 422

    # ── Models ──

    async def test_list_models_empty(self, client: AsyncClient) -> None:
        resp = await client.get("/api/models")
        assert resp.status_code == 200
        assert resp.json() == []


# ── Job Data ─────────────────────────────────────────────────────────


class TestJobData:
    # ── Logs ──

    async def test_logs_empty(
        self, client: AsyncClient, seed_job: SeedJobFn
    ) -> None:
        jid = await seed_job(state="running", job_id="logs-1")
        resp = await client.get(f"/api/jobs/{jid}/logs")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_logs_with_level_filter(
        self, client: AsyncClient, seed_job: SeedJobFn
    ) -> None:
        jid = await seed_job(state="running", job_id="logs-level")
        resp = await client.get(
            f"/api/jobs/{jid}/logs", params={"level": "error"}
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_logs_invalid_level(
        self, client: AsyncClient, seed_job: SeedJobFn
    ) -> None:
        jid = await seed_job(state="running", job_id="logs-bad")
        resp = await client.get(
            f"/api/jobs/{jid}/logs", params={"level": "critical"}
        )
        assert resp.status_code == 422

    # ── Diff ──

    async def test_diff_empty(
        self, client: AsyncClient, seed_job: SeedJobFn
    ) -> None:
        jid = await seed_job(state="succeeded", job_id="diff-1")
        resp = await client.get(f"/api/jobs/{jid}/diff")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_diff_not_found(self, client: AsyncClient) -> None:
        resp = await client.get("/api/jobs/ghost/diff")
        assert resp.status_code == 404

    # ── Transcript ──

    async def test_transcript_empty(
        self, client: AsyncClient, seed_job: SeedJobFn
    ) -> None:
        jid = await seed_job(state="running", job_id="transcript-1")
        resp = await client.get(f"/api/jobs/{jid}/transcript")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_transcript_with_limit(
        self, client: AsyncClient, seed_job: SeedJobFn
    ) -> None:
        jid = await seed_job(state="running", job_id="transcript-lim")
        resp = await client.get(
            f"/api/jobs/{jid}/transcript", params={"limit": 10}
        )
        assert resp.status_code == 200

    # ── Timeline ──

    async def test_timeline_empty(
        self, client: AsyncClient, seed_job: SeedJobFn
    ) -> None:
        jid = await seed_job(state="running", job_id="timeline-1")
        resp = await client.get(f"/api/jobs/{jid}/timeline")
        assert resp.status_code == 200
        assert resp.json() == []

    # ── Telemetry ──

    async def test_telemetry_unavailable(
        self, client: AsyncClient, seed_job: SeedJobFn
    ) -> None:
        jid = await seed_job(state="running", job_id="telem-1")
        resp = await client.get(f"/api/jobs/{jid}/telemetry")
        assert resp.status_code == 200
        data = resp.json()
        assert data["jobId"] == jid
        assert data["available"] is False


# ── Job Resolution ───────────────────────────────────────────────────


class TestJobResolution:
    # ── Resolve: merge ──

    async def test_resolve_merge(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
        mock_merge_service: AsyncMock,
    ) -> None:
        jid = await seed_job(state="succeeded", job_id="resolve-merge")
        mock_merge_service.resolve_job.return_value = FakeMergeResult(
            status="merged"
        )

        resp = await client.post(
            f"/api/jobs/{jid}/resolve", json={"action": "merge"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["resolution"] == "merged"
        assert data.get("prUrl") is None

    # ── Resolve: create_pr ──

    async def test_resolve_create_pr(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
        mock_merge_service: AsyncMock,
    ) -> None:
        jid = await seed_job(state="succeeded", job_id="resolve-pr")
        mock_merge_service.resolve_job.return_value = FakeMergeResult(
            status="pr_created",
            pr_url="https://github.com/test/repo/pull/1",
        )

        resp = await client.post(
            f"/api/jobs/{jid}/resolve", json={"action": "create_pr"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["resolution"] == "pr_created"
        assert data["prUrl"] == "https://github.com/test/repo/pull/1"

    # ── Resolve: discard ──

    async def test_resolve_discard(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
        mock_merge_service: AsyncMock,
    ) -> None:
        jid = await seed_job(state="succeeded", job_id="resolve-disc")
        mock_merge_service.resolve_job.return_value = FakeMergeResult(
            status="discarded"
        )

        resp = await client.post(
            f"/api/jobs/{jid}/resolve", json={"action": "discard"}
        )
        assert resp.status_code == 200
        assert resp.json()["resolution"] == "discarded"

    # ── Resolve: conflict ──

    async def test_resolve_conflict(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
        mock_merge_service: AsyncMock,
    ) -> None:
        jid = await seed_job(state="succeeded", job_id="resolve-conf")
        mock_merge_service.resolve_job.return_value = FakeMergeResult(
            status="conflict",
            conflict_files=["src/main.py", "README.md"],
        )

        resp = await client.post(
            f"/api/jobs/{jid}/resolve", json={"action": "merge"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["resolution"] == "conflict"
        assert "src/main.py" in data["conflictFiles"]

    # ── Resolve: not succeeded → 409 ──

    async def test_resolve_not_succeeded(
        self, client: AsyncClient, seed_job: SeedJobFn
    ) -> None:
        jid = await seed_job(state="running", job_id="resolve-running")
        resp = await client.post(
            f"/api/jobs/{jid}/resolve", json={"action": "merge"}
        )
        assert resp.status_code == 409

    # ── Resolve: not found ──

    async def test_resolve_not_found(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/jobs/ghost/resolve", json={"action": "merge"}
        )
        assert resp.status_code == 404

    # ── Resolve: already resolved ──

    async def test_resolve_already_resolved(
        self, client: AsyncClient, seed_job: SeedJobFn
    ) -> None:
        jid = await seed_job(
            state="succeeded", job_id="resolve-dup", resolution="merged"
        )
        resp = await client.post(
            f"/api/jobs/{jid}/resolve", json={"action": "merge"}
        )
        assert resp.status_code == 409

    # ── Archive ──

    async def test_archive_succeeded_job(
        self, client: AsyncClient, seed_job: SeedJobFn
    ) -> None:
        jid = await seed_job(state="succeeded", job_id="arch-1")
        resp = await client.post(f"/api/jobs/{jid}/archive")
        assert resp.status_code == 204

        # Verify the job is now archived
        get_resp = await client.get(f"/api/jobs/{jid}")
        assert get_resp.json()["archivedAt"] is not None

    async def test_archive_active_job_returns_409(
        self, client: AsyncClient, seed_job: SeedJobFn
    ) -> None:
        jid = await seed_job(state="running", job_id="arch-active")
        resp = await client.post(f"/api/jobs/{jid}/archive")
        assert resp.status_code == 409

    async def test_archive_not_found(self, client: AsyncClient) -> None:
        resp = await client.post("/api/jobs/ghost/archive")
        assert resp.status_code == 404

    # ── Unarchive ──

    async def test_unarchive_job(
        self, client: AsyncClient, seed_job: SeedJobFn
    ) -> None:
        jid = await seed_job(
            state="succeeded",
            job_id="unarch-1",
            archived_at=datetime.now(UTC),
        )
        resp = await client.post(f"/api/jobs/{jid}/unarchive")
        assert resp.status_code == 204

        get_resp = await client.get(f"/api/jobs/{jid}")
        assert get_resp.json()["archivedAt"] is None

    async def test_unarchive_not_found(self, client: AsyncClient) -> None:
        resp = await client.post("/api/jobs/ghost/unarchive")
        assert resp.status_code == 404
