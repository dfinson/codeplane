"""Tests for index/_internal/indexing/shape_resolver.py module.

Covers:
- ShapeInferenceStats dataclass
- TypeMatch dataclass
- ShapeInferenceResolver class
- resolve_shape_inference() convenience function
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from codeplane.index._internal.indexing.shape_resolver import (
    ShapeInferenceResolver,
    ShapeInferenceStats,
    TypeMatch,
    resolve_shape_inference,
)


class TestShapeInferenceStats:
    """Tests for ShapeInferenceStats dataclass."""

    def test_default_values(self) -> None:
        """Default values are all zero."""
        stats = ShapeInferenceStats()
        assert stats.shapes_processed == 0
        assert stats.shapes_matched == 0
        assert stats.shapes_ambiguous == 0
        assert stats.shapes_unmatched == 0
        assert stats.accesses_upgraded == 0

    def test_custom_values(self) -> None:
        """Can set custom values."""
        stats = ShapeInferenceStats(
            shapes_processed=100,
            shapes_matched=50,
            shapes_ambiguous=10,
            shapes_unmatched=40,
            accesses_upgraded=25,
        )
        assert stats.shapes_processed == 100
        assert stats.shapes_matched == 50
        assert stats.shapes_ambiguous == 10
        assert stats.shapes_unmatched == 40
        assert stats.accesses_upgraded == 25

    def test_values_are_mutable(self) -> None:
        """Stats values can be modified."""
        stats = ShapeInferenceStats()
        stats.shapes_processed = 10
        stats.shapes_matched = 5
        assert stats.shapes_processed == 10
        assert stats.shapes_matched == 5


class TestTypeMatch:
    """Tests for TypeMatch dataclass."""

    def test_creation(self) -> None:
        """Can create TypeMatch."""
        match = TypeMatch(
            type_name="MyClass",
            confidence=0.8,
            matched_members=["foo", "bar"],
            unmatched_members=["baz"],
        )
        assert match.type_name == "MyClass"
        assert match.confidence == 0.8
        assert match.matched_members == ["foo", "bar"]
        assert match.unmatched_members == ["baz"]

    def test_high_confidence(self) -> None:
        """TypeMatch with high confidence."""
        match = TypeMatch(
            type_name="MyClass",
            confidence=0.95,
            matched_members=["a", "b", "c"],
            unmatched_members=[],
        )
        assert match.confidence >= 0.7  # Above threshold

    def test_low_confidence(self) -> None:
        """TypeMatch with low confidence."""
        match = TypeMatch(
            type_name="MyClass",
            confidence=0.3,
            matched_members=["a"],
            unmatched_members=["b", "c", "d"],
        )
        assert match.confidence < 0.7  # Below threshold


class TestShapeInferenceResolver:
    """Tests for ShapeInferenceResolver class."""

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
        resolver = ShapeInferenceResolver(mock_db)
        assert resolver._db == mock_db
        assert resolver._type_shapes == {}
        assert resolver._type_member_uids == {}

    def test_confidence_threshold(self, mock_db: MagicMock) -> None:
        """Confidence threshold is set."""
        resolver = ShapeInferenceResolver(mock_db)
        assert resolver.CONFIDENCE_THRESHOLD == 0.7

    def test_resolve_all_empty(self, mock_db: MagicMock) -> None:
        """Returns empty stats when no shapes."""
        session = MagicMock()
        mock_db.session.return_value.__enter__.return_value = session
        session.exec.return_value.all.return_value = []

        resolver = ShapeInferenceResolver(mock_db)
        stats = resolver.resolve_all()

        assert stats.shapes_processed == 0
        assert stats.shapes_matched == 0
        assert stats.shapes_ambiguous == 0
        assert stats.shapes_unmatched == 0

    def test_resolve_for_files_empty(self, mock_db: MagicMock) -> None:
        """Returns empty stats when no shapes for files."""
        session = MagicMock()
        mock_db.session.return_value.__enter__.return_value = session
        session.exec.return_value.all.return_value = []

        resolver = ShapeInferenceResolver(mock_db)
        stats = resolver.resolve_for_files([1, 2, 3])

        assert stats.shapes_processed == 0

    def test_build_type_shape_cache(self, mock_db: MagicMock) -> None:
        """Builds type shape cache from members."""
        session = MagicMock()
        mock_db.session.return_value.__enter__.return_value = session

        # Create mock members
        member1 = MagicMock()
        member1.parent_type_name = "MyClass"
        member1.member_name = "foo"
        member1.member_def_uid = "uid_foo"

        member2 = MagicMock()
        member2.parent_type_name = "MyClass"
        member2.member_name = "bar"
        member2.member_def_uid = "uid_bar"

        member3 = MagicMock()
        member3.parent_type_name = "OtherClass"
        member3.member_name = "baz"
        member3.member_def_uid = "uid_baz"

        session.exec.return_value.all.return_value = [member1, member2, member3]

        resolver = ShapeInferenceResolver(mock_db)
        resolver._build_type_shape_cache(session)

        assert "MyClass" in resolver._type_shapes
        assert "foo" in resolver._type_shapes["MyClass"]
        assert "bar" in resolver._type_shapes["MyClass"]
        assert "OtherClass" in resolver._type_shapes
        assert "baz" in resolver._type_shapes["OtherClass"]

        assert resolver._type_member_uids["MyClass"]["foo"] == "uid_foo"
        assert resolver._type_member_uids["MyClass"]["bar"] == "uid_bar"
        assert resolver._type_member_uids["OtherClass"]["baz"] == "uid_baz"

    def test_match_shape_no_observed_members(self, mock_db: MagicMock) -> None:
        """Returns unmatched when no observed members."""
        session = MagicMock()
        shape = MagicMock()
        shape.get_observed_members.return_value = {"fields": [], "methods": []}

        resolver = ShapeInferenceResolver(mock_db)
        result = resolver._match_shape(session, shape)

        assert result == "unmatched"

    def test_match_shape_no_matching_types(self, mock_db: MagicMock) -> None:
        """Returns unmatched when no types match observed members."""
        session = MagicMock()
        shape = MagicMock()
        shape.get_observed_members.return_value = {"fields": ["unknown_field"], "methods": []}

        resolver = ShapeInferenceResolver(mock_db)
        # Type shapes don't have "unknown_field"
        resolver._type_shapes = {"MyClass": {"foo", "bar"}}

        result = resolver._match_shape(session, shape)
        assert result == "unmatched"

    def test_match_shape_high_confidence_single_match(self, mock_db: MagicMock) -> None:
        """Returns matched for single high-confidence match."""
        session = MagicMock()
        session.exec.return_value.all.return_value = []  # No accesses to upgrade

        shape = MagicMock()
        shape.file_id = 1
        shape.receiver_name = "obj"
        shape.scope_id = None
        shape.get_observed_members.return_value = {"fields": ["foo", "bar"], "methods": []}

        resolver = ShapeInferenceResolver(mock_db)
        resolver._type_shapes = {"MyClass": {"foo", "bar", "extra"}}
        resolver._type_member_uids = {"MyClass": {"foo": "uid1", "bar": "uid2"}}

        result = resolver._match_shape(session, shape)
        assert result == "matched"
        assert shape.best_match_type == "MyClass"
        assert shape.match_confidence >= 0.7

    def test_match_shape_ambiguous(self, mock_db: MagicMock) -> None:
        """Returns ambiguous when multiple high-confidence matches."""
        session = MagicMock()

        shape = MagicMock()
        shape.get_observed_members.return_value = {"fields": ["foo"], "methods": []}

        resolver = ShapeInferenceResolver(mock_db)
        # Both types have "foo", both match 100%
        resolver._type_shapes = {
            "ClassA": {"foo"},
            "ClassB": {"foo"},
        }

        result = resolver._match_shape(session, shape)
        assert result == "ambiguous"

    def test_match_shape_low_confidence(self, mock_db: MagicMock) -> None:
        """Returns unmatched for low-confidence match."""
        session = MagicMock()

        shape = MagicMock()
        # Observes 4 members, only 1 matches
        shape.get_observed_members.return_value = {"fields": ["foo", "x", "y", "z"], "methods": []}

        resolver = ShapeInferenceResolver(mock_db)
        resolver._type_shapes = {"MyClass": {"foo", "other1", "other2"}}

        result = resolver._match_shape(session, shape)
        assert result == "unmatched"
        # But should record the low-confidence match
        assert shape.best_match_type == "MyClass"
        assert shape.match_confidence < 0.7

    def test_upgrade_accesses(self, mock_db: MagicMock) -> None:
        """Upgrades accesses for resolved type."""
        session = MagicMock()

        access = MagicMock()
        access.final_member = "foo"
        access.start_line = 10
        session.exec.return_value.all.return_value = [access]
        session.exec.return_value.first.return_value = None  # No RefFact

        resolver = ShapeInferenceResolver(mock_db)
        resolver._type_member_uids = {"MyClass": {"foo": "uid_foo"}}

        count = resolver._upgrade_accesses(
            session,
            file_id=1,
            receiver_name="obj",
            scope_id=None,
            inferred_type="MyClass",
            confidence=0.85,
        )

        assert count == 1
        assert access.receiver_declared_type == "MyClass"
        assert access.final_target_def_uid == "uid_foo"
        assert access.resolution_method == "shape_matched"
        assert access.resolution_confidence == 0.85

    def test_upgrade_accesses_with_scope(self, mock_db: MagicMock) -> None:
        """Upgrades accesses with specific scope."""
        session = MagicMock()
        session.exec.return_value.all.return_value = []

        resolver = ShapeInferenceResolver(mock_db)
        resolver._type_member_uids = {}

        count = resolver._upgrade_accesses(
            session,
            file_id=1,
            receiver_name="obj",
            scope_id=5,
            inferred_type="MyClass",
            confidence=0.8,
        )

        assert count == 0

    def test_upgrade_ref(self, mock_db: MagicMock) -> None:
        """Upgrades RefFact based on shape inference."""
        session = MagicMock()

        ref = MagicMock()
        ref.target_def_uid = None  # Not yet resolved
        session.exec.return_value.first.return_value = ref

        resolver = ShapeInferenceResolver(mock_db)
        resolver._upgrade_ref(
            session, file_id=1, line=10, token="foo", target_def_uid="uid_foo", _confidence=0.8
        )

        assert ref.target_def_uid == "uid_foo"
        assert ref.ref_tier == "anchored"

    def test_upgrade_ref_already_resolved(self, mock_db: MagicMock) -> None:
        """Doesn't upgrade already resolved RefFact."""
        session = MagicMock()

        ref = MagicMock()
        ref.target_def_uid = "existing_uid"  # Already resolved
        session.exec.return_value.first.return_value = ref

        resolver = ShapeInferenceResolver(mock_db)
        resolver._upgrade_ref(
            session, file_id=1, line=10, token="foo", target_def_uid="new_uid", _confidence=0.8
        )

        # Should not overwrite
        assert ref.target_def_uid == "existing_uid"

    def test_upgrade_ref_not_found(self, mock_db: MagicMock) -> None:
        """Handles missing RefFact gracefully."""
        session = MagicMock()
        session.exec.return_value.first.return_value = None

        resolver = ShapeInferenceResolver(mock_db)
        # Should not raise
        resolver._upgrade_ref(
            session, file_id=1, line=10, token="foo", target_def_uid="uid_foo", _confidence=0.8
        )


class TestResolveShapeInference:
    """Tests for resolve_shape_inference() convenience function."""

    def test_calls_resolve_all_without_file_ids(self) -> None:
        """Calls resolve_all when file_ids is None."""
        with patch.object(ShapeInferenceResolver, "resolve_all") as mock_resolve:
            mock_resolve.return_value = ShapeInferenceStats(shapes_processed=5)
            mock_db = MagicMock()

            stats = resolve_shape_inference(mock_db)

            mock_resolve.assert_called_once()
            assert stats.shapes_processed == 5

    def test_calls_resolve_for_files_with_file_ids(self) -> None:
        """Calls resolve_for_files when file_ids provided."""
        with patch.object(ShapeInferenceResolver, "resolve_for_files") as mock_resolve:
            mock_resolve.return_value = ShapeInferenceStats(shapes_processed=3)
            mock_db = MagicMock()

            stats = resolve_shape_inference(mock_db, file_ids=[1, 2])

            mock_resolve.assert_called_once_with([1, 2])
            assert stats.shapes_processed == 3

    def test_returns_stats(self) -> None:
        """Returns ShapeInferenceStats object."""
        with patch.object(ShapeInferenceResolver, "resolve_all") as mock_resolve:
            expected = ShapeInferenceStats(
                shapes_processed=10,
                shapes_matched=5,
                shapes_ambiguous=2,
                shapes_unmatched=3,
            )
            mock_resolve.return_value = expected
            mock_db = MagicMock()

            stats = resolve_shape_inference(mock_db)

            assert stats == expected
