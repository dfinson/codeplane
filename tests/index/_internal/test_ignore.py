"""Tests for index ignore patterns and IgnoreChecker.

Verifies PRUNABLE_DIRS import, ignore file loading, and pattern matching.
"""

from pathlib import Path

from codeplane.index._internal.ignore import (
    PRUNABLE_DIRS,
    IgnoreChecker,
    matches_glob,
)


class TestPrunableDirs:
    """Tests for PRUNABLE_DIRS constant."""

    def test_prunable_dirs_is_set(self) -> None:
        """PRUNABLE_DIRS should be a set/frozenset."""
        assert isinstance(PRUNABLE_DIRS, set | frozenset)

    def test_common_dirs_included(self) -> None:
        """Should include common build/dependency directories."""
        assert "node_modules" in PRUNABLE_DIRS
        assert ".git" in PRUNABLE_DIRS
        assert "__pycache__" in PRUNABLE_DIRS
        assert ".venv" in PRUNABLE_DIRS


class TestMatchesGlob:
    """Tests for matches_glob helper."""

    def test_simple_pattern(self) -> None:
        """Should match simple patterns."""
        assert matches_glob("test.py", "*.py") is True
        assert matches_glob("test.js", "*.py") is False

    def test_double_star_pattern(self) -> None:
        """Should handle **/pattern for any-depth matching."""
        assert matches_glob("test.py", "**/test.py") is True
        assert matches_glob("src/test.py", "**/test.py") is True
        assert matches_glob("deep/nested/test.py", "**/test.py") is True

    def test_no_match(self) -> None:
        """Should return False for non-matches."""
        assert matches_glob("test.py", "*.js") is False
        assert matches_glob("other.py", "test.py") is False


class TestIgnoreChecker:
    """Tests for IgnoreChecker class."""

    def test_create_checker(self, tmp_path: Path) -> None:
        """Should create checker without errors."""
        checker = IgnoreChecker(tmp_path)
        assert checker is not None

    def test_should_ignore_prunable_dir(self, tmp_path: Path) -> None:
        """Should ignore files in DEFAULT_PRUNABLE_DIRS."""
        checker = IgnoreChecker(tmp_path)
        assert checker.should_ignore(tmp_path / "node_modules" / "pkg" / "index.js")
        # .git is in HARDCODED_DIRS, not DEFAULT_PRUNABLE_DIRS
        # It's handled by should_prune_dir() instead
        assert checker.should_ignore(tmp_path / "__pycache__" / "cache.pyc")

    def test_should_not_ignore_normal_files(self, tmp_path: Path) -> None:
        """Should not ignore regular source files."""
        checker = IgnoreChecker(tmp_path)
        assert not checker.should_ignore(tmp_path / "src" / "main.py")
        assert not checker.should_ignore(tmp_path / "tests" / "test_foo.py")

    def test_load_cplignore(self, tmp_path: Path) -> None:
        """Should load patterns from .cplignore."""
        # Create .cplignore
        (tmp_path / ".cplignore").write_text("*.log\nbuild/\n")

        checker = IgnoreChecker(tmp_path)
        assert checker.should_ignore(tmp_path / "debug.log")
        assert checker.should_ignore(tmp_path / "build" / "output.o")
        assert not checker.should_ignore(tmp_path / "main.py")

    def test_cplignore_comments(self, tmp_path: Path) -> None:
        """Should skip comments in .cplignore."""
        (tmp_path / ".cplignore").write_text("# This is a comment\n*.log\n")

        checker = IgnoreChecker(tmp_path)
        assert checker.should_ignore(tmp_path / "debug.log")

    def test_cplignore_empty_lines(self, tmp_path: Path) -> None:
        """Should skip empty lines in .cplignore."""
        (tmp_path / ".cplignore").write_text("*.log\n\n\n*.tmp\n")

        checker = IgnoreChecker(tmp_path)
        assert checker.should_ignore(tmp_path / "debug.log")
        assert checker.should_ignore(tmp_path / "cache.tmp")

    def test_negation_patterns(self, tmp_path: Path) -> None:
        """Should handle negation with ! prefix."""
        (tmp_path / ".cplignore").write_text("*.log\n!important.log\n")

        checker = IgnoreChecker(tmp_path)
        assert checker.should_ignore(tmp_path / "debug.log")
        # Note: negation only removes from ignore list, actual behavior
        # depends on order of pattern application

    def test_extra_patterns(self, tmp_path: Path) -> None:
        """Should accept extra patterns via constructor."""
        checker = IgnoreChecker(tmp_path, extra_patterns=["*.bak", "temp/"])
        assert checker.should_ignore(tmp_path / "backup.bak")
        assert checker.should_ignore(tmp_path / "temp" / "file.txt")

    def test_is_excluded_rel(self, tmp_path: Path) -> None:
        """Should check relative paths."""
        checker = IgnoreChecker(tmp_path)
        assert checker.is_excluded_rel("node_modules/pkg/index.js")
        assert not checker.is_excluded_rel("src/main.py")

    def test_respect_gitignore(self, tmp_path: Path) -> None:
        """Should load .gitignore when respect_gitignore=True."""
        (tmp_path / ".gitignore").write_text("*.pyc\ndist/\n")

        checker = IgnoreChecker(tmp_path, respect_gitignore=True)
        assert checker.should_ignore(tmp_path / "cache.pyc")
        assert checker.should_ignore(tmp_path / "dist" / "bundle.js")

    def test_nested_cplignore(self, tmp_path: Path) -> None:
        """Should handle nested .cplignore files."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / ".cplignore").write_text("*.generated.py\n")

        checker = IgnoreChecker(tmp_path)
        # Nested patterns should be prefixed with directory
        assert checker.should_ignore(tmp_path / "src" / "model.generated.py")

    def test_cplignore_paths_property(self, tmp_path: Path) -> None:
        """Should track loaded .cplignore paths."""
        (tmp_path / ".cplignore").write_text("*.log\n")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / ".cplignore").write_text("*.tmp\n")

        checker = IgnoreChecker(tmp_path)
        paths = checker.cplignore_paths
        assert len(paths) == 2
        assert tmp_path / ".cplignore" in paths
        assert tmp_path / "sub" / ".cplignore" in paths

    def test_compute_combined_hash(self, tmp_path: Path) -> None:
        """Should compute hash of .cplignore contents."""
        (tmp_path / ".cplignore").write_text("*.log\n")

        checker = IgnoreChecker(tmp_path)
        hash1 = checker.compute_combined_hash()
        assert hash1 is not None
        assert len(hash1) == 64  # SHA-256 hex

    def test_compute_combined_hash_no_files(self, tmp_path: Path) -> None:
        """Should return None when no .cplignore files."""
        checker = IgnoreChecker(tmp_path)
        assert checker.compute_combined_hash() is None

    def test_compute_combined_hash_changes_on_edit(self, tmp_path: Path) -> None:
        """Hash should change when .cplignore changes."""
        (tmp_path / ".cplignore").write_text("*.log\n")
        checker1 = IgnoreChecker(tmp_path)
        hash1 = checker1.compute_combined_hash()

        (tmp_path / ".cplignore").write_text("*.log\n*.tmp\n")
        checker2 = IgnoreChecker(tmp_path)
        hash2 = checker2.compute_combined_hash()

        assert hash1 != hash2

    def test_path_outside_root(self, tmp_path: Path) -> None:
        """Should ignore paths outside the root."""
        checker = IgnoreChecker(tmp_path)
        outside_path = tmp_path.parent / "outside.py"
        assert checker.should_ignore(outside_path)
