"""Tests for MCP session management.

Covers:
- SessionState dataclass and touch()
- EditTicket dataclass
- SessionManager.get_or_create()
- SessionManager.get()
- SessionManager.close()
- SessionManager.cleanup_stale()
- Exclusive lock for blocking tools (checkpoint, semantic_diff, map_repo)
"""

from __future__ import annotations

import asyncio
import time

import pytest

from codeplane.config.models import TimeoutsConfig
from codeplane.mcp.session import SessionManager, SessionState


class TestSessionState:
    """Tests for SessionState dataclass."""

    def test_create_basic(self) -> None:
        """Create a basic session state."""
        now = time.time()
        state = SessionState(
            session_id="sess_001",
            created_at=now,
            last_active=now,
        )
        assert state.session_id == "sess_001"
        assert state.task_id is None
        assert state.fingerprints == {}
        assert state.counters == {}

    def test_create_with_task(self) -> None:
        """Create session state with task binding."""
        now = time.time()
        state = SessionState(
            session_id="sess_002",
            created_at=now,
            last_active=now,
            task_id="task_123",
        )
        assert state.task_id == "task_123"

    def test_touch_updates_last_active(self) -> None:
        """touch() updates last_active timestamp."""
        initial = time.time() - 100  # 100 seconds ago
        state = SessionState(
            session_id="sess_003",
            created_at=initial,
            last_active=initial,
        )

        before = time.time()
        state.touch()
        after = time.time()

        assert state.last_active >= before
        assert state.last_active <= after
        # created_at should not change
        assert state.created_at == initial

    def test_fingerprints_dict(self) -> None:
        """Session can store fingerprints."""
        state = SessionState(
            session_id="sess_004",
            created_at=0,
            last_active=0,
            fingerprints={"repo": "abc123", "index": "def456"},
        )
        assert state.fingerprints["repo"] == "abc123"
        assert state.fingerprints["index"] == "def456"

    def test_counters_dict(self) -> None:
        """Session can store counters."""
        state = SessionState(
            session_id="sess_005",
            created_at=0,
            last_active=0,
            counters={"reads": 10, "writes": 5},
        )
        assert state.counters["reads"] == 10
        assert state.counters["writes"] == 5


class TestSessionManagerGetOrCreate:
    """Tests for SessionManager.get_or_create."""

    def test_create_new_session_no_id(self) -> None:
        """Create new session without providing ID."""
        mgr = SessionManager()
        session = mgr.get_or_create()

        assert session.session_id.startswith("sess_")
        assert len(session.session_id) == 17  # "sess_" + 12 hex chars

    def test_create_new_session_with_id(self) -> None:
        """Create new session with provided ID."""
        mgr = SessionManager()
        session = mgr.get_or_create("custom_session")

        assert session.session_id == "custom_session"

    def test_get_existing_session(self) -> None:
        """Get existing session by ID."""
        mgr = SessionManager()
        created = mgr.get_or_create("existing")
        retrieved = mgr.get_or_create("existing")

        assert created is retrieved

    def test_get_existing_touches_session(self) -> None:
        """Getting existing session updates last_active."""
        mgr = SessionManager()
        session = mgr.get_or_create("touch_test")
        original_active = session.last_active

        # Wait a tiny bit
        time.sleep(0.001)

        mgr.get_or_create("touch_test")
        assert session.last_active > original_active

    def test_creates_multiple_sessions(self) -> None:
        """Can create multiple distinct sessions."""
        mgr = SessionManager()
        s1 = mgr.get_or_create("s1")
        s2 = mgr.get_or_create("s2")
        s3 = mgr.get_or_create("s3")

        assert s1.session_id != s2.session_id
        assert s2.session_id != s3.session_id
        assert len(mgr._sessions) == 3


class TestSessionManagerGet:
    """Tests for SessionManager.get."""

    def test_get_existing(self) -> None:
        """Get returns existing session."""
        mgr = SessionManager()
        created = mgr.get_or_create("get_test")
        retrieved = mgr.get("get_test")

        assert retrieved is created

    def test_get_nonexistent(self) -> None:
        """Get returns None for non-existent session."""
        mgr = SessionManager()
        result = mgr.get("does_not_exist")

        assert result is None


class TestSessionManagerClose:
    """Tests for SessionManager.close."""

    def test_close_removes_session(self) -> None:
        """close() removes session."""
        mgr = SessionManager()
        mgr.get_or_create("close_test")

        mgr.close("close_test")

        assert mgr.get("close_test") is None

    def test_close_nonexistent_is_safe(self) -> None:
        """close() on non-existent session doesn't raise."""
        mgr = SessionManager()
        mgr.close("never_existed")  # Should not raise

    def test_close_one_keeps_others(self) -> None:
        """close() only removes specified session."""
        mgr = SessionManager()
        mgr.get_or_create("keep")
        mgr.get_or_create("remove")

        mgr.close("remove")

        assert mgr.get("keep") is not None
        assert mgr.get("remove") is None


class TestSessionManagerCleanupStale:
    """Tests for SessionManager.cleanup_stale."""

    def test_cleanup_removes_stale_sessions(self) -> None:
        """cleanup_stale removes idle sessions."""
        # Use short timeout for testing
        config = TimeoutsConfig(session_idle_sec=0.1)
        mgr = SessionManager(config=config)

        # Create and make stale
        session = mgr.get_or_create("stale")
        session.last_active = time.time() - 1  # 1 second ago, > 0.1s timeout

        removed = mgr.cleanup_stale()

        assert removed == 1
        assert mgr.get("stale") is None

    def test_cleanup_keeps_active_sessions(self) -> None:
        """cleanup_stale keeps active sessions."""
        config = TimeoutsConfig(session_idle_sec=3600)  # 1 hour
        mgr = SessionManager(config=config)

        mgr.get_or_create("active")

        removed = mgr.cleanup_stale()

        assert removed == 0
        assert mgr.get("active") is not None

    def test_cleanup_returns_count(self) -> None:
        """cleanup_stale returns count of removed sessions."""
        config = TimeoutsConfig(session_idle_sec=0.001)
        mgr = SessionManager(config=config)

        for i in range(5):
            s = mgr.get_or_create(f"stale_{i}")
            s.last_active = time.time() - 1

        # Add one active session
        mgr.get_or_create("active")

        removed = mgr.cleanup_stale()

        assert removed == 5
        assert mgr.get("active") is not None

    def test_cleanup_with_default_config(self) -> None:
        """cleanup_stale works with default config."""
        mgr = SessionManager()  # Default config
        mgr.get_or_create("test")

        # Should not remove recent sessions
        removed = mgr.cleanup_stale()
        assert removed == 0


class TestSessionManagerConfig:
    """Tests for SessionManager configuration."""

    def test_default_config(self) -> None:
        """SessionManager creates default config if None."""
        mgr = SessionManager()
        assert mgr._config is not None
        assert isinstance(mgr._config, TimeoutsConfig)

    def test_custom_config(self) -> None:
        """SessionManager uses provided config."""
        config = TimeoutsConfig(session_idle_sec=999)
        mgr = SessionManager(config=config)
        assert mgr._config.session_idle_sec == 999


class TestSessionManagerIntegration:
    """Integration tests for session lifecycle."""

    def test_full_lifecycle(self) -> None:
        """Test full session lifecycle."""
        mgr = SessionManager()

        # Create
        session = mgr.get_or_create("lifecycle")
        assert session is not None

        # Use
        session.fingerprints["repo"] = "abc"
        session.counters["ops"] = 5

        # Retrieve and verify state
        retrieved = mgr.get_or_create("lifecycle")
        assert retrieved.fingerprints["repo"] == "abc"
        assert retrieved.counters["ops"] == 5

        # Close
        mgr.close("lifecycle")
        assert mgr.get("lifecycle") is None

    def test_concurrent_sessions(self) -> None:
        """Multiple sessions maintain independent state."""
        mgr = SessionManager()

        s1 = mgr.get_or_create("s1")
        s2 = mgr.get_or_create("s2")

        s1.fingerprints["key"] = "value1"
        s2.fingerprints["key"] = "value2"

        assert mgr.get("s1").fingerprints["key"] == "value1"  # type: ignore[union-attr]
        assert mgr.get("s2").fingerprints["key"] == "value2"  # type: ignore[union-attr]


# =========================================================================
# Exclusive Lock Tests
# =========================================================================


class TestExclusiveLock:
    """Tests for the exclusive session lock used by checkpoint/semantic_diff/map_repo."""

    def test_exclusive_holder_default_none(self) -> None:
        """Exclusive holder is None when no tool holds the lock."""
        state = SessionState(session_id="x", created_at=0, last_active=0)
        assert state.exclusive_holder is None

    @pytest.mark.asyncio
    async def test_exclusive_sets_and_clears_holder(self) -> None:
        """exclusive() context manager sets holder during, clears after."""
        state = SessionState(session_id="x", created_at=0, last_active=0)
        async with state.exclusive("checkpoint"):
            assert state.exclusive_holder == "checkpoint"
        assert state.exclusive_holder is None

    @pytest.mark.asyncio
    async def test_exclusive_blocks_concurrent_calls(self) -> None:
        """Second call to exclusive() blocks until first completes."""
        state = SessionState(session_id="x", created_at=0, last_active=0)
        order: list[str] = []

        async def hold_lock(name: str, delay: float) -> None:
            async with state.exclusive(name):
                order.append(f"{name}_start")
                await asyncio.sleep(delay)
                order.append(f"{name}_end")

        # Start checkpoint (holds lock for 0.1s), then immediately start search
        task1 = asyncio.create_task(hold_lock("checkpoint", 0.1))
        await asyncio.sleep(0.01)  # Let checkpoint acquire first
        task2 = asyncio.create_task(hold_lock("search", 0.0))

        await asyncio.gather(task1, task2)

        # search must start AFTER checkpoint ends
        assert order == [
            "checkpoint_start",
            "checkpoint_end",
            "search_start",
            "search_end",
        ]

    @pytest.mark.asyncio
    async def test_exclusive_clears_on_exception(self) -> None:
        """Holder is cleared even if the tool raises an exception."""
        state = SessionState(session_id="x", created_at=0, last_active=0)
        with pytest.raises(ValueError, match="boom"):
            async with state.exclusive("checkpoint"):
                raise ValueError("boom")
        assert state.exclusive_holder is None

    def test_exclusive_tools_frozenset(self) -> None:
        """EXCLUSIVE_TOOLS contains expected tool names."""
        from codeplane.mcp.session import EXCLUSIVE_TOOLS

        assert "checkpoint" in EXCLUSIVE_TOOLS
        assert "semantic_diff" in EXCLUSIVE_TOOLS
        assert "map_repo" not in EXCLUSIVE_TOOLS


class TestEditTicket:
    """Tests for EditTicket dataclass."""

    def test_create(self) -> None:
        from codeplane.mcp.session import EditTicket

        t = EditTicket(
            ticket_id="abc:0:deadbeef",
            path="src/foo.py",
            sha256="deadbeef" * 8,
            candidate_id="abc:0",
            issued_by="resolve",
        )
        assert t.ticket_id == "abc:0:deadbeef"
        assert t.path == "src/foo.py"
        assert t.used is False

    def test_used_flag(self) -> None:
        from codeplane.mcp.session import EditTicket

        t = EditTicket(
            ticket_id="abc:0:deadbeef",
            path="src/foo.py",
            sha256="deadbeef" * 8,
            candidate_id="abc:0",
            issued_by="resolve",
        )
        assert t.used is False
        t.used = True
        assert t.used is True


class TestSessionStateEditTickets:
    """Tests for edit ticket fields on SessionState."""

    def test_defaults(self) -> None:
        s = SessionState(session_id="s", created_at=0, last_active=0)
        assert s.edit_tickets == {}
        assert s.edits_since_checkpoint == 0

    def test_ticket_storage(self) -> None:
        from codeplane.mcp.session import EditTicket

        s = SessionState(session_id="s", created_at=0, last_active=0)
        t = EditTicket(
            ticket_id="r:0:abcd1234",
            path="foo.py",
            sha256="abcd1234" * 8,
            candidate_id="r:0",
            issued_by="resolve",
        )
        s.edit_tickets[t.ticket_id] = t
        assert "r:0:abcd1234" in s.edit_tickets

    def test_max_edit_batches_constant(self) -> None:
        from codeplane.mcp.session import _MAX_EDIT_BATCHES

        assert _MAX_EDIT_BATCHES == 2

    def test_last_recon_id_default(self) -> None:
        """Gap 4: last_recon_id defaults to None."""
        s = SessionState(session_id="s", created_at=0, last_active=0)
        assert s.last_recon_id is None

    def test_last_recon_id_set(self) -> None:
        """Gap 4: last_recon_id can be set."""
        s = SessionState(session_id="s", created_at=0, last_active=0)
        s.last_recon_id = "recon_abc123"
        assert s.last_recon_id == "recon_abc123"


class TestMutationContext:
    """Tests for MutationContext unified lifecycle tracker."""

    def test_defaults(self) -> None:
        from codeplane.mcp.session import MutationContext

        ctx = MutationContext()
        assert ctx.plan is None
        assert ctx.edit_tickets == {}
        assert ctx.pending_refactors == {}
        assert ctx.mutations_since_checkpoint == 0
        assert ctx.context_id  # auto-generated

    def test_has_plan(self) -> None:
        from codeplane.mcp.session import MutationContext, RefactorPlan

        ctx = MutationContext()
        assert not ctx.has_plan
        ctx.plan = RefactorPlan(plan_id="p1", recon_id="r1", description="test")
        assert ctx.has_plan

    def test_has_pending_refactors(self) -> None:
        from codeplane.mcp.session import MutationContext

        ctx = MutationContext()
        assert not ctx.has_pending_refactors
        ctx.pending_refactors["ref1"] = "rename"
        assert ctx.has_pending_refactors

    def test_is_empty(self) -> None:
        from codeplane.mcp.session import MutationContext, RefactorPlan

        ctx = MutationContext()
        assert ctx.is_empty
        ctx.plan = RefactorPlan(plan_id="p1", recon_id="r1", description="test")
        assert not ctx.is_empty
        ctx.plan = None
        ctx.pending_refactors["ref1"] = "rename"
        assert not ctx.is_empty

    def test_clear(self) -> None:
        from codeplane.mcp.session import EditTicket, MutationContext, RefactorPlan

        ctx = MutationContext()
        ctx.plan = RefactorPlan(plan_id="p1", recon_id="r1", description="test")
        ctx.edit_tickets["t1"] = EditTicket(
            ticket_id="t1",
            path="a.py",
            sha256="abc",
            candidate_id="c1",
            issued_by="resolve",
        )
        ctx.pending_refactors["ref1"] = "rename"
        ctx.mutations_since_checkpoint = 3

        ctx.clear()

        assert ctx.plan is None
        assert ctx.edit_tickets == {}
        assert ctx.pending_refactors == {}
        assert ctx.mutations_since_checkpoint == 0


class TestBackwardCompatProperties:
    """Tests that SessionState backward-compat properties delegate to mutation_ctx."""

    def test_active_plan_getter(self) -> None:
        from codeplane.mcp.session import RefactorPlan

        s = SessionState(session_id="s", created_at=0, last_active=0)
        assert s.active_plan is None
        plan = RefactorPlan(plan_id="p1", recon_id="r1", description="test")
        s.mutation_ctx.plan = plan
        assert s.active_plan is plan

    def test_active_plan_setter(self) -> None:
        from codeplane.mcp.session import RefactorPlan

        s = SessionState(session_id="s", created_at=0, last_active=0)
        plan = RefactorPlan(plan_id="p1", recon_id="r1", description="test")
        s.active_plan = plan
        assert s.mutation_ctx.plan is plan
        s.active_plan = None
        assert s.mutation_ctx.plan is None

    def test_edit_tickets_delegates(self) -> None:
        from codeplane.mcp.session import EditTicket

        s = SessionState(session_id="s", created_at=0, last_active=0)
        ticket = EditTicket(
            ticket_id="t1",
            path="a.py",
            sha256="abc",
            candidate_id="c1",
            issued_by="resolve",
        )
        s.mutation_ctx.edit_tickets["t1"] = ticket
        assert s.edit_tickets["t1"] is ticket
        assert s.edit_tickets is s.mutation_ctx.edit_tickets

    def test_edits_since_checkpoint_getter(self) -> None:
        s = SessionState(session_id="s", created_at=0, last_active=0)
        assert s.edits_since_checkpoint == 0
        s.mutation_ctx.mutations_since_checkpoint = 5
        assert s.edits_since_checkpoint == 5

    def test_edits_since_checkpoint_setter(self) -> None:
        s = SessionState(session_id="s", created_at=0, last_active=0)
        s.edits_since_checkpoint = 3
        assert s.mutation_ctx.mutations_since_checkpoint == 3
