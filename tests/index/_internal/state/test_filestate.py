"""Tests for file state computation."""

from __future__ import annotations

from unittest.mock import MagicMock

from codeplane.index._internal.state.filestate import (
    FileStateService,
    MutationGateResult,
)
from codeplane.index.models import Certainty, FileState, Freshness


class TestMutationGateResult:
    """Tests for MutationGateResult class."""

    def test_construction(self) -> None:
        """MutationGateResult stores gate check results."""
        result = MutationGateResult(
            allowed=[1, 2],
            needs_decision=[3, 4],
            blocked=[(5, "dirty"), (6, "unindexed")],
            all_allowed=False,
        )
        assert result.allowed == [1, 2]
        assert result.needs_decision == [3, 4]
        assert result.blocked == [(5, "dirty"), (6, "unindexed")]
        assert result.all_allowed is False

    def test_all_allowed_when_empty(self) -> None:
        """all_allowed is True when no blocked or needs_decision."""
        result = MutationGateResult(
            allowed=[1, 2, 3],
            needs_decision=[],
            blocked=[],
            all_allowed=True,
        )
        assert result.all_allowed is True


class TestFileStateService:
    """Tests for FileStateService."""

    def test_init_stores_db(self) -> None:
        """FileStateService stores database reference."""
        mock_db = MagicMock()
        service = FileStateService(mock_db)
        assert service._db is mock_db

    def test_get_file_state_uses_memoization(self) -> None:
        """get_file_state uses provided memo dict."""
        mock_db = MagicMock()
        service = FileStateService(mock_db)

        memo: dict[tuple[int, int], FileState] = {}
        cached_state = FileState(
            freshness=Freshness.CLEAN,
            certainty=Certainty.CERTAIN,
        )
        memo[(1, 2)] = cached_state

        result = service.get_file_state(1, 2, memo=memo)
        assert result is cached_state
        # Database should not be accessed
        mock_db.session.assert_not_called()

    def test_get_file_state_returns_unindexed_for_missing_file(self) -> None:
        """Returns UNINDEXED for file not in database."""
        mock_db = MagicMock()
        mock_session = MagicMock()
        mock_session.get.return_value = None
        mock_db.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.session.return_value.__exit__ = MagicMock(return_value=False)

        service = FileStateService(mock_db)
        result = service.get_file_state(999, 1)

        assert result.freshness == Freshness.UNINDEXED
        assert result.certainty == Certainty.UNCERTAIN

    def test_get_file_state_returns_unindexed_when_not_indexed(self) -> None:
        """Returns UNINDEXED when file has no indexed_at timestamp."""
        mock_db = MagicMock()
        mock_session = MagicMock()
        mock_file = MagicMock()
        mock_file.indexed_at = None
        mock_session.get.return_value = mock_file
        mock_db.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.session.return_value.__exit__ = MagicMock(return_value=False)

        service = FileStateService(mock_db)
        result = service.get_file_state(1, 1)

        assert result.freshness == Freshness.UNINDEXED
        assert result.certainty == Certainty.UNCERTAIN

    def test_get_file_state_returns_clean_uncertain_for_indexed(self) -> None:
        """Returns CLEAN/UNCERTAIN for indexed file in Tier 0+1 model."""
        mock_db = MagicMock()
        mock_session = MagicMock()
        mock_file = MagicMock()
        mock_file.indexed_at = 1234567890.0
        mock_session.get.return_value = mock_file
        mock_db.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.session.return_value.__exit__ = MagicMock(return_value=False)

        service = FileStateService(mock_db)
        result = service.get_file_state(1, 1)

        assert result.freshness == Freshness.CLEAN
        assert result.certainty == Certainty.UNCERTAIN

    def test_get_file_states_batch(self) -> None:
        """get_file_states_batch returns states for multiple files."""
        mock_db = MagicMock()
        mock_session = MagicMock()
        mock_file = MagicMock()
        mock_file.indexed_at = 1234567890.0
        mock_session.get.return_value = mock_file
        mock_db.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.session.return_value.__exit__ = MagicMock(return_value=False)

        service = FileStateService(mock_db)
        result = service.get_file_states_batch([1, 2, 3], context_id=1)

        assert len(result) == 3
        assert 1 in result
        assert 2 in result
        assert 3 in result

    def test_check_mutation_gate_blocks_unindexed(self) -> None:
        """check_mutation_gate blocks unindexed files."""
        mock_db = MagicMock()
        mock_session = MagicMock()
        mock_session.get.return_value = None  # File not found
        mock_db.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.session.return_value.__exit__ = MagicMock(return_value=False)

        service = FileStateService(mock_db)
        result = service.check_mutation_gate([1, 2], context_id=1)

        assert result.all_allowed is False
        assert len(result.blocked) == 2
        assert all(reason == "unindexed" for _, reason in result.blocked)

    def test_check_mutation_gate_needs_decision_for_clean(self) -> None:
        """check_mutation_gate puts CLEAN files in needs_decision."""
        mock_db = MagicMock()
        mock_session = MagicMock()
        mock_file = MagicMock()
        mock_file.indexed_at = 1234567890.0
        mock_session.get.return_value = mock_file
        mock_db.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.session.return_value.__exit__ = MagicMock(return_value=False)

        service = FileStateService(mock_db)
        result = service.check_mutation_gate([1], context_id=1)

        # In Tier 0+1, all CLEAN files need decision (no semantic proof)
        assert 1 in result.needs_decision
        assert result.all_allowed is False

    def test_mark_file_dirty_is_noop(self) -> None:
        """mark_file_dirty is a no-op in Tier 0+1 model."""
        mock_db = MagicMock()
        service = FileStateService(mock_db)
        # Should not raise
        service.mark_file_dirty(1, 1)
        mock_db.session.assert_not_called()

    def test_mark_file_stale_is_noop(self) -> None:
        """mark_file_stale is a no-op in Tier 0+1 model."""
        mock_db = MagicMock()
        service = FileStateService(mock_db)
        # Should not raise
        service.mark_file_stale(1, 1)
        mock_db.session.assert_not_called()
