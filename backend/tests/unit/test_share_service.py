"""Durable sharing with real SQLite connections and isolated test databases."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import delete, select
from structlog.testing import capture_logs

from backend.models.db import Base, JobRow, ProjectRow, ShareTokenRow
from backend.persistence.database import create_engine, create_session_factory, serialized_write
from backend.services.sharing.share_service import InvalidShareTokenError, ShareService


@pytest.fixture
async def store(tmp_path):
    engine = create_engine(tmp_path / "sharing.db")
    factory = create_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    now = datetime.now(UTC)
    async with serialized_write(factory) as session:
        session.add(ProjectRow(id="project", name="Test", repo_paths="[]", created_at=now, updated_at=now))
        await session.flush()
        for job_id in ("job-1", "job-2"):
            session.add(
                JobRow(
                    id=job_id,
                    project_id="project",
                    repo="/test/repo",
                    base_ref="main",
                    prompt="Test",
                    state="running",
                    created_at=now,
                    updated_at=now,
                )
            )
    try:
        yield factory
    finally:
        await engine.dispose()


async def test_creation_commits_hash_not_credential_and_never_logs_token(store):
    svc = ShareService(store)
    with capture_logs() as logs:
        entry = await svc.create_token("job-1")
        assert await ShareService(store).validate(entry.token) == "job-1"
        async with store() as session:
            row = (await session.scalars(select(ShareTokenRow))).one()
            assert row.token_hash == hashlib.sha256(entry.token.encode()).hexdigest()
            assert row.token_hash != entry.token
        assert await svc.revoke(entry.token, "job-1")
        with pytest.raises(InvalidShareTokenError):
            await svc.validate(entry.token)
    assert entry.expires_at - entry.created_at == timedelta(hours=24)
    assert entry.token[:8] not in repr(logs)
    assert entry.token not in repr(entry)


async def test_tokens_survive_disposed_engine_and_service_restart(store, tmp_path):
    svc = ShareService(store, ttl=60)
    entry = await svc.create_token("job-1")
    await store.kw["bind"].dispose()
    restarted_engine = create_engine(tmp_path / "sharing.db")
    try:
        restarted = ShareService(create_session_factory(restarted_engine))
        assert await restarted.validate(entry.token) == "job-1"
        with patch("backend.services.sharing.share_service.datetime") as clock:
            clock.now.return_value = entry.expires_at
            with pytest.raises(InvalidShareTokenError):
                await restarted.validate(entry.token)
    finally:
        await restarted_engine.dispose()


@pytest.mark.parametrize("offset", [-0.001, 0, 1])
async def test_expiry_boundary_is_exclusive(store, offset):
    svc = ShareService(store, ttl=60)
    entry = await svc.create_token("job-1")
    with patch("backend.services.sharing.share_service.datetime") as clock:
        clock.now.return_value = entry.expires_at + timedelta(seconds=offset)
        if offset < 0:
            assert await svc.validate(entry.token) == "job-1"
        else:
            with pytest.raises(InvalidShareTokenError):
                await svc.validate(entry.token)


async def test_revocation_is_durable_job_scoped_and_independent_per_link(store):
    creator, viewer = ShareService(store), ShareService(store)
    first = await creator.create_token("job-1")
    second = await creator.create_token("job-1")
    assert first.token != second.token
    assert await creator.revoke(first.token, "job-2") is False
    assert await viewer.validate(first.token) == "job-1"
    assert await creator.revoke(first.token, "job-1") is True
    assert await creator.revoke(first.token, "job-1") is False
    with pytest.raises(InvalidShareTokenError):
        await viewer.validate(first.token)
    with pytest.raises(InvalidShareTokenError):
        await ShareService(store).validate(first.token)
    assert await viewer.validate(second.token) == "job-1"


async def test_missing_token_is_rejected_and_revocation_is_idempotent(store):
    svc = ShareService(store)
    with pytest.raises(InvalidShareTokenError):
        await svc.validate("missing")
    assert await svc.revoke("missing", "job-1") is False


async def test_creation_prunes_only_expired_links(store):
    svc = ShareService(store, ttl=60)
    first = await svc.create_token("job-1")
    with patch("backend.services.sharing.share_service.datetime") as clock:
        clock.now.return_value = first.expires_at
        second = await svc.create_token("job-2")
        assert await svc.validate(second.token) == "job-2"
    async with store() as session:
        rows = (await session.scalars(select(ShareTokenRow))).all()
        assert len(rows) == 1
        assert rows[0].job_id == "job-2"
    with pytest.raises(InvalidShareTokenError):
        await svc.validate(first.token)


async def test_deleting_job_cascades_share_credentials(store):
    svc = ShareService(store)
    entry = await svc.create_token("job-1")
    async with serialized_write(store) as session:
        await session.execute(delete(JobRow).where(JobRow.id == "job-1"))
    with pytest.raises(InvalidShareTokenError):
        await svc.validate(entry.token)
