"""Tests for MCP refactor tools.

Verifies summary helpers and serialization.
"""

from unittest.mock import MagicMock

from codeplane.mcp.tools.refactor import (
    _display_refactor,
    _serialize_refactor_result,
    _summarize_refactor,
)


class TestSummarizeRefactor:
    """Tests for _summarize_refactor helper."""

    def test_cancelled(self) -> None:
        """Cancelled status."""
        result = _summarize_refactor(status="cancelled", files_affected=0, preview=None)
        assert "cancelled" in result

    def test_applied(self) -> None:
        """Applied status."""
        result = _summarize_refactor(status="applied", files_affected=5, preview=None)
        assert "applied" in result
        assert "5 files" in result

    def test_pending_preview(self) -> None:
        """Pending with preview."""
        preview = MagicMock()
        preview.high_certainty_count = 10
        preview.medium_certainty_count = 3
        preview.low_certainty_count = 2

        result = _summarize_refactor(status="pending", files_affected=4, preview=preview)
        assert "preview" in result
        assert "15 changes" in result  # 10 + 3 + 2
        assert "4 files" in result

    def test_pending_with_low_certainty(self) -> None:
        """Pending with low certainty matches."""
        preview = MagicMock()
        preview.high_certainty_count = 5
        preview.medium_certainty_count = 0
        preview.low_certainty_count = 3

        result = _summarize_refactor(status="pending", files_affected=2, preview=preview)
        assert "need review" in result
        assert "3" in result

    def test_unknown_status(self) -> None:
        """Unknown status returns status itself."""
        result = _summarize_refactor(status="unknown", files_affected=0, preview=None)
        assert result == "unknown"


class TestDisplayRefactor:
    """Tests for _display_refactor helper."""

    def test_cancelled_message(self) -> None:
        """Cancelled message."""
        result = _display_refactor(
            status="cancelled", files_affected=0, preview=None, refactor_id="abc123"
        )
        assert "cancelled" in result.lower()

    def test_applied_message(self) -> None:
        """Applied message."""
        result = _display_refactor(
            status="applied", files_affected=5, preview=None, refactor_id="abc123"
        )
        assert "applied" in result.lower()
        assert "5" in result

    def test_pending_message_with_id(self) -> None:
        """Pending shows refactor ID."""
        preview = MagicMock()
        preview.high_certainty_count = 10
        preview.medium_certainty_count = 0
        preview.low_certainty_count = 0

        result = _display_refactor(
            status="pending", files_affected=3, preview=preview, refactor_id="abc123"
        )
        assert "abc123" in result
        assert "preview" in result.lower() or "ready" in result.lower()

    def test_pending_with_review_needed(self) -> None:
        """Pending with low certainty shows review needed."""
        preview = MagicMock()
        preview.high_certainty_count = 5
        preview.medium_certainty_count = 0
        preview.low_certainty_count = 2

        result = _display_refactor(
            status="pending", files_affected=2, preview=preview, refactor_id="xyz789"
        )
        assert "review" in result.lower()


class TestSerializeRefactorResult:
    """Tests for _serialize_refactor_result helper."""

    def test_basic_result(self) -> None:
        """Basic result serialization."""
        result = MagicMock()
        result.refactor_id = "test-123"
        result.status = "pending"
        result.preview = None
        result.applied = None
        result.divergence = None

        output = _serialize_refactor_result(result)
        assert output["refactor_id"] == "test-123"
        assert output["status"] == "pending"
        assert "summary" in output
        assert "display_to_user" in output

    def test_with_preview(self) -> None:
        """Result with preview."""
        result = MagicMock()
        result.refactor_id = "test-456"
        result.status = "pending"
        result.applied = None
        result.divergence = None

        # Setup preview
        preview = MagicMock()
        preview.files_affected = 3
        preview.high_certainty_count = 10
        preview.medium_certainty_count = 2
        preview.low_certainty_count = 1
        preview.verification_required = False
        preview.edits = []
        result.preview = preview

        output = _serialize_refactor_result(result)
        assert "preview" in output
        assert output["preview"]["files_affected"] == 3
        assert output["preview"]["high_certainty_count"] == 10

    def test_with_applied_delta(self) -> None:
        """Result with applied delta."""
        result = MagicMock()
        result.refactor_id = "test-789"
        result.status = "applied"
        result.preview = None
        result.divergence = None

        applied = MagicMock()
        applied.files_changed = 5
        result.applied = applied

        output = _serialize_refactor_result(result)
        assert "5 files" in output["summary"]

    def test_with_divergence(self) -> None:
        """Result with divergence."""
        result = MagicMock()
        result.refactor_id = "test-div"
        result.status = "diverged"
        result.preview = None
        result.applied = None

        divergence = MagicMock()
        divergence.conflicting_hunks = 2
        divergence.resolution_options = ["abort", "force"]
        result.divergence = divergence

        output = _serialize_refactor_result(result)
        assert "divergence" in output
        assert output["divergence"]["conflicting_hunks"] == 2

    def test_preview_with_verification(self) -> None:
        """Preview requiring verification."""
        result = MagicMock()
        result.refactor_id = "test-verify"
        result.status = "pending"
        result.applied = None
        result.divergence = None

        preview = MagicMock()
        preview.files_affected = 2
        preview.high_certainty_count = 3
        preview.medium_certainty_count = 0
        preview.low_certainty_count = 2
        preview.verification_required = True
        preview.verification_guidance = "Review these files carefully"
        # Create a low-certainty hunk so low_certainty_matches is populated
        low_hunk = MagicMock()
        low_hunk.old = "old_text"
        low_hunk.new = "new_text"
        low_hunk.line = 10
        low_hunk.certainty = "low"
        file_edit = MagicMock()
        file_edit.path = "a.py"
        file_edit.hunks = [low_hunk]
        preview.edits = [file_edit]
        result.preview = preview

        output = _serialize_refactor_result(result)
        assert output["preview"]["verification_required"] is True
        assert len(output["preview"]["low_certainty_matches"]) == 1
        assert output["preview"]["low_certainty_matches"][0]["path"] == "a.py"
        assert output["preview"]["low_certainty_matches"][0]["certainty"] == "low"
