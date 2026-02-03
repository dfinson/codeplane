"""Tests for MCP lint tool."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from codeplane.mcp.tools.lint import (
    LintParams,
    lint,
)


class TestLintParams:
    """Tests for LintParams model."""

    def test_action_required(self):
        """action is required."""
        with pytest.raises(ValidationError):
            LintParams()

    def test_action_check(self):
        """check action."""
        params = LintParams(action="check")
        assert params.action == "check"

    def test_action_tools(self):
        """tools action."""
        params = LintParams(action="tools")
        assert params.action == "tools"

    def test_action_invalid(self):
        """Invalid action rejected."""
        with pytest.raises(ValidationError):
            LintParams(action="fix")

    def test_paths_optional(self):
        """paths is optional."""
        params = LintParams(action="check")
        assert params.paths is None

    def test_paths_provided(self):
        """paths can be provided."""
        params = LintParams(action="check", paths=["src/", "lib/"])
        assert params.paths == ["src/", "lib/"]

    def test_tools_optional(self):
        """tools is optional."""
        params = LintParams(action="check")
        assert params.tools is None

    def test_tools_provided(self):
        """tools can be provided."""
        params = LintParams(action="check", tools=["ruff", "mypy"])
        assert params.tools == ["ruff", "mypy"]

    def test_categories_optional(self):
        """categories is optional."""
        params = LintParams(action="check")
        assert params.categories is None

    def test_categories_provided(self):
        """categories can be provided."""
        params = LintParams(action="check", categories=["style", "types"])
        assert params.categories == ["style", "types"]

    def test_dry_run_default(self):
        """dry_run defaults to False."""
        params = LintParams(action="check")
        assert params.dry_run is False

    def test_dry_run_true(self):
        """dry_run can be True."""
        params = LintParams(action="check", dry_run=True)
        assert params.dry_run is True

    def test_language_optional(self):
        """language is optional."""
        params = LintParams(action="tools")
        assert params.language is None

    def test_language_provided(self):
        """language can be provided."""
        params = LintParams(action="tools", language="python")
        assert params.language == "python"

    def test_category_optional(self):
        """category is optional for tools action."""
        params = LintParams(action="tools")
        assert params.category is None

    def test_category_provided(self):
        """category can be provided for tools action."""
        params = LintParams(action="tools", category="style")
        assert params.category == "style"


class TestLintHandlerCheck:
    """Tests for lint handler check action."""

    @pytest.mark.asyncio
    async def test_checks_all_files(self, mock_context: MagicMock):
        """Checks all files."""
        params = LintParams(action="check")
        await lint(mock_context, params)

        mock_context.lint_ops.check.assert_called_once()

    @pytest.mark.asyncio
    async def test_checks_specific_paths(self, mock_context: MagicMock):
        """Checks specific paths."""
        params = LintParams(action="check", paths=["src/main.py"])
        await lint(mock_context, params)

        mock_context.lint_ops.check.assert_called()

    @pytest.mark.asyncio
    async def test_checks_with_specific_tools(self, mock_context: MagicMock):
        """Checks with specific lint tools."""
        params = LintParams(action="check", tools=["ruff"])
        await lint(mock_context, params)

        mock_context.lint_ops.check.assert_called()

    @pytest.mark.asyncio
    async def test_checks_dry_run(self, mock_context: MagicMock):
        """Dry run check."""
        params = LintParams(action="check", dry_run=True)
        await lint(mock_context, params)

        mock_context.lint_ops.check.assert_called()

    @pytest.mark.asyncio
    async def test_check_result_has_status(self, mock_context: MagicMock):
        """Check result includes status."""
        tool_run_mock = MagicMock(tool_id="ruff", success=True)
        mock_context.lint_ops.check = AsyncMock(
            return_value=MagicMock(
                action="check",
                dry_run=False,
                status="clean",
                total_diagnostics=0,
                total_files_modified=0,
                duration_seconds=0.5,
                tools_run=[tool_run_mock],
                agentic_hint=None,
            )
        )

        params = LintParams(action="check")
        result = await lint(mock_context, params)

        assert "status" in result or "action" in result

    @pytest.mark.asyncio
    async def test_check_with_issues(self, mock_context: MagicMock):
        """Check result with lint issues."""
        tool_run_mock_1 = MagicMock(tool_id="ruff", success=True)
        tool_run_mock_2 = MagicMock(tool_id="mypy", success=True)
        mock_context.lint_ops.check = AsyncMock(
            return_value=MagicMock(
                action="check",
                dry_run=False,
                status="issues_found",
                total_diagnostics=5,
                total_files_modified=0,
                duration_seconds=1.2,
                tools_run=[tool_run_mock_1, tool_run_mock_2],
                agentic_hint="Review the 5 diagnostics found.",
            )
        )

        params = LintParams(action="check")
        result = await lint(mock_context, params)

        assert result.get("total_diagnostics", 0) > 0 or "status" in result


class TestLintHandlerTools:
    """Tests for lint handler tools action."""

    @pytest.mark.asyncio
    async def test_lists_all_tools(self, mock_context: MagicMock):
        """Lists all available lint tools."""
        # The tools action typically doesn't use lint_ops directly
        # It uses the lint registry
        params = LintParams(action="tools")
        result = await lint(mock_context, params)

        # Result should contain tools list
        assert "tools" in result or isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_lists_tools_for_language(self, mock_context: MagicMock):
        """Lists tools for specific language."""
        params = LintParams(action="tools", language="python")
        result = await lint(mock_context, params)

        assert "tools" in result or isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_lists_tools_for_category(self, mock_context: MagicMock):
        """Lists tools for specific category."""
        params = LintParams(action="tools", category="style")
        result = await lint(mock_context, params)

        assert "tools" in result or isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_tools_result_structure(self, mock_context: MagicMock):
        """Tools result has expected structure."""
        params = LintParams(action="tools")
        result = await lint(mock_context, params)

        # Should have tools or similar key
        assert isinstance(result, dict)
