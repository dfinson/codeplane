"""Crash recovery tests for state management.

Tests that server restart recovery paths work correctly:
- Approval futures are recreated from DB state
- Dead-letter queue retries failed event persists
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.models.db import Base, JobRow
from backend.persistence.database import _set_sqlite_pragmas
from backend.services.approval_service import ApprovalService

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


@pytest.fixture
async def session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sa_event.listen(engine.sync_engine, "connect", _set_sqlite_pragmas)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    # Create job rows for FK constraints
    async with factory() as session:
        for jid in ["job-1", "job-2", "job-3"]:
            session.add(
                JobRow(
                    id=jid,
                    repo="/test",
                    prompt="test",
                    state="waiting_for_approval",
                    base_ref="main",
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )
        await session.commit()
    yield factory
    await engine.dispose()


class TestApprovalRecovery:
    """Tests that pending approvals are recovered after server restart."""

    @pytest.mark.asyncio
    async def test_recover_recreates_futures(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Simulate: create approvals, destroy service, create new service, recover."""
        # Phase 1: Create pending approvals
        svc1 = ApprovalService(session_factory)
        a1 = await svc1.create_request("job-1", "Deploy?")
        a2 = await svc1.create_request("job-2", "Restart?")

        # Phase 2: Simulate server restart — create fresh service (no futures)
        svc2 = ApprovalService(session_factory)
        assert len(svc2._pending_futures) == 0

        # Phase 3: Recover
        recovered = await svc2.recover_pending_approvals()
        assert recovered == 2
        assert a1.id in svc2._pending_futures
        assert a2.id in svc2._pending_futures

    @pytest.mark.asyncio
    async def test_recovered_future_can_be_resolved(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """After recovery, resolving an approval unblocks the future."""
        svc1 = ApprovalService(session_factory)
        a1 = await svc1.create_request("job-1", "OK?")

        # New service after restart
        svc2 = ApprovalService(session_factory)
        await svc2.recover_pending_approvals()

        # Resolve through the new service
        resolved = await svc2.resolve(a1.id, "approved")
        assert resolved.resolution == "approved"

        # The recovered future should be done
        future = svc2._pending_futures.get(a1.id)
        # Future was popped by resolve(), but the resolution was applied
        assert future is None  # popped after resolve
        assert resolved.resolved_at is not None

    @pytest.mark.asyncio
    async def test_already_resolved_not_recovered(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Approvals that were resolved before restart are not recovered."""
        svc1 = ApprovalService(session_factory)
        a1 = await svc1.create_request("job-1", "OK?")
        await svc1.resolve(a1.id, "approved")

        a2 = await svc1.create_request("job-2", "Check?")

        # New service
        svc2 = ApprovalService(session_factory)
        recovered = await svc2.recover_pending_approvals()
        assert recovered == 1  # Only a2 (unresolved)
        assert a2.id in svc2._pending_futures
        assert a1.id not in svc2._pending_futures

    @pytest.mark.asyncio
    async def test_recovery_is_idempotent(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Calling recover twice doesn't duplicate futures."""
        svc = ApprovalService(session_factory)
        await svc.create_request("job-1", "OK?")

        # Simulate restart
        svc2 = ApprovalService(session_factory)
        count1 = await svc2.recover_pending_approvals()
        count2 = await svc2.recover_pending_approvals()
        assert count1 == 1
        assert count2 == 0  # Already tracked


class TestDeadLetterRetry:
    """Tests for the event persistence dead-letter queue."""

    @pytest.mark.asyncio
    async def test_dead_letter_retries_and_succeeds(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Simulate event persist failure then success on retry."""
        from backend.models.events import DomainEvent, DomainEventKind
        from backend.lifespan import _persist_event_with_retry

        event = DomainEvent(
            event_id="test-evt-1",
            job_id="job-1",
            timestamp=datetime.now(UTC),
            kind=DomainEventKind.log_line_emitted,
            payload={"seq": 1, "message": "test", "level": "info"},
        )

        lock = asyncio.Lock()

        # First call should succeed normally
        await _persist_event_with_retry(
            event=event,
            session_factory=session_factory,
            write_lock=lock,
        )
        assert event.db_id is not None

    @pytest.mark.asyncio
    async def test_persist_retries_on_lock_error(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Event persist retries on SQLite lock errors."""
        from sqlalchemy.exc import OperationalError
        from backend.models.events import DomainEvent, DomainEventKind
        from backend.persistence.event_repo import EventRepository
        from backend.lifespan import _persist_event_with_retry

        event = DomainEvent(
            event_id="test-evt-retry",
            job_id="job-1",
            timestamp=datetime.now(UTC),
            kind=DomainEventKind.log_line_emitted,
            payload={"seq": 1, "message": "retry test", "level": "info"},
        )

        call_count = 0
        original_append = EventRepository.append

        async def flaky_append(self: EventRepository, evt: DomainEvent) -> int:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OperationalError("", [], Exception("database is locked"))
            return await original_append(self, evt)

        lock = asyncio.Lock()

        with patch.object(EventRepository, "append", flaky_append):
            await _persist_event_with_retry(
                event=event,
                session_factory=session_factory,
                write_lock=lock,
                retry_delay_s=0.01,
            )

        assert call_count == 2  # Failed once, succeeded on retry
        assert event.db_id is not None
