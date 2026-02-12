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
- Rollback journal for crash recovery

Atomicity Implementation:
- Solution A: Rollback journal written before commits, deleted after
- Solution B: Tantivy staging - changes buffered until commit_staged()
- On crash, recovery can detect incomplete epochs and repair
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from sqlmodel import select

from codeplane.index.models import Epoch, RepoState

if TYPE_CHECKING:
    from codeplane.index._internal.db import Database
    from codeplane.index._internal.indexing import LexicalIndex

logger = structlog.get_logger()


@dataclass
class EpochStats:
    """Statistics from an epoch publication."""

    epoch_id: int
    files_indexed: int
    published_at: float
    commit_hash: str | None


@dataclass
class EpochJournal:
    """Rollback journal for epoch atomicity.

    Written to disk before commits, deleted after successful completion.
    On crash recovery, presence of journal indicates incomplete epoch.
    """

    epoch_id: int
    tantivy_committed: bool = False
    sqlite_committed: bool = False
    created_at: float = 0.0

    def to_dict(self) -> dict[str, int | bool | float]:
        return {
            "epoch_id": self.epoch_id,
            "tantivy_committed": self.tantivy_committed,
            "sqlite_committed": self.sqlite_committed,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, int | bool | float]) -> EpochJournal:
        return cls(
            epoch_id=int(data["epoch_id"]),
            tantivy_committed=bool(data.get("tantivy_committed", False)),
            sqlite_committed=bool(data.get("sqlite_committed", False)),
            created_at=float(data.get("created_at", 0.0)),
        )


class EpochManager:
    """Manages epoch lifecycle for atomic index updates.

    Implements two-phase commit with rollback journal:
    1. Write journal to disk (marks epoch as in-progress)
    2. Commit Tantivy staged changes
    3. Update journal (tantivy_committed=True)
    4. Commit SQLite epoch record
    5. Delete journal (marks epoch as complete)

    On crash recovery:
    - If journal exists with tantivy_committed=False: Tantivy is unchanged, safe
    - If journal exists with tantivy_committed=True, sqlite_committed=False:
      SQLite doesn't have the epoch, but Tantivy does. Recovery rebuilds Tantivy.
    """

    def __init__(
        self,
        db: Database,
        lexical: LexicalIndex | None = None,
        journal_dir: Path | None = None,
    ) -> None:
        self.db = db
        self.lexical = lexical
        self._journal_dir = journal_dir or (
            db.db_path.parent if hasattr(db, "db_path") else Path.cwd()
        )

    def _journal_path(self, epoch_id: int) -> Path:
        """Get path for epoch journal file."""
        return self._journal_dir / f"epoch_{epoch_id}.journal"

    def _write_journal(self, journal: EpochJournal) -> None:
        """Write journal to disk with fsync for durability."""
        path = self._journal_path(journal.epoch_id)
        with open(path, "w") as f:
            json.dump(journal.to_dict(), f)
            f.flush()
            os.fsync(f.fileno())
        logger.debug("epoch_journal_written", epoch_id=journal.epoch_id)

    def _update_journal(self, journal: EpochJournal) -> None:
        """Update existing journal on disk."""
        self._write_journal(journal)

    def _delete_journal(self, epoch_id: int) -> None:
        """Delete journal after successful epoch completion."""
        path = self._journal_path(epoch_id)
        try:
            path.unlink()
            logger.debug("epoch_journal_deleted", epoch_id=epoch_id)
        except FileNotFoundError:
            pass

    def _read_journal(self, epoch_id: int) -> EpochJournal | None:
        """Read journal from disk if it exists."""
        path = self._journal_path(epoch_id)
        if not path.exists():
            return None
        try:
            with open(path) as f:
                return EpochJournal.from_dict(json.load(f))
        except (json.JSONDecodeError, KeyError):
            return None

    def find_incomplete_epochs(self) -> list[EpochJournal]:
        """Find any incomplete epoch journals (for crash recovery)."""
        incomplete = []
        for path in self._journal_dir.glob("epoch_*.journal"):
            try:
                with open(path) as f:
                    journal = EpochJournal.from_dict(json.load(f))
                    incomplete.append(journal)
            except (json.JSONDecodeError, KeyError, OSError):
                continue
        return incomplete

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
        Atomically publish a new epoch with two-phase commit.

        This commits all pending SQLite changes and Tantivy updates,
        then advances the epoch counter.

        Atomicity is achieved via:
        1. Rollback journal written before any commits
        2. Tantivy staged changes committed first
        3. SQLite epoch record committed second
        4. Journal deleted on success

        Args:
            files_indexed: Number of files indexed in this epoch
            commit_hash: Git commit hash at time of indexing
            indexed_paths: Paths of files indexed, to update last_indexed_epoch

        Per SPEC.md §7.6: Publishing an epoch means SQLite + Tantivy committed atomically.
        """
        current = self.get_current_epoch()
        new_epoch_id = current + 1
        published_at = time.time()

        # Phase 1: Write rollback journal
        journal = EpochJournal(
            epoch_id=new_epoch_id,
            tantivy_committed=False,
            sqlite_committed=False,
            created_at=published_at,
        )
        self._write_journal(journal)

        try:
            # Phase 2: Commit Tantivy staged changes
            if self.lexical and self.lexical.has_staged_changes():
                staged_count = self.lexical.commit_staged()
                logger.debug("tantivy_staged_committed", count=staged_count, epoch_id=new_epoch_id)
            elif self.lexical:
                # Just reload to see any direct writes
                self.lexical.reload()

            journal.tantivy_committed = True
            self._update_journal(journal)

            # Phase 3: Create epoch record and update RepoState atomically in SQLite
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
                    # Use SQLAlchemy's in_() for cleaner parameterization
                    from sqlalchemy import update

                    from codeplane.index.models import File

                    stmt = (
                        update(File.__table__)  # type: ignore
                        .where(File.path.in_(indexed_paths))  # type: ignore
                        .values(last_indexed_epoch=new_epoch_id)
                    )
                    session.execute(stmt)

                # Snapshot DefFacts for semantic diff
                if indexed_paths:
                    from codeplane.index.models import DefFact, DefSnapshotRecord

                    snap_stmt = (
                        select(DefFact, File.path)
                        .join(File, DefFact.file_id == File.id)  # type: ignore[arg-type]
                        .where(File.path.in_(indexed_paths))  # type: ignore[attr-defined]
                    )
                    for def_fact, fpath in session.execute(snap_stmt).all():
                        session.add(
                            DefSnapshotRecord(
                                epoch_id=new_epoch_id,
                                file_path=fpath,
                                kind=def_fact.kind,
                                name=def_fact.name,
                                lexical_path=def_fact.lexical_path,
                                signature_hash=def_fact.signature_hash,
                                display_name=def_fact.display_name,
                                start_line=def_fact.start_line,
                                end_line=def_fact.end_line,
                            )
                        )

                session.commit()

            journal.sqlite_committed = True
            # Don't need to write journal again - we're about to delete it

            # Phase 4: Delete journal (marks successful completion)
            self._delete_journal(new_epoch_id)

            logger.info(
                "epoch_published",
                epoch_id=new_epoch_id,
                files_indexed=files_indexed,
            )

        except Exception as e:
            # On failure, discard any uncommitted Tantivy changes
            if self.lexical and not journal.tantivy_committed:
                self.lexical.discard_staged()
            logger.error("epoch_publish_failed", epoch_id=new_epoch_id, error=str(e))
            raise

        return EpochStats(
            epoch_id=new_epoch_id,
            files_indexed=files_indexed,
            published_at=published_at,
            commit_hash=commit_hash,
        )

    def recover_incomplete_epoch(self, journal: EpochJournal) -> bool:
        """
        Recover from an incomplete epoch found on startup.

        Strategy:
        - If Tantivy was committed but SQLite wasn't, we need to rebuild
          Tantivy from SQLite (SQLite is authoritative)
        - If neither was committed, just delete the journal

        Returns:
            True if recovery was needed and performed, False if no action needed
        """
        if journal.sqlite_committed:
            # Both committed, just clean up the journal
            self._delete_journal(journal.epoch_id)
            return False

        if journal.tantivy_committed:
            # Tantivy has changes SQLite doesn't know about
            # This is a problem - Tantivy needs to be rebuilt from SQLite
            logger.warning(
                "epoch_recovery_tantivy_desync",
                epoch_id=journal.epoch_id,
                message="Tantivy committed but SQLite didn't. Tantivy rebuild required.",
            )
            self._delete_journal(journal.epoch_id)
            return True  # Caller should trigger Tantivy rebuild

        # Neither committed - just clean up
        logger.info("epoch_recovery_clean", epoch_id=journal.epoch_id)
        self._delete_journal(journal.epoch_id)
        return False

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
