"""Tests for index/_internal/indexing/type_resolver.py module.

Covers:
- TypeTracedStats dataclass
- TypeTracedResolver class
- resolve_type_traced() convenience function
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from codeplane.index._internal.indexing.type_resolver import (
    TypeTracedResolver,
    TypeTracedStats,
    resolve_type_traced,
)


class TestTypeTracedStats:
    """Tests for TypeTracedStats dataclass."""

    def test_default_values(self) -> None:
        """Default values are all zero."""
        stats = TypeTracedStats()
        assert stats.accesses_processed == 0
        assert stats.accesses_resolved == 0
        assert stats.accesses_partial == 0
        assert stats.accesses_unresolved == 0
        assert stats.refs_upgraded == 0

    def test_custom_values(self) -> None:
        """Can set custom values."""
        stats = TypeTracedStats(
            accesses_processed=100,
            accesses_resolved=60,
            accesses_partial=20,
            accesses_unresolved=20,
            refs_upgraded=50,
        )
        assert stats.accesses_processed == 100
        assert stats.accesses_resolved == 60
        assert stats.accesses_partial == 20

    def test_values_are_mutable(self) -> None:
        """Stats values can be modified."""
        stats = TypeTracedStats()
        stats.accesses_resolved = 10
        assert stats.accesses_resolved == 10


class TestTypeTracedResolver:
    """Tests for TypeTracedResolver class."""

    @pytest.fixture
    def mock_db(self) -> MagicMock:
        """Create mock database."""
        db = MagicMock()
        session = MagicMock()
        db.session.return_value.__enter__ = MagicMock(return_value=session)
        db.session.return_value.__exit__ = MagicMock(return_value=False)
        return db

    def test_init(self, mock_db: MagicMock) -> None:
        """Can create resolver."""
        resolver = TypeTracedResolver(mock_db)
        assert resolver._db == mock_db

    def test_resolve_all_empty(self, mock_db: MagicMock) -> None:
        """Returns empty stats when no accesses."""
        session = MagicMock()
        mock_db.session.return_value.__enter__.return_value = session
        session.exec.return_value.all.return_value = []

        resolver = TypeTracedResolver(mock_db)
        stats = resolver.resolve_all()

        assert stats.accesses_processed == 0
        assert stats.accesses_resolved == 0

    def test_resolve_for_files(self, mock_db: MagicMock) -> None:
        """Resolves accesses for specific files."""
        session = MagicMock()
        mock_db.session.return_value.__enter__.return_value = session
        session.exec.return_value.all.return_value = []

        resolver = TypeTracedResolver(mock_db)
        stats = resolver.resolve_for_files([1, 2, 3])

        assert stats.accesses_processed == 0

    def test_build_type_cache(self, mock_db: MagicMock) -> None:
        """Builds type annotation cache."""
        session = MagicMock()
        mock_db.session.return_value.__enter__.return_value = session

        # Mock annotations
        ann = MagicMock()
        ann.target_name = "ctx"
        ann.scope_id = 1
        ann.base_type = "AppContext"
        session.exec.return_value.all.return_value = [ann]

        resolver = TypeTracedResolver(mock_db)
        resolver._build_type_cache(session)

        assert ("ctx", 1) in resolver._type_map
        assert resolver._type_map[("ctx", 1)] == "AppContext"

    def test_build_member_cache(self, mock_db: MagicMock) -> None:
        """Builds type member cache."""
        session = MagicMock()
        mock_db.session.return_value.__enter__.return_value = session

        # Mock member
        member = MagicMock()
        member.parent_type_name = "AppContext"
        member.member_name = "file_ops"
        member.member_def_uid = "def-123"
        member.base_type = "FileOps"
        session.exec.return_value.all.return_value = [member]

        resolver = TypeTracedResolver(mock_db)
        resolver._build_member_cache(session)

        key = ("AppContext", "file_ops")
        assert key in resolver._member_map
        assert resolver._member_map[key] is member


class TestResolveTypeTracedFunction:
    """Tests for resolve_type_traced convenience function."""

    def test_calls_resolve_all_when_no_file_ids(self) -> None:
        """Calls resolve_all when file_ids is None."""
        with patch.object(TypeTracedResolver, "resolve_all") as mock_resolve:
            mock_resolve.return_value = TypeTracedStats(accesses_processed=10)

            mock_db = MagicMock()
            stats = resolve_type_traced(mock_db, file_ids=None)

            mock_resolve.assert_called_once()
            assert stats.accesses_processed == 10

    def test_calls_resolve_for_files_when_file_ids_provided(self) -> None:
        """Calls resolve_for_files when file_ids is provided."""
        with patch.object(TypeTracedResolver, "resolve_for_files") as mock_resolve:
            mock_resolve.return_value = TypeTracedStats(accesses_resolved=5)

            mock_db = MagicMock()
            stats = resolve_type_traced(mock_db, file_ids=[1, 2, 3])

            mock_resolve.assert_called_once_with([1, 2, 3])
            assert stats.accesses_resolved == 5
