"""Tests for epoch management."""

from __future__ import annotations

import time
from pathlib import Path

from codeplane.index._internal.db import Database, EpochManager
from codeplane.index._internal.indexing import LexicalIndex
from codeplane.index.models import RepoState


class TestEpochManager:
    """Tests for EpochManager."""

    def test_initial_epoch_is_zero(self, tmp_path: Path) -> None:
        """Without any published epochs, current epoch is 0."""
        db_path = tmp_path / "index.db"
        db = Database(db_path)
        db.create_all()

        manager = EpochManager(db)
        assert manager.get_current_epoch() == 0

    def test_publish_epoch_increments(self, tmp_path: Path) -> None:
        """publish_epoch() increments epoch ID."""
        db_path = tmp_path / "index.db"
        db = Database(db_path)
        db.create_all()

        manager = EpochManager(db)

        stats1 = manager.publish_epoch(files_indexed=5)
        assert stats1.epoch_id == 1
        assert stats1.files_indexed == 5
        assert manager.get_current_epoch() == 1

        stats2 = manager.publish_epoch(files_indexed=3, commit_hash="abc123")
        assert stats2.epoch_id == 2
        assert stats2.commit_hash == "abc123"
        assert manager.get_current_epoch() == 2

    def test_epoch_record_persisted(self, tmp_path: Path) -> None:
        """Epoch records are persisted to database."""
        db_path = tmp_path / "index.db"
        db = Database(db_path)
        db.create_all()

        manager = EpochManager(db)
        manager.publish_epoch(files_indexed=10, commit_hash="def456")

        epoch = manager.get_epoch(1)
        assert epoch is not None
        assert epoch.epoch_id == 1
        assert epoch.files_indexed == 10
        assert epoch.commit_hash == "def456"
        assert epoch.published_at is not None

    def test_get_latest_epochs(self, tmp_path: Path) -> None:
        """get_latest_epochs returns epochs in descending order."""
        db_path = tmp_path / "index.db"
        db = Database(db_path)
        db.create_all()

        manager = EpochManager(db)
        for i in range(5):
            manager.publish_epoch(files_indexed=i)

        epochs = manager.get_latest_epochs(limit=3)
        assert len(epochs) == 3
        assert epochs[0].epoch_id == 5
        assert epochs[1].epoch_id == 4
        assert epochs[2].epoch_id == 3

    def test_await_epoch_immediate(self, tmp_path: Path) -> None:
        """await_epoch returns immediately if already at target."""
        db_path = tmp_path / "index.db"
        db = Database(db_path)
        db.create_all()

        manager = EpochManager(db)
        manager.publish_epoch()
        manager.publish_epoch()

        start = time.time()
        result = manager.await_epoch(2, timeout_seconds=1.0)
        elapsed = time.time() - start

        assert result is True
        assert elapsed < 0.1  # Should be nearly instant

    def test_await_epoch_timeout(self, tmp_path: Path) -> None:
        """await_epoch times out if target not reached."""
        db_path = tmp_path / "index.db"
        db = Database(db_path)
        db.create_all()

        manager = EpochManager(db)
        # Never publish, epoch stays at 0

        start = time.time()
        result = manager.await_epoch(1, timeout_seconds=0.1)
        elapsed = time.time() - start

        assert result is False
        assert elapsed >= 0.1

    def test_publish_with_tantivy(self, tmp_path: Path) -> None:
        """publish_epoch triggers Tantivy reload."""
        db_path = tmp_path / "index.db"
        tantivy_path = tmp_path / "tantivy"
        db = Database(db_path)
        db.create_all()

        lexical = LexicalIndex(tantivy_path)
        manager = EpochManager(db, lexical)

        # Add a file to tantivy
        lexical.add_file("test.py", "def foo(): pass", context_id=1)

        # Publish epoch (should reload tantivy)
        stats = manager.publish_epoch(files_indexed=1)
        assert stats.epoch_id == 1

        # Tantivy should be searchable
        assert lexical.doc_count() == 1

    def test_repo_state_updated(self, tmp_path: Path) -> None:
        """RepoState.current_epoch_id is updated on publish."""
        db_path = tmp_path / "index.db"
        db = Database(db_path)
        db.create_all()

        manager = EpochManager(db)
        manager.publish_epoch()

        with db.session() as session:
            state = session.get(RepoState, 1)
            assert state is not None
            assert state.current_epoch_id == 1
