"""File state computation for mutation gating.

This module implements the File State Model from SPEC.md §7.8:
- Freshness axis: CLEAN, DIRTY, STALE, PENDING_CHECK, UNINDEXED
- Certainty axis: CERTAIN, AMBIGUOUS, UNKNOWN

The combined state determines mutation behavior:
- CLEAN + CERTAIN: Automatic semantic edits allowed
- CLEAN + AMBIGUOUS: Return needs_decision (agent must confirm)
- DIRTY/STALE/PENDING_CHECK + *: Block with witness packet
- UNINDEXED + *: Block, suggest refresh
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlmodel import Session, select

from codeplane.index.models import (
    Certainty,
    Edge,
    File,
    FileSemanticFacts,
    FileState,
    Freshness,
)

if TYPE_CHECKING:
    from codeplane.index._internal.db import Database


class FileStateService:
    """
    Computes file state (Freshness × Certainty) for mutation gating.

    State computation requires:
    1. Content hash comparison (has file changed since indexing?)
    2. Dependency analysis (have dependencies' interfaces changed?)
    3. Ambiguity flag inspection (are there unresolved semantic ambiguities?)

    Uses per-request memoization to handle dependency cycles efficiently.
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
        """
        Compute file state with cycle-safe memoization.

        Args:
            file_id: ID of the file to check
            context_id: Context for semantic facts lookup
            memo: Optional memoization dict (created if None)

        Returns:
            FileState with freshness and certainty values
        """
        if memo is None:
            memo = {}

        key = (file_id, context_id)
        if key in memo:
            return memo[key]

        # Mark as in-progress to detect cycles
        # Cycles are treated as PENDING_CHECK (dependency unknown)
        memo[key] = FileState(freshness=Freshness.PENDING_CHECK, certainty=Certainty.UNKNOWN)

        with self._db.session() as session:
            state = self._compute_state(session, file_id, context_id, memo)

        memo[key] = state
        return state

    def get_file_states_batch(
        self,
        file_ids: list[int],
        context_id: int,
    ) -> dict[int, FileState]:
        """
        Compute states for multiple files efficiently.

        Shares memoization across all files to avoid redundant
        dependency chain traversals.

        Args:
            file_ids: IDs of files to check
            context_id: Context for semantic facts lookup

        Returns:
            Dict mapping file_id -> FileState
        """
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
        """
        Check if files are eligible for automatic mutation.

        Args:
            file_ids: Files to check
            context_id: Context for the operation

        Returns:
            MutationGateResult indicating eligibility and blockers
        """
        states = self.get_file_states_batch(file_ids, context_id)

        allowed: list[int] = []
        needs_decision: list[int] = []
        blocked: list[tuple[int, str]] = []

        for file_id, state in states.items():
            if state.freshness == Freshness.CLEAN:
                if state.certainty == Certainty.CERTAIN:
                    allowed.append(file_id)
                else:
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
        context_id: int,
        memo: dict[tuple[int, int], FileState],
    ) -> FileState:
        """Internal state computation with session."""
        # Get file record
        file = session.get(File, file_id)
        if file is None:
            return FileState(freshness=Freshness.UNINDEXED, certainty=Certainty.UNKNOWN)

        # Get semantic facts for this file + context
        stmt = select(FileSemanticFacts).where(
            FileSemanticFacts.file_id == file_id,
            FileSemanticFacts.context_id == context_id,
        )
        facts = session.exec(stmt).first()

        if facts is None:
            return FileState(freshness=Freshness.UNINDEXED, certainty=Certainty.UNKNOWN)

        # Check content freshness
        if facts.content_hash_at_index != file.content_hash:
            return FileState(freshness=Freshness.DIRTY, certainty=Certainty.UNKNOWN)

        # Check dependency freshness
        freshness = self._compute_dependency_freshness(
            session, file_id, context_id, memo
        )

        # Check certainty from ambiguity flags
        certainty = Certainty.CERTAIN
        if facts.ambiguity_flags:
            flags = facts.get_ambiguity_flags()
            if flags:
                certainty = Certainty.AMBIGUOUS

        return FileState(freshness=freshness, certainty=certainty)

    def _compute_dependency_freshness(
        self,
        session: Session,
        file_id: int,
        context_id: int,
        memo: dict[tuple[int, int], FileState],
    ) -> Freshness:
        """
        Check if any dependency has changed, affecting this file's freshness.

        Rules:
        - If any dep is STALE -> this file is STALE
        - If any dep is DIRTY and freshness is CLEAN -> PENDING_CHECK
        - Otherwise -> CLEAN
        """
        # Get dependency file IDs via Edge table
        stmt = select(Edge.target_file).where(
            Edge.source_file == file_id,
            Edge.context_id == context_id,
        )
        dep_ids = list(session.exec(stmt).all())

        if not dep_ids:
            return Freshness.CLEAN

        freshness = Freshness.CLEAN

        for dep_id in dep_ids:
            dep_state = self.get_file_state(dep_id, context_id, memo=memo)

            if dep_state.freshness == Freshness.STALE:
                # Dependency confirmed changed -> we're stale
                return Freshness.STALE
            if dep_state.freshness == Freshness.DIRTY and freshness == Freshness.CLEAN:
                # Dependency dirty, interface change unknown
                freshness = Freshness.PENDING_CHECK

        return freshness

    def mark_file_dirty(self, file_id: int, context_id: int) -> None:
        """
        Mark a file as dirty (content changed).

        This is called by the Reconciler when file content hash changes.
        Does NOT propagate - dependents will compute PENDING_CHECK on demand.
        """
        with self._db.session() as session:
            stmt = select(FileSemanticFacts).where(
                FileSemanticFacts.file_id == file_id,
                FileSemanticFacts.context_id == context_id,
            )
            facts = session.exec(stmt).first()
            if facts is not None:
                # Clear the content hash to force DIRTY state
                facts.content_hash_at_index = None
                session.add(facts)
                session.commit()

    def mark_file_stale(self, file_id: int, context_id: int) -> None:
        """
        Mark a file as stale (dependency interface changed).

        This is called when we confirm that an imported symbol's
        interface has changed (e.g., function signature changed).
        """
        with self._db.session() as session:
            stmt = select(FileSemanticFacts).where(
                FileSemanticFacts.file_id == file_id,
                FileSemanticFacts.context_id == context_id,
            )
            facts = session.exec(stmt).first()
            if facts is not None:
                # Set a sentinel to indicate staleness
                # We use ambiguity_flags with a special key
                flags = facts.get_ambiguity_flags()
                flags["__stale__"] = "dependency_interface_changed"
                facts.ambiguity_flags = str(flags)
                session.add(facts)
                session.commit()


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
