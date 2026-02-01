"""Tests for FileStateService and mutation gating."""

from __future__ import annotations

import time

import pytest

from codeplane.index._internal.db import Database
from codeplane.index._internal.state.filestate import FileStateService, MutationGateResult
from codeplane.index.models import Certainty, File, Freshness


@pytest.fixture
def db(tmp_path) -> Database:
    """Create a test database."""
    db_path = tmp_path / "test.db"
    database = Database(db_path)
    database.create_all()
    return database


@pytest.fixture
def service(db: Database) -> FileStateService:
    """Create FileStateService with test database."""
    return FileStateService(db)


class TestGetFileState:
    """Tests for get_file_state method."""

    def test_nonexistent_file_returns_unindexed_uncertain(self, service: FileStateService) -> None:
        """Nonexistent file returns UNINDEXED/UNCERTAIN."""
        state = service.get_file_state(file_id=999, context_id=1)

        assert state.freshness == Freshness.UNINDEXED
        assert state.certainty == Certainty.UNCERTAIN

    def test_file_without_indexed_at_returns_unindexed(
        self, db: Database, service: FileStateService
    ) -> None:
        """File with no indexed_at timestamp returns UNINDEXED."""
        # Create file without indexed_at
        with db.session() as session:
            file = File(
                path="test.py",
                content_hash="abc123",
                indexed_at=None,
            )
            session.add(file)
            session.commit()
            file_id = file.id

        state = service.get_file_state(file_id=file_id, context_id=1)

        assert state.freshness == Freshness.UNINDEXED
        assert state.certainty == Certainty.UNCERTAIN

    def test_indexed_file_returns_clean_uncertain(
        self, db: Database, service: FileStateService
    ) -> None:
        """Indexed file returns CLEAN/UNCERTAIN in Tier 0+1."""
        # Create indexed file
        with db.session() as session:
            file = File(
                path="test.py",
                content_hash="abc123",
                indexed_at=time.time(),
            )
            session.add(file)
            session.commit()
            file_id = file.id

        state = service.get_file_state(file_id=file_id, context_id=1)

        assert state.freshness == Freshness.CLEAN
        assert state.certainty == Certainty.UNCERTAIN

    def test_memoization_returns_cached_state(
        self, db: Database, service: FileStateService
    ) -> None:
        """Memoization caches results by (file_id, context_id)."""
        # Create indexed file
        with db.session() as session:
            file = File(
                path="test.py",
                content_hash="abc123",
                indexed_at=time.time(),
            )
            session.add(file)
            session.commit()
            file_id = file.id

        memo: dict = {}
        state1 = service.get_file_state(file_id=file_id, context_id=1, memo=memo)
        state2 = service.get_file_state(file_id=file_id, context_id=1, memo=memo)

        # Same object returned from cache
        assert state1 is state2
        assert (file_id, 1) in memo


class TestGetFileStatesBatch:
    """Tests for batch file state retrieval."""

    def test_batch_retrieves_multiple_file_states(
        self, db: Database, service: FileStateService
    ) -> None:
        """Batch retrieval gets states for multiple files."""
        # Create multiple files
        file_ids = []
        with db.session() as session:
            for i in range(3):
                file = File(
                    path=f"test{i}.py",
                    content_hash=f"hash{i}",
                    indexed_at=time.time() if i < 2 else None,
                )
                session.add(file)
                session.commit()
                file_ids.append(file.id)

        states = service.get_file_states_batch(file_ids, context_id=1)

        assert len(states) == 3
        # First two are indexed -> CLEAN
        assert states[file_ids[0]].freshness == Freshness.CLEAN
        assert states[file_ids[1]].freshness == Freshness.CLEAN
        # Third not indexed -> UNINDEXED
        assert states[file_ids[2]].freshness == Freshness.UNINDEXED

    def test_batch_with_empty_list_returns_empty_dict(self, service: FileStateService) -> None:
        """Empty file list returns empty dict."""
        states = service.get_file_states_batch([], context_id=1)
        assert states == {}


class TestCheckMutationGate:
    """Tests for mutation gate checking."""

    def test_indexed_files_need_decision(self, db: Database, service: FileStateService) -> None:
        """Indexed (CLEAN) files need human/agent decision in Tier 0+1."""
        # Create indexed files
        file_ids = []
        with db.session() as session:
            for i in range(2):
                file = File(
                    path=f"test{i}.py",
                    content_hash=f"hash{i}",
                    indexed_at=time.time(),
                )
                session.add(file)
                session.commit()
                file_ids.append(file.id)

        result = service.check_mutation_gate(file_ids, context_id=1)

        assert result.allowed == []
        assert set(result.needs_decision) == set(file_ids)
        assert result.blocked == []
        assert result.all_allowed is False

    def test_unindexed_files_are_blocked(self, db: Database, service: FileStateService) -> None:
        """Unindexed files are blocked from mutation."""
        # Create unindexed file
        with db.session() as session:
            file = File(
                path="test.py",
                content_hash="hash",
                indexed_at=None,
            )
            session.add(file)
            session.commit()
            file_id = file.id

        result = service.check_mutation_gate([file_id], context_id=1)

        assert result.allowed == []
        assert result.needs_decision == []
        assert len(result.blocked) == 1
        assert result.blocked[0] == (file_id, "unindexed")
        assert result.all_allowed is False

    def test_nonexistent_files_are_blocked(self, service: FileStateService) -> None:
        """Nonexistent files are blocked as unindexed."""
        result = service.check_mutation_gate([999], context_id=1)

        assert result.allowed == []
        assert result.needs_decision == []
        assert result.blocked == [(999, "unindexed")]
        assert result.all_allowed is False

    def test_mixed_files_categorized_correctly(
        self, db: Database, service: FileStateService
    ) -> None:
        """Mix of indexed and unindexed files categorized correctly."""
        file_ids = []
        with db.session() as session:
            # Indexed file
            indexed = File(
                path="indexed.py",
                content_hash="hash1",
                indexed_at=time.time(),
            )
            session.add(indexed)
            session.commit()
            file_ids.append(indexed.id)

            # Unindexed file
            unindexed = File(
                path="unindexed.py",
                content_hash="hash2",
                indexed_at=None,
            )
            session.add(unindexed)
            session.commit()
            file_ids.append(unindexed.id)

        # Add nonexistent file
        file_ids.append(999)

        result = service.check_mutation_gate(file_ids, context_id=1)

        assert result.needs_decision == [file_ids[0]]
        blocked_ids = [b[0] for b in result.blocked]
        assert file_ids[1] in blocked_ids
        assert 999 in blocked_ids

    def test_empty_file_list_all_allowed(self, service: FileStateService) -> None:
        """Empty file list returns all_allowed=True."""
        result = service.check_mutation_gate([], context_id=1)

        assert result.allowed == []
        assert result.needs_decision == []
        assert result.blocked == []
        assert result.all_allowed is True


class TestMarkFileDirtyAndStale:
    """Tests for mark_file_dirty and mark_file_stale (no-ops in Tier 0+1)."""

    def test_mark_file_dirty_is_noop(self, service: FileStateService) -> None:
        """mark_file_dirty is a no-op in Tier 0+1."""
        # Should not raise
        service.mark_file_dirty(file_id=1, context_id=1)

    def test_mark_file_stale_is_noop(self, service: FileStateService) -> None:
        """mark_file_stale is a no-op in Tier 0+1."""
        # Should not raise
        service.mark_file_stale(file_id=1, context_id=1)


class TestMutationGateResult:
    """Tests for MutationGateResult dataclass."""

    def test_attributes_accessible(self) -> None:
        """All attributes are accessible."""
        result = MutationGateResult(
            allowed=[1, 2],
            needs_decision=[3],
            blocked=[(4, "reason")],
            all_allowed=False,
        )

        assert result.allowed == [1, 2]
        assert result.needs_decision == [3]
        assert result.blocked == [(4, "reason")]
        assert result.all_allowed is False

    def test_slots_defined(self) -> None:
        """Class uses __slots__ for memory efficiency."""
        assert hasattr(MutationGateResult, "__slots__")
        result = MutationGateResult(
            allowed=[],
            needs_decision=[],
            blocked=[],
            all_allowed=True,
        )
        # Cannot add arbitrary attributes
        with pytest.raises(AttributeError):
            result.extra = "value"  # type: ignore
