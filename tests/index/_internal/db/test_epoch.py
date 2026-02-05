"""Tests for epoch management."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

from codeplane.index._internal.db.epoch import EpochManager, EpochStats
from codeplane.index.models import Epoch, RepoState


class TestEpochStats:
    """Tests for EpochStats dataclass."""

    def test_epoch_stats_construction(self) -> None:
        """EpochStats holds epoch publication statistics."""
        stats = EpochStats(
            epoch_id=5,
            files_indexed=100,
            published_at=1234567890.0,
            commit_hash="abc123",
        )
        assert stats.epoch_id == 5
        assert stats.files_indexed == 100
        assert stats.published_at == 1234567890.0
        assert stats.commit_hash == "abc123"

    def test_epoch_stats_optional_commit_hash(self) -> None:
        """commit_hash is optional."""
        stats = EpochStats(
            epoch_id=1,
            files_indexed=0,
            published_at=time.time(),
            commit_hash=None,
        )
        assert stats.commit_hash is None


class TestEpochManager:
    """Tests for EpochManager."""

    def test_init_with_db_only(self) -> None:
        """EpochManager can be created with just a database."""
        mock_db = MagicMock()
        manager = EpochManager(mock_db)
        assert manager.db is mock_db
        assert manager.lexical is None

    def test_init_with_lexical(self) -> None:
        """EpochManager accepts optional lexical index."""
        mock_db = MagicMock()
        mock_lexical = MagicMock()
        manager = EpochManager(mock_db, lexical=mock_lexical)
        assert manager.lexical is mock_lexical

    def test_get_current_epoch_returns_zero_when_no_state(self) -> None:
        """Returns 0 when no RepoState exists."""
        mock_db = MagicMock()
        mock_session = MagicMock()
        mock_session.get.return_value = None
        mock_db.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.session.return_value.__exit__ = MagicMock(return_value=False)

        manager = EpochManager(mock_db)
        assert manager.get_current_epoch() == 0
        mock_session.get.assert_called_once_with(RepoState, 1)

    def test_get_current_epoch_returns_zero_when_epoch_none(self) -> None:
        """Returns 0 when RepoState has no epoch."""
        mock_db = MagicMock()
        mock_session = MagicMock()
        mock_state = MagicMock()
        mock_state.current_epoch_id = None
        mock_session.get.return_value = mock_state
        mock_db.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.session.return_value.__exit__ = MagicMock(return_value=False)

        manager = EpochManager(mock_db)
        assert manager.get_current_epoch() == 0

    def test_get_current_epoch_returns_stored_value(self) -> None:
        """Returns current_epoch_id from RepoState."""
        mock_db = MagicMock()
        mock_session = MagicMock()
        mock_state = MagicMock()
        mock_state.current_epoch_id = 42
        mock_session.get.return_value = mock_state
        mock_db.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.session.return_value.__exit__ = MagicMock(return_value=False)

        manager = EpochManager(mock_db)
        assert manager.get_current_epoch() == 42

    def test_get_epoch_fetches_by_id(self) -> None:
        """get_epoch retrieves epoch by ID."""
        mock_db = MagicMock()
        mock_session = MagicMock()
        mock_epoch = MagicMock(spec=Epoch)
        mock_session.get.return_value = mock_epoch
        mock_db.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.session.return_value.__exit__ = MagicMock(return_value=False)

        manager = EpochManager(mock_db)
        result = manager.get_epoch(5)
        assert result is mock_epoch
        mock_session.get.assert_called_once_with(Epoch, 5)

    def test_get_epoch_returns_none_when_not_found(self) -> None:
        """get_epoch returns None for missing epoch."""
        mock_db = MagicMock()
        mock_session = MagicMock()
        mock_session.get.return_value = None
        mock_db.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.session.return_value.__exit__ = MagicMock(return_value=False)

        manager = EpochManager(mock_db)
        assert manager.get_epoch(999) is None

    def test_get_latest_epochs_queries_descending(self) -> None:
        """get_latest_epochs returns epochs in descending order."""
        mock_db = MagicMock()
        mock_session = MagicMock()
        mock_epochs = [MagicMock(epoch_id=3), MagicMock(epoch_id=2)]
        mock_result = MagicMock()
        mock_result.all.return_value = mock_epochs
        mock_session.exec.return_value = mock_result
        mock_db.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.session.return_value.__exit__ = MagicMock(return_value=False)

        manager = EpochManager(mock_db)
        result = manager.get_latest_epochs(limit=10)
        assert len(result) == 2

    def test_await_epoch_returns_true_when_already_reached(self) -> None:
        """await_epoch returns True immediately if epoch already reached."""
        mock_db = MagicMock()
        mock_session = MagicMock()
        mock_state = MagicMock()
        mock_state.current_epoch_id = 10
        mock_session.get.return_value = mock_state
        mock_db.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.session.return_value.__exit__ = MagicMock(return_value=False)

        manager = EpochManager(mock_db)
        assert manager.await_epoch(5, timeout_seconds=0.1) is True

    def test_await_epoch_returns_false_on_timeout(self) -> None:
        """await_epoch returns False when epoch not reached in time."""
        mock_db = MagicMock()
        mock_session = MagicMock()
        mock_state = MagicMock()
        mock_state.current_epoch_id = 1
        mock_session.get.return_value = mock_state
        mock_db.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.session.return_value.__exit__ = MagicMock(return_value=False)

        manager = EpochManager(mock_db)
        # Short timeout to avoid slow test
        assert manager.await_epoch(100, timeout_seconds=0.05) is False

    def test_publish_epoch_increments_epoch_id(self, tmp_path: Path) -> None:
        """publish_epoch creates new epoch with incremented ID."""
        mock_db = MagicMock()

        # Setup for get_current_epoch
        mock_read_session = MagicMock()
        mock_state_read = MagicMock()
        mock_state_read.current_epoch_id = 5
        mock_read_session.get.return_value = mock_state_read

        # Setup for immediate_transaction
        mock_write_session = MagicMock()
        mock_state_write = MagicMock()
        mock_write_session.get.return_value = mock_state_write

        # Use side_effect to return different sessions

        def session_side_effect() -> MagicMock:
            cm = MagicMock()
            cm.__enter__ = MagicMock(return_value=mock_read_session)
            cm.__exit__ = MagicMock(return_value=False)
            return cm

        def transaction_side_effect() -> MagicMock:
            cm = MagicMock()
            cm.__enter__ = MagicMock(return_value=mock_write_session)
            cm.__exit__ = MagicMock(return_value=False)
            return cm

        mock_db.session = session_side_effect
        mock_db.immediate_transaction = transaction_side_effect

        manager = EpochManager(mock_db, journal_dir=tmp_path)
        stats = manager.publish_epoch(files_indexed=10, commit_hash="abc")

        assert stats.epoch_id == 6  # 5 + 1
        assert stats.files_indexed == 10
        assert stats.commit_hash == "abc"
        assert stats.published_at > 0

    def test_publish_epoch_reloads_lexical_if_present(self, tmp_path: Path) -> None:
        """publish_epoch reloads lexical index when available."""
        mock_db = MagicMock()
        mock_lexical = MagicMock()

        # Setup sessions
        mock_read_session = MagicMock()
        mock_state = MagicMock()
        mock_state.current_epoch_id = 0
        mock_read_session.get.return_value = mock_state

        mock_write_session = MagicMock()
        mock_write_session.get.return_value = None  # No existing state

        mock_db.session = lambda: MagicMock(
            __enter__=MagicMock(return_value=mock_read_session),
            __exit__=MagicMock(return_value=False),
        )
        mock_db.immediate_transaction = lambda: MagicMock(
            __enter__=MagicMock(return_value=mock_write_session),
            __exit__=MagicMock(return_value=False),
        )

        manager = EpochManager(mock_db, lexical=mock_lexical, journal_dir=tmp_path)
        manager.publish_epoch()

        mock_lexical.reload.assert_called_once()

    def test_publish_epoch_creates_repo_state_if_missing(self, tmp_path: Path) -> None:
        """publish_epoch creates RepoState if it doesn't exist."""
        mock_db = MagicMock()

        mock_read_session = MagicMock()
        mock_read_session.get.return_value = None  # No RepoState

        mock_write_session = MagicMock()
        mock_write_session.get.return_value = None  # Still no RepoState

        mock_db.session = lambda: MagicMock(
            __enter__=MagicMock(return_value=mock_read_session),
            __exit__=MagicMock(return_value=False),
        )
        mock_db.immediate_transaction = lambda: MagicMock(
            __enter__=MagicMock(return_value=mock_write_session),
            __exit__=MagicMock(return_value=False),
        )

        manager = EpochManager(mock_db, journal_dir=tmp_path)
        stats = manager.publish_epoch()

        # Should create epoch 1 (0 + 1)
        assert stats.epoch_id == 1
        # Should have called session.add for both Epoch and RepoState
        assert mock_write_session.add.call_count >= 2
