"""Tests for MCP AppContext."""

from dataclasses import fields
from pathlib import Path
from unittest.mock import MagicMock

from codeplane.mcp.context import AppContext


class TestAppContext:
    """Tests for AppContext dataclass."""

    def test_is_dataclass(self):
        """AppContext is a dataclass."""
        from dataclasses import is_dataclass

        assert is_dataclass(AppContext)

    def test_has_required_fields(self):
        """AppContext has all required fields."""
        field_names = {f.name for f in fields(AppContext)}
        expected = {
            "repo_root",
            "git_ops",
            "coordinator",
            "file_ops",
            "mutation_ops",
            "refactor_ops",
            "test_ops",
            "lint_ops",
            "session_manager",
        }
        assert expected <= field_names

    def test_manual_construction(self, tmp_path: Path):
        """Can construct AppContext manually."""
        ctx = AppContext(
            repo_root=tmp_path,
            git_ops=MagicMock(),
            coordinator=MagicMock(),
            file_ops=MagicMock(),
            mutation_ops=MagicMock(),
            refactor_ops=MagicMock(),
            test_ops=MagicMock(),
            lint_ops=MagicMock(),
            session_manager=MagicMock(),
        )
        assert ctx.repo_root == tmp_path


class TestAppContextCreate:
    """Tests for AppContext.create factory."""

    def test_create_signature(self):
        """create has expected signature."""
        import inspect

        sig = inspect.signature(AppContext.create)
        params = list(sig.parameters.keys())
        assert "repo_root" in params
        assert "db_path" in params
        assert "tantivy_path" in params
        assert "coordinator" in params

    def test_create_coordinator_optional(self):
        """coordinator parameter is optional."""
        import inspect

        sig = inspect.signature(AppContext.create)
        param = sig.parameters["coordinator"]
        assert param.default is None
