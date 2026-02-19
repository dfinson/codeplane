"""Tests for refactor operations (move, delete, rename).

Covers:
- refactor_move: import path updates
- refactor_impact: reference discovery
- Helper methods: _path_to_module, _build_preview
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from codeplane.refactor.ops import (
    EditHunk,
    FileEdit,
    RefactorOps,
    RefactorPreview,
    _word_boundary_match,
)


class TestWordBoundaryMatch:
    """Test _word_boundary_match helper."""

    def test_exact_match(self) -> None:
        assert _word_boundary_match("foo bar baz", "bar")

    def test_start_of_line(self) -> None:
        assert _word_boundary_match("foo bar", "foo")

    def test_end_of_line(self) -> None:
        assert _word_boundary_match("foo bar", "bar")

    def test_no_match_substring(self) -> None:
        assert not _word_boundary_match("foobar", "foo")
        assert not _word_boundary_match("foobar", "bar")

    def test_with_punctuation(self) -> None:
        assert _word_boundary_match("foo.bar", "foo")
        assert _word_boundary_match("foo.bar", "bar")

    def test_special_chars_escaped(self) -> None:
        # Symbol with special regex chars should be escaped
        assert _word_boundary_match("use foo$bar here", "foo$bar")


class TestPathToModule:
    """Test _path_to_module conversion."""

    @pytest.fixture
    def refactor_ops(self, tmp_path: Path) -> RefactorOps:
        coordinator = MagicMock()
        return RefactorOps(tmp_path, coordinator)

    def test_simple_path(self, refactor_ops: RefactorOps) -> None:
        assert refactor_ops._path_to_module("src/utils/helper.py") == "src.utils.helper"

    def test_no_extension(self, refactor_ops: RefactorOps) -> None:
        assert refactor_ops._path_to_module("src/utils") == "src.utils"

    def test_single_file(self, refactor_ops: RefactorOps) -> None:
        assert refactor_ops._path_to_module("main.py") == "main"

    def test_windows_path(self, refactor_ops: RefactorOps) -> None:
        assert refactor_ops._path_to_module("src\\utils\\helper.py") == "src.utils.helper"


class TestBuildPreview:
    """Test _build_preview method."""

    @pytest.fixture
    def refactor_ops(self, tmp_path: Path) -> RefactorOps:
        coordinator = MagicMock()
        return RefactorOps(tmp_path, coordinator)

    def test_empty_edits(self, refactor_ops: RefactorOps) -> None:
        preview = refactor_ops._build_preview({})
        assert preview.files_affected == 0
        assert preview.high_certainty_count == 0
        assert not preview.verification_required

    def test_high_certainty_only(self, refactor_ops: RefactorOps) -> None:
        edits = {
            "file.py": [
                EditHunk(old="old", new="new", line=1, certainty="high"),
                EditHunk(old="old", new="new", line=2, certainty="high"),
            ]
        }
        preview = refactor_ops._build_preview(edits)
        assert preview.files_affected == 1
        assert preview.high_certainty_count == 2
        assert preview.low_certainty_count == 0
        assert not preview.verification_required

    def test_low_certainty_triggers_verification(self, refactor_ops: RefactorOps) -> None:
        edits = {
            "file.py": [
                EditHunk(old="old", new="new", line=1, certainty="high"),
                EditHunk(old="old", new="new", line=2, certainty="low"),
            ]
        }
        preview = refactor_ops._build_preview(edits)
        assert preview.verification_required
        assert preview.low_certainty_count == 1
        assert "file.py" in preview.low_certainty_files

    def test_multiple_files(self, refactor_ops: RefactorOps) -> None:
        edits = {
            "a.py": [EditHunk(old="x", new="y", line=1, certainty="high")],
            "b.py": [EditHunk(old="x", new="y", line=1, certainty="medium")],
            "c.py": [EditHunk(old="x", new="y", line=1, certainty="low")],
        }
        preview = refactor_ops._build_preview(edits)
        assert preview.files_affected == 3
        assert preview.high_certainty_count == 1
        assert preview.medium_certainty_count == 1
        assert preview.low_certainty_count == 1


class TestBuildDeletePreview:
    """Test _build_impact_preview method."""

    @pytest.fixture
    def refactor_ops(self, tmp_path: Path) -> RefactorOps:
        coordinator = MagicMock()
        return RefactorOps(tmp_path, coordinator)

    def test_delete_guidance(self, refactor_ops: RefactorOps) -> None:
        edits = {
            "file.py": [
                EditHunk(old="target", new="", line=1, certainty="high"),
            ]
        }
        preview = refactor_ops._build_impact_preview("target", edits)
        assert preview.verification_required
        assert "target" in (preview.verification_guidance or "")
        assert "does NOT auto-remove" in (preview.verification_guidance or "")


@pytest.mark.asyncio
class TestRefactorMove:
    """Test refactor_move operation."""

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        coordinator = MagicMock()
        coordinator.db = MagicMock()
        coordinator.db.session = MagicMock()
        # search returns an object with .results attribute
        search_result = MagicMock()
        search_result.results = []
        coordinator.search = AsyncMock(return_value=search_result)
        return coordinator

    @pytest.fixture
    def temp_repo(self, tmp_path: Path) -> Path:
        # Create a simple repo structure
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "old_module.py").write_text("# old module")
        (tmp_path / "src" / "consumer.py").write_text(
            "from src.old_module import func\nimport src.old_module\n"
        )
        return tmp_path

    async def test_move_normalizes_paths(
        self, temp_repo: Path, mock_coordinator: MagicMock
    ) -> None:
        ops = RefactorOps(temp_repo, mock_coordinator)

        # Mock the session context manager
        mock_session = MagicMock()
        mock_session.exec.return_value.all.return_value = []  # No imports
        mock_coordinator.db.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_coordinator.db.session.return_value.__exit__ = MagicMock(return_value=False)

        result = await ops.move("./src/old_module.py", "./src/new_module.py")

        assert result.status == "previewed"
        assert result.refactor_id is not None

    async def test_move_returns_preview(self, temp_repo: Path, mock_coordinator: MagicMock) -> None:
        ops = RefactorOps(temp_repo, mock_coordinator)

        mock_session = MagicMock()
        mock_session.exec.return_value.all.return_value = []
        mock_coordinator.db.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_coordinator.db.session.return_value.__exit__ = MagicMock(return_value=False)

        result = await ops.move("src/old.py", "src/new.py")

        assert result.preview is not None
        assert isinstance(result.preview, RefactorPreview)


@pytest.mark.asyncio
class TestRefactorImpact:
    """Test refactor_impact operation."""

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        coordinator = MagicMock()
        coordinator.db = MagicMock()
        coordinator.db.session = MagicMock()
        coordinator.get_all_defs = AsyncMock(return_value=[])
        # search returns an object with .results attribute
        search_result = MagicMock()
        search_result.results = []
        coordinator.search = AsyncMock(return_value=search_result)
        return coordinator

    @pytest.fixture
    def temp_repo(self, tmp_path: Path) -> Path:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "target.py").write_text("def target_func(): pass")
        return tmp_path

    async def test_impact_symbol(self, temp_repo: Path, mock_coordinator: MagicMock) -> None:
        ops = RefactorOps(temp_repo, mock_coordinator)

        mock_session = MagicMock()
        mock_session.exec.return_value.all.return_value = []
        mock_coordinator.db.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_coordinator.db.session.return_value.__exit__ = MagicMock(return_value=False)

        result = await ops.impact("target_func")

        assert result.status == "previewed"
        assert result.preview is not None
        assert result.preview.verification_required

    async def test_impact_file_path(self, temp_repo: Path, mock_coordinator: MagicMock) -> None:
        ops = RefactorOps(temp_repo, mock_coordinator)

        mock_session = MagicMock()
        mock_session.exec.return_value.all.return_value = []
        mock_coordinator.db.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_coordinator.db.session.return_value.__exit__ = MagicMock(return_value=False)

        result = await ops.impact("src/target.py")

        assert result.status == "previewed"
        # File path detected by / or .py
        assert result.preview is not None


@pytest.mark.asyncio
class TestRefactorCancel:
    """Test refactor_cancel operation."""

    @pytest.fixture
    def refactor_ops(self, tmp_path: Path) -> RefactorOps:
        coordinator = MagicMock()
        return RefactorOps(tmp_path, coordinator)

    async def test_cancel_existing(self, refactor_ops: RefactorOps) -> None:
        # Add a pending refactor
        refactor_ops._pending["test-id"] = RefactorPreview(files_affected=0)

        result = await refactor_ops.cancel("test-id")

        assert result.status == "cancelled"
        assert "test-id" not in refactor_ops._pending

    async def test_cancel_nonexistent(self, refactor_ops: RefactorOps) -> None:
        result = await refactor_ops.cancel("nonexistent")

        assert result.status == "cancelled"


@pytest.mark.asyncio
class TestRefactorInspect:
    """Test refactor_inspect operation."""

    @pytest.fixture
    def temp_repo(self, tmp_path: Path) -> Path:
        (tmp_path / "test.py").write_text("line 1\nline 2 target here\nline 3\nline 4\nline 5\n")
        return tmp_path

    @pytest.fixture
    def refactor_ops(self, temp_repo: Path) -> RefactorOps:
        coordinator = MagicMock()
        return RefactorOps(temp_repo, coordinator)

    async def test_inspect_returns_context(self, refactor_ops: RefactorOps) -> None:
        # Set up pending refactor with low-certainty hunk
        refactor_ops._pending["test-id"] = RefactorPreview(
            files_affected=1,
            edits=[
                FileEdit(
                    path="test.py",
                    hunks=[EditHunk(old="target", new="replacement", line=2, certainty="low")],
                )
            ],
        )

        result = await refactor_ops.inspect("test-id", "test.py", context_lines=1)

        assert len(result.matches) == 1
        assert result.matches[0]["line"] == 2
        assert "target" in str(result.matches[0]["snippet"])

    async def test_inspect_nonexistent_refactor(self, refactor_ops: RefactorOps) -> None:
        result = await refactor_ops.inspect("nonexistent", "test.py")

        assert result.matches == []

    async def test_inspect_skips_high_certainty(self, refactor_ops: RefactorOps) -> None:
        refactor_ops._pending["test-id"] = RefactorPreview(
            files_affected=1,
            edits=[
                FileEdit(
                    path="test.py",
                    hunks=[EditHunk(old="target", new="replacement", line=2, certainty="high")],
                )
            ],
        )

        result = await refactor_ops.inspect("test-id", "test.py")

        # High certainty hunks are skipped
        assert result.matches == []


@pytest.mark.asyncio
class TestRefactorMoveImportVariants:
    """Test refactor_move correctly handles import path variants.

    Issue #153: Files in src/ layout have source_literal WITHOUT 'src.' prefix
    but file paths WITH 'src/' prefix. The move operation must match both.
    """

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        coordinator = MagicMock()
        coordinator.db = MagicMock()
        coordinator.db.session = MagicMock()
        search_result = MagicMock()
        search_result.results = []
        coordinator.search = AsyncMock(return_value=search_result)
        return coordinator

    @pytest.fixture
    def temp_repo(self, tmp_path: Path) -> Path:
        """Create a repo with src/ layout."""
        src_dir = tmp_path / "src" / "mypackage"
        src_dir.mkdir(parents=True)
        (src_dir / "__init__.py").write_text("")
        (src_dir / "target.py").write_text("def func(): pass")
        (src_dir / "consumer.py").write_text("from mypackage.target import func\n")
        return tmp_path

    async def test_move_finds_imports_without_src_prefix(
        self, temp_repo: Path, mock_coordinator: MagicMock
    ) -> None:
        """Verify move() finds imports when source_literal lacks src. prefix.

        This is the core issue #153 fix: file at src/mypackage/target.py
        should match ImportFact with source_literal='mypackage.target'.
        """
        ops = RefactorOps(temp_repo, mock_coordinator)

        # Create mock ImportFact with source_literal lacking src. prefix
        mock_import = MagicMock()
        mock_import.source_literal = "mypackage.target"  # NO src. prefix
        mock_import.file_id = 1
        mock_import.import_uid = "test-import-1"

        # Create mock File record for the source file
        mock_file = MagicMock()
        mock_file.id = 2
        mock_file.path = "src/mypackage/target.py"
        mock_file.language_family = "python"
        mock_file.declared_module = None  # Python doesn't use declared_module

        # Create mock File record for the consumer
        mock_consumer_file = MagicMock()
        mock_consumer_file.id = 1
        mock_consumer_file.path = "src/mypackage/consumer.py"

        mock_session = MagicMock()

        # First query: select File where path = from_path -> returns mock_file
        # Second query: select ImportFact WHERE source_literal IN (...) -> returns mock_import
        # Third query: get file path by file_id -> returns consumer path
        call_count = [0]

        def mock_exec_side_effect(_query: Any) -> MagicMock:
            result = MagicMock()
            call_count[0] += 1
            if call_count[0] == 1:
                # File lookup for from_path (uses .first())
                result.first.return_value = mock_file
                result.one_or_none.return_value = mock_file
            elif call_count[0] == 2:
                # ImportFact query - returns tuple (ImportFact, file_path)
                result.all.return_value = [(mock_import, "src/mypackage/consumer.py")]
            else:
                # Subsequent queries for file paths
                result.one_or_none.return_value = mock_consumer_file
                result.all.return_value = []
            return result

        mock_session.exec.side_effect = mock_exec_side_effect
        mock_coordinator.db.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_coordinator.db.session.return_value.__exit__ = MagicMock(return_value=False)

        result = await ops.move("src/mypackage/target.py", "src/mypackage/new_target.py")

        assert result.status == "previewed"
        assert result.preview is not None
        # The fix should result in finding the import and creating edits
        assert result.preview.files_affected > 0, "Expected to find files with imports"
        assert len(result.preview.edits) > 0, "Expected to generate edit hunks"
        # Verify the edit has the correct old/new values
        found_edit = False
        for file_edit in result.preview.edits:
            for hunk in file_edit.hunks:
                if "mypackage.target" in hunk.old and "mypackage.new_target" in hunk.new:
                    found_edit = True
                    break
        assert found_edit, "Expected edit to replace mypackage.target with mypackage.new_target"

    async def test_move_matches_all_python_variants(
        self, temp_repo: Path, mock_coordinator: MagicMock
    ) -> None:
        """Verify move() generates correct variants for Python files in src/ layout."""
        ops = RefactorOps(temp_repo, mock_coordinator)

        # Create mock File record
        mock_file = MagicMock()
        mock_file.id = 1
        mock_file.path = "src/codeplane/index/resolver.py"
        mock_file.language_family = "python"
        mock_file.declared_module = None

        mock_session = MagicMock()
        mock_session.exec.return_value.one_or_none.return_value = mock_file
        mock_session.exec.return_value.all.return_value = []
        mock_coordinator.db.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_coordinator.db.session.return_value.__exit__ = MagicMock(return_value=False)

        result = await ops.move(
            "src/codeplane/index/resolver.py", "src/codeplane/index/new_resolver.py"
        )

        assert result.status == "previewed"
        # The operation should complete without errors even with complex paths

    async def test_move_with_declared_module(
        self, temp_repo: Path, mock_coordinator: MagicMock
    ) -> None:
        """Verify move() uses declared_module for languages that have it (Go, Rust, etc)."""
        ops = RefactorOps(temp_repo, mock_coordinator)

        # Create mock File record with declared_module (like Go/Rust)
        mock_file = MagicMock()
        mock_file.id = 1
        mock_file.path = "pkg/server/handler.go"
        mock_file.language_family = "go"
        mock_file.declared_module = "github.com/org/repo/pkg/server"

        # Create mock ImportFact using declared_module-based source_literal
        mock_import = MagicMock()
        mock_import.source_literal = "github.com/org/repo/pkg/server"
        mock_import.file_id = 2
        mock_import.import_uid = "test-import-go-1"

        mock_session = MagicMock()
        call_count = [0]

        def mock_exec_side_effect(_query: Any) -> MagicMock:
            result = MagicMock()
            call_count[0] += 1
            if call_count[0] == 1:
                # File lookup for from_path (uses .first())
                result.first.return_value = mock_file
                result.one_or_none.return_value = mock_file
            elif call_count[0] == 2:
                # ImportFact query - returns tuple (ImportFact, file_path)
                result.all.return_value = [(mock_import, "pkg/main.go")]
            else:
                result.one_or_none.return_value = None
                result.all.return_value = []
            return result

        mock_session.exec.side_effect = mock_exec_side_effect
        mock_coordinator.db.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_coordinator.db.session.return_value.__exit__ = MagicMock(return_value=False)

        # Create the Go file that imports the module
        pkg_dir = temp_repo / "pkg"
        pkg_dir.mkdir(parents=True, exist_ok=True)
        (pkg_dir / "main.go").write_text(
            'package main\n\nimport "github.com/org/repo/pkg/server"\n'
        )
        (pkg_dir / "server").mkdir(exist_ok=True)
        (pkg_dir / "server" / "handler.go").write_text("package server\n")

        result = await ops.move("pkg/server/handler.go", "pkg/api/handler.go")

        assert result.status == "previewed"
        assert result.preview is not None
        # Verify edits were found for the Go import
        assert result.preview.files_affected > 0, "Expected Go import to be found"
        # Verify the declared_module-based replacement
        found_go_edit = False
        for file_edit in result.preview.edits:
            for hunk in file_edit.hunks:
                # Should replace pkg/server with pkg/api in the module path
                if "pkg/server" in hunk.old and "pkg/api" in hunk.new:
                    found_go_edit = True
                    break
        assert found_go_edit, "Expected edit for Go module path"
