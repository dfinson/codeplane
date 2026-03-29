"""Tests for backend.services.tool_error_classifier — batch LLM classification."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.services.tool_error_classifier import (
    _parse_classifications,
    classify_tool_errors_batch,
)

# Patch targets — the imports are deferred inside the function, so we patch
# at the source module, not at the tool_error_classifier module.
_SPANS_REPO = "backend.persistence.telemetry_spans_repo.TelemetrySpansRepo"
_SUMMARY_REPO = "backend.persistence.telemetry_summary_repo.TelemetrySummaryRepo"


# ---------------------------------------------------------------------------
# _parse_classifications — pure function, easy to test exhaustively
# ---------------------------------------------------------------------------


class TestParseClassifications:
    """Test the JSON response parser."""

    def test_clean_json_array(self) -> None:
        raw = '["agent_error", "tool_error", "agent_error"]'
        assert _parse_classifications(raw, 3) == [
            "agent_error",
            "tool_error",
            "agent_error",
        ]

    def test_single_element(self) -> None:
        assert _parse_classifications('["tool_error"]', 1) == ["tool_error"]

    def test_markdown_fenced_json(self) -> None:
        raw = '```json\n["agent_error", "tool_error"]\n```'
        assert _parse_classifications(raw, 2) == ["agent_error", "tool_error"]

    def test_markdown_fenced_no_language(self) -> None:
        raw = '```\n["tool_error"]\n```'
        assert _parse_classifications(raw, 1) == ["tool_error"]

    def test_json_embedded_in_prose(self) -> None:
        raw = 'Here is the classification:\n["agent_error", "tool_error"]\nDone.'
        assert _parse_classifications(raw, 2) == ["agent_error", "tool_error"]

    def test_wrong_count_returns_none(self) -> None:
        raw = '["agent_error", "tool_error"]'
        assert _parse_classifications(raw, 3) is None

    def test_empty_array_matches_zero(self) -> None:
        assert _parse_classifications("[]", 0) == []

    def test_invalid_label_returns_none(self) -> None:
        raw = '["agent_error", "unknown_error"]'
        assert _parse_classifications(raw, 2) is None

    def test_totally_unparseable(self) -> None:
        assert _parse_classifications("I don't know", 1) is None

    def test_no_brackets_at_all(self) -> None:
        assert _parse_classifications("agent_error", 1) is None

    def test_whitespace_and_casing_tolerated(self) -> None:
        raw = '["Agent_Error", " tool_error "]'
        # Our parser lowercases and strips
        assert _parse_classifications(raw, 2) == ["agent_error", "tool_error"]

    def test_not_a_list(self) -> None:
        raw = '{"result": "agent_error"}'
        assert _parse_classifications(raw, 1) is None

    def test_nested_array_rejected(self) -> None:
        raw = '[["agent_error"]]'
        # Inner element is a list, str() → "['agent_error']" → not valid
        assert _parse_classifications(raw, 1) is None


# ---------------------------------------------------------------------------
# classify_tool_errors_batch — integration with mocked DB and LLM
# ---------------------------------------------------------------------------


def _make_mock_session():
    """Create a mock AsyncSession that can be used as a context manager."""
    return MagicMock()


class TestClassifyToolErrorsBatch:
    """Test the batch classification orchestrator."""

    @pytest.mark.asyncio
    async def test_no_errors_returns_zero(self) -> None:
        session = _make_mock_session()
        utility_complete = AsyncMock(return_value="should not be called")

        with patch(_SPANS_REPO) as mock_repo:
            mock_repo.return_value.get_unclassified_errors = AsyncMock(return_value=[])
            result = await classify_tool_errors_batch(session, "job-1", utility_complete)

        assert result == 0
        utility_complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_successful_classification(self) -> None:
        session = _make_mock_session()
        errors = [
            {"id": 10, "name": "replace_string_in_file", "error_text": "oldString not found in file"},
            {"id": 11, "name": "run_in_terminal", "error_text": "permission denied: /etc/shadow"},
        ]
        utility_complete = AsyncMock(return_value='["agent_error", "tool_error"]')

        with (
            patch(_SPANS_REPO) as mock_spans_repo,
            patch(_SUMMARY_REPO) as mock_summary_repo,
        ):
            mock_spans_repo.return_value.get_unclassified_errors = AsyncMock(return_value=errors)
            mock_spans_repo.return_value.batch_update_error_kind = AsyncMock()
            mock_summary_repo.return_value.increment = AsyncMock()

            result = await classify_tool_errors_batch(session, "job-1", utility_complete)

        assert result == 2
        # Verify the prompt was sent
        utility_complete.assert_called_once()
        prompt = utility_complete.call_args[0][0]
        assert "replace_string_in_file" in prompt
        assert "oldString not found" in prompt
        assert "permission denied" in prompt

        # Verify DB updates
        mock_spans_repo.return_value.batch_update_error_kind.assert_called_once_with(
            [(10, "agent_error"), (11, "tool_error")]
        )
        # Only 1 agent_error, so increment called with 1
        mock_summary_repo.return_value.increment.assert_called_once_with("job-1", agent_error_count=1)

    @pytest.mark.asyncio
    async def test_all_tool_errors_skips_summary_increment(self) -> None:
        session = _make_mock_session()
        errors = [
            {"id": 20, "name": "run_in_terminal", "error_text": "disk full"},
        ]
        utility_complete = AsyncMock(return_value='["tool_error"]')

        with (
            patch(_SPANS_REPO) as mock_spans_repo,
            patch(_SUMMARY_REPO) as mock_summary_repo,
        ):
            mock_spans_repo.return_value.get_unclassified_errors = AsyncMock(return_value=errors)
            mock_spans_repo.return_value.batch_update_error_kind = AsyncMock()

            result = await classify_tool_errors_batch(session, "job-1", utility_complete)

        assert result == 1
        # No agent errors → summary increment should NOT be called
        mock_summary_repo.return_value.increment.assert_not_called()

    @pytest.mark.asyncio
    async def test_llm_returns_empty_string(self) -> None:
        session = _make_mock_session()
        errors = [{"id": 30, "name": "read_file", "error_text": "some error"}]
        utility_complete = AsyncMock(return_value="")

        with patch(_SPANS_REPO) as mock_repo:
            mock_repo.return_value.get_unclassified_errors = AsyncMock(return_value=errors)
            mock_repo.return_value.batch_update_error_kind = AsyncMock()

            result = await classify_tool_errors_batch(session, "job-1", utility_complete)

        assert result == 0
        mock_repo.return_value.batch_update_error_kind.assert_not_called()

    @pytest.mark.asyncio
    async def test_llm_returns_none(self) -> None:
        session = _make_mock_session()
        errors = [{"id": 31, "name": "read_file", "error_text": "some error"}]
        utility_complete = AsyncMock(return_value=None)

        with patch(_SPANS_REPO) as mock_repo:
            mock_repo.return_value.get_unclassified_errors = AsyncMock(return_value=errors)
            mock_repo.return_value.batch_update_error_kind = AsyncMock()

            result = await classify_tool_errors_batch(session, "job-1", utility_complete)

        assert result == 0

    @pytest.mark.asyncio
    async def test_llm_raises_exception(self) -> None:
        session = _make_mock_session()
        errors = [{"id": 40, "name": "grep_search", "error_text": "error"}]
        utility_complete = AsyncMock(side_effect=TimeoutError("LLM timed out"))

        with patch(_SPANS_REPO) as mock_repo:
            mock_repo.return_value.get_unclassified_errors = AsyncMock(return_value=errors)
            mock_repo.return_value.batch_update_error_kind = AsyncMock()

            result = await classify_tool_errors_batch(session, "job-1", utility_complete)

        assert result == 0
        mock_repo.return_value.batch_update_error_kind.assert_not_called()

    @pytest.mark.asyncio
    async def test_llm_returns_garbage(self) -> None:
        session = _make_mock_session()
        errors = [
            {"id": 50, "name": "file_search", "error_text": "not found"},
            {"id": 51, "name": "run_in_terminal", "error_text": "OOM killed"},
        ]
        utility_complete = AsyncMock(return_value="I think error 1 is an agent error and error 2 is a tool error.")

        with patch(_SPANS_REPO) as mock_repo:
            mock_repo.return_value.get_unclassified_errors = AsyncMock(return_value=errors)
            mock_repo.return_value.batch_update_error_kind = AsyncMock()

            result = await classify_tool_errors_batch(session, "job-1", utility_complete)

        assert result == 0

    @pytest.mark.asyncio
    async def test_llm_returns_wrong_count(self) -> None:
        session = _make_mock_session()
        errors = [
            {"id": 60, "name": "read_file", "error_text": "error a"},
            {"id": 61, "name": "read_file", "error_text": "error b"},
        ]
        # LLM returns 3 items for 2 errors
        utility_complete = AsyncMock(return_value='["agent_error", "tool_error", "agent_error"]')

        with patch(_SPANS_REPO) as mock_repo:
            mock_repo.return_value.get_unclassified_errors = AsyncMock(return_value=errors)
            mock_repo.return_value.batch_update_error_kind = AsyncMock()

            result = await classify_tool_errors_batch(session, "job-1", utility_complete)

        assert result == 0

    @pytest.mark.asyncio
    async def test_error_text_truncated_in_prompt(self) -> None:
        """Long error text should be capped at 500 chars in the prompt."""
        session = _make_mock_session()
        long_error = "x" * 2000
        errors = [{"id": 70, "name": "run_in_terminal", "error_text": long_error}]
        utility_complete = AsyncMock(return_value='["agent_error"]')

        with (
            patch(_SPANS_REPO) as mock_spans_repo,
            patch(_SUMMARY_REPO) as mock_summary_repo,
        ):
            mock_spans_repo.return_value.get_unclassified_errors = AsyncMock(return_value=errors)
            mock_spans_repo.return_value.batch_update_error_kind = AsyncMock()
            mock_summary_repo.return_value.increment = AsyncMock()

            await classify_tool_errors_batch(session, "job-1", utility_complete)

        prompt = utility_complete.call_args[0][0]
        # The prompt should NOT contain the full 2000 chars
        assert "x" * 501 not in prompt
        # But should contain exactly 500
        assert "x" * 500 in prompt

    @pytest.mark.asyncio
    async def test_multiple_agent_errors_counted(self) -> None:
        session = _make_mock_session()
        errors = [
            {"id": 80, "name": "replace_string_in_file", "error_text": "oldString not found"},
            {"id": 81, "name": "create_file", "error_text": "file already exists"},
            {"id": 82, "name": "run_in_terminal", "error_text": "disk full"},
        ]
        utility_complete = AsyncMock(return_value='["agent_error", "agent_error", "tool_error"]')

        with (
            patch(_SPANS_REPO) as mock_spans_repo,
            patch(_SUMMARY_REPO) as mock_summary_repo,
        ):
            mock_spans_repo.return_value.get_unclassified_errors = AsyncMock(return_value=errors)
            mock_spans_repo.return_value.batch_update_error_kind = AsyncMock()
            mock_summary_repo.return_value.increment = AsyncMock()

            result = await classify_tool_errors_batch(session, "job-1", utility_complete)

        assert result == 3
        mock_summary_repo.return_value.increment.assert_called_once_with("job-1", agent_error_count=2)
