"""Tests for filesystem reconciliation."""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from codeplane.index._internal.db.reconcile import (
    ChangedFile,
    Reconciler,
    ReconcileResult,
)
from codeplane.index.models import Freshness


class TestChangedFile:
    """Tests for ChangedFile dataclass."""

    def test_changed_file_construction(self) -> None:
        """ChangedFile holds change details."""
        cf = ChangedFile(
            path="src/test.py",
            old_hash="abc123",
            new_hash="def456",
            change_type="modified",
        )
        assert cf.path == "src/test.py"
        assert cf.old_hash == "abc123"
        assert cf.new_hash == "def456"
        assert cf.change_type == "modified"

    def test_changed_file_added(self) -> None:
        """ChangedFile for added files has None old_hash."""
        cf = ChangedFile(
            path="new_file.py",
            old_hash=None,
            new_hash="newsha",
            change_type="added",
        )
        assert cf.old_hash is None
        assert cf.change_type == "added"


class TestReconcileResult:
    """Tests for ReconcileResult dataclass."""

    def test_reconcile_result_defaults(self) -> None:
        """ReconcileResult has sensible defaults."""
        result = ReconcileResult()
        assert result.files_checked == 0
        assert result.files_added == 0
        assert result.files_modified == 0
        assert result.files_removed == 0
        assert result.files_unchanged == 0
        assert result.head_before is None
        assert result.head_after is None
        assert result.duration_ms == 0.0
        assert result.errors == []
        assert result.cplignore_changed is False

    def test_files_changed_property(self) -> None:
        """files_changed sums add/modify/remove."""
        result = ReconcileResult(
            files_added=5,
            files_modified=10,
            files_removed=3,
        )
        assert result.files_changed == 18

    def test_files_changed_zero(self) -> None:
        """files_changed is zero when no changes."""
        result = ReconcileResult()
        assert result.files_changed == 0

    def test_errors_accumulate(self) -> None:
        """Errors can be accumulated."""
        result = ReconcileResult(errors=["error1"])
        result.errors.append("error2")
        assert len(result.errors) == 2


class TestReconciler:
    """Tests for Reconciler."""

    def test_init_stores_dependencies(self) -> None:
        """Reconciler stores db and repo_root."""
        mock_db = MagicMock()
        repo_root = Path("/test/repo")
        reconciler = Reconciler(mock_db, repo_root)
        assert reconciler.db is mock_db
        assert reconciler.repo_root == repo_root

    def test_cplignore_path_property(self) -> None:
        """cplignore_path returns expected path."""
        mock_db = MagicMock()
        repo_root = Path("/test/repo")
        reconciler = Reconciler(mock_db, repo_root)
        expected = repo_root / ".codeplane" / ".cplignore"
        assert reconciler.cplignore_path == expected

    def test_git_property_lazy_initialization(self) -> None:
        """git property lazily initializes GitOps."""
        mock_db = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            reconciler = Reconciler(mock_db, repo_root)
            assert reconciler._git is None

            # Mock GitOps to avoid needing real git repo
            with patch("codeplane.index._internal.db.reconcile.GitOps") as mock_git_class:
                mock_git_instance = MagicMock()
                mock_git_class.return_value = mock_git_instance

                git = reconciler.git
                assert git is mock_git_instance
                mock_git_class.assert_called_once_with(repo_root)

    def test_compute_hash(self) -> None:
        """_compute_hash returns SHA-256 of file content."""
        mock_db = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            test_file = repo_root / "test.py"
            content = b"print('hello')"
            test_file.write_bytes(content)

            expected_hash = hashlib.sha256(content).hexdigest()

            reconciler = Reconciler(mock_db, repo_root)
            actual_hash = reconciler._compute_hash(test_file)
            assert actual_hash == expected_hash

    def test_normalize_path_absolute(self) -> None:
        """_normalize_path converts absolute to relative POSIX."""
        mock_db = MagicMock()
        repo_root = Path("/test/repo")
        reconciler = Reconciler(mock_db, repo_root)

        abs_path = repo_root / "src" / "module.py"
        result = reconciler._normalize_path(abs_path)
        assert result == "src/module.py"

    def test_normalize_path_relative(self) -> None:
        """_normalize_path keeps relative paths as POSIX."""
        mock_db = MagicMock()
        repo_root = Path("/test/repo")
        reconciler = Reconciler(mock_db, repo_root)

        rel_path = Path("src/module.py")
        result = reconciler._normalize_path(rel_path)
        assert result == "src/module.py"

    @pytest.mark.parametrize(
        "ext,expected",
        [
            (".py", "python"),
            (".pyi", "python"),
            (".js", "javascript"),
            (".jsx", "javascript"),
            (".ts", "typescript"),
            (".tsx", "typescript"),
            (".go", "go"),
            (".rs", "rust"),
            (".java", "java"),
            (".kt", "kotlin"),
            (".scala", "scala"),
            (".cs", "csharp"),
            (".rb", "ruby"),
            (".php", "php"),
            (".swift", "swift"),
            (".ex", "elixir"),
            (".exs", "elixir"),
            (".hs", "haskell"),
            (".tf", "terraform"),
            (".sql", "sql"),
            (".md", "markdown"),
            (".json", "json"),
            (".yaml", "yaml"),
            (".yml", "yaml"),
            (".toml", "toml"),
            (".proto", "protobuf"),
            (".graphql", "graphql"),
            (".gql", "graphql"),
            (".nix", "nix"),
            (".unknown", None),
        ],
    )
    def test_detect_language(self, ext: str, expected: str | None) -> None:
        """_detect_language maps extensions to languages."""
        mock_db = MagicMock()
        reconciler = Reconciler(mock_db, Path("/test"))
        result = reconciler._detect_language(f"file{ext}")
        assert result == expected

    def test_detect_language_case_insensitive(self) -> None:
        """_detect_language is case insensitive."""
        mock_db = MagicMock()
        reconciler = Reconciler(mock_db, Path("/test"))
        assert reconciler._detect_language("FILE.PY") == "python"
        assert reconciler._detect_language("Test.Js") == "javascript"

    def test_get_file_state_unindexed(self) -> None:
        """get_file_state returns UNINDEXED for unknown files."""
        mock_db = MagicMock()
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.first.return_value = None
        mock_session.exec.return_value = mock_result
        mock_db.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.session.return_value.__exit__ = MagicMock(return_value=False)

        reconciler = Reconciler(mock_db, Path("/test"))
        result = reconciler.get_file_state("unknown.py")
        assert result == Freshness.UNINDEXED

    def test_get_file_state_dirty_file_missing(self) -> None:
        """get_file_state returns DIRTY when file doesn't exist."""
        mock_db = MagicMock()
        mock_session = MagicMock()
        mock_file = MagicMock()
        mock_file.content_hash = "abc123"
        mock_result = MagicMock()
        mock_result.first.return_value = mock_file
        mock_session.exec.return_value = mock_result
        mock_db.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.session.return_value.__exit__ = MagicMock(return_value=False)

        with tempfile.TemporaryDirectory() as tmp:
            reconciler = Reconciler(mock_db, Path(tmp))
            # File doesn't exist on disk
            result = reconciler.get_file_state("missing.py")
            assert result == Freshness.DIRTY

    def test_get_file_state_dirty_hash_mismatch(self) -> None:
        """get_file_state returns DIRTY when hash doesn't match."""
        mock_db = MagicMock()
        mock_session = MagicMock()
        mock_file = MagicMock()
        mock_file.content_hash = "old_hash"
        mock_file.indexed_at = 1234567890.0
        mock_result = MagicMock()
        mock_result.first.return_value = mock_file
        mock_session.exec.return_value = mock_result
        mock_db.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.session.return_value.__exit__ = MagicMock(return_value=False)

        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            test_file = repo_root / "test.py"
            test_file.write_text("new content")

            reconciler = Reconciler(mock_db, repo_root)
            result = reconciler.get_file_state("test.py")
            assert result == Freshness.DIRTY

    def test_get_file_state_unindexed_no_indexed_at(self) -> None:
        """get_file_state returns UNINDEXED when indexed_at is None."""
        mock_db = MagicMock()
        mock_session = MagicMock()

        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            test_file = repo_root / "test.py"
            content = "content"
            test_file.write_text(content)
            content_hash = hashlib.sha256(content.encode()).hexdigest()

            mock_file = MagicMock()
            mock_file.content_hash = content_hash
            mock_file.indexed_at = None
            mock_result = MagicMock()
            mock_result.first.return_value = mock_file
            mock_session.exec.return_value = mock_result
            mock_db.session.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_db.session.return_value.__exit__ = MagicMock(return_value=False)

            reconciler = Reconciler(mock_db, repo_root)
            result = reconciler.get_file_state("test.py")
            assert result == Freshness.UNINDEXED

    def test_get_file_state_clean(self) -> None:
        """get_file_state returns CLEAN when hash matches and indexed."""
        mock_db = MagicMock()
        mock_session = MagicMock()

        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            test_file = repo_root / "test.py"
            content = "clean content"
            test_file.write_text(content)
            content_hash = hashlib.sha256(content.encode()).hexdigest()

            mock_file = MagicMock()
            mock_file.content_hash = content_hash
            mock_file.indexed_at = 1234567890.0
            mock_result = MagicMock()
            mock_result.first.return_value = mock_file
            mock_session.exec.return_value = mock_result
            mock_db.session.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_db.session.return_value.__exit__ = MagicMock(return_value=False)

            reconciler = Reconciler(mock_db, repo_root)
            result = reconciler.get_file_state("test.py")
            assert result == Freshness.CLEAN

    def test_get_changed_files_empty_when_no_previous_head(self) -> None:
        """get_changed_files returns empty when no previous head."""
        mock_db = MagicMock()
        mock_session = MagicMock()
        mock_session.get.return_value = None
        mock_db.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.session.return_value.__exit__ = MagicMock(return_value=False)

        reconciler = Reconciler(mock_db, Path("/test"))
        result = reconciler.get_changed_files(since_head=None)
        assert result == []

    def test_get_changed_files_empty_when_same_head(self) -> None:
        """get_changed_files returns empty when heads are same."""
        mock_db = MagicMock()
        reconciler = Reconciler(mock_db, Path("/test"))

        with patch.object(reconciler, "_get_git_head", return_value="abc123"):
            result = reconciler.get_changed_files(since_head="abc123")
            assert result == []

    def test_get_db_hashes_empty_paths(self) -> None:
        """_get_db_hashes returns empty dict for empty paths."""
        mock_db = MagicMock()
        reconciler = Reconciler(mock_db, Path("/test"))
        result = reconciler._get_db_hashes([])
        assert result == {}
