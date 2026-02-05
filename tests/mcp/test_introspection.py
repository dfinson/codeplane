"""Tests for MCP introspection tool (describe).

Tests the actual exports:
- _get_version() helper
- _derive_features() helper

Handler tests use conftest.py fixtures for integration testing.
"""

from codeplane.mcp.tools.introspection import (
    _derive_features,
    _get_version,
)


class TestGetVersion:
    """Tests for _get_version helper."""

    def test_returns_string(self):
        """Returns a version string."""
        version = _get_version()
        assert isinstance(version, str)


class TestDeriveFeatures:
    """Tests for _derive_features helper."""

    def test_empty_tools(self):
        """Empty tool list returns empty features."""
        result = _derive_features([])
        assert result == []

    def test_git_tools(self):
        """Git tools derive git_ops feature."""
        result = _derive_features(["git_status", "git_commit"])
        assert "git_ops" in result

    def test_refactor_tools(self):
        """Refactor tools derive refactoring feature."""
        result = _derive_features(["refactor_rename", "refactor_move"])
        assert "refactoring" in result

    def test_index_tools(self):
        """Index tools derive indexing feature."""
        result = _derive_features(["search", "map_repo"])
        assert "indexing" in result

    def test_results_sorted(self):
        """Results are sorted alphabetically."""
        result = _derive_features(["search", "git_status", "describe"])
        assert result == sorted(result)
