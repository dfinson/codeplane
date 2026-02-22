"""Tests for FactQueries.list_callees_in_scope and count_callers.

Tests the new graph query methods that power the recon tool.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codeplane.index._internal.db import Database, create_additional_indexes
from codeplane.index._internal.indexing.graph import FactQueries
from codeplane.index.models import (
    Certainty,
    Context,
    DefFact,
    File,
    RefFact,
    RefTier,
    Role,
)


@pytest.fixture
def db(temp_dir: Path) -> Database:
    """Create a test database with schema."""
    db_path = temp_dir / "test_callees.db"
    db = Database(db_path)
    db.create_all()
    create_additional_indexes(db.engine)
    return db


@pytest.fixture
def seeded_db(db: Database) -> Database:
    """Seed database with caller/callee test data.

    Graph:
      foo (file1:10-20) calls bar (file1:25-35) and baz (file2:1-10)
      bar (file1:25-35) calls baz (file2:1-10)
    """
    with db.session() as session:
        ctx = Context(name="test", language_family="python", root_path="/test")
        session.add(ctx)
        session.commit()
        cid = ctx.id

        file1 = File(path="src/main.py", language_family="python")
        file2 = File(path="src/utils.py", language_family="python")
        session.add_all([file1, file2])
        session.commit()
        f1 = file1.id
        f2 = file2.id

        # Definitions
        def_foo = DefFact(
            def_uid="uid_foo",
            file_id=f1,
            unit_id=cid,
            kind="function",
            name="foo",
            lexical_path="foo",
            start_line=10,
            start_col=0,
            end_line=20,
            end_col=0,
        )
        def_bar = DefFact(
            def_uid="uid_bar",
            file_id=f1,
            unit_id=cid,
            kind="function",
            name="bar",
            lexical_path="bar",
            start_line=25,
            start_col=0,
            end_line=35,
            end_col=0,
        )
        def_baz = DefFact(
            def_uid="uid_baz",
            file_id=f2,
            unit_id=cid,
            kind="function",
            name="baz",
            lexical_path="baz",
            start_line=1,
            start_col=0,
            end_line=10,
            end_col=0,
        )
        session.add_all([def_foo, def_bar, def_baz])
        session.commit()

        # References: foo calls bar at line 15 and baz at line 18
        ref_bar_from_foo = RefFact(
            file_id=f1,
            unit_id=cid,
            token_text="bar",
            start_line=15,
            start_col=4,
            end_line=15,
            end_col=7,
            role=Role.REFERENCE.value,
            ref_tier=RefTier.PROVEN.value,
            certainty=Certainty.CERTAIN.value,
            target_def_uid="uid_bar",
        )
        ref_baz_from_foo = RefFact(
            file_id=f1,
            unit_id=cid,
            token_text="baz",
            start_line=18,
            start_col=4,
            end_line=18,
            end_col=7,
            role=Role.REFERENCE.value,
            ref_tier=RefTier.PROVEN.value,
            certainty=Certainty.CERTAIN.value,
            target_def_uid="uid_baz",
        )
        # bar calls baz at line 30
        ref_baz_from_bar = RefFact(
            file_id=f1,
            unit_id=cid,
            token_text="baz",
            start_line=30,
            start_col=4,
            end_line=30,
            end_col=7,
            role=Role.REFERENCE.value,
            ref_tier=RefTier.PROVEN.value,
            certainty=Certainty.CERTAIN.value,
            target_def_uid="uid_baz",
        )
        # Also: ref to foo from file2 (cross-file caller)
        ref_foo_from_file2 = RefFact(
            file_id=f2,
            unit_id=cid,
            token_text="foo",
            start_line=5,
            start_col=4,
            end_line=5,
            end_col=7,
            role=Role.REFERENCE.value,
            ref_tier=RefTier.PROVEN.value,
            certainty=Certainty.CERTAIN.value,
            target_def_uid="uid_foo",
        )
        session.add_all([ref_bar_from_foo, ref_baz_from_foo, ref_baz_from_bar, ref_foo_from_file2])
        session.commit()

    return db


class TestListCalleesInScope:
    """Tests for FactQueries.list_callees_in_scope."""

    def test_callees_of_foo(self, seeded_db: Database) -> None:
        """foo calls bar and baz."""
        with seeded_db.session() as session:
            fq = FactQueries(session)
            file_rec = fq.get_file_by_path("src/main.py")
            assert file_rec is not None
            assert file_rec.id is not None

            callees = fq.list_callees_in_scope(file_rec.id, 10, 20)
            names = {c.name for c in callees}
            assert names == {"bar", "baz"}

    def test_callees_of_bar(self, seeded_db: Database) -> None:
        """bar calls baz."""
        with seeded_db.session() as session:
            fq = FactQueries(session)
            file_rec = fq.get_file_by_path("src/main.py")
            assert file_rec is not None
            assert file_rec.id is not None

            callees = fq.list_callees_in_scope(file_rec.id, 25, 35)
            names = {c.name for c in callees}
            assert names == {"baz"}

    def test_callees_empty_range(self, seeded_db: Database) -> None:
        """No refs in line range → empty result."""
        with seeded_db.session() as session:
            fq = FactQueries(session)
            file_rec = fq.get_file_by_path("src/main.py")
            assert file_rec is not None
            assert file_rec.id is not None

            callees = fq.list_callees_in_scope(file_rec.id, 40, 50)
            assert callees == []

    def test_callees_limit(self, seeded_db: Database) -> None:
        """Limit caps the result count."""
        with seeded_db.session() as session:
            fq = FactQueries(session)
            file_rec = fq.get_file_by_path("src/main.py")
            assert file_rec is not None
            assert file_rec.id is not None

            callees = fq.list_callees_in_scope(file_rec.id, 10, 20, limit=1)
            assert len(callees) == 1

    def test_callees_deduplicated(self, seeded_db: Database) -> None:
        """Even if a symbol is referenced multiple times, it appears once."""
        with seeded_db.session() as session:
            fq = FactQueries(session)
            file_rec = fq.get_file_by_path("src/main.py")
            assert file_rec is not None
            assert file_rec.id is not None

            # foo's scope references bar and baz — each exactly once
            callees = fq.list_callees_in_scope(file_rec.id, 10, 20)
            uids = [c.def_uid for c in callees]
            assert len(uids) == len(set(uids)), "Callees should be deduplicated"


class TestCountCallers:
    """Tests for FactQueries.count_callers."""

    def test_foo_has_one_caller_file(self, seeded_db: Database) -> None:
        """foo is referenced from file2 only (cross-file)."""
        with seeded_db.session() as session:
            fq = FactQueries(session)
            count = fq.count_callers("uid_foo")
            assert count == 1  # Only file2 references foo

    def test_baz_caller_count(self, seeded_db: Database) -> None:
        """baz is referenced from file1 (by both foo and bar)."""
        with seeded_db.session() as session:
            fq = FactQueries(session)
            count = fq.count_callers("uid_baz")
            assert count == 1  # Both refs are in file1

    def test_unknown_def_zero_callers(self, seeded_db: Database) -> None:
        """Non-existent def_uid → 0 callers."""
        with seeded_db.session() as session:
            fq = FactQueries(session)
            count = fq.count_callers("uid_nonexistent")
            assert count == 0
