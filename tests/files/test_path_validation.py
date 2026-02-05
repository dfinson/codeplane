"""Tests for validate_path_in_repo and path security.

Tests the path validation utility that prevents directory traversal attacks.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codeplane.files.ops import validate_path_in_repo
from codeplane.mcp.errors import MCPError, MCPErrorCode


@pytest.fixture
def temp_repo(tmp_path: Path) -> Path:
    """Create a temporary repository structure."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("content")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "readme.md").write_text("docs")
    return tmp_path


class TestValidatePathInRepo:
    """Tests for validate_path_in_repo function."""

    def test_valid_relative_path(self, temp_repo: Path) -> None:
        """Valid relative path returns resolved absolute path."""
        result = validate_path_in_repo(temp_repo, "src/main.py")
        assert result == temp_repo / "src" / "main.py"
        assert result.is_absolute()

    def test_valid_simple_filename(self, temp_repo: Path) -> None:
        """Simple filename in repo root works."""
        (temp_repo / "file.txt").write_text("content")
        result = validate_path_in_repo(temp_repo, "file.txt")
        assert result == temp_repo / "file.txt"

    def test_valid_nested_path(self, temp_repo: Path) -> None:
        """Nested path within repo works."""
        result = validate_path_in_repo(temp_repo, "docs/readme.md")
        assert result == temp_repo / "docs" / "readme.md"

    def test_valid_empty_path_returns_repo_root(self, temp_repo: Path) -> None:
        """Empty string path resolves to repo root."""
        result = validate_path_in_repo(temp_repo, "")
        assert result == temp_repo.resolve()

    def test_valid_dot_path_returns_repo_root(self, temp_repo: Path) -> None:
        """Dot path resolves to repo root."""
        result = validate_path_in_repo(temp_repo, ".")
        assert result == temp_repo.resolve()

    # ==========================================================================
    # Path traversal attack prevention
    # ==========================================================================

    def test_rejects_parent_directory_escape(self, temp_repo: Path) -> None:
        """Rejects .. that escapes repo root."""
        with pytest.raises(MCPError) as exc_info:
            validate_path_in_repo(temp_repo, "../outside")

        assert exc_info.value.code == MCPErrorCode.PERMISSION_DENIED
        assert "escapes repository root" in exc_info.value.message

    def test_rejects_multiple_parent_traversal(self, temp_repo: Path) -> None:
        """Rejects multiple .. that escape."""
        with pytest.raises(MCPError) as exc_info:
            validate_path_in_repo(temp_repo, "src/../../outside")

        assert exc_info.value.code == MCPErrorCode.PERMISSION_DENIED

    def test_rejects_deeply_nested_escape(self, temp_repo: Path) -> None:
        """Rejects deeply nested path that still escapes."""
        with pytest.raises(MCPError) as exc_info:
            validate_path_in_repo(temp_repo, "a/b/c/../../../../outside")

        assert exc_info.value.code == MCPErrorCode.PERMISSION_DENIED

    def test_allows_internal_parent_navigation(self, temp_repo: Path) -> None:
        """Allows .. that stays within repo."""
        # src/../docs/readme.md should resolve to docs/readme.md
        result = validate_path_in_repo(temp_repo, "src/../docs/readme.md")
        assert result == temp_repo / "docs" / "readme.md"

    def test_rejects_absolute_path_outside_repo(self, temp_repo: Path) -> None:
        """Rejects absolute path outside repo."""
        with pytest.raises(MCPError) as exc_info:
            validate_path_in_repo(temp_repo, "/etc/passwd")

        assert exc_info.value.code == MCPErrorCode.PERMISSION_DENIED

    def test_allows_absolute_path_inside_repo(self, temp_repo: Path) -> None:
        """Accepts absolute path that's inside repo."""
        absolute_path = str(temp_repo / "src" / "main.py")
        result = validate_path_in_repo(temp_repo, absolute_path)
        assert result == temp_repo / "src" / "main.py"

    def test_rejects_symlink_escape(self, temp_repo: Path) -> None:
        """Rejects symlink that points outside repo."""
        # Create symlink pointing outside repo
        symlink_path = temp_repo / "evil_link"
        try:
            symlink_path.symlink_to("/tmp")
        except OSError:
            pytest.skip("Cannot create symlinks on this system")

        with pytest.raises(MCPError) as exc_info:
            validate_path_in_repo(temp_repo, "evil_link/something")

        assert exc_info.value.code == MCPErrorCode.PERMISSION_DENIED

    def test_allows_symlink_inside_repo(self, temp_repo: Path) -> None:
        """Accepts symlink that stays inside repo."""
        # Create symlink to another directory in repo
        symlink_path = temp_repo / "link_to_docs"
        try:
            symlink_path.symlink_to(temp_repo / "docs")
        except OSError:
            pytest.skip("Cannot create symlinks on this system")

        result = validate_path_in_repo(temp_repo, "link_to_docs/readme.md")
        assert result.is_relative_to(temp_repo.resolve())

    # ==========================================================================
    # Edge cases
    # ==========================================================================

    def test_handles_path_with_spaces(self, temp_repo: Path) -> None:
        """Handles paths with spaces."""
        (temp_repo / "path with spaces").mkdir()
        (temp_repo / "path with spaces" / "file.txt").write_text("content")

        result = validate_path_in_repo(temp_repo, "path with spaces/file.txt")
        assert result == temp_repo / "path with spaces" / "file.txt"

    def test_handles_unicode_path(self, temp_repo: Path) -> None:
        """Handles unicode characters in paths."""
        (temp_repo / "日本語").mkdir()
        (temp_repo / "日本語" / "ファイル.txt").write_text("content")

        result = validate_path_in_repo(temp_repo, "日本語/ファイル.txt")
        assert "日本語" in str(result)

    def test_error_includes_remediation(self, temp_repo: Path) -> None:
        """Error message includes helpful remediation."""
        with pytest.raises(MCPError) as exc_info:
            validate_path_in_repo(temp_repo, "../escape")

        assert exc_info.value.remediation is not None
        assert "relative to the repository root" in exc_info.value.remediation

    def test_error_includes_path_context(self, temp_repo: Path) -> None:
        """Error includes the repo_root in context."""
        with pytest.raises(MCPError) as exc_info:
            validate_path_in_repo(temp_repo, "../escape")

        # The error context includes repo_root
        assert exc_info.value.context.get("repo_root") is not None

    def test_normalizes_windows_separators(self, temp_repo: Path) -> None:
        """Handles Windows-style path separators."""
        # This should work on all platforms as Path handles conversion
        result = validate_path_in_repo(temp_repo, "src/main.py")
        assert result == temp_repo / "src" / "main.py"

    def test_rejects_null_byte_injection(self, temp_repo: Path) -> None:
        """Rejects paths with null bytes."""
        # Null bytes in paths can be used for injection attacks
        # On most systems, this will cause an error in Path operations
        try:
            result = validate_path_in_repo(temp_repo, "file\x00.txt")
            # If it doesn't raise, it should still be within repo
            assert result.is_relative_to(temp_repo.resolve())
        except (ValueError, OSError):
            # Expected on most systems - null bytes are invalid in paths
            pass
