"""Session management for CodePlane MCP server.

Handles session lifecycle, state tracking, and task binding per Spec §23.3.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from codeplane.config.models import TimeoutsConfig
from codeplane.mcp.gate import CallPatternDetector, GateManager

# Tools that acquire an exclusive session lock while running.
# No other tool may execute concurrently on the same session.
EXCLUSIVE_TOOLS: frozenset[str] = frozenset({"checkpoint", "semantic_diff"})


@dataclass
class EditTicket:
    """Proof that a file was resolved — required by refactor_edit.

    Minted by recon_resolve, consumed (and refreshed) by refactor_edit.
    Format: ``{candidate_id}:{sha256_prefix}`` e.g. ``"abc123:0:3bd2b2fb"``
    """

    ticket_id: str
    path: str
    sha256: str
    candidate_id: str
    issued_by: str  # "resolve" or "continuation"
    used: bool = False


@dataclass
class RefactorPlan:
    """Declared edit plan — gates refactor_edit to planned files.

    Created by ``refactor_plan``, consumed by ``refactor_edit``,
    cleared by ``checkpoint``.  Ensures agents commit to an edit set
    before they can modify files.
    """

    plan_id: str
    recon_id: str
    description: str
    # How many refactor_edit calls the agent expects to make.
    # Default 1 — agents must justify >1 with batch_justification.
    expected_edit_calls: int = 1
    # Required when expected_edit_calls > 1: explain why a single
    # batched refactor_edit call is not possible.
    batch_justification: str | None = None
    # How many refactor_edit calls have been made against this plan.
    edit_calls_made: int = 0
    # candidate_id → repo-relative path (files declared for editing)
    edit_targets: dict[str, str] = field(default_factory=dict)
    # ticket_id → EditTicket (minted at plan time)
    edit_tickets: dict[str, EditTicket] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


# Soft cap: plans with more targets than this require gate_reason.
_MAX_PLAN_TARGETS = 8

# Maximum mutation batches (refactor_edit or refactor_commit) before
# checkpoint is required.  Resets on successful checkpoint.
_MAX_EDIT_BATCHES = 2


@dataclass
class MutationContext:
    """Unified lifecycle tracker for all in-flight mutations.

    Replaces the old split state (active_plan + edit_tickets +
    edits_since_checkpoint) with a single object that tracks both
    plan+edit and refactor_* mutations under one budget.
    """

    context_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: float = field(default_factory=time.time)

    # Plan+Edit tracking
    plan: RefactorPlan | None = None
    edit_tickets: dict[str, EditTicket] = field(default_factory=dict)

    # Refactor_* tracking: refactor_id → type ("rename"/"move"/"impact")
    pending_refactors: dict[str, str] = field(default_factory=dict)

    # Shared mutation budget (replaces edits_since_checkpoint)
    mutations_since_checkpoint: int = 0

    @property
    def has_plan(self) -> bool:
        return self.plan is not None

    @property
    def has_pending_refactors(self) -> bool:
        return bool(self.pending_refactors)

    @property
    def is_empty(self) -> bool:
        """True when no plan and no pending refactors — safe to discard."""
        return self.plan is None and not self.pending_refactors

    def clear(self) -> None:
        """Reset all mutation state.  Called by checkpoint."""
        self.plan = None
        self.edit_tickets.clear()
        self.pending_refactors.clear()
        self.mutations_since_checkpoint = 0


@dataclass
class SessionState:
    """State for a single session."""

    session_id: str
    created_at: float
    last_active: float
    task_id: str | None = None
    fingerprints: dict[str, str] = field(default_factory=dict)
    counters: dict[str, int] = field(default_factory=dict)
    # Maps recon_id → {candidate_id: repo_relative_path}.
    # Populated by recon pipeline, consumed by recon_resolve for
    # ID-based file selection (no raw path access).
    candidate_maps: dict[str, dict[str, str]] = field(default_factory=dict)
    # Unified mutation lifecycle — tracks plan+edit AND refactor_*
    # under one budget.  Created lazily, cleared by checkpoint.
    mutation_ctx: MutationContext = field(default_factory=MutationContext)
    # Resolve batch counter — used for escalating pressure hints.
    resolve_batch_count: int = 0
    # Tracks which files the agent has already resolved in this session.
    # Maps repo-relative path → sha256 hash.  Used by resolve hint to
    # remind agents what they already have (anti-reread mitigation).
    resolved_paths: dict[str, str] = field(default_factory=dict)
    # Read-only intent: True = research-only session (mutations blocked),
    # False = read-write session, None = not yet declared.
    # Set by recon(read_only=...), reset on new recon call.
    read_only: bool | None = None
    # Last recon_id — set by recon, required by resolve for ordering.
    last_recon_id: str | None = None
    gate_manager: GateManager = field(default_factory=GateManager)
    pattern_detector: CallPatternDetector = field(default_factory=CallPatternDetector)

    # Exclusive-tool lock: prevents concurrent tool execution during
    # long-running operations like checkpoint, semantic_diff, map_repo.
    _exclusive_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _exclusive_holder: str | None = field(default=None, repr=False)

    # ── Backward-compat properties ──
    # Delegate to mutation_ctx so existing callers keep working.

    @property
    def active_plan(self) -> RefactorPlan | None:
        return self.mutation_ctx.plan

    @active_plan.setter
    def active_plan(self, value: RefactorPlan | None) -> None:
        self.mutation_ctx.plan = value

    @property
    def edit_tickets(self) -> dict[str, EditTicket]:
        return self.mutation_ctx.edit_tickets

    @property
    def edits_since_checkpoint(self) -> int:
        return self.mutation_ctx.mutations_since_checkpoint

    @edits_since_checkpoint.setter
    def edits_since_checkpoint(self, value: int) -> None:
        self.mutation_ctx.mutations_since_checkpoint = value

    def touch(self) -> None:
        """Update last active timestamp."""
        self.last_active = time.time()

    @asynccontextmanager
    async def exclusive(self, tool_name: str) -> AsyncIterator[None]:
        """Acquire the exclusive lock for a long-running tool.

        While held, any other tool call on this session will block until
        the exclusive tool completes.
        """
        async with self._exclusive_lock:
            self._exclusive_holder = tool_name
            try:
                yield
            finally:
                self._exclusive_holder = None

    @property
    def exclusive_holder(self) -> str | None:
        """Name of the tool currently holding the exclusive lock, or None."""
        return self._exclusive_holder


class SessionManager:
    """Manages active sessions."""

    def __init__(self, config: TimeoutsConfig | None = None) -> None:
        self._sessions: dict[str, SessionState] = {}
        self._config = config or TimeoutsConfig()

    def get_or_create(self, session_id: str | None = None) -> SessionState:
        """Get existing session or create new one.

        Args:
            session_id: Optional session ID. If None, creates new session.

        Returns:
            SessionState object
        """
        if session_id and session_id in self._sessions:
            session = self._sessions[session_id]
            session.touch()
            return session

        # Create new session
        new_id = session_id or f"sess_{uuid.uuid4().hex[:12]}"
        session = SessionState(
            session_id=new_id,
            created_at=time.time(),
            last_active=time.time(),
        )
        self._sessions[new_id] = session
        return session

    def get(self, session_id: str) -> SessionState | None:
        """Get session if exists."""
        return self._sessions.get(session_id)

    def close(self, session_id: str) -> None:
        """Close a session."""
        if session_id in self._sessions:
            del self._sessions[session_id]

    def cleanup_stale(self) -> int:
        """Remove stale sessions.

        Returns:
            Number of sessions removed
        """
        now = time.time()
        to_remove = [
            sid
            for sid, s in self._sessions.items()
            if now - s.last_active > self._config.session_idle_sec
        ]
        for sid in to_remove:
            del self._sessions[sid]
        return len(to_remove)
