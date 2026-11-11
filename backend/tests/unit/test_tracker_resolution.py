"""Tests for the shared Job -> Project -> TrackerLink resolution (Story 6.1, CAP-13)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import update

from backend import config as backend_config
from backend.models.db import Base, CredentialRow, JobRow
from backend.persistence.credential_repo import CredentialRepository
from backend.persistence.database import create_engine, create_session_factory
from backend.persistence.project_repo import ProjectRepository
from backend.persistence.task_link_repo import TaskLinkRepository
from backend.persistence.tracker_link_repo import TrackerLinkRepository
from backend.services.tracker_adapter import GitHubProjectsTrackerAdapter, TrackerAdapterError, TrackerAdapterInterface
from backend.services.tracker_resolution import (
    TrackerResolutionError,
    dispatch_tracker_write,
    resolve_tracker_for_job,
)
from backend.services.tracker_write_service import TrackerWriteAction, TrackerWriteRequest

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture
async def session_factory(tmp_path, monkeypatch):
    monkeypatch.setattr(backend_config, "_codeplane_dir", tmp_path / "credentials")
    engine = create_engine(tmp_path / "tracker-resolution.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield create_session_factory(engine)
    finally:
        await engine.dispose()


@pytest.fixture
async def session(session_factory) -> AsyncGenerator[AsyncSession, None]:
    async with session_factory() as sess:
        yield sess


async def _make_project(session: AsyncSession, project_id: str = "proj-1") -> str:
    project = await ProjectRepository(session).create(project_id, "Test Project", ["/repo/a"])
    await session.commit()
    return project.id


async def _make_credential(session: AsyncSession, credential_id: str = "cred-1") -> str:
    row = await CredentialRepository(session).create(
        credential_id=credential_id, provider="github", label="GH", base_url="https://api.github.com", pat="secret"
    )
    await session.commit()
    return str(row["id"])


async def _make_job(session: AsyncSession, job_id: str = "job-1", project_id: str = "proj-1") -> None:
    session.add(
        JobRow(
            id=job_id,
            repo="/repo/a",
            project_id=project_id,
            prompt="do work",
            state="running",
            base_ref="main",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
    )
    await session.commit()


@pytest.mark.asyncio
async def test_resolves_ticket_and_tracker_link_for_jobs_task_link(session: AsyncSession) -> None:
    project_id = await _make_project(session)
    credential_id = await _make_credential(session)
    await TrackerLinkRepository(session).create(
        link_id="link-1", project_id=project_id, credential_id=credential_id, external_ref="board-1"
    )
    await session.commit()
    await _make_job(session)
    task_link = await TaskLinkRepository(session).create_manual(
        project_id=project_id,
        repo_path="/repo/a",
        tracker_link_id="link-1",
        tracker_ticket_ref="ABC-123",
        prompt_override="do it",
    )
    await TaskLinkRepository(session).set_job_id(task_link.id, "job-1")
    await session.commit()

    resolved = await resolve_tracker_for_job(session, "job-1")

    assert resolved.tracker_link_id == "link-1"
    assert resolved.credential_id == credential_id
    assert resolved.ticket_ref == "ABC-123"


@pytest.mark.asyncio
async def test_raises_when_job_has_no_task_link(session: AsyncSession) -> None:
    await _make_project(session)
    await _make_job(session)

    with pytest.raises(TrackerResolutionError, match="no associated TaskLink"):
        await resolve_tracker_for_job(session, "job-1")


@pytest.mark.asyncio
async def test_raises_when_task_link_has_no_paired_ticket(session: AsyncSession) -> None:
    project_id = await _make_project(session)
    await _make_job(session)
    task_link = await TaskLinkRepository(session).upsert_many(
        project_id,
        entries=[{"repo_path": "/repo/a", "story_node_id": "story-1", "depends_on": []}],
    )
    await TaskLinkRepository(session).set_job_id(task_link[0].id, "job-1")
    await session.commit()

    with pytest.raises(TrackerResolutionError, match="no explicit TrackerLink/ticket pair"):
        await resolve_tracker_for_job(session, "job-1")


@pytest.mark.asyncio
async def test_raises_when_project_has_no_tracker_link(session: AsyncSession) -> None:
    project_id = await _make_project(session)
    credential_id = await _make_credential(session)
    tracker_repo = TrackerLinkRepository(session)
    await tracker_repo.create(
        link_id="link-1",
        project_id=project_id,
        credential_id=credential_id,
        external_ref="board-1",
    )
    await _make_job(session)
    task_link = await TaskLinkRepository(session).create_manual(
        project_id=project_id,
        repo_path="/repo/a",
        tracker_link_id="link-1",
        tracker_ticket_ref="ABC-123",
        prompt_override="do it",
    )
    await TaskLinkRepository(session).set_job_id(task_link.id, "job-1")
    await session.commit()
    await tracker_repo.delete_for_project(project_id=project_id, link_id="link-1")
    await session.commit()
    session.expire_all()

    with pytest.raises(TrackerResolutionError, match="no explicit TrackerLink/ticket pair"):
        await resolve_tracker_for_job(session, "job-1")


@pytest.mark.asyncio
async def test_resolves_explicit_link_instead_of_first_project_link(
    session: AsyncSession,
) -> None:
    project_id = await _make_project(session)
    credential_1 = await _make_credential(session, "cred-1")
    credential_2 = await _make_credential(session, "cred-2")
    repo = TrackerLinkRepository(session)
    await repo.create(
        link_id="link-first",
        project_id=project_id,
        credential_id=credential_1,
        external_ref="board-1",
    )
    await repo.create(
        link_id="link-target",
        project_id=project_id,
        credential_id=credential_2,
        external_ref="board-2",
    )
    await session.commit()
    await _make_job(session)
    task_link = await TaskLinkRepository(session).create_manual(
        project_id=project_id,
        repo_path="/repo/a",
        tracker_link_id="link-target",
        tracker_ticket_ref="ABC-123",
        prompt_override="do it",
    )
    await TaskLinkRepository(session).set_job_id(task_link.id, "job-1")
    await session.commit()

    resolved = await resolve_tracker_for_job(session, "job-1")

    assert resolved.tracker_link_id == "link-target"
    assert resolved.credential_id == credential_2


@pytest.mark.parametrize(
    "receipt",
    [
        None,
        {"id": "comment-42", "url": "https://github.com/acme/shop/issues/42", "http_status": 201},
    ],
)
async def test_dispatch_returns_receipt_for_exact_explicit_route(session, session_factory, receipt) -> None:
    project_id = await _make_project(session)
    credential_id = await _make_credential(session)
    repo = TrackerLinkRepository(session)
    await repo.create(
        link_id="link-first",
        project_id=project_id,
        credential_id=credential_id,
        external_ref="acme/1",
    )
    await repo.create(
        link_id="link-target",
        project_id=project_id,
        credential_id=credential_id,
        external_ref="acme/7",
    )
    await session.commit()
    adapter = AsyncMock(spec=TrackerAdapterInterface)
    adapter.write.return_value = receipt
    request = TrackerWriteRequest(
        "link-target",
        "acme/shop#42",
        TrackerWriteAction.comment,
        "  Exact body.\nRésumé ✅\r\n",
    )

    actual = await dispatch_tracker_write(session_factory, request, adapters={"github": adapter})

    assert actual == receipt
    adapter.write.assert_awaited_once_with(
        base_url="https://api.github.com",
        external_ref="acme/7",
        token="secret",
        ticket_ref="acme/shop#42",
        action="comment",
        value="  Exact body.\nRésumé ✅\r\n",
        email=None,
    )


@pytest.mark.parametrize("failure, uncertain", [(422, False), (503, True), ("lost_response", True)])
async def test_dispatch_preserves_adapter_delivery_classification(session, session_factory, failure, uncertain) -> None:
    project_id = await _make_project(session)
    credential_id = await _make_credential(session)
    await TrackerLinkRepository(session).create(
        link_id="link-target",
        project_id=project_id,
        credential_id=credential_id,
        external_ref="acme/7",
    )
    await session.commit()
    submitted = []

    async def handler(request: httpx.Request) -> httpx.Response:
        submitted.append(request)
        if failure == "lost_response":
            raise httpx.ReadTimeout("secret in lost response", request=request)
        return httpx.Response(failure, text="secret in rejection body")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = GitHubProjectsTrackerAdapter(client)
        with pytest.raises(TrackerAdapterError) as caught:
            await dispatch_tracker_write(
                session_factory,
                TrackerWriteRequest("link-target", "acme/shop#42", TrackerWriteAction.comment, "Original comment"),
                adapters={"github": adapter},
            )
    assert len(submitted) == 1
    assert caught.value.delivery_uncertain is uncertain
    assert "secret" not in str(caught.value)


async def test_credential_decryption_failure_is_definite_before_dispatch(session, session_factory) -> None:
    project_id = await _make_project(session)
    credential_id = await _make_credential(session)
    await TrackerLinkRepository(session).create(
        link_id="link-target",
        project_id=project_id,
        credential_id=credential_id,
        external_ref="acme/7",
    )
    await session.execute(
        update(CredentialRow).where(CredentialRow.id == credential_id).values(encrypted_secret="invalid-ciphertext")
    )
    await session.commit()
    adapter = AsyncMock(spec=TrackerAdapterInterface)

    with pytest.raises(TrackerAdapterError) as caught:
        await dispatch_tracker_write(
            session_factory,
            TrackerWriteRequest("link-target", "acme/shop#42", TrackerWriteAction.comment, "Original comment"),
            adapters={"github": adapter},
        )

    adapter.write.assert_not_awaited()
    assert caught.value.code == "credential_unavailable"
    assert caught.value.delivery_uncertain is False
    assert "secret" not in str(caught.value)
    assert "invalid-ciphertext" not in str(caught.value)
