"""Tests for mcp/tools/introspection.py module.

Covers:
- _get_version() helper
- _derive_features() helper
"""

from __future__ import annotations

from codeplane.mcp.tools.introspection import (
    _derive_features,
    _get_version,
)


class TestGetVersion:
    """Tests for _get_version helper."""

    def test_returns_string(self) -> None:
        """Returns a string version."""
        version = _get_version()
        assert isinstance(version, str)
        # Either a version number or "unknown"
        assert version == "unknown" or "." in version or version.startswith("0")


class TestDeriveFeatures:
    """Tests for _derive_features helper."""

    def test_empty_tools(self) -> None:
        """Empty tool list returns empty features."""
        features = _derive_features([])
        assert features == []

    def test_git_tools(self) -> None:
        """Git-related tools add git_ops feature."""
        features = _derive_features(["checkpoint"])
        assert "git_ops" in features

    def test_refactor_tools(self) -> None:
        """Refactor tools add refactoring feature."""
        features = _derive_features(["refactor_rename", "refactor_move"])
        assert "refactoring" in features

    def test_verify_tool(self) -> None:
        """Checkpoint tool adds testing, linting, and git_ops features."""
        features = _derive_features(["checkpoint"])
        assert "testing" in features
        assert "linting" in features
        assert "git_ops" in features

    def test_index_tools(self) -> None:
        """Index tools add indexing feature."""
        features = _derive_features(["search", "map_repo"])
        assert "indexing" in features

    def test_file_tools(self) -> None:
        """File tools add file_ops feature."""
        features = _derive_features(["read_source", "list_files", "write_source"])
        assert "file_ops" in features

    def test_describe_tool(self) -> None:
        """Describe tool adds introspection feature."""
        features = _derive_features(["describe"])
        assert "introspection" in features

    def test_multiple_features(self) -> None:
        """Multiple tool types add multiple features."""
        tools = [
            "checkpoint",
            "refactor_rename",
            "search",
            "read_source",
            "describe",
        ]
        features = _derive_features(tools)
        assert "git_ops" in features
        assert "refactoring" in features
        assert "testing" in features
        assert "linting" in features
        assert "indexing" in features
        assert "file_ops" in features
        assert "introspection" in features

    def test_sorted_output(self) -> None:
        """Features are sorted alphabetically."""
        tools = ["describe", "checkpoint"]
        features = _derive_features(tools)
        assert features == sorted(features)

    def test_no_duplicates(self) -> None:
        """Multiple tools of same type don't create duplicates."""
        tools = ["checkpoint", "checkpoint"]
        features = _derive_features(tools)
        assert features.count("git_ops") == 1
