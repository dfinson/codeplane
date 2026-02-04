"""Tests for mcp/tools/introspection.py module.

Covers:
- DescribeParams model
- _get_version() helper
- _derive_features() helper
- describe handler actions (tool, error, capabilities, workflows, operations)
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from pydantic import ValidationError

from codeplane.mcp.tools.introspection import (
    DescribeParams,
    _derive_features,
    _get_version,
)


class TestDescribeParams:
    """Tests for DescribeParams model."""

    def test_tool_action(self) -> None:
        """Tool action params."""
        params = DescribeParams(action="tool", name="read_files")
        assert params.action == "tool"
        assert params.name == "read_files"

    def test_error_action(self) -> None:
        """Error action params."""
        params = DescribeParams(action="error", code="NOT_FOUND")
        assert params.action == "error"
        assert params.code == "NOT_FOUND"

    def test_capabilities_action(self) -> None:
        """Capabilities action params."""
        params = DescribeParams(action="capabilities")
        assert params.action == "capabilities"

    def test_workflows_action(self) -> None:
        """Workflows action params."""
        params = DescribeParams(action="workflows")
        assert params.action == "workflows"

    def test_operations_action(self) -> None:
        """Operations action params."""
        params = DescribeParams(action="operations", path="src/", limit=10)
        assert params.action == "operations"
        assert params.path == "src/"
        assert params.limit == 10

    def test_invalid_action(self) -> None:
        """Invalid action rejected."""
        with pytest.raises(ValidationError):
            DescribeParams(action="invalid")  # type: ignore[arg-type]

    def test_defaults(self) -> None:
        """Default values."""
        params = DescribeParams(action="capabilities")
        assert params.name is None
        assert params.code is None
        assert params.path is None
        assert params.success_only is False
        assert params.limit == 50


class TestGetVersion:
    """Tests for _get_version helper."""

    def test_returns_string(self) -> None:
        """Returns a string."""
        version = _get_version()
        assert isinstance(version, str)

    def test_returns_unknown_on_error(self) -> None:
        """Returns 'unknown' when package not found."""
        with patch("importlib.metadata.version", side_effect=Exception()):
            version = _get_version()
            assert version == "unknown"


class TestDeriveFeatures:
    """Tests for _derive_features helper."""

    def test_git_ops(self) -> None:
        """Derives git_ops from git_ prefixed tools."""
        features = _derive_features(["git_status", "git_commit"])
        assert "git_ops" in features

    def test_refactoring(self) -> None:
        """Derives refactoring from refactor_ prefixed tools."""
        features = _derive_features(["refactor_rename", "refactor_apply"])
        assert "refactoring" in features

    def test_testing(self) -> None:
        """Derives testing from test tool."""
        features = _derive_features(["test"])
        assert "testing" in features

    def test_linting(self) -> None:
        """Derives linting from lint tool."""
        features = _derive_features(["lint"])
        assert "linting" in features

    def test_indexing(self) -> None:
        """Derives indexing from search/map_repo tools."""
        features = _derive_features(["search", "map_repo"])
        assert "indexing" in features

    def test_file_ops(self) -> None:
        """Derives file_ops from file tools."""
        features = _derive_features(["read_files", "list_files", "write_files"])
        assert "file_ops" in features

    def test_introspection(self) -> None:
        """Derives introspection from describe tool."""
        features = _derive_features(["describe"])
        assert "introspection" in features

    def test_multiple_features(self) -> None:
        """Multiple features derived."""
        features = _derive_features(["git_status", "test", "read_files", "search"])
        assert set(features) >= {"git_ops", "testing", "file_ops", "indexing"}

    def test_empty_list(self) -> None:
        """Empty tool list returns empty features."""
        features = _derive_features([])
        assert features == []

    def test_sorted_output(self) -> None:
        """Features are sorted."""
        features = _derive_features(["test", "git_status", "lint"])
        assert features == sorted(features)

    def test_unknown_tools_ignored(self) -> None:
        """Unknown tools don't add features."""
        features = _derive_features(["unknown_tool", "another_one"])
        assert features == []
