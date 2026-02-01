"""Epoch management for atomic index updates.

Epochs are incremental snapshot barriers ensuring consistent index state.
Per SPEC.md §7.6:

- Epochs are incremental (no duplication of unchanged data)
- Only changed files are reindexed between epochs
- Publishing an epoch means: SQLite facts committed + Tantivy updates committed
- Epoch ID is monotonically increasing

The EpochManager provides:
- Current epoch tracking
- Atomic publish_epoch() that commits both SQLite and Tantivy
- Freshness checks for UX operations
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlmodel import select

from codeplane.index.models import Epoch, RepoState

if TYPE_CHECKING:
    from codeplane.index._internal.db import Database
    from codeplane.index._internal.indexing import LexicalIndex


@dataclass
class EpochStats:
    """Statistics from an epoch publication."""

    epoch_id: int
    files_indexed: int
    published_at: float
    commit_hash: str | None


class EpochManager:
    """Manages epoch lifecycle for atomic index updates."""

    def __init__(self, db: Database, lexical: LexicalIndex | None = None) -> None:
        self.db = db
        self.lexical = lexical

    def get_current_epoch(self) -> int:
        """Return current epoch ID from RepoState, or 0 if none."""
        with self.db.session() as session:
            state = session.get(RepoState, 1)
            if state and state.current_epoch_id is not None:
                return state.current_epoch_id
            return 0

    def publish_epoch(
        self,
        files_indexed: int = 0,
        commit_hash: str | None = None,
        indexed_paths: list[str] | None = None,
    ) -> EpochStats:
        """
        Atomically publish a new epoch.

        This commits all pending SQLite changes and Tantivy updates,
        then advances the epoch counter.

        Args:
            files_indexed: Number of files indexed in this epoch
            commit_hash: Git commit hash at time of indexing
            indexed_paths: Paths of files indexed, to update last_indexed_epoch

        Per SPEC.md §7.6: Publishing an epoch means SQLite + Tantivy committed.
        """
        current = self.get_current_epoch()
        new_epoch_id = current + 1
        published_at = time.time()

        # Commit Tantivy if available (LexicalIndex commits on add_file)
        # Here we just ensure the searcher sees latest
        if self.lexical:
            self.lexical.reload()

        # Create epoch record and update RepoState atomically
        with self.db.immediate_transaction() as session:
            # Create epoch record
            epoch = Epoch(
                epoch_id=new_epoch_id,
                published_at=published_at,
                files_indexed=files_indexed,
                commit_hash=commit_hash,
            )
            session.add(epoch)

            # Update RepoState
            state = session.get(RepoState, 1)
            if state:
                state.current_epoch_id = new_epoch_id
            else:
                state = RepoState(id=1, current_epoch_id=new_epoch_id)
                session.add(state)

            # Update last_indexed_epoch for indexed files
            if indexed_paths:
                # Use batch update for efficiency
                placeholders = ", ".join(f":p{i}" for i in range(len(indexed_paths)))
                params: dict[str, str | int] = {f"p{i}": p for i, p in enumerate(indexed_paths)}
                params["epoch"] = new_epoch_id
                from sqlalchemy import text

                session.exec(  # type: ignore[call-overload]
                    text(
                        f"UPDATE files SET last_indexed_epoch = :epoch "
                        f"WHERE path IN ({placeholders})"
                    ).bindparams(**params)
                )

            session.commit()

        return EpochStats(
            epoch_id=new_epoch_id,
            files_indexed=files_indexed,
            published_at=published_at,
            commit_hash=commit_hash,
        )

    def get_epoch(self, epoch_id: int) -> Epoch | None:
        """Get epoch record by ID."""
        with self.db.session() as session:
            return session.get(Epoch, epoch_id)

    def get_latest_epochs(self, limit: int = 10) -> list[Epoch]:
        """Return latest epochs in descending order."""
        with self.db.session() as session:
            stmt = (
                select(Epoch)
                .order_by(Epoch.epoch_id.desc())  # type: ignore[union-attr]
                .limit(limit)
            )
            return list(session.exec(stmt).all())

    def await_epoch(self, target_epoch: int, timeout_seconds: float = 5.0) -> bool:
        """
        Block until epoch >= target_epoch or timeout.

        Per SPEC.md §7.6 Freshness Contract: UX never reads stale data.
        This is used to wait for background indexing to catch up.

        Returns True if epoch reached, False on timeout.
        """
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            if self.get_current_epoch() >= target_epoch:
                return True
            time.sleep(0.01)  # 10ms poll
        return False
