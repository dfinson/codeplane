"""Tests for MCP refactor tools."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from codeplane.mcp.tools.refactor import (
    RefactorApplyParams,
    RefactorCancelParams,
    RefactorDeleteParams,
    RefactorInspectParams,
    RefactorMoveParams,
    RefactorRenameParams,
    refactor_apply,
    refactor_cancel,
    refactor_delete,
    refactor_inspect,
    refactor_move,
    refactor_rename,
)


class TestRefactorRenameParams:
    """Tests for RefactorRenameParams model."""

    def test_required_fields(self):
        """symbol and new_name are required."""
        with pytest.raises(ValidationError):
            RefactorRenameParams()
        with pytest.raises(ValidationError):
            RefactorRenameParams(symbol="old_name")
        with pytest.raises(ValidationError):
            RefactorRenameParams(new_name="new_name")

    def test_valid_params(self):
        """Accepts valid parameters."""
        params = RefactorRenameParams(symbol="old_func", new_name="new_func")
        assert params.symbol == "old_func"
        assert params.new_name == "new_func"

    def test_include_comments_default(self):
        """include_comments defaults to True."""
        params = RefactorRenameParams(symbol="x", new_name="y")
        assert params.include_comments is True

    def test_contexts_optional(self):
        """contexts is optional."""
        params = RefactorRenameParams(symbol="x", new_name="y")
        assert params.contexts is None


class TestRefactorMoveParams:
    """Tests for RefactorMoveParams model."""

    def test_required_fields(self):
        """from_path and to_path are required."""
        with pytest.raises(ValidationError):
            RefactorMoveParams()
        with pytest.raises(ValidationError):
            RefactorMoveParams(from_path="src/old.py")
        with pytest.raises(ValidationError):
            RefactorMoveParams(to_path="src/new.py")

    def test_valid_params(self):
        """Accepts valid parameters."""
        params = RefactorMoveParams(from_path="src/old.py", to_path="src/new.py")
        assert params.from_path == "src/old.py"
        assert params.to_path == "src/new.py"

    def test_include_comments_default(self):
        """include_comments defaults to True."""
        params = RefactorMoveParams(from_path="a.py", to_path="b.py")
        assert params.include_comments is True


class TestRefactorDeleteParams:
    """Tests for RefactorDeleteParams model."""

    def test_target_required(self):
        """target is required."""
        with pytest.raises(ValidationError):
            RefactorDeleteParams()

    def test_valid_params(self):
        """Accepts valid parameters."""
        params = RefactorDeleteParams(target="unused_func")
        assert params.target == "unused_func"

    def test_include_comments_default(self):
        """include_comments defaults to True."""
        params = RefactorDeleteParams(target="x")
        assert params.include_comments is True


class TestRefactorApplyParams:
    """Tests for RefactorApplyParams model."""

    def test_refactor_id_required(self):
        """refactor_id is required."""
        with pytest.raises(ValidationError):
            RefactorApplyParams()

    def test_valid_params(self):
        """Accepts valid parameters."""
        params = RefactorApplyParams(refactor_id="ref_123")
        assert params.refactor_id == "ref_123"


class TestRefactorCancelParams:
    """Tests for RefactorCancelParams model."""

    def test_refactor_id_required(self):
        """refactor_id is required."""
        with pytest.raises(ValidationError):
            RefactorCancelParams()

    def test_valid_params(self):
        """Accepts valid parameters."""
        params = RefactorCancelParams(refactor_id="ref_456")
        assert params.refactor_id == "ref_456"


class TestRefactorInspectParams:
    """Tests for RefactorInspectParams model."""

    def test_required_fields(self):
        """refactor_id and path are required."""
        with pytest.raises(ValidationError):
            RefactorInspectParams()
        with pytest.raises(ValidationError):
            RefactorInspectParams(refactor_id="ref_123")
        with pytest.raises(ValidationError):
            RefactorInspectParams(path="file.py")

    def test_valid_params(self):
        """Accepts valid parameters."""
        params = RefactorInspectParams(refactor_id="ref_123", path="src/module.py")
        assert params.refactor_id == "ref_123"
        assert params.path == "src/module.py"

    def test_context_lines_default(self):
        """context_lines defaults to 2."""
        params = RefactorInspectParams(refactor_id="ref", path="f.py")
        assert params.context_lines == 2

    def test_context_lines_custom(self):
        """context_lines can be customized."""
        params = RefactorInspectParams(refactor_id="ref", path="f.py", context_lines=5)
        assert params.context_lines == 5


class TestRefactorRenameHandler:
    """Tests for refactor_rename handler."""

    @pytest.mark.asyncio
    async def test_creates_rename_preview(self, mock_context: MagicMock):
        """Creates rename refactoring preview."""
        params = RefactorRenameParams(symbol="old_name", new_name="new_name")
        await refactor_rename(mock_context, params)

        mock_context.refactor_ops.rename.assert_called_once()

    @pytest.mark.asyncio
    async def test_rename_result_has_refactor_id(self, mock_context: MagicMock):
        """Result includes refactor_id."""
        params = RefactorRenameParams(symbol="x", new_name="y")
        result = await refactor_rename(mock_context, params)

        assert "refactor_id" in result


class TestRefactorMoveHandler:
    """Tests for refactor_move handler."""

    @pytest.mark.asyncio
    async def test_creates_move_preview(self, mock_context: MagicMock):
        """Creates move refactoring preview."""
        params = RefactorMoveParams(from_path="src/old.py", to_path="src/new.py")
        await refactor_move(mock_context, params)

        mock_context.refactor_ops.move.assert_called_once()

    @pytest.mark.asyncio
    async def test_move_result_has_refactor_id(self, mock_context: MagicMock):
        """Result includes refactor_id."""
        params = RefactorMoveParams(from_path="a.py", to_path="b.py")
        result = await refactor_move(mock_context, params)

        assert "refactor_id" in result


class TestRefactorDeleteHandler:
    """Tests for refactor_delete handler."""

    @pytest.mark.asyncio
    async def test_finds_references(self, mock_context: MagicMock):
        """Finds references for deletion."""
        params = RefactorDeleteParams(target="unused_func")
        await refactor_delete(mock_context, params)

        mock_context.refactor_ops.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_result_has_refactor_id(self, mock_context: MagicMock):
        """Result includes refactor_id."""
        params = RefactorDeleteParams(target="x")
        result = await refactor_delete(mock_context, params)

        assert "refactor_id" in result


class TestRefactorApplyHandler:
    """Tests for refactor_apply handler."""

    @pytest.mark.asyncio
    async def test_applies_refactoring(self, mock_context: MagicMock):
        """Applies pending refactoring."""
        params = RefactorApplyParams(refactor_id="ref_123")
        await refactor_apply(mock_context, params)

        mock_context.refactor_ops.apply.assert_called_once()

    @pytest.mark.asyncio
    async def test_apply_returns_status(self, mock_context: MagicMock):
        """Apply returns status."""
        mock_context.refactor_ops.apply = AsyncMock(
            return_value=MagicMock(
                refactor_id="ref_123",
                status="applied",
                preview=None,
                divergence=None,
            )
        )

        params = RefactorApplyParams(refactor_id="ref_123")
        result = await refactor_apply(mock_context, params)

        assert result["status"] == "applied"


class TestRefactorCancelHandler:
    """Tests for refactor_cancel handler."""

    @pytest.mark.asyncio
    async def test_cancels_refactoring(self, mock_context: MagicMock):
        """Cancels pending refactoring."""
        params = RefactorCancelParams(refactor_id="ref_456")
        await refactor_cancel(mock_context, params)

        mock_context.refactor_ops.cancel.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancel_returns_status(self, mock_context: MagicMock):
        """Cancel returns status."""
        params = RefactorCancelParams(refactor_id="ref_456")
        result = await refactor_cancel(mock_context, params)

        assert result["status"] == "cancelled"


class TestRefactorInspectHandler:
    """Tests for refactor_inspect handler."""

    @pytest.mark.asyncio
    async def test_inspects_matches(self, mock_context: MagicMock):
        """Inspects low-certainty matches."""
        mock_context.refactor_ops.inspect = AsyncMock(
            return_value=MagicMock(
                path="test.py",
                matches=[],
            )
        )

        params = RefactorInspectParams(refactor_id="ref_789", path="test.py")
        await refactor_inspect(mock_context, params)

        mock_context.refactor_ops.inspect.assert_called_once()

    @pytest.mark.asyncio
    async def test_inspect_with_context_lines(self, mock_context: MagicMock):
        """Inspect uses context_lines parameter."""
        mock_context.refactor_ops.inspect = AsyncMock(
            return_value=MagicMock(path="f.py", matches=[])
        )

        params = RefactorInspectParams(refactor_id="ref", path="f.py", context_lines=5)
        await refactor_inspect(mock_context, params)

        mock_context.refactor_ops.inspect.assert_called_once()
