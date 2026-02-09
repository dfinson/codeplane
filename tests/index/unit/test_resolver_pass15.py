"""Tests for Pass 1.5 resolver improvements.

Covers:
- Design 2C: Schema validation — SQL column references match actual tables
- Design 3A: Paginated get_references / list_refs_by_def_uid
- Design 4B: Star-import single-UPDATE resolution
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import select

from codeplane.index._internal.db import Database, create_additional_indexes
from codeplane.index._internal.indexing.graph import FactQueries
from codeplane.index._internal.indexing.resolver import (
    _TYPE_KIND_FILTER,
    _build_file_filter,
    resolve_star_import_refs,
)
from codeplane.index._internal.indexing.structural import StructuralIndexer
from codeplane.index.models import (
    Certainty,
    Context,
    DefFact,
    File,
    RefFact,
    RefTier,
    Role,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db(temp_dir: Path) -> Database:
    """Create a test database with schema."""
    db_path = temp_dir / "test_resolver_pass15.db"
    db = Database(db_path)
    db.create_all()
    create_additional_indexes(db.engine)
    return db


@pytest.fixture
def seeded_db(db: Database) -> Database:
    """Database seeded with a context and some basic facts for pagination tests."""
    with db.session() as session:
        ctx = Context(name="test", language_family="python", root_path="/test")
        session.add(ctx)
        session.commit()

        f = File(path="test.py", language_family="python")
        session.add(f)
        session.commit()
        file_id = f.id
        assert file_id is not None

        # Create a definition
        session.add(
            DefFact(
                def_uid="def_target_001",
                file_id=file_id,
                unit_id=ctx.id or 1,
                kind="function",
                name="my_func",
                lexical_path="my_func",
                start_line=1,
                start_col=0,
                end_line=3,
                end_col=0,
            )
        )

        # Create 300 references to it (more than default limit of 250)
        for i in range(300):
            session.add(
                RefFact(
                    file_id=file_id,
                    unit_id=ctx.id or 1,
                    token_text="my_func",
                    start_line=10 + i,
                    start_col=0,
                    end_line=10 + i,
                    end_col=7,
                    role=Role.REFERENCE.value,
                    ref_tier=RefTier.PROVEN.value,
                    certainty=Certainty.CERTAIN.value,
                    target_def_uid="def_target_001",
                )
            )
        session.commit()
    return db


# ============================================================================
# Design 2C: Schema Validation Tests
# ============================================================================


class TestSchemaValidation:
    """Verify SQL column references in Pass 1.5 resolvers match actual schema."""

    _REF_FACTS_COLUMNS = {
        "ref_id",
        "file_id",
        "unit_id",
        "scope_id",
        "token_text",
        "start_line",
        "start_col",
        "end_line",
        "end_col",
        "role",
        "ref_tier",
        "certainty",
        "target_def_uid",
    }

    _DEF_FACTS_COLUMNS = {
        "def_uid",
        "file_id",
        "unit_id",
        "kind",
        "name",
        "qualified_name",
        "lexical_path",
        "namespace",
        "start_line",
        "start_col",
        "end_line",
        "end_col",
        "signature_hash",
        "display_name",
    }

    _IMPORT_FACTS_COLUMNS = {
        "import_uid",
        "file_id",
        "unit_id",
        "scope_id",
        "imported_name",
        "alias",
        "source_literal",
        "import_kind",
        "certainty",
    }

    _FILES_COLUMNS = {
        "id",
        "path",
        "language_family",
        "content_hash",
        "line_count",
        "indexed_at",
        "last_indexed_epoch",
    }

    def test_actual_schema_matches_expected_ref_facts(self, db: Database) -> None:
        """ref_facts table has all columns referenced in resolver SQL."""
        actual = self._introspect_columns(db, "ref_facts")
        assert self._REF_FACTS_COLUMNS.issubset(actual), (
            f"Missing columns in ref_facts: {self._REF_FACTS_COLUMNS - actual}"
        )

    def test_actual_schema_matches_expected_def_facts(self, db: Database) -> None:
        """def_facts table has all columns referenced in resolver SQL."""
        actual = self._introspect_columns(db, "def_facts")
        assert self._DEF_FACTS_COLUMNS.issubset(actual), (
            f"Missing columns in def_facts: {self._DEF_FACTS_COLUMNS - actual}"
        )

    def test_actual_schema_matches_expected_import_facts(self, db: Database) -> None:
        """import_facts table has all columns referenced in resolver SQL."""
        actual = self._introspect_columns(db, "import_facts")
        assert self._IMPORT_FACTS_COLUMNS.issubset(actual), (
            f"Missing columns in import_facts: {self._IMPORT_FACTS_COLUMNS - actual}"
        )

    def test_actual_schema_matches_expected_files(self, db: Database) -> None:
        """files table has all columns referenced in resolver SQL."""
        actual = self._introspect_columns(db, "files")
        assert self._FILES_COLUMNS.issubset(actual), (
            f"Missing columns in files: {self._FILES_COLUMNS - actual}"
        )

    def test_type_kind_filter_contains_record(self) -> None:
        """_TYPE_KIND_FILTER includes 'record' for C# record types."""
        assert "'record'" in _TYPE_KIND_FILTER

    def test_type_kind_filter_is_valid_sql_in_list(self) -> None:
        """_TYPE_KIND_FILTER is a valid parenthesised SQL IN list."""
        assert _TYPE_KIND_FILTER.startswith("(")
        assert _TYPE_KIND_FILTER.endswith(")")
        # Parse the kinds between parens
        inner = _TYPE_KIND_FILTER[1:-1]
        kinds = [k.strip().strip("'") for k in inner.split(",")]
        assert len(kinds) >= 4  # at least class, struct, interface, enum
        for kind in kinds:
            assert kind.isalpha() or "_" in kind, f"Unexpected kind: {kind}"

    def test_build_file_filter_empty(self) -> None:
        """No file_ids produces empty filter."""
        sql, binds = _build_file_filter(None)
        assert sql == ""
        assert binds == {}

    def test_build_file_filter_parameterized(self) -> None:
        """file_ids produce parameterized IN clause, not f-string interpolation."""
        sql, binds = _build_file_filter([1, 2, 3])
        assert ":fid_0" in sql
        assert ":fid_1" in sql
        assert ":fid_2" in sql
        assert binds == {"fid_0": 1, "fid_1": 2, "fid_2": 3}
        # Must NOT contain raw integer literals
        for fid in [1, 2, 3]:
            assert f" {fid}" not in sql.replace(":fid_", ": fid_")

    @staticmethod
    def _introspect_columns(db: Database, table_name: str) -> set[str]:
        """Return the set of column names for a table via PRAGMA."""
        from sqlalchemy import text

        with db.session() as session:
            rows = session.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
            return {row[1] for row in rows}


# ============================================================================
# Design 3A: Pagination Tests
# ============================================================================


class TestPaginatedReferences:
    """list_refs_by_def_uid and list_all_refs_by_def_uid pagination."""

    def test_default_limit_is_250(self, seeded_db: Database) -> None:
        """Default limit returns at most 250 refs."""
        with seeded_db.session() as session:
            fq = FactQueries(session)
            refs = fq.list_refs_by_def_uid("def_target_001")
            assert len(refs) == 250

    def test_offset_paginates_correctly(self, seeded_db: Database) -> None:
        """Offset skips the right number of rows."""
        with seeded_db.session() as session:
            fq = FactQueries(session)
            page1 = fq.list_refs_by_def_uid("def_target_001", limit=100, offset=0)
            page2 = fq.list_refs_by_def_uid("def_target_001", limit=100, offset=100)
            page3 = fq.list_refs_by_def_uid("def_target_001", limit=100, offset=200)
            page4 = fq.list_refs_by_def_uid("def_target_001", limit=100, offset=300)

            assert len(page1) == 100
            assert len(page2) == 100
            assert len(page3) == 100
            assert len(page4) == 0

            # No overlap between pages
            ids1 = {r.ref_id for r in page1}
            ids2 = {r.ref_id for r in page2}
            ids3 = {r.ref_id for r in page3}
            assert ids1.isdisjoint(ids2)
            assert ids2.isdisjoint(ids3)

    def test_list_all_returns_complete_set(self, seeded_db: Database) -> None:
        """list_all_refs_by_def_uid returns every reference."""
        with seeded_db.session() as session:
            fq = FactQueries(session)
            all_refs = fq.list_all_refs_by_def_uid("def_target_001")
            assert len(all_refs) == 300

    def test_list_all_with_small_page_size(self, seeded_db: Database) -> None:
        """Exhaustive pagination works with a small page size."""
        with seeded_db.session() as session:
            fq = FactQueries(session)
            all_refs = fq.list_all_refs_by_def_uid("def_target_001", page_size=50)
            assert len(all_refs) == 300

    def test_list_all_empty(self, seeded_db: Database) -> None:
        """list_all for a nonexistent def_uid returns empty list."""
        with seeded_db.session() as session:
            fq = FactQueries(session)
            refs = fq.list_all_refs_by_def_uid("nonexistent")
            assert refs == []

    def test_results_ordered_by_ref_id(self, seeded_db: Database) -> None:
        """Pagination returns deterministic order by ref_id."""
        with seeded_db.session() as session:
            fq = FactQueries(session)
            refs = fq.list_refs_by_def_uid("def_target_001", limit=300)
            ids = [r.ref_id for r in refs]
            assert ids == sorted(ids)

    def test_tier_filter_with_pagination(self, seeded_db: Database) -> None:
        """Tier filter works in combination with pagination."""
        with seeded_db.session() as session:
            fq = FactQueries(session)
            proven = fq.list_refs_by_def_uid("def_target_001", tier=RefTier.PROVEN, limit=50)
            assert len(proven) == 50
            assert all(r.ref_tier == RefTier.PROVEN.value for r in proven)

            # STRONG tier should return nothing (all refs are PROVEN)
            strong = fq.list_refs_by_def_uid("def_target_001", tier=RefTier.STRONG)
            assert len(strong) == 0


# ============================================================================
# Design 4B: Star-Import Single-UPDATE Resolution Tests
# ============================================================================


class TestStarImportSingleUpdate:
    """Verify resolve_star_import_refs uses batch resolution correctly."""

    def test_star_import_upgrades_refs_to_strong(self, db: Database, temp_dir: Path) -> None:
        """Star-imported names from project modules upgrade to STRONG."""
        (temp_dir / "utils.py").write_text(
            """def helper():
    pass

class Utility:
    pass
"""
        )
        (temp_dir / "main.py").write_text(
            """from utils import *

x = helper()
y = Utility()
"""
        )

        db.create_all()
        with db.session() as session:
            ctx = Context(name="test", language_family="python", root_path=str(temp_dir))
            session.add(ctx)
            session.commit()
            context_id = ctx.id

        indexer = StructuralIndexer(db, temp_dir)
        indexer.index_files(["utils.py", "main.py"], context_id=context_id or 1)

        # Before resolution: refs should be UNKNOWN
        with db.session() as session:
            main_file = session.exec(select(File).where(File.path == "main.py")).first()
            assert main_file is not None
            helper_refs = session.exec(
                select(RefFact).where(
                    RefFact.file_id == main_file.id,
                    RefFact.token_text == "helper",
                    RefFact.role == Role.REFERENCE.value,
                )
            ).all()
            assert len(helper_refs) >= 1
            assert all(r.ref_tier == RefTier.UNKNOWN.value for r in helper_refs)

        stats = resolve_star_import_refs(db)
        assert stats.refs_upgraded >= 2  # helper + Utility

        # After: should be STRONG with target_def_uid linked
        with db.session() as session:
            helper_refs_after = session.exec(
                select(RefFact).where(
                    RefFact.file_id == main_file.id,
                    RefFact.token_text == "helper",
                    RefFact.role == Role.REFERENCE.value,
                )
            ).all()
            assert all(r.ref_tier == RefTier.STRONG.value for r in helper_refs_after)
            helper_def = session.exec(select(DefFact).where(DefFact.name == "helper")).first()
            assert helper_def is not None
            assert all(r.target_def_uid == helper_def.def_uid for r in helper_refs_after)

    def test_external_star_import_stays_unknown(self, db: Database, temp_dir: Path) -> None:
        """Star imports from external packages stay UNKNOWN."""
        (temp_dir / "consumer.py").write_text(
            """from os.path import *

result = join("/a", "b")
"""
        )

        db.create_all()
        with db.session() as session:
            ctx = Context(name="test", language_family="python", root_path=str(temp_dir))
            session.add(ctx)
            session.commit()
            context_id = ctx.id

        indexer = StructuralIndexer(db, temp_dir)
        indexer.index_files(["consumer.py"], context_id=context_id or 1)

        stats = resolve_star_import_refs(db)
        assert stats.refs_upgraded == 0

    def test_private_names_excluded(self, db: Database, temp_dir: Path) -> None:
        """Private names (leading underscore) are not resolved."""
        (temp_dir / "lib.py").write_text(
            """def _private():
    pass

def public():
    pass
"""
        )
        (temp_dir / "app.py").write_text(
            """from lib import *

x = _private()
y = public()
"""
        )

        db.create_all()
        with db.session() as session:
            ctx = Context(name="test", language_family="python", root_path=str(temp_dir))
            session.add(ctx)
            session.commit()
            context_id = ctx.id

        indexer = StructuralIndexer(db, temp_dir)
        indexer.index_files(["lib.py", "app.py"], context_id=context_id or 1)

        resolve_star_import_refs(db)

        with db.session() as session:
            app_file = session.exec(select(File).where(File.path == "app.py")).first()
            assert app_file is not None

            # _private should stay UNKNOWN
            private_refs = session.exec(
                select(RefFact).where(
                    RefFact.file_id == app_file.id,
                    RefFact.token_text == "_private",
                    RefFact.role == Role.REFERENCE.value,
                )
            ).all()
            for ref in private_refs:
                assert ref.ref_tier == RefTier.UNKNOWN.value

            # public should be STRONG
            public_refs = session.exec(
                select(RefFact).where(
                    RefFact.file_id == app_file.id,
                    RefFact.token_text == "public",
                    RefFact.role == Role.REFERENCE.value,
                )
            ).all()
            assert len(public_refs) >= 1
            assert all(r.ref_tier == RefTier.STRONG.value for r in public_refs)

    def test_file_ids_scoping(self, db: Database, temp_dir: Path) -> None:
        """file_ids parameter restricts which files are resolved."""
        (temp_dir / "mod_a.py").write_text("def func_a():\n    pass\n")
        (temp_dir / "mod_b.py").write_text("def func_b():\n    pass\n")
        (temp_dir / "consumer_a.py").write_text("from mod_a import *\nx = func_a()\n")
        (temp_dir / "consumer_b.py").write_text("from mod_b import *\ny = func_b()\n")

        db.create_all()
        with db.session() as session:
            ctx = Context(name="test", language_family="python", root_path=str(temp_dir))
            session.add(ctx)
            session.commit()
            context_id = ctx.id

        indexer = StructuralIndexer(db, temp_dir)
        indexer.index_files(
            ["mod_a.py", "mod_b.py", "consumer_a.py", "consumer_b.py"],
            context_id=context_id or 1,
        )

        # Only resolve for consumer_a's file
        with db.session() as session:
            consumer_a = session.exec(select(File).where(File.path == "consumer_a.py")).first()
            assert consumer_a is not None
            consumer_b = session.exec(select(File).where(File.path == "consumer_b.py")).first()
            assert consumer_b is not None

        stats = resolve_star_import_refs(
            db,
            file_ids=[consumer_a.id],  # type: ignore[list-item]
        )
        assert stats.refs_upgraded >= 1

        # consumer_a refs should be upgraded
        with db.session() as session:
            a_refs = session.exec(
                select(RefFact).where(
                    RefFact.file_id == consumer_a.id,
                    RefFact.token_text == "func_a",
                    RefFact.role == Role.REFERENCE.value,
                )
            ).all()
            assert all(r.ref_tier == RefTier.STRONG.value for r in a_refs)

            # consumer_b refs should remain UNKNOWN
            b_refs = session.exec(
                select(RefFact).where(
                    RefFact.file_id == consumer_b.id,
                    RefFact.token_text == "func_b",
                    RefFact.role == Role.REFERENCE.value,
                )
            ).all()
            assert all(r.ref_tier == RefTier.UNKNOWN.value for r in b_refs)

    def test_stats_counts_match(self, db: Database, temp_dir: Path) -> None:
        """refs_scanned >= refs_upgraded, and both are correct."""
        (temp_dir / "defs.py").write_text(
            """def alpha():
    pass

def beta():
    pass
"""
        )
        (temp_dir / "use.py").write_text(
            """from defs import *

a = alpha()
b = beta()
"""
        )

        db.create_all()
        with db.session() as session:
            ctx = Context(name="test", language_family="python", root_path=str(temp_dir))
            session.add(ctx)
            session.commit()
            context_id = ctx.id or 1

        indexer = StructuralIndexer(db, temp_dir)
        indexer.index_files(["defs.py", "use.py"], context_id=context_id)

        stats = resolve_star_import_refs(db)
        assert stats.refs_upgraded >= 2
        assert stats.refs_scanned >= stats.refs_upgraded

    def test_empty_module_no_crash(self, db: Database, temp_dir: Path) -> None:
        """Star-importing from an empty module doesn't crash."""
        (temp_dir / "empty.py").write_text("\n")
        (temp_dir / "importer.py").write_text("from empty import *\nresult = something()\n")

        db.create_all()
        with db.session() as session:
            ctx = Context(name="test", language_family="python", root_path=str(temp_dir))
            session.add(ctx)
            session.commit()
            context_id = ctx.id or 1

        indexer = StructuralIndexer(db, temp_dir)
        indexer.index_files(["empty.py", "importer.py"], context_id=context_id)

        # Should not raise
        stats = resolve_star_import_refs(db)
        assert stats.refs_upgraded == 0


# ============================================================================
# Design 3A: Integration-level pagination tests
# ============================================================================


class TestPaginatedReferencesIntegration:
    """Integration tests verifying pagination with real indexed data."""

    def test_exhaustive_fetch_finds_all_refs(self, db: Database, temp_dir: Path) -> None:
        """list_all_refs_by_def_uid finds every reference from real indexing."""
        # Create a module with a symbol referenced many times
        lines = ["from utils import helper\n"]
        for i in range(60):
            lines.append(f"x{i} = helper()\n")
        (temp_dir / "heavy.py").write_text("".join(lines))
        (temp_dir / "utils.py").write_text("def helper():\n    pass\n")

        db.create_all()
        with db.session() as session:
            ctx = Context(name="test", language_family="python", root_path=str(temp_dir))
            session.add(ctx)
            session.commit()
            context_id = ctx.id or 1

        indexer = StructuralIndexer(db, temp_dir)
        indexer.index_files(["utils.py", "heavy.py"], context_id=context_id)

        # Resolve Pass 2 references
        from codeplane.index._internal.indexing.resolver import (
            resolve_references,
        )

        resolve_references(db)

        with db.session() as session:
            helper_def = session.exec(select(DefFact).where(DefFact.name == "helper")).first()
            assert helper_def is not None

            fq = FactQueries(session)

            # Paginated: should cap at page_size
            page = fq.list_refs_by_def_uid(helper_def.def_uid, limit=10)
            assert len(page) <= 10

            # Exhaustive: should find all
            all_refs = fq.list_all_refs_by_def_uid(helper_def.def_uid, page_size=10)
            assert len(all_refs) >= 60  # at least the 60 call-site refs

            # Verify no duplicates
            ids = [r.ref_id for r in all_refs]
            assert len(ids) == len(set(ids))
