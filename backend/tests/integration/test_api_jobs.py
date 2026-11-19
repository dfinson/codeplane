"""API integration tests for Jobs and Health endpoints.

Tests exercise the full request → route → service → DB path using
the shared ``conftest.py`` fixtures (in-memory SQLite, mocked runtime
and merge services, real JobService + EventBus).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from backend.models.db import EventRow
from backend.models.domain import JobNotFoundError, StateConflictError
from backend.models.events import EventKind
from backend.tests.unit.conftest import make_job

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from .conftest import SeedJobFn


# ── Module-wide fixture: patch GitService so create/rerun/continue ──
# ── routes use the mock instead of hitting real git ──────────────────


@pytest.fixture(autouse=True)
def _patch_for_job_creation(
    monkeypatch: pytest.MonkeyPatch,
    mock_git_service: AsyncMock,
) -> None:
    """Replace GitService so create/rerun/continue routes use the mock."""
    monkeypatch.setattr(
        "backend.services.git.git_service.GitService",
        lambda config: mock_git_service,
    )


@pytest.fixture(autouse=True)
def _source_aware_cancel(mock_runtime_service, session_factory, event_bus):
    from backend.config import CPLConfig
    from backend.services.runtime import RuntimeService

    runtime = RuntimeService(
        session_factory=session_factory,
        event_bus=event_bus,
        adapter_registry=MagicMock(),
        config=CPLConfig(),
    )
    mock_runtime_service.cancel_job.side_effect = runtime.cancel_job


@pytest.fixture(autouse=True)
async def _seed_default_project(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A Job can never exist without an owning Project (AD-5).

    ``/test/repo`` is the default repo used across this file's create-job
    tests — seed a Project owning it so ``JobService.create_job`` resolves it.
    """
    from backend.persistence.project_repo import ProjectRepository

    async with session_factory() as session:
        project_repo = ProjectRepository(session)
        if (await project_repo.get("proj-default")) is None:
            await project_repo.create("proj-default", "Default", ["/test/repo"])
        await session.commit()


# ── Helpers ──────────────────────────────────────────────────────────


def _create_body(**overrides: object) -> dict[str, object]:
    """Minimal valid POST /api/jobs body."""
    base: dict[str, object] = {"repo": "/test/repo", "prompt": "Fix bug"}
    base.update(overrides)
    return base


class TestJobCostProvenance:
    @pytest.mark.parametrize(
        "kind", ["copilot", "verified-usd", "legacy", "observation", "forged-usd", "foreign-owner"]
    )
    async def test_source_units_reach_job_detail_and_list_without_rewriting_legacy_totals(
        self, client, session_factory, seed_job, kind
    ):
        from backend.models.events import new_event
        from backend.models.session_event import EventMetadata
        from backend.models.usage import UsageAmount
        from backend.persistence.event_repo import EventRepository
        from backend.persistence.job_repo import JobRepository
        from backend.persistence.telemetry_summary_repo import TelemetrySummaryRepository
        from backend.services.events.sse_manager import SSEConnection, SSEManager

        job_id = await seed_job(state="completed")
        currency = kind in {"verified-usd", "observation", "forged-usd", "foreign-owner"}
        producer = "claude" if currency else "copilot"
        async with session_factory() as session:
            summaries = TelemetrySummaryRepository(session)
            await summaries.init_job(job_id, sdk="codeplane")
            await summaries.increment(
                job_id, total_cost_usd=2.31, input_tokens=67464, output_tokens=985, cache_read_tokens=48229
            )
            events = EventRepository(session)
            for index in range(7):
                usage = UsageAmount(
                    amount="0.33",
                    unit="USD" if currency else "copilot_cost_unit",
                    producer=producer,
                    model="fixture",
                    job_id=job_id,
                    execution_epoch="epoch",
                    role="primary",
                    source="native-usage",
                    verified=currency,
                    currency="USD" if currency else None,
                )
                source_usage = usage.model_dump(mode="json")
                if kind == "forged-usd":
                    source_usage["verified"] = False
                elif kind == "foreign-owner":
                    source_usage["job_id"] = "another-job"
                event = new_event(
                    job_id,
                    "telemetry.usage",
                    {
                        "cost_usd": 0.33,
                        **({"native_usage": [source_usage]} if kind != "legacy" else {}),
                    },
                    event_id=f"{job_id}-usage-{index}",
                    timestamp=datetime(2020, 1, 1, tzinfo=UTC),
                    metadata=EventMetadata(
                        source_framework=producer,
                        execution_epoch="epoch",
                        integration_binding_id="observer" if kind == "observation" else None,
                        namespace=("observation-source",) if kind == "observation" else (),
                    ),
                )
                await events.append(event)
                await events.append(event)
            await session.commit()
            before = await events.list_all_events_by_job(job_id)
        detail = await client.get(f"/api/jobs/{job_id}")
        listing = await client.get("/api/jobs")
        hydration = await client.get(f"/api/jobs/{job_id}/snapshot")
        assert detail.status_code == listing.status_code == hydration.status_code == 200
        listed = next(item for item in listing.json()["items"] if item["id"] == job_id)
        async with session_factory() as session:
            sse = SSEManager()
            connection = SSEConnection(job_id)
            await sse.replay_events(
                connection, EventRepository(session), JobRepository(session), last_event_id=0, session=session
            )
            frame = connection.queue.get_nowait()
            import json

            snapshot = json.loads(next(line[6:] for line in frame.splitlines() if line.startswith("data: ")))
        for body in (detail.json(), listed, hydration.json()["job"], snapshot["jobs"][0]):
            assert body["totalCostUsd"] == 2.31 and body["totalTokens"] == 116678
            if kind in {"legacy", "observation", "forged-usd", "foreign-owner"}:
                assert body["costUsage"] == []
            else:
                (cost,) = body["costUsage"]
                assert cost["amount"] == "2.31" and cost["producer"] == producer
                assert cost["unit"] == ("USD" if currency else "copilot_cost_unit")
                assert cost["verified"] is currency and cost["currency"] == ("USD" if currency else None)
                assert cost["role"] == "primary" and cost["source"] == "native-usage"
        async with session_factory() as session:
            assert await EventRepository(session).list_all_events_by_job(job_id) == before
            assert (await TelemetrySummaryRepository(session).batch_cost_tokens([job_id]))[job_id][
                "total_cost_usd"
            ] == 2.31


class TestRuntimeContract:
    async def test_cancel_routes_delivery_before_state_change(self):
        from backend.api.jobs import cancel_job
        from backend.tests.unit.conftest import make_job

        jobs = AsyncMock()
        runtime = AsyncMock()
        ingest = AsyncMock()
        job = make_job(state="canceled", source="copilot_cli", external_session_id="native-1")
        jobs.cancel_job.return_value = job
        runtime.cancel_job.return_value = job
        await cancel_job("job-1", jobs, runtime, ingest)
        assert runtime.cancel_job.await_count == 1
        jobs.cancel_job.assert_not_awaited()


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
        assert data["version"]
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
        assert data["state"] in ("preparing", "queued", "running")
        assert "createdAt" in data

    async def test_create_job_response_fields(self, client: AsyncClient) -> None:
        resp = await client.post("/api/jobs", json=_create_body())
        data = resp.json()
        assert data["sdk"] == "copilot"
        # In preparing state, branch/worktreePath may not be set yet
        # They are populated by the background workspace setup task

    async def test_create_job_invalid_repo(self, client: AsyncClient) -> None:
        resp = await client.post("/api/jobs", json=_create_body(repo="/not/allowed"))
        assert resp.status_code == 400

    async def test_newly_registered_repo_immediately_usable(
        self, client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Regression: a repo newly added to a Project must be usable without a server restart.

        A Job can never exist without an owning Project (AD-5) — job creation
        resolves the owning Project for the repo on every call, so a repo
        added to a Project's repo_paths after startup must work immediately.
        """
        from backend.persistence.project_repo import ProjectRepository

        # Repo not yet owned by any Project — must be rejected
        resp = await client.post("/api/jobs", json=_create_body(repo="/new/repo"))
        assert resp.status_code == 400

        # Simulate what registering a repo to a Project does
        async with session_factory() as session:
            await ProjectRepository(session).create("proj-new-repo", "New Repo Project", ["/new/repo"])
            await session.commit()

        # Job creation must now succeed without a server restart
        resp = await client.post("/api/jobs", json=_create_body(repo="/new/repo"))
        assert resp.status_code == 201

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

    async def test_create_job_calls_runtime_start(self, client: AsyncClient, mock_runtime_service: AsyncMock) -> None:
        await client.post("/api/jobs", json=_create_body())
        # setup_and_start is fire-and-forget; give the event loop a tick
        await asyncio.sleep(0.05)
        mock_runtime_service.setup_and_start.assert_called_once()

    # ── List ──

    async def test_list_jobs_empty(self, client: AsyncClient) -> None:
        resp = await client.get("/api/jobs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["hasMore"] is False

    async def test_list_jobs_returns_seeded(self, client: AsyncClient, seed_job: SeedJobFn) -> None:
        jid = await seed_job(state="running")
        resp = await client.get("/api/jobs")
        data = resp.json()
        ids = [j["id"] for j in data["items"]]
        assert jid in ids

    async def test_list_jobs_includes_latest_progress_preview(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        jid = await seed_job(state="review", job_id="preview-1")
        async with session_factory() as session:
            session.add(
                EventRow(
                    event_id=f"evt-{uuid4().hex}",
                    job_id=jid,
                    kind=EventKind.progress_headline.value,
                    timestamp=datetime.now(UTC),
                    payload=(
                        '{"headline":"Audit keyboard shortcuts",'
                        '"summary":"Reviewed existing shortcuts and documented follow-up changes."}'
                    ),
                )
            )
            await session.commit()

        resp = await client.get("/api/jobs")
        data = resp.json()
        preview_job = next(job for job in data["items"] if job["id"] == jid)
        assert preview_job["progressHeadline"] == "Audit keyboard shortcuts"
        assert preview_job["progressSummary"] == "Reviewed existing shortcuts and documented follow-up changes."

    async def test_list_jobs_state_filter(self, client: AsyncClient, seed_job: SeedJobFn) -> None:
        await seed_job(state="running", job_id="run-1")
        await seed_job(state="review", job_id="rev-1")

        resp = await client.get("/api/jobs", params={"state": "review"})
        data = resp.json()
        assert all(j["state"] == "review" for j in data["items"])

    async def test_list_jobs_pagination(self, client: AsyncClient, seed_job: SeedJobFn) -> None:
        for i in range(3):
            await seed_job(state="running", job_id=f"page-{i}")

        resp = await client.get("/api/jobs", params={"limit": 2})
        data = resp.json()
        assert len(data["items"]) == 2
        assert data["hasMore"] is True
        assert data["cursor"] is not None

        resp2 = await client.get("/api/jobs", params={"limit": 2, "cursor": data["cursor"]})
        data2 = resp2.json()
        assert len(data2["items"]) >= 1

    async def test_list_jobs_archived_filter(self, client: AsyncClient, seed_job: SeedJobFn) -> None:
        await seed_job(
            state="completed",
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

    async def test_get_job(self, client: AsyncClient, seed_job: SeedJobFn) -> None:
        jid = await seed_job(state="running", job_id="get-1")
        resp = await client.get(f"/api/jobs/{jid}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == jid
        assert data["state"] == "running"
        assert data["repo"] == "/test/repo"

    async def test_get_job_includes_latest_progress_preview(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        jid = await seed_job(state="review", job_id="get-preview-1")
        async with session_factory() as session:
            session.add(
                EventRow(
                    event_id=f"evt-{uuid4().hex}",
                    job_id=jid,
                    kind=EventKind.progress_headline.value,
                    timestamp=datetime.now(UTC),
                    payload=(
                        '{"headline":"Finalize shortcut audit",'
                        '"summary":"Captured the last validation pass before handoff."}'
                    ),
                )
            )
            await session.commit()

        resp = await client.get(f"/api/jobs/{jid}")
        data = resp.json()
        assert data["progressHeadline"] == "Finalize shortcut audit"
        assert data["progressSummary"] == "Captured the last validation pass before handoff."

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
        mock_runtime_service.cancel_job.assert_called_once_with(jid)

    async def test_cancel_queued_job(self, client: AsyncClient, seed_job: SeedJobFn) -> None:
        jid = await seed_job(state="queued", job_id="cancel-q")
        resp = await client.post(f"/api/jobs/{jid}/cancel")
        assert resp.status_code == 200
        assert resp.json()["state"] == "canceled"

    async def test_cancel_already_terminal_job(self, client: AsyncClient, seed_job: SeedJobFn) -> None:
        jid = await seed_job(state="completed", job_id="cancel-term")
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
        jid = await seed_job(state="completed", job_id="rerun-1")
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
        mock_runtime_service: AsyncMock,
    ) -> None:
        mock_runtime_service.create_followup_job.side_effect = None
        mock_runtime_service.create_followup_job.return_value = MagicMock(
            id="cont-2",
            state="running",
            title="Follow-up task",
            branch="cpl/cont-2",
            worktree_path="/tmp/cont-2",
            sdk="copilot",
            created_at=datetime.now(UTC),
        )
        resp = await client.post(
            "/api/jobs/cont-1/continue",
            json={"instruction": "Add tests"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["id"] == "cont-2"
        mock_runtime_service.create_followup_job.assert_awaited_once_with("cont-1", "Add tests")

    async def test_continue_not_found(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/jobs/ghost/continue",
            json={"instruction": "Add tests"},
        )
        assert resp.status_code == 404

    async def test_continue_missing_instruction(self, client: AsyncClient, seed_job: SeedJobFn) -> None:
        jid = await seed_job(state="review", job_id="cont-missing")
        resp = await client.post(f"/api/jobs/{jid}/continue", json={})
        assert resp.status_code == 422

    async def test_continue_blank_instruction(self, client: AsyncClient, seed_job: SeedJobFn) -> None:
        jid = await seed_job(state="review", job_id="cont-blank")
        resp = await client.post(f"/api/jobs/{jid}/continue", json={"instruction": "   "})
        assert resp.status_code == 422

    # ── Resume ──

    async def test_resume_terminal_job(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
        mock_runtime_service: AsyncMock,
    ) -> None:
        jid = await seed_job(state="failed", job_id="resume-1")
        mock_runtime_service.resume_job.return_value = make_job(
            id=jid,
            repo="/test/repo",
            prompt="Test prompt",
            state="running",
            worktree_path="/tmp/wt",
            branch="fix/branch",
        )

        resp = await client.post(
            f"/api/jobs/{jid}/resume",
            json={"instruction": "Continue with the fix"},
        )
        assert resp.status_code == 200
        assert resp.json()["state"] == "running"
        mock_runtime_service.resume_job.assert_called_once_with(jid, "Continue with the fix")

    async def test_resume_not_found(
        self,
        client: AsyncClient,
        mock_runtime_service: AsyncMock,
    ) -> None:
        mock_runtime_service.resume_job.side_effect = JobNotFoundError("Job ghost not found")
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
        mock_runtime_service.resume_job.side_effect = StateConflictError("Job is still running")
        resp = await client.post(
            f"/api/jobs/{jid}/resume",
            json={"instruction": "Something"},
        )
        assert resp.status_code == 409

    async def test_resume_missing_instruction(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
        mock_runtime_service: AsyncMock,
    ) -> None:
        jid = await seed_job(state="failed", job_id="resume-bad")
        mock_runtime_service.resume_job.return_value = make_job(
            id=jid,
            repo="/test/repo",
            prompt="Test prompt",
            state="running",
            worktree_path="/tmp/wt",
            branch="fix/branch",
        )

        resp = await client.post(f"/api/jobs/{jid}/resume", json={})
        assert resp.status_code == 200
        assert resp.json()["state"] == "running"
        mock_runtime_service.resume_job.assert_called_once_with(jid, None)

    # ── Models ──

    async def test_list_models_empty(self, client: AsyncClient) -> None:
        # Disabled integration catalogs fail before any provider discovery.
        resp = await client.get("/api/models", params={"sdk": "claude"})
        assert resp.status_code == 400
        assert "disabled" in resp.json()["detail"]


# ── Job Data ─────────────────────────────────────────────────────────


class TestJobData:
    # ── Logs ──

    async def test_logs_empty(self, client: AsyncClient, seed_job: SeedJobFn) -> None:
        jid = await seed_job(state="running", job_id="logs-1")
        resp = await client.get(f"/api/jobs/{jid}/logs")
        assert resp.status_code == 200
        assert resp.json() == {"items": []}

    async def test_logs_with_level_filter(self, client: AsyncClient, seed_job: SeedJobFn) -> None:
        jid = await seed_job(state="running", job_id="logs-level")
        resp = await client.get(f"/api/jobs/{jid}/logs", params={"level": "error"})
        assert resp.status_code == 200
        assert isinstance(resp.json()["items"], list)

    async def test_logs_invalid_level(self, client: AsyncClient, seed_job: SeedJobFn) -> None:
        jid = await seed_job(state="running", job_id="logs-bad")
        resp = await client.get(f"/api/jobs/{jid}/logs", params={"level": "critical"})
        assert resp.status_code == 422

    # ── Diff ──

    async def test_diff_empty(self, client: AsyncClient, seed_job: SeedJobFn) -> None:
        jid = await seed_job(state="review", job_id="diff-1")
        resp = await client.get(f"/api/jobs/{jid}/diff")
        assert resp.status_code == 200
        assert resp.json() == {"items": []}

    async def test_diff_not_found(self, client: AsyncClient) -> None:
        resp = await client.get("/api/jobs/ghost/diff")
        assert resp.status_code == 404

    # ── Transcript ──

    async def test_transcript_empty(self, client: AsyncClient, seed_job: SeedJobFn) -> None:
        jid = await seed_job(state="running", job_id="transcript-1")
        resp = await client.get(f"/api/jobs/{jid}/transcript")
        assert resp.status_code == 200
        assert resp.json() == {"items": []}

    async def test_transcript_with_limit(self, client: AsyncClient, seed_job: SeedJobFn) -> None:
        jid = await seed_job(state="running", job_id="transcript-lim")
        resp = await client.get(f"/api/jobs/{jid}/transcript", params={"limit": 10})
        assert resp.status_code == 200

    async def test_transcript_exposes_event_identity_and_optional_producer_sequence(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        jid = await seed_job(state="running", job_id="transcript-identity")
        producer_sequence = 10_000
        async with session_factory() as session:
            sequenced_row = EventRow(
                event_id="evt-sequenced",
                job_id=jid,
                kind=EventKind.message_assistant.value,
                timestamp=datetime.now(UTC),
                payload='{"content":"Sequenced"}',
                event_metadata=f'{{"sequence":{producer_sequence}}}',
            )
            unsequenced_row = EventRow(
                event_id="evt-unsequenced",
                job_id=jid,
                kind=EventKind.message_assistant.value,
                timestamp=datetime.now(UTC),
                payload='{"content":"Unsequenced"}',
                event_metadata="{}",
            )
            session.add_all([sequenced_row, unsequenced_row])
            await session.flush()
            assert sequenced_row.id != producer_sequence
            await session.commit()

        resp = await client.get(f"/api/jobs/{jid}/transcript")

        assert resp.status_code == 200
        items = resp.json()["items"]
        assert [(item["eventId"], item["sequence"]) for item in items] == [
            ("evt-sequenced", producer_sequence),
            ("evt-unsequenced", None),
        ]
        assert all("seq" not in item for item in items)

    async def test_snapshots_preserve_persisted_transcript_identity_for_job_and_share_paths(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        jid = await seed_job(state="completed", job_id="snapshot-identity")
        producer_sequence = 10_000
        payload_sequence = 77_777
        async with session_factory() as session:
            sequenced_row = EventRow(
                event_id="evt-snapshot-sequenced",
                job_id=jid,
                kind=EventKind.message_assistant.value,
                timestamp=datetime.now(UTC),
                payload=f'{{"content":"Sequenced","seq":{payload_sequence}}}',
                event_metadata=f'{{"sequence":{producer_sequence}}}',
            )
            unsequenced_row = EventRow(
                event_id="evt-snapshot-unsequenced",
                job_id=jid,
                kind=EventKind.message_assistant.value,
                timestamp=datetime.now(UTC),
                payload=f'{{"content":"Unsequenced","seq":{payload_sequence + 1}}}',
                event_metadata="{}",
            )
            session.add_all([sequenced_row, unsequenced_row])
            await session.flush()
            storage_cursors = {sequenced_row.id, unsequenced_row.id}
            assert producer_sequence not in storage_cursors
            assert payload_sequence not in storage_cursors
            await session.commit()

        job_snapshot = await client.get(f"/api/jobs/{jid}/snapshot")
        share_link = await client.post(f"/api/jobs/{jid}/share")
        shared_snapshot = await client.get(f"/api/share/{share_link.json()['token']}/snapshot")

        for response in (job_snapshot, shared_snapshot):
            assert response.status_code == 200
            transcript = response.json()["transcript"]
            assert [(item["eventId"], item["sequence"]) for item in transcript] == [
                ("evt-snapshot-sequenced", producer_sequence),
                ("evt-snapshot-unsequenced", None),
            ]
            assert all("seq" not in item for item in transcript)
            assert all(item["eventId"] not in {str(cursor) for cursor in storage_cursors} for item in transcript)

    # ── Timeline ──

    async def test_timeline_empty(self, client: AsyncClient, seed_job: SeedJobFn) -> None:
        jid = await seed_job(state="running", job_id="timeline-1")
        resp = await client.get(f"/api/jobs/{jid}/timeline")
        assert resp.status_code == 200
        assert resp.json() == {"items": []}

    # ── Telemetry ──

    async def test_telemetry_unavailable(self, client: AsyncClient, seed_job: SeedJobFn) -> None:
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
        jid = await seed_job(state="review", job_id="resolve-merge")
        mock_merge_service.resolve_job.return_value = FakeMergeResult(status="merged")

        resp = await client.post(f"/api/jobs/{jid}/resolve", json={"action": "merge"})
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
        jid = await seed_job(state="review", job_id="resolve-pr")
        mock_merge_service.resolve_job.return_value = FakeMergeResult(
            status="pr_created",
            pr_url="https://github.com/test/repo/pull/1",
        )

        resp = await client.post(f"/api/jobs/{jid}/resolve", json={"action": "create_pr"})
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
        jid = await seed_job(state="review", job_id="resolve-disc")
        mock_merge_service.resolve_job.return_value = FakeMergeResult(status="skipped")

        resp = await client.post(f"/api/jobs/{jid}/resolve", json={"action": "discard"})
        assert resp.status_code == 200
        assert resp.json()["resolution"] == "discarded"

    # ── Resolve: conflict ──

    async def test_resolve_conflict(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
        mock_merge_service: AsyncMock,
    ) -> None:
        jid = await seed_job(state="review", job_id="resolve-conf")
        mock_merge_service.resolve_job.return_value = FakeMergeResult(
            status="conflict",
            conflict_files=["src/main.py", "README.md"],
        )

        resp = await client.post(f"/api/jobs/{jid}/resolve", json={"action": "merge"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["resolution"] == "conflict"
        assert "src/main.py" in data["conflictFiles"]

    async def test_resolve_error_returns_unresolved_with_error(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
        mock_merge_service: AsyncMock,
    ) -> None:
        jid = await seed_job(state="review", job_id="resolve-error")
        mock_merge_service.resolve_job.return_value = FakeMergeResult(
            status="error",
            error="Cherry-pick failed without conflict markers; check git configuration or hooks",
        )

        resp = await client.post(f"/api/jobs/{jid}/resolve", json={"action": "smart_merge"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["resolution"] == "unresolved"
        assert data["error"] == "Cherry-pick failed without conflict markers; check git configuration or hooks"

    async def test_resolve_with_agent_resumes_conflict_job(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
        mock_runtime_service: AsyncMock,
    ) -> None:
        jid = await seed_job(
            state="review",
            job_id="resolve-agent",
            branch="cpl/resolve-agent",
            resolution="conflict",
            merge_status="conflict",
        )

        resp = await client.post(f"/api/jobs/{jid}/resolve", json={"action": "agent_merge"})
        assert resp.status_code == 200
        assert resp.json()["resolution"] == "agent_merge"
        mock_runtime_service.resume_job.assert_called_once()

    # ── Resolve: not review → 409 ──

    async def test_resolve_not_review(self, client: AsyncClient, seed_job: SeedJobFn) -> None:
        jid = await seed_job(state="running", job_id="resolve-running")
        resp = await client.post(f"/api/jobs/{jid}/resolve", json={"action": "merge"})
        assert resp.status_code == 409

    # ── Resolve: not found ──

    async def test_resolve_not_found(self, client: AsyncClient) -> None:
        resp = await client.post("/api/jobs/ghost/resolve", json={"action": "merge"})
        assert resp.status_code == 404

    # ── Resolve: already resolved ──

    async def test_resolve_already_resolved(self, client: AsyncClient, seed_job: SeedJobFn) -> None:
        jid = await seed_job(state="review", job_id="resolve-dup", resolution="merged")
        resp = await client.post(f"/api/jobs/{jid}/resolve", json={"action": "merge"})
        assert resp.status_code == 409

    # ── Archive ──

    async def test_archive_completed_job(self, client: AsyncClient, seed_job: SeedJobFn) -> None:
        jid = await seed_job(state="completed", job_id="arch-1")
        resp = await client.post(f"/api/jobs/{jid}/archive")
        assert resp.status_code == 204

        # Verify the job is now archived
        get_resp = await client.get(f"/api/jobs/{jid}")
        assert get_resp.json()["archivedAt"] is not None

    async def test_archive_active_job_returns_409(self, client: AsyncClient, seed_job: SeedJobFn) -> None:
        jid = await seed_job(state="running", job_id="arch-active")
        resp = await client.post(f"/api/jobs/{jid}/archive")
        assert resp.status_code == 409

    async def test_archive_not_found(self, client: AsyncClient) -> None:
        resp = await client.post("/api/jobs/ghost/archive")
        assert resp.status_code == 404

    # ── Unarchive ──

    async def test_unarchive_job_returns_409(self, client: AsyncClient, seed_job: SeedJobFn) -> None:
        jid = await seed_job(
            state="completed",
            job_id="unarch-1",
            archived_at=datetime.now(UTC),
        )
        resp = await client.post(f"/api/jobs/{jid}/unarchive")
        assert resp.status_code == 409
        assert resp.json()["detail"] == "Archived jobs are complete; create a follow-up job instead."

    async def test_unarchive_not_found(self, client: AsyncClient) -> None:
        resp = await client.post("/api/jobs/ghost/unarchive")
        assert resp.status_code == 404
