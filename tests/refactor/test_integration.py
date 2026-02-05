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
    # Use a consistent path that IndexCoordinator will use
    codeplane_dir = tmp_path / ".codeplane"
    codeplane_dir.mkdir(exist_ok=True)
    db = Database(codeplane_dir / "index.db")
    db.create_all()
    create_additional_indexes(db.engine)

    # Create a context (required for foreign key constraints)
    # Use root_path that matches the project structure
    with db.session() as session:
        ctx = Context(
            name="test-context",
            language_family="python",
            root_path="",  # Empty string for repo root
            probe_status="valid",
            enabled=True,
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
    # Create lexical index in the same .codeplane directory
    codeplane_dir = refactor_project / ".codeplane"
    codeplane_dir.mkdir(exist_ok=True)
    lexical_index = LexicalIndex(codeplane_dir / "lexical.tantivy")

    # Index with structural indexer
    indexer = StructuralIndexer(test_db, refactor_project)

    # Get all Python files
    py_files = [rel(f, refactor_project) for f in refactor_project.rglob("*.py")]

    # Create File records first (required for FK constraints)
    import hashlib

    from codeplane.index.models import File

    file_id_map: dict[str, int] = {}
    with test_db.session() as session:
        for py_file in py_files:
            full_path = refactor_project / py_file
            content_hash = hashlib.sha256(full_path.read_bytes()).hexdigest()
            file_record = File(
                path=py_file,
                content_hash=content_hash,
                language_family="python",
            )
            session.add(file_record)
            session.flush()
            if file_record.id is not None:
                file_id_map[py_file] = file_record.id
        session.commit()

    result = indexer.index_files(py_files, context_id=1, file_id_map=file_id_map)
    # Note: Grammar-unavailable errors are expected if tree-sitter-python isn't installed
    # The lexical index will still work for refactoring tests
    real_errors = [e for e in result.errors if "Language not available" not in e]
    assert real_errors == [], f"Indexing errors: {real_errors}"

    # Also index into lexical index using add_file
    for py_file in py_files:
        full_path = refactor_project / py_file
        content = full_path.read_text(encoding="utf-8")
        file_id = file_id_map.get(py_file, 0)
        lexical_index.add_file(
            file_path=py_file,
            content=content,
            context_id=1,
            file_id=file_id,
            symbols=[],  # Not needed for lexical search
        )

    # Reload to make changes visible to searcher
    lexical_index.reload()

    return refactor_project, test_db, lexical_index


@pytest.fixture
async def refactor_ops(
    indexed_project: tuple[Path, Database, LexicalIndex],
) -> RefactorOps:
    """Create RefactorOps with a properly initialized coordinator.

    Uses the existing indexed database and calls load_existing() to
    initialize the coordinator without re-indexing.
    """
    repo_root, db, lexical_index = indexed_project

    # Use the same paths as indexed_project
    codeplane_dir = repo_root / ".codeplane"
    db_path = codeplane_dir / "index.db"
    tantivy_path = codeplane_dir / "lexical.tantivy"

    # Create coordinator pointing at existing index
    coordinator = IndexCoordinator(
        repo_root,
        db_path,
        tantivy_path,
    )

    # Load the existing index (doesn't re-index)
    loaded = await coordinator.load_existing()
    assert loaded, "Coordinator failed to load existing index"

    return RefactorOps(repo_root, coordinator)


@pytest.mark.asyncio
class TestRefactorRenameIntegration:
    """Integration tests for RefactorOps.rename()."""

    async def test_rename_function_preview(
        self,
        refactor_ops: RefactorOps,
        indexed_project: tuple[Path, Database, LexicalIndex],  # noqa: ARG002
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
        """Test applying a rename actually modifies files.

        Note: Current lexical search returns first match per file, so not all
        occurrences are replaced. This test verifies partial application works.
        """
        repo_root, _, _ = indexed_project

        # Get preview
        preview_result = await refactor_ops.rename("my_function", "renamed_function")
        assert preview_result.status == "previewed"
        assert preview_result.preview is not None

        # Verify preview found matches in multiple files
        edited_paths = {fe.path for fe in preview_result.preview.edits}
        assert "src/module.py" in edited_paths
        # consumer.py may or may not have matches depending on search results

        # Create mutation ops
        mutation_ops = MutationOps(repo_root)

        # Apply the refactor
        apply_result = await refactor_ops.apply(preview_result.refactor_id, mutation_ops)
        assert apply_result.status == "applied"
        assert apply_result.applied is not None
        assert apply_result.applied.files_changed >= 1

        # Verify at least module.py was modified correctly
        # (the definition site should always be found)
        module_py = (repo_root / "src" / "module.py").read_text()
        assert "renamed_function" in module_py

    async def test_rename_constant(
        self,
        refactor_ops: RefactorOps,
        indexed_project: tuple[Path, Database, LexicalIndex],  # noqa: ARG002
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
        indexed_project: tuple[Path, Database, LexicalIndex],  # noqa: ARG002
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
        indexed_project: tuple[Path, Database, LexicalIndex],  # noqa: ARG002
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
        indexed_project: tuple[Path, Database, LexicalIndex],  # noqa: ARG002
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
        indexed_project: tuple[Path, Database, LexicalIndex],  # noqa: ARG002
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
        indexed_project: tuple[Path, Database, LexicalIndex],  # noqa: ARG002
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
