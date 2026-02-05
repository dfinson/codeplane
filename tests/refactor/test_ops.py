"""Tests for refactor operations (move, delete, rename).

Covers:
- refactor_move: import path updates
- refactor_delete: reference discovery
- Helper methods: _path_to_module, _build_preview
"""

from __future__ import annotations

from pathlib import Path
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
    """Test _build_delete_preview method."""

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
        preview = refactor_ops._build_delete_preview("target", edits)
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
class TestRefactorDelete:
    """Test refactor_delete operation."""

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

    async def test_delete_symbol(self, temp_repo: Path, mock_coordinator: MagicMock) -> None:
        ops = RefactorOps(temp_repo, mock_coordinator)

        mock_session = MagicMock()
        mock_session.exec.return_value.all.return_value = []
        mock_coordinator.db.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_coordinator.db.session.return_value.__exit__ = MagicMock(return_value=False)

        result = await ops.delete("target_func")

        assert result.status == "previewed"
        assert result.preview is not None
        assert result.preview.verification_required

    async def test_delete_file_path(self, temp_repo: Path, mock_coordinator: MagicMock) -> None:
        ops = RefactorOps(temp_repo, mock_coordinator)

        mock_session = MagicMock()
        mock_session.exec.return_value.all.return_value = []
        mock_coordinator.db.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_coordinator.db.session.return_value.__exit__ = MagicMock(return_value=False)

        result = await ops.delete("src/target.py")

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
