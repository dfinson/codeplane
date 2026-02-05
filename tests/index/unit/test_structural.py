"""Unit tests for structural indexer (structural.py).

Tests cover:
- Extraction of all Tier 1 fact types (DefFact, RefFact, ScopeFact, ImportFact, LocalBindFact, DynamicAccessSite)
- Integration with Tree-sitter parser
- Batch processing and error handling
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codeplane.index._internal.db import Database
from codeplane.index._internal.indexing.structural import (
    BatchResult,
    ExtractionResult,
    StructuralIndexer,
    _compute_def_uid,
    _extract_file,
    _find_containing_scope,
)
from codeplane.index._internal.parsing import SyntacticScope
from codeplane.index.models import Context, RefTier, Role


@pytest.fixture
def db(temp_dir: Path) -> Database:
    """Create a test database with schema."""
    from codeplane.index._internal.db import create_additional_indexes

    db_path = temp_dir / "test_structural.db"
    db = Database(db_path)
    db.create_all()
    create_additional_indexes(db.engine)
    return db


@pytest.fixture
def indexer(db: Database, temp_dir: Path) -> StructuralIndexer:
    """Create a StructuralIndexer instance."""
    return StructuralIndexer(db, temp_dir)


class TestDefUidComputation:
    """Tests for def_uid computation."""

    def test_compute_def_uid_basic(self) -> None:
        """def_uid should be deterministic."""
        uid1 = _compute_def_uid("src/foo.py", 1, "function", "foo", None)
        uid2 = _compute_def_uid("src/foo.py", 1, "function", "foo", None)
        assert uid1 == uid2

    def test_compute_def_uid_different_inputs(self) -> None:
        """Different inputs should produce different def_uids."""
        uid1 = _compute_def_uid("src/foo.py", 1, "function", "foo", None)
        uid2 = _compute_def_uid("src/foo.py", 1, "function", "bar", None)
        uid3 = _compute_def_uid("src/foo.py", 2, "function", "foo", None)
        uid4 = _compute_def_uid("src/bar.py", 1, "function", "foo", None)  # Different file
        assert uid1 != uid2
        assert uid1 != uid3
        assert uid1 != uid4

    def test_compute_def_uid_with_signature(self) -> None:
        """Signature hash should affect def_uid."""
        uid1 = _compute_def_uid("src/foo.py", 1, "function", "foo", "abc123")
        uid2 = _compute_def_uid("src/foo.py", 1, "function", "foo", "def456")
        assert uid1 != uid2

    def test_compute_def_uid_length(self) -> None:
        """def_uid should be 16 characters (truncated SHA256)."""
        uid = _compute_def_uid("src/foo.py", 1, "function", "foo", None)
        assert len(uid) == 16


class TestFindContainingScope:
    """Tests for scope containment."""

    def test_file_scope_default(self) -> None:
        """Should return file scope (0) when no scopes contain position."""
        scopes: list[SyntacticScope] = []
        result = _find_containing_scope(scopes, 10, 5)
        assert result == 0

    def test_find_containing_scope_basic(self) -> None:
        """Should find the scope containing a position."""
        scopes = [
            SyntacticScope(
                scope_id=0,
                parent_scope_id=None,
                kind="file",
                start_line=1,
                start_col=0,
                end_line=100,
                end_col=0,
            ),
            SyntacticScope(
                scope_id=1,
                parent_scope_id=0,
                kind="function",
                start_line=5,
                start_col=0,
                end_line=20,
                end_col=0,
            ),
        ]
        result = _find_containing_scope(scopes, 10, 5)
        assert result == 1  # Inside function scope

    def test_find_innermost_scope(self) -> None:
        """Should return innermost scope when multiple scopes contain position."""
        scopes = [
            SyntacticScope(
                scope_id=0,
                parent_scope_id=None,
                kind="file",
                start_line=1,
                start_col=0,
                end_line=100,
                end_col=0,
            ),
            SyntacticScope(
                scope_id=1,
                parent_scope_id=0,
                kind="class",
                start_line=5,
                start_col=0,
                end_line=50,
                end_col=0,
            ),
            SyntacticScope(
                scope_id=2,
                parent_scope_id=1,
                kind="function",
                start_line=10,
                start_col=4,
                end_line=30,
                end_col=0,
            ),
        ]
        result = _find_containing_scope(scopes, 15, 8)
        assert result == 2  # Innermost (function) scope


class TestExtractFile:
    """Tests for single file extraction."""

    def test_extract_python_file(self, temp_dir: Path) -> None:
        """Should extract facts from Python file."""
        content = """
def hello():
    return "Hello"

class Greeter:
    def greet(self, name):
        return f"Hello, {name}"
"""
        file_path = temp_dir / "test.py"
        file_path.write_text(content)

        result = _extract_file("test.py", str(temp_dir), unit_id=1)

        assert result.error is None
        assert len(result.defs) >= 3  # hello, Greeter, greet
        assert len(result.refs) > 0  # At least definition refs
        assert len(result.scopes) >= 1  # At least file scope

    def test_extract_with_imports(self, temp_dir: Path) -> None:
        """Should extract import facts."""
        content = """
import os
from pathlib import Path
"""
        file_path = temp_dir / "test.py"
        file_path.write_text(content)

        result = _extract_file("test.py", str(temp_dir), unit_id=1)

        assert result.error is None
        assert len(result.imports) >= 2  # os and Path

        # Check import structure
        import_names = [i["imported_name"] for i in result.imports]
        assert "os" in import_names
        assert "Path" in import_names

    def test_extract_with_local_binds(self, temp_dir: Path) -> None:
        """Should extract local binding facts."""
        content = """
def foo():
    x = 1
    return x
"""
        file_path = temp_dir / "test.py"
        file_path.write_text(content)

        result = _extract_file("test.py", str(temp_dir), unit_id=1)

        assert result.error is None
        # Should have binding for function definition
        assert len(result.binds) >= 1

    def test_extract_nonexistent_file(self, temp_dir: Path) -> None:
        """Should return error for nonexistent file."""
        result = _extract_file("nonexistent.py", str(temp_dir), unit_id=1)

        assert result.error is not None
        assert "not found" in result.error.lower()

    def test_extract_unsupported_extension(self, temp_dir: Path) -> None:
        """Should gracefully skip unsupported file types."""
        file_path = temp_dir / "test.unknown"
        file_path.write_text("content")

        result = _extract_file("test.unknown", str(temp_dir), unit_id=1)

        # Unsupported files are skipped (no error), but marked as no-grammar
        assert result.error is None
        assert result.skipped_no_grammar is True

    def test_extract_content_hash(self, temp_dir: Path) -> None:
        """Should compute content hash."""
        content = "def foo(): pass"
        file_path = temp_dir / "test.py"
        file_path.write_text(content)

        result = _extract_file("test.py", str(temp_dir), unit_id=1)

        assert result.content_hash is not None
        assert len(result.content_hash) == 64  # SHA256 hex

    def test_extract_dynamic_access(self, temp_dir: Path) -> None:
        """Should extract dynamic access sites."""
        content = """
x = getattr(obj, "foo")
y = obj["key"]
"""
        file_path = temp_dir / "test.py"
        file_path.write_text(content)

        result = _extract_file("test.py", str(temp_dir), unit_id=1)

        assert result.error is None
        # Should detect getattr and bracket access
        assert len(result.dynamic_sites) >= 2


class TestExtractionResult:
    """Tests for ExtractionResult structure."""

    def test_extraction_result_defaults(self) -> None:
        """ExtractionResult should have correct defaults."""
        result = ExtractionResult(file_path="test.py")

        assert result.file_path == "test.py"
        assert result.defs == []
        assert result.refs == []
        assert result.scopes == []
        assert result.imports == []
        assert result.binds == []
        assert result.dynamic_sites == []
        assert result.error is None


class TestBatchResult:
    """Tests for BatchResult structure."""

    def test_batch_result_defaults(self) -> None:
        """BatchResult should have correct defaults."""
        result = BatchResult()

        assert result.files_processed == 0
        assert result.defs_extracted == 0
        assert result.refs_extracted == 0
        assert result.scopes_extracted == 0
        assert result.imports_extracted == 0
        assert result.binds_extracted == 0
        assert result.dynamic_sites_extracted == 0
        assert result.errors == []
        assert result.duration_ms == 0


class TestStructuralIndexer:
    """Tests for StructuralIndexer."""

    def test_indexer_creation(self, indexer: StructuralIndexer) -> None:
        """Should create indexer instance."""
        assert indexer is not None
        assert indexer._parser is not None

    def test_index_single_file(
        self, db: Database, indexer: StructuralIndexer, temp_dir: Path
    ) -> None:
        """Should index a single file."""
        content = """
def hello():
    return "Hello"
"""
        file_path = temp_dir / "test.py"
        file_path.write_text(content)

        db.create_all()
        # Create a context first
        with db.session() as session:
            ctx = Context(
                name="test",
                language_family="python",
                root_path=str(temp_dir),
            )
            session.add(ctx)
            session.commit()
            context_id = ctx.id

        result = indexer.index_files(["test.py"], context_id=context_id or 1)

        assert result.files_processed == 1
        assert result.defs_extracted >= 1  # hello function
        assert result.errors == []

    def test_index_multiple_files(
        self, db: Database, indexer: StructuralIndexer, temp_dir: Path
    ) -> None:
        """Should index multiple files."""
        (temp_dir / "a.py").write_text("def foo(): pass")
        (temp_dir / "b.py").write_text("def bar(): pass")

        db.create_all()
        with db.session() as session:
            ctx = Context(
                name="test",
                language_family="python",
                root_path=str(temp_dir),
            )
            session.add(ctx)
            session.commit()
            context_id = ctx.id

        result = indexer.index_files(["a.py", "b.py"], context_id=context_id or 1)

        assert result.files_processed == 2
        assert result.defs_extracted >= 2  # foo and bar

    def test_index_with_errors(
        self, db: Database, indexer: StructuralIndexer, temp_dir: Path
    ) -> None:
        """Should handle files with errors gracefully."""
        (temp_dir / "good.py").write_text("def foo(): pass")

        db.create_all()
        with db.session() as session:
            ctx = Context(
                name="test",
                language_family="python",
                root_path=str(temp_dir),
            )
            session.add(ctx)
            session.commit()
            context_id = ctx.id

        result = indexer.index_files(["good.py", "nonexistent.py"], context_id=context_id or 1)

        assert result.files_processed == 2
        assert result.defs_extracted >= 1  # From good.py
        assert len(result.errors) >= 1  # From nonexistent.py


class TestRefTierAssignment:
    """Tests for RefTier assignment during extraction."""

    def test_definition_refs_are_proven(self, temp_dir: Path) -> None:
        """Definition sites should have PROVEN ref_tier."""
        content = "def foo(): pass"
        file_path = temp_dir / "test.py"
        file_path.write_text(content)

        result = _extract_file("test.py", str(temp_dir), unit_id=1)

        # Find the definition ref for foo
        def_refs = [r for r in result.refs if r["role"] == Role.DEFINITION.value]
        assert len(def_refs) >= 1
        assert all(r["ref_tier"] == RefTier.PROVEN.value for r in def_refs)

    def test_same_file_refs_are_proven(self, temp_dir: Path) -> None:
        """References to same-file definitions should be PROVEN."""
        content = """
def foo():
    return 1

x = foo()
"""
        file_path = temp_dir / "test.py"
        file_path.write_text(content)

        result = _extract_file("test.py", str(temp_dir), unit_id=1)

        # Find reference to foo (not the definition)
        usage_refs = [
            r for r in result.refs if r["token_text"] == "foo" and r["role"] == Role.REFERENCE.value
        ]
        # Should have at least one PROVEN reference
        proven_refs = [r for r in usage_refs if r["ref_tier"] == RefTier.PROVEN.value]
        assert len(proven_refs) >= 1

    def test_import_refs_are_unknown_or_strong(self, temp_dir: Path) -> None:
        """Import statements should have UNKNOWN or STRONG ref_tier."""
        content = """
import os
os.path.exists(".")
"""
        file_path = temp_dir / "test.py"
        file_path.write_text(content)

        result = _extract_file("test.py", str(temp_dir), unit_id=1)

        # Find import ref
        import_refs = [r for r in result.refs if r["role"] == Role.IMPORT.value]
        assert len(import_refs) >= 1
        # Import statements are UNKNOWN until cross-file resolution
        assert all(
            r["ref_tier"] in (RefTier.UNKNOWN.value, RefTier.STRONG.value) for r in import_refs
        )
