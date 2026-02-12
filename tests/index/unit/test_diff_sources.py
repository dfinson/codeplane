"""Unit tests for diff sources (sources.py).

Tests cover:
- snapshots_from_index (reads from DefFact + File)
- snapshots_from_epoch (reads from DefSnapshotRecord)
- _compute_lexical_path
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codeplane.index._internal.db import Database
from codeplane.index._internal.diff.sources import (
    _compute_lexical_path,
    snapshots_from_epoch,
    snapshots_from_index,
)
from codeplane.index._internal.parsing.treesitter import SyntacticSymbol
from codeplane.index.models import (
    Context,
    DefFact,
    DefSnapshotRecord,
    File,
)


@pytest.fixture
def db(temp_dir: Path) -> Database:
    """Create a test database with schema."""
    db_path = temp_dir / "test_sources.db"
    db = Database(db_path)
    db.create_all()
    return db


@pytest.fixture
def seeded_db(db: Database) -> Database:
    """Create a database with test data."""
    with db.session() as session:
        ctx = Context(
            name="test",
            language_family="python",
            root_path="/test",
        )
        session.add(ctx)
        session.commit()

        f = File(path="src/main.py", language_family="python")
        session.add(f)
        session.commit()

        d = DefFact(
            def_uid="uid1",
            file_id=f.id,
            unit_id=ctx.id,
            kind="function",
            name="foo",
            lexical_path="foo",
            start_line=1,
            start_col=0,
            end_line=10,
            end_col=0,
            signature_hash="abcd1234",
            display_name="def foo()",
        )
        session.add(d)
        session.commit()

    return db


# ============================================================================
# Tests: snapshots_from_index
# ============================================================================


class TestSnapshotsFromIndex:
    """Tests for snapshots_from_index."""

    def test_returns_snapshots(self, seeded_db: Database) -> None:
        with seeded_db.session() as session:
            snaps = snapshots_from_index(session, "src/main.py")
        assert len(snaps) == 1
        assert snaps[0].kind == "function"
        assert snaps[0].name == "foo"
        assert snaps[0].signature_hash == "abcd1234"

    def test_empty_for_unknown_file(self, seeded_db: Database) -> None:
        with seeded_db.session() as session:
            snaps = snapshots_from_index(session, "src/nonexistent.py")
        assert len(snaps) == 0


# ============================================================================
# Tests: snapshots_from_epoch
# ============================================================================


class TestSnapshotsFromEpoch:
    """Tests for snapshots_from_epoch."""

    def test_returns_epoch_snapshots(self, db: Database) -> None:
        with db.session() as session:
            record = DefSnapshotRecord(
                epoch_id=1,
                file_path="src/main.py",
                kind="function",
                name="foo",
                lexical_path="foo",
                signature_hash="abcd1234",
                display_name="def foo()",
                start_line=1,
                end_line=10,
            )
            session.add(record)
            session.commit()

        with db.session() as session:
            snaps = snapshots_from_epoch(session, 1, "src/main.py")
        assert len(snaps) == 1
        assert snaps[0].name == "foo"

    def test_empty_for_wrong_epoch(self, db: Database) -> None:
        """Epoch 0 should not see a record written at epoch 1."""
        with db.session() as session:
            record = DefSnapshotRecord(
                epoch_id=1,
                file_path="src/main.py",
                kind="function",
                name="foo",
                lexical_path="foo",
                start_line=1,
                end_line=10,
            )
            session.add(record)
            session.commit()

        with db.session() as session:
            # epoch 0 is before the record at epoch 1
            snaps = snapshots_from_epoch(session, 0, "src/main.py")
        assert len(snaps) == 0

    def test_reconstructs_state_at_later_epoch(self, db: Database) -> None:
        """Querying epoch 99 should find a record written at epoch 1 (<=)."""
        with db.session() as session:
            record = DefSnapshotRecord(
                epoch_id=1,
                file_path="src/main.py",
                kind="function",
                name="foo",
                lexical_path="foo",
                start_line=1,
                end_line=10,
            )
            session.add(record)
            session.commit()

        with db.session() as session:
            snaps = snapshots_from_epoch(session, 99, "src/main.py")
        assert len(snaps) == 1
        assert snaps[0].name == "foo"


# ============================================================================
# Tests: _compute_lexical_path
# ============================================================================


class TestComputeLexicalPath:
    """Tests for _compute_lexical_path."""

    def test_function_at_module_level(self) -> None:
        sym = SyntacticSymbol("foo", "function", 1, 0, 10, 0)
        assert _compute_lexical_path(sym, []) == "foo"

    def test_method_with_parent(self) -> None:
        sym = SyntacticSymbol("bar", "method", 5, 4, 10, 0, parent_name="MyClass")
        assert _compute_lexical_path(sym, []) == "MyClass.bar"

    def test_method_inside_class(self) -> None:
        cls = SyntacticSymbol("MyClass", "class", 1, 0, 20, 0)
        method = SyntacticSymbol("bar", "method", 5, 4, 10, 0)
        assert _compute_lexical_path(method, [cls]) == "MyClass.bar"

    def test_variable_at_module_level(self) -> None:
        sym = SyntacticSymbol("x", "variable", 1, 0, 1, 5)
        assert _compute_lexical_path(sym, []) == "x"
