"""Unit tests for FactQueries (graph.py).

Tests cover:
- Definition lookups (get_def, list_defs_by_name, list_defs_in_file)
- Reference lookups (list_refs_by_def_uid, list_proven_refs, list_refs_in_file, list_refs_by_token)
- Scope lookups (get_scope, list_scopes_in_file)
- Binding lookups (get_local_bind, list_binds_in_scope)
- Import lookups (list_imports, get_import)
- Export lookups (get_export_surface, list_export_entries)
- Anchor group lookups (get_anchor_group, list_anchor_groups)
- File lookups (get_file, get_file_by_path, list_files)
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import select

from codeplane.index._internal.db import Database, create_additional_indexes
from codeplane.index._internal.indexing.graph import FactQueries, SymbolGraph
from codeplane.index.models import (
    AnchorGroup,
    Certainty,
    Context,
    DefFact,
    ExportEntry,
    ExportSurface,
    File,
    ImportFact,
    LocalBindFact,
    RefFact,
    RefTier,
    Role,
    ScopeFact,
    ScopeKind,
)


@pytest.fixture
def db(temp_dir: Path) -> Database:
    """Create a test database with schema."""
    db_path = temp_dir / "test_graph.db"
    db = Database(db_path)
    db.create_all()
    create_additional_indexes(db.engine)
    return db


@pytest.fixture
def seeded_db(db: Database) -> Database:
    """Create a database with test data."""
    with db.session() as session:
        # Create context
        ctx = Context(
            name="test",
            language_family="python",
            root_path="/test",
        )
        session.add(ctx)
        session.commit()
        context_id = ctx.id

        # Create files
        file1 = File(path="src/main.py", language_family="python")
        file2 = File(path="src/utils.py", language_family="python")
        session.add(file1)
        session.add(file2)
        session.commit()
        file1_id = file1.id
        file2_id = file2.id

        # Create scopes
        scope1 = ScopeFact(
            file_id=file1_id,
            unit_id=context_id,
            kind=ScopeKind.FILE.value,
            start_line=1,
            start_col=0,
            end_line=100,
            end_col=0,
        )
        scope2 = ScopeFact(
            file_id=file1_id,
            unit_id=context_id,
            parent_scope_id=None,
            kind=ScopeKind.FUNCTION.value,
            start_line=10,
            start_col=0,
            end_line=20,
            end_col=0,
        )
        session.add(scope1)
        session.add(scope2)
        session.commit()
        scope1_id = scope1.scope_id

        # Create definitions
        def1 = DefFact(
            def_uid="def_foo_123",
            file_id=file1_id,
            unit_id=context_id,
            kind="function",
            name="foo",
            lexical_path="foo",
            start_line=10,
            start_col=0,
            end_line=20,
            end_col=0,
        )
        def2 = DefFact(
            def_uid="def_bar_456",
            file_id=file1_id,
            unit_id=context_id,
            kind="function",
            name="bar",
            lexical_path="bar",
            start_line=25,
            start_col=0,
            end_line=35,
            end_col=0,
        )
        def3 = DefFact(
            def_uid="def_foo_789",
            file_id=file2_id,
            unit_id=context_id,
            kind="function",
            name="foo",  # Same name, different file
            lexical_path="foo",
            start_line=1,
            start_col=0,
            end_line=10,
            end_col=0,
        )
        session.add_all([def1, def2, def3])
        session.commit()

        # Create references
        ref1 = RefFact(
            file_id=file1_id,
            unit_id=context_id,
            token_text="foo",
            start_line=10,
            start_col=4,
            end_line=10,
            end_col=7,
            role=Role.DEFINITION.value,
            ref_tier=RefTier.PROVEN.value,
            certainty=Certainty.CERTAIN.value,
            target_def_uid="def_foo_123",
        )
        ref2 = RefFact(
            file_id=file1_id,
            unit_id=context_id,
            token_text="foo",
            start_line=30,
            start_col=4,
            end_line=30,
            end_col=7,
            role=Role.REFERENCE.value,
            ref_tier=RefTier.PROVEN.value,
            certainty=Certainty.CERTAIN.value,
            target_def_uid="def_foo_123",
        )
        ref3 = RefFact(
            file_id=file1_id,
            unit_id=context_id,
            token_text="bar",
            start_line=25,
            start_col=4,
            end_line=25,
            end_col=7,
            role=Role.DEFINITION.value,
            ref_tier=RefTier.PROVEN.value,
            certainty=Certainty.CERTAIN.value,
            target_def_uid="def_bar_456",
        )
        ref4 = RefFact(
            file_id=file2_id,
            unit_id=context_id,
            token_text="foo",
            start_line=15,
            start_col=0,
            end_line=15,
            end_col=3,
            role=Role.REFERENCE.value,
            ref_tier=RefTier.UNKNOWN.value,
            certainty=Certainty.UNCERTAIN.value,
            target_def_uid=None,
        )
        session.add_all([ref1, ref2, ref3, ref4])
        session.commit()

        # Create local bindings
        bind1 = LocalBindFact(
            file_id=file1_id,
            unit_id=context_id,
            scope_id=scope1_id,
            name="foo",
            target_kind="def",
            target_uid="def_foo_123",
            certainty=Certainty.CERTAIN.value,
            reason_code="def_in_scope",
        )
        session.add(bind1)
        session.commit()

        # Create imports
        import1 = ImportFact(
            import_uid="imp_os_123",
            file_id=file1_id,
            unit_id=context_id,
            imported_name="os",
            import_kind="python_import",
            certainty=Certainty.CERTAIN.value,
        )
        import2 = ImportFact(
            import_uid="imp_path_456",
            file_id=file1_id,
            unit_id=context_id,
            imported_name="Path",
            alias="P",
            source_literal="pathlib",
            import_kind="python_from",
            certainty=Certainty.CERTAIN.value,
        )
        session.add_all([import1, import2])
        session.commit()

        # Create export surface and entries
        surface = ExportSurface(unit_id=context_id)
        session.add(surface)
        session.commit()
        surface_id = surface.surface_id

        entry1 = ExportEntry(
            surface_id=surface_id,
            exported_name="foo",
            def_uid="def_foo_123",
            certainty=Certainty.CERTAIN.value,
        )
        entry2 = ExportEntry(
            surface_id=surface_id,
            exported_name="bar",
            def_uid="def_bar_456",
            certainty=Certainty.CERTAIN.value,
        )
        session.add_all([entry1, entry2])
        session.commit()

        # Create anchor groups
        anchor1 = AnchorGroup(
            unit_id=context_id,
            member_token="method",
            receiver_shape="self.",
            total_count=5,
        )
        anchor2 = AnchorGroup(
            unit_id=context_id,
            member_token="method",
            receiver_shape=None,  # No receiver
            total_count=3,
        )
        session.add_all([anchor1, anchor2])
        session.commit()

    return db


class TestDefinitionLookups:
    """Tests for definition lookup methods."""

    def test_get_def_existing(self, seeded_db: Database) -> None:
        """Should get an existing definition by UID."""
        with seeded_db.session() as session:
            queries = FactQueries(session)
            result = queries.get_def("def_foo_123")

            assert result is not None
            assert result.def_uid == "def_foo_123"
            assert result.name == "foo"
            assert result.kind == "function"

    def test_get_def_nonexistent(self, seeded_db: Database) -> None:
        """Should return None for nonexistent definition."""
        with seeded_db.session() as session:
            queries = FactQueries(session)
            result = queries.get_def("nonexistent_uid")

            assert result is None

    def test_list_defs_by_name(self, seeded_db: Database) -> None:
        """Should list definitions by name within a unit."""
        with seeded_db.session() as session:
            queries = FactQueries(session)
            # Get context ID
            ctx = session.exec(select(Context)).first()
            assert ctx is not None
            assert ctx.id is not None

            results = queries.list_defs_by_name(ctx.id, "foo")

            assert len(results) == 2  # foo in main.py and utils.py
            names = [d.name for d in results]
            assert all(n == "foo" for n in names)

    def test_list_defs_by_name_with_limit(self, seeded_db: Database) -> None:
        """Should respect limit parameter."""
        with seeded_db.session() as session:
            queries = FactQueries(session)
            ctx = session.exec(select(Context)).first()
            assert ctx is not None
            assert ctx.id is not None

            results = queries.list_defs_by_name(ctx.id, "foo", limit=1)

            assert len(results) == 1

    def test_list_defs_in_file(self, seeded_db: Database) -> None:
        """Should list all definitions in a file."""
        with seeded_db.session() as session:
            queries = FactQueries(session)
            file = queries.get_file_by_path("src/main.py")
            assert file is not None
            assert file.id is not None

            results = queries.list_defs_in_file(file.id)

            assert len(results) == 2  # foo and bar
            names = {d.name for d in results}
            assert names == {"foo", "bar"}


class TestReferenceLookups:
    """Tests for reference lookup methods."""

    def test_list_refs_by_def_uid(self, seeded_db: Database) -> None:
        """Should list references to a definition."""
        with seeded_db.session() as session:
            queries = FactQueries(session)
            results = queries.list_refs_by_def_uid("def_foo_123")

            assert len(results) == 2  # definition ref + usage ref
            assert all(r.target_def_uid == "def_foo_123" for r in results)

    def test_list_refs_by_def_uid_with_tier_filter(self, seeded_db: Database) -> None:
        """Should filter references by tier."""
        with seeded_db.session() as session:
            queries = FactQueries(session)
            results = queries.list_refs_by_def_uid("def_foo_123", tier=RefTier.PROVEN)

            assert len(results) == 2
            assert all(r.ref_tier == RefTier.PROVEN.value for r in results)

    def test_list_proven_refs(self, seeded_db: Database) -> None:
        """Should list only PROVEN references."""
        with seeded_db.session() as session:
            queries = FactQueries(session)
            results = queries.list_proven_refs("def_foo_123")

            assert len(results) == 2
            assert all(r.ref_tier == RefTier.PROVEN.value for r in results)

    def test_list_refs_in_file(self, seeded_db: Database) -> None:
        """Should list all references in a file."""
        with seeded_db.session() as session:
            queries = FactQueries(session)
            file = queries.get_file_by_path("src/main.py")
            assert file is not None
            assert file.id is not None

            results = queries.list_refs_in_file(file.id)

            assert len(results) == 3  # Two foo refs + one bar ref

    def test_list_refs_by_token(self, seeded_db: Database) -> None:
        """Should list references by token text."""
        with seeded_db.session() as session:
            queries = FactQueries(session)
            ctx = session.exec(select(Context)).first()
            assert ctx is not None
            assert ctx.id is not None

            results = queries.list_refs_by_token(ctx.id, "foo")

            assert len(results) == 3  # Two in main.py, one in utils.py


class TestScopeLookups:
    """Tests for scope lookup methods."""

    def test_get_scope(self, seeded_db: Database) -> None:
        """Should get a scope by ID."""
        with seeded_db.session() as session:
            queries = FactQueries(session)
            # Get a scope ID first
            file = queries.get_file_by_path("src/main.py")
            assert file is not None
            assert file.id is not None
            scopes = queries.list_scopes_in_file(file.id)
            assert len(scopes) > 0
            assert scopes[0].scope_id is not None

            result = queries.get_scope(scopes[0].scope_id)

            assert result is not None
            assert result.file_id == file.id

    def test_get_scope_nonexistent(self, seeded_db: Database) -> None:
        """Should return None for nonexistent scope."""
        with seeded_db.session() as session:
            queries = FactQueries(session)
            result = queries.get_scope(99999)

            assert result is None

    def test_list_scopes_in_file(self, seeded_db: Database) -> None:
        """Should list all scopes in a file."""
        with seeded_db.session() as session:
            queries = FactQueries(session)
            file = queries.get_file_by_path("src/main.py")
            assert file is not None
            assert file.id is not None

            results = queries.list_scopes_in_file(file.id)

            assert len(results) == 2  # file scope + function scope


class TestBindingLookups:
    """Tests for binding lookup methods."""

    def test_get_local_bind(self, seeded_db: Database) -> None:
        """Should get a local binding by scope and name."""
        with seeded_db.session() as session:
            queries = FactQueries(session)
            file = queries.get_file_by_path("src/main.py")
            assert file is not None
            assert file.id is not None
            scopes = queries.list_scopes_in_file(file.id)
            file_scope = next(s for s in scopes if s.kind == ScopeKind.FILE.value)
            assert file_scope.scope_id is not None

            result = queries.get_local_bind(file_scope.scope_id, "foo")

            assert result is not None
            assert result.name == "foo"
            assert result.target_uid == "def_foo_123"

    def test_get_local_bind_nonexistent(self, seeded_db: Database) -> None:
        """Should return None for nonexistent binding."""
        with seeded_db.session() as session:
            queries = FactQueries(session)
            file = queries.get_file_by_path("src/main.py")
            assert file is not None
            assert file.id is not None
            scopes = queries.list_scopes_in_file(file.id)
            file_scope = next(s for s in scopes if s.kind == ScopeKind.FILE.value)
            assert file_scope.scope_id is not None

            result = queries.get_local_bind(file_scope.scope_id, "nonexistent")

            assert result is None

    def test_list_binds_in_scope(self, seeded_db: Database) -> None:
        """Should list all bindings in a scope."""
        with seeded_db.session() as session:
            queries = FactQueries(session)
            file = queries.get_file_by_path("src/main.py")
            assert file is not None
            assert file.id is not None
            scopes = queries.list_scopes_in_file(file.id)
            file_scope = next(s for s in scopes if s.kind == ScopeKind.FILE.value)
            assert file_scope.scope_id is not None

            results = queries.list_binds_in_scope(file_scope.scope_id)

            assert len(results) == 1
            assert results[0].name == "foo"


class TestImportLookups:
    """Tests for import lookup methods."""

    def test_list_imports(self, seeded_db: Database) -> None:
        """Should list all imports in a file."""
        with seeded_db.session() as session:
            queries = FactQueries(session)
            file = queries.get_file_by_path("src/main.py")
            assert file is not None
            assert file.id is not None

            results = queries.list_imports(file.id)

            assert len(results) == 2
            names = {i.imported_name for i in results}
            assert names == {"os", "Path"}

    def test_get_import(self, seeded_db: Database) -> None:
        """Should get an import by UID."""
        with seeded_db.session() as session:
            queries = FactQueries(session)
            result = queries.get_import("imp_os_123")

            assert result is not None
            assert result.imported_name == "os"

    def test_get_import_nonexistent(self, seeded_db: Database) -> None:
        """Should return None for nonexistent import."""
        with seeded_db.session() as session:
            queries = FactQueries(session)
            result = queries.get_import("nonexistent")

            assert result is None


class TestExportLookups:
    """Tests for export lookup methods."""

    def test_get_export_surface(self, seeded_db: Database) -> None:
        """Should get export surface for a unit."""
        with seeded_db.session() as session:
            queries = FactQueries(session)
            ctx = session.exec(select(Context)).first()
            assert ctx is not None
            assert ctx.id is not None

            result = queries.get_export_surface(ctx.id)

            assert result is not None
            assert result.unit_id == ctx.id

    def test_get_export_surface_nonexistent(self, seeded_db: Database) -> None:
        """Should return None for nonexistent surface."""
        with seeded_db.session() as session:
            queries = FactQueries(session)
            result = queries.get_export_surface(99999)

            assert result is None

    def test_list_export_entries(self, seeded_db: Database) -> None:
        """Should list export entries for a surface."""
        with seeded_db.session() as session:
            queries = FactQueries(session)
            ctx = session.exec(select(Context)).first()
            assert ctx is not None
            assert ctx.id is not None
            surface = queries.get_export_surface(ctx.id)
            assert surface is not None
            assert surface.surface_id is not None

            results = queries.list_export_entries(surface.surface_id)

            assert len(results) == 2
            names = {e.exported_name for e in results}
            assert names == {"foo", "bar"}


class TestAnchorGroupLookups:
    """Tests for anchor group lookup methods."""

    def test_get_anchor_group_with_receiver(self, seeded_db: Database) -> None:
        """Should get anchor group with receiver shape."""
        with seeded_db.session() as session:
            queries = FactQueries(session)
            ctx = session.exec(select(Context)).first()
            assert ctx is not None
            assert ctx.id is not None

            result = queries.get_anchor_group(ctx.id, "method", "self.")

            assert result is not None
            assert result.member_token == "method"
            assert result.receiver_shape == "self."
            assert result.total_count == 5

    def test_get_anchor_group_without_receiver(self, seeded_db: Database) -> None:
        """Should get anchor group without receiver shape."""
        with seeded_db.session() as session:
            queries = FactQueries(session)
            ctx = session.exec(select(Context)).first()
            assert ctx is not None
            assert ctx.id is not None

            result = queries.get_anchor_group(ctx.id, "method", None)

            assert result is not None
            assert result.member_token == "method"
            assert result.receiver_shape is None
            assert result.total_count == 3

    def test_get_anchor_group_nonexistent(self, seeded_db: Database) -> None:
        """Should return None for nonexistent anchor group."""
        with seeded_db.session() as session:
            queries = FactQueries(session)
            ctx = session.exec(select(Context)).first()
            assert ctx is not None
            assert ctx.id is not None

            result = queries.get_anchor_group(ctx.id, "nonexistent", None)

            assert result is None

    def test_list_anchor_groups(self, seeded_db: Database) -> None:
        """Should list anchor groups in a unit."""
        with seeded_db.session() as session:
            queries = FactQueries(session)
            ctx = session.exec(select(Context)).first()
            assert ctx is not None
            assert ctx.id is not None

            results = queries.list_anchor_groups(ctx.id)

            assert len(results) == 2


class TestFileLookups:
    """Tests for file lookup methods."""

    def test_get_file(self, seeded_db: Database) -> None:
        """Should get a file by ID."""
        with seeded_db.session() as session:
            queries = FactQueries(session)
            # First get the file by path to get the ID
            file = queries.get_file_by_path("src/main.py")
            assert file is not None
            assert file.id is not None

            result = queries.get_file(file.id)

            assert result is not None
            assert result.path == "src/main.py"

    def test_get_file_nonexistent(self, seeded_db: Database) -> None:
        """Should return None for nonexistent file."""
        with seeded_db.session() as session:
            queries = FactQueries(session)
            result = queries.get_file(99999)

            assert result is None

    def test_get_file_by_path(self, seeded_db: Database) -> None:
        """Should get a file by path."""
        with seeded_db.session() as session:
            queries = FactQueries(session)
            result = queries.get_file_by_path("src/main.py")

            assert result is not None
            assert result.path == "src/main.py"
            assert result.language_family == "python"

    def test_get_file_by_path_nonexistent(self, seeded_db: Database) -> None:
        """Should return None for nonexistent path."""
        with seeded_db.session() as session:
            queries = FactQueries(session)
            result = queries.get_file_by_path("nonexistent.py")

            assert result is None

    def test_list_files(self, seeded_db: Database) -> None:
        """Should list all files."""
        with seeded_db.session() as session:
            queries = FactQueries(session)
            results = queries.list_files()

            assert len(results) == 2
            paths = {f.path for f in results}
            assert paths == {"src/main.py", "src/utils.py"}

    def test_list_files_with_limit(self, seeded_db: Database) -> None:
        """Should respect limit parameter."""
        with seeded_db.session() as session:
            queries = FactQueries(session)
            results = queries.list_files(limit=1)

            assert len(results) == 1


class TestBackwardsCompatibility:
    """Tests for backwards compatibility."""

    def test_symbol_graph_alias(self) -> None:
        """SymbolGraph should be an alias for FactQueries."""
        assert SymbolGraph is FactQueries
