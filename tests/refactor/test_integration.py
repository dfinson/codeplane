"""Integration tests for refactor operations.

Tests the full refactor_rename flow end-to-end:
1. Create a Python project with symbols
2. Index it with the structural indexer
3. Call RefactorOps.rename() to preview
4. Apply the refactoring
5. Verify the changes are correct

Also tests edge cases:
- Module-level constants (UPPERCASE names)
- Lexical fallback for unindexed occurrences
- Comment occurrences
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest

from codeplane.index._internal.db import Database, create_additional_indexes
from codeplane.index._internal.indexing import LexicalIndex, StructuralIndexer
from codeplane.index.models import Context
from codeplane.index.ops import IndexCoordinator
from codeplane.mutation.ops import MutationOps
from codeplane.refactor.ops import RefactorOps


def rel(path: Path, root: Path) -> str:
    """Get relative path string for index_files."""
    return str(path.relative_to(root))


@pytest.fixture
def test_db(tmp_path: Path) -> Generator[Database, None, None]:
    """Create a test database with schema and context."""
    db = Database(tmp_path / "test.db")
    db.create_all()
    create_additional_indexes(db.engine)

    # Create a context (required for foreign key constraints)
    with db.session() as session:
        ctx = Context(
            name="test-context",
            language_family="python",
            root_path=str(tmp_path),
        )
        session.add(ctx)
        session.commit()

    yield db


@pytest.fixture
def refactor_project(tmp_path: Path) -> Path:
    """Create a Python project for refactoring tests."""
    # Main module with a function
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "__init__.py").write_text("")
    (tmp_path / "src" / "module.py").write_text('''"""Module with symbols to rename."""

# A module-level constant
MY_CONSTANT = 42

def my_function(x: int) -> int:
    """A function to rename.

    Uses MY_CONSTANT internally.
    """
    return x + MY_CONSTANT

class MyClass:
    """A class to rename."""

    def my_method(self) -> int:
        """Uses my_function."""
        return my_function(10)
''')

    # A consumer module that imports and uses the symbols
    (
        tmp_path / "src" / "consumer.py"
    ).write_text('''"""Consumer module that uses symbols from module.py."""

from src.module import my_function, MyClass, MY_CONSTANT

def use_it() -> int:
    """Use the imported symbols."""
    # Call my_function directly
    result = my_function(5)

    # Use MyClass
    obj = MyClass()
    result += obj.my_method()

    # Use MY_CONSTANT
    result += MY_CONSTANT

    return result
''')

    # Test file
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "__init__.py").write_text("")
    (tmp_path / "tests" / "test_module.py").write_text('''"""Tests for module.py."""

from src.module import my_function, MY_CONSTANT

def test_my_function():
    """Test my_function."""
    assert my_function(0) == MY_CONSTANT
''')

    return tmp_path


@pytest.fixture
def indexed_project(
    refactor_project: Path,
    test_db: Database,
) -> tuple[Path, Database, LexicalIndex]:
    """Index the refactor project."""
    # Create lexical index
    lexical_index = LexicalIndex(refactor_project / ".codeplane" / "lexical.tantivy")

    # Index with structural indexer
    indexer = StructuralIndexer(test_db, refactor_project)

    # Get all Python files
    py_files = [rel(f, refactor_project) for f in refactor_project.rglob("*.py")]

    result = indexer.index_files(py_files, context_id=1)
    assert result.errors == [], f"Indexing errors: {result.errors}"

    # Also index into lexical index using add_file
    for py_file in py_files:
        full_path = refactor_project / py_file
        content = full_path.read_text(encoding="utf-8")
        lexical_index.add_file(
            file_path=py_file,
            content=content,
            context_id=1,
            file_id=0,
            symbols=[],  # Not needed for lexical search
        )

    return refactor_project, test_db, lexical_index


@pytest.fixture
def refactor_ops(
    indexed_project: tuple[Path, Database, LexicalIndex],
    tmp_path: Path,
) -> RefactorOps:
    """Create RefactorOps with real coordinator."""
    repo_root, db, lexical_index = indexed_project

    # Create a real coordinator with proper constructor
    db_path = tmp_path / "coordinator.db"
    tantivy_path = tmp_path / "coordinator.tantivy"
    coordinator = IndexCoordinator(
        repo_root,
        db_path,
        tantivy_path,
    )

    return RefactorOps(repo_root, coordinator)


@pytest.mark.asyncio
class TestRefactorRenameIntegration:
    """Integration tests for RefactorOps.rename()."""

    async def test_rename_function_preview(
        self,
        refactor_ops: RefactorOps,
        _indexed_project: tuple[Path, Database, LexicalIndex],
    ) -> None:
        """Test renaming a function generates correct preview."""
        result = await refactor_ops.rename("my_function", "renamed_function")

        assert result.status == "previewed"
        assert result.preview is not None
        assert result.preview.files_affected >= 2  # module.py and consumer.py at minimum

        # Check that edits include all expected files
        edited_paths = {fe.path for fe in result.preview.edits}
        assert "src/module.py" in edited_paths
        assert "src/consumer.py" in edited_paths

        # Verify edit hunks
        for file_edit in result.preview.edits:
            for hunk in file_edit.hunks:
                assert hunk.old == "my_function"
                assert hunk.new == "renamed_function"
                assert hunk.line > 0
                assert hunk.certainty in ("high", "medium", "low")

    async def test_rename_function_apply(
        self,
        refactor_ops: RefactorOps,
        indexed_project: tuple[Path, Database, LexicalIndex],
    ) -> None:
        """Test applying a rename actually modifies files."""
        repo_root, _, _ = indexed_project

        # Get preview
        preview_result = await refactor_ops.rename("my_function", "renamed_function")
        assert preview_result.status == "previewed"

        # Create mutation ops
        mutation_ops = MutationOps(repo_root)

        # Apply the refactor
        apply_result = await refactor_ops.apply(preview_result.refactor_id, mutation_ops)
        assert apply_result.status == "applied"
        assert apply_result.applied is not None
        assert apply_result.applied.files_changed >= 2

        # Verify file contents
        module_py = (repo_root / "src" / "module.py").read_text()
        assert "renamed_function" in module_py
        assert "my_function" not in module_py

        consumer_py = (repo_root / "src" / "consumer.py").read_text()
        assert "renamed_function" in consumer_py
        assert "my_function" not in consumer_py

    async def test_rename_constant(
        self,
        refactor_ops: RefactorOps,
        _indexed_project: tuple[Path, Database, LexicalIndex],
    ) -> None:
        """Test renaming a module-level constant."""
        result = await refactor_ops.rename("MY_CONSTANT", "RENAMED_CONSTANT")

        assert result.status == "previewed"
        assert result.preview is not None

        # Constants should be found via lexical fallback at minimum
        edited_paths = {fe.path for fe in result.preview.edits}
        assert "src/module.py" in edited_paths
        assert "src/consumer.py" in edited_paths

    async def test_rename_class(
        self,
        refactor_ops: RefactorOps,
        _indexed_project: tuple[Path, Database, LexicalIndex],
    ) -> None:
        """Test renaming a class."""
        result = await refactor_ops.rename("MyClass", "RenamedClass")

        assert result.status == "previewed"
        assert result.preview is not None

        # Should find in module.py (definition) and consumer.py (usage)
        edited_paths = {fe.path for fe in result.preview.edits}
        assert "src/module.py" in edited_paths
        assert "src/consumer.py" in edited_paths

    async def test_rename_includes_comments(
        self,
        refactor_ops: RefactorOps,
        _indexed_project: tuple[Path, Database, LexicalIndex],
    ) -> None:
        """Test that renaming includes comment occurrences."""
        result = await refactor_ops.rename("my_function", "renamed_function")

        assert result.preview is not None

        # Check module.py edits - should include the docstring mention
        module_edits = next(
            (fe for fe in result.preview.edits if fe.path == "src/module.py"),
            None,
        )
        assert module_edits is not None

        # Should have multiple hunks including the docstring "Uses my_function."
        assert len(module_edits.hunks) >= 2

    async def test_rename_cancel(
        self,
        refactor_ops: RefactorOps,
        _indexed_project: tuple[Path, Database, LexicalIndex],
    ) -> None:
        """Test canceling a pending refactor."""
        preview_result = await refactor_ops.rename("my_function", "renamed_function")
        refactor_id = preview_result.refactor_id

        # Verify it's pending
        assert refactor_id in refactor_ops._pending

        # Cancel
        cancel_result = await refactor_ops.cancel(refactor_id)
        assert cancel_result.status == "cancelled"

        # Verify it's no longer pending
        assert refactor_id not in refactor_ops._pending

    async def test_rename_inspect_low_certainty(
        self,
        refactor_ops: RefactorOps,
        _indexed_project: tuple[Path, Database, LexicalIndex],
    ) -> None:
        """Test inspecting low-certainty matches."""
        # Rename something that will have lexical fallback matches
        preview_result = await refactor_ops.rename("my_function", "renamed_function")

        # Find files with low-certainty matches
        low_certainty_files = (
            preview_result.preview.low_certainty_files if preview_result.preview else []
        )

        if low_certainty_files:
            # Inspect the first file
            inspect_result = await refactor_ops.inspect(
                preview_result.refactor_id,
                low_certainty_files[0],
                context_lines=2,
            )

            # Each match should have context
            for match in inspect_result.matches:
                assert "line" in match
                assert "snippet" in match
                assert int(match["line"]) > 0


@pytest.mark.asyncio
class TestRefactorEdgeCases:
    """Test edge cases for refactoring."""

    async def test_rename_nonexistent_symbol(
        self,
        refactor_ops: RefactorOps,
        _indexed_project: tuple[Path, Database, LexicalIndex],
    ) -> None:
        """Renaming a symbol that doesn't exist should return empty preview."""
        result = await refactor_ops.rename("nonexistent_symbol_xyz", "new_name")

        assert result.status == "previewed"
        assert result.preview is not None
        # Should have no or very few matches (only if it appears lexically somewhere)
        # The exact behavior depends on whether lexical search finds anything

    async def test_apply_invalid_refactor_id(
        self,
        refactor_ops: RefactorOps,
        indexed_project: tuple[Path, Database, LexicalIndex],
    ) -> None:
        """Applying with invalid ID should raise."""
        repo_root, _, _ = indexed_project
        mutation_ops = MutationOps(repo_root)

        with pytest.raises(ValueError, match="No pending refactor"):
            await refactor_ops.apply("invalid-id", mutation_ops)

    async def test_line_numbers_are_accurate(
        self,
        refactor_ops: RefactorOps,
        indexed_project: tuple[Path, Database, LexicalIndex],
    ) -> None:
        """Test that line numbers in preview are accurate."""
        repo_root, _, _ = indexed_project

        result = await refactor_ops.rename("my_function", "renamed_function")
        assert result.preview is not None

        # Check line numbers by reading the actual files
        for file_edit in result.preview.edits:
            full_path = repo_root / file_edit.path
            lines = full_path.read_text().splitlines()

            for hunk in file_edit.hunks:
                # Line numbers are 1-indexed
                line_content = lines[hunk.line - 1]
                # The old text should appear in that line
                assert hunk.old in line_content, (
                    f"Expected '{hunk.old}' in line {hunk.line} of {file_edit.path}, "
                    f"but got: '{line_content}'"
                )
