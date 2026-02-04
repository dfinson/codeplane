"""Tests for MCP refactor tools.

Verifies parameter models and summary helpers.
"""

from unittest.mock import MagicMock

from codeplane.mcp.tools.refactor import (
    RefactorApplyParams,
    RefactorCancelParams,
    RefactorDeleteParams,
    RefactorInspectParams,
    RefactorMoveParams,
    RefactorRenameParams,
    _display_refactor,
    _summarize_refactor,
)


class TestRefactorRenameParams:
    """Tests for RefactorRenameParams model."""

    def test_minimal_params(self) -> None:
        """Should accept minimal params."""
        params = RefactorRenameParams(symbol="old_name", new_name="new_name")
        assert params.symbol == "old_name"
        assert params.new_name == "new_name"
        assert params.include_comments is True

    def test_all_params(self) -> None:
        """Should accept all params."""
        params = RefactorRenameParams(
            symbol="MyClass",
            new_name="BetterClass",
            include_comments=False,
            contexts=["ctx1", "ctx2"],
        )
        assert params.include_comments is False
        assert params.contexts == ["ctx1", "ctx2"]


class TestRefactorMoveParams:
    """Tests for RefactorMoveParams model."""

    def test_create_params(self) -> None:
        """Should create params."""
        params = RefactorMoveParams(
            from_path="src/old.py",
            to_path="src/new.py",
        )
        assert params.from_path == "src/old.py"
        assert params.to_path == "src/new.py"
        assert params.include_comments is True


class TestRefactorDeleteParams:
    """Tests for RefactorDeleteParams model."""

    def test_create_params(self) -> None:
        """Should create params."""
        params = RefactorDeleteParams(target="unused_function")
        assert params.target == "unused_function"
        assert params.include_comments is True


class TestRefactorApplyParams:
    """Tests for RefactorApplyParams model."""

    def test_create_params(self) -> None:
        """Should create params."""
        params = RefactorApplyParams(refactor_id="abc123")
        assert params.refactor_id == "abc123"


class TestRefactorCancelParams:
    """Tests for RefactorCancelParams model."""

    def test_create_params(self) -> None:
        """Should create params."""
        params = RefactorCancelParams(refactor_id="abc123")
        assert params.refactor_id == "abc123"


class TestRefactorInspectParams:
    """Tests for RefactorInspectParams model."""

    def test_create_params(self) -> None:
        """Should create params."""
        params = RefactorInspectParams(
            refactor_id="abc123",
            path="src/file.py",
        )
        assert params.refactor_id == "abc123"
        assert params.path == "src/file.py"
        assert params.context_lines == 2

    def test_custom_context_lines(self) -> None:
        """Should accept custom context_lines."""
        params = RefactorInspectParams(
            refactor_id="abc123",
            path="src/file.py",
            context_lines=5,
        )
        assert params.context_lines == 5


class TestSummarizeRefactor:
    """Tests for _summarize_refactor helper."""

    def test_cancelled_status(self) -> None:
        """Should handle cancelled."""
        summary = _summarize_refactor("cancelled", 0, None)
        assert "cancelled" in summary

    def test_applied_status(self) -> None:
        """Should handle applied."""
        summary = _summarize_refactor("applied", 5, None)
        assert "applied" in summary
        assert "5 files" in summary

    def test_pending_with_preview(self) -> None:
        """Should handle pending with preview."""
        preview = MagicMock()
        preview.high_certainty_count = 10
        preview.medium_certainty_count = 5
        preview.low_certainty_count = 2

        summary = _summarize_refactor("pending", 3, preview)
        assert "preview" in summary
        assert "17 changes" in summary  # 10+5+2
        assert "3 files" in summary
        assert "2 need review" in summary

    def test_pending_no_low_certainty(self) -> None:
        """Should not show review count when no low certainty."""
        preview = MagicMock()
        preview.high_certainty_count = 10
        preview.medium_certainty_count = 5
        preview.low_certainty_count = 0

        summary = _summarize_refactor("pending", 2, preview)
        assert "need review" not in summary


class TestDisplayRefactor:
    """Tests for _display_refactor helper."""

    def test_cancelled(self) -> None:
        """Should handle cancelled."""
        display = _display_refactor("cancelled", 0, None, "abc123")
        assert "cancelled" in display.lower()

    def test_applied(self) -> None:
        """Should handle applied."""
        display = _display_refactor("applied", 3, None, "abc123")
        assert "applied" in display.lower()
        assert "3 files" in display

    def test_pending_with_low_certainty(self) -> None:
        """Should mention review needed for low certainty."""
        preview = MagicMock()
        preview.high_certainty_count = 5
        preview.medium_certainty_count = 3
        preview.low_certainty_count = 2

        display = _display_refactor("pending", 2, preview, "abc123")
        assert "Preview ready" in display
        assert "2 require review" in display
        assert "abc123" in display

    def test_pending_all_high_certainty(self) -> None:
        """Should not mention review when all high certainty."""
        preview = MagicMock()
        preview.high_certainty_count = 10
        preview.medium_certainty_count = 0
        preview.low_certainty_count = 0

        display = _display_refactor("pending", 3, preview, "abc123")
        assert "Preview ready" in display
        assert "require review" not in display
