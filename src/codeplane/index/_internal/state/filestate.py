"""File state computation for mutation gating.

This module implements file freshness tracking. With the Tier 0 + Tier 1
architecture (no semantic layer), the state model is simplified:

- Freshness: CLEAN, DIRTY, UNINDEXED
- Certainty: CERTAIN, UNCERTAIN (based on ref_tier classification)

The Tier 0 + Tier 1 index provides no semantic guarantees, so automatic
mutation decisions must be made by the refactor planner layer (future).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlmodel import Session

from codeplane.index.models import (
    Certainty,
    File,
    FileState,
    Freshness,
)

if TYPE_CHECKING:
    from codeplane.index._internal.db import Database


class FileStateService:
    """Computes file state (Freshness × Certainty) for mutation gating.

    With the Tier 0 + Tier 1 architecture, this provides a simplified
    state model based on content hash comparison only. No dependency
    chain analysis is performed (that would require semantic facts).
    """

    def __init__(self, db: Database) -> None:
        """Initialize with database connection."""
        self._db = db

    def get_file_state(
        self,
        file_id: int,
        context_id: int,
        *,
        memo: dict[tuple[int, int], FileState] | None = None,
    ) -> FileState:
        """Compute file state.

        Args:
            file_id: ID of the file to check
            context_id: Context for lookup (currently unused in Tier 0+1)
            memo: Optional memoization dict

        Returns:
            FileState with freshness and certainty values
        """
        if memo is None:
            memo = {}

        key = (file_id, context_id)
        if key in memo:
            return memo[key]

        with self._db.session() as session:
            state = self._compute_state(session, file_id)

        memo[key] = state
        return state

    def get_file_states_batch(
        self,
        file_ids: list[int],
        context_id: int,
    ) -> dict[int, FileState]:
        """Compute states for multiple files efficiently."""
        memo: dict[tuple[int, int], FileState] = {}
        result: dict[int, FileState] = {}

        for file_id in file_ids:
            result[file_id] = self.get_file_state(file_id, context_id, memo=memo)

        return result

    def check_mutation_gate(
        self,
        file_ids: list[int],
        context_id: int,
    ) -> MutationGateResult:
        """Check if files are eligible for mutation.

        With Tier 0 + Tier 1 architecture, this provides basic freshness
        checking only. No semantic certainty is available.
        """
        states = self.get_file_states_batch(file_ids, context_id)

        allowed: list[int] = []
        needs_decision: list[int] = []
        blocked: list[tuple[int, str]] = []

        for file_id, state in states.items():
            if state.freshness == Freshness.CLEAN:
                # In Tier 0+1, all CLEAN files need decision (no semantic proof)
                needs_decision.append(file_id)
            elif state.freshness == Freshness.UNINDEXED:
                blocked.append((file_id, "unindexed"))
            else:
                blocked.append((file_id, state.freshness.value))

        return MutationGateResult(
            allowed=allowed,
            needs_decision=needs_decision,
            blocked=blocked,
            all_allowed=len(blocked) == 0 and len(needs_decision) == 0,
        )

    def _compute_state(
        self,
        session: Session,
        file_id: int,
    ) -> FileState:
        """Internal state computation."""
        file = session.get(File, file_id)
        if file is None:
            return FileState(freshness=Freshness.UNINDEXED, certainty=Certainty.UNCERTAIN)

        # Check if file has been indexed
        if file.indexed_at is None:
            return FileState(freshness=Freshness.UNINDEXED, certainty=Certainty.UNCERTAIN)

        # In Tier 0+1, we can't determine staleness without semantic facts
        # All indexed files are considered CLEAN but UNCERTAIN
        return FileState(freshness=Freshness.CLEAN, certainty=Certainty.UNCERTAIN)

    def mark_file_dirty(self, file_id: int, context_id: int) -> None:
        """Mark a file as dirty (content changed).

        Called by the Reconciler when file content hash changes.
        """
        # In Tier 0+1, dirtiness is implicit from content_hash mismatch
        # No separate tracking needed
        pass

    def mark_file_stale(self, file_id: int, context_id: int) -> None:
        """Mark a file as stale (dependency changed).

        In Tier 0+1, we don't track dependency chains, so this is a no-op.
        """
        pass


class MutationGateResult:
    """Result of mutation gate check."""

    __slots__ = ("allowed", "needs_decision", "blocked", "all_allowed")

    def __init__(
        self,
        *,
        allowed: list[int],
        needs_decision: list[int],
        blocked: list[tuple[int, str]],
        all_allowed: bool,
    ) -> None:
        self.allowed = allowed
        self.needs_decision = needs_decision
        self.blocked = blocked
        self.all_allowed = all_allowed
