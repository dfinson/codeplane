"""Tests for backend.services.summarization_service — session summarization."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.models.events import DomainEvent, DomainEventKind
from backend.services.summarization_service import (
    SummarizationService,
    _build_resume_prompt,
    _clean_transcript,
    _extract_changed_files,
    _extract_json,
    _format_transcript,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _transcript_event(role: str, content: str, timestamp: str = "2024-01-01T00:00:00Z") -> DomainEvent:
    """Create a transcript_updated DomainEvent for testing."""
    return DomainEvent(
        event_id="evt-test",
        job_id="job-1",
        timestamp=datetime.now(UTC),
        kind=DomainEventKind.transcript_updated,
        payload={"role": role, "content": content, "timestamp": timestamp},
    )


def _diff_event(changed_files: list[dict]) -> DomainEvent:
    """Create a diff_updated DomainEvent for testing."""
    return DomainEvent(
        event_id="evt-diff",
        job_id="job-1",
        timestamp=datetime.now(UTC),
        kind=DomainEventKind.diff_updated,
        payload={"changed_files": changed_files},
    )


def _valid_summary_json() -> str:
    """Return a minimal valid summary JSON string."""
    return json.dumps(
        {
            "original_task": "Fix the bug",
            "session_number": 1,
            "accomplished": [],
            "file_states": [],
            "decisions": [],
            "operator_instructions": [],
            "in_progress": None,
            "resume_instructions": "Continue fixing.",
            "blockers_and_open_questions": None,
            "verification_state": {
                "tests_run": False,
                "tests_passed": None,
                "build_run": False,
                "build_passed": None,
                "notes": None,
            },
        }
    )


# ---------------------------------------------------------------------------
# _clean_transcript
# ---------------------------------------------------------------------------


class TestCleanTranscript:
    """Filtering and deduplication of transcript events."""

    def test_keeps_agent_and_operator_turns(self) -> None:
        events = [
            _transcript_event("agent", "Hello"),
            _transcript_event("operator", "Do this"),
        ]
        result = _clean_transcript(events)
        assert len(result) == 2
        assert result[0]["role"] == "agent"
        assert result[1]["role"] == "operator"

    def test_normalises_user_to_operator(self) -> None:
        events = [_transcript_event("user", "I want this")]
        result = _clean_transcript(events)
        assert len(result) == 1
        assert result[0]["role"] == "operator"
        assert result[0]["content"] == "I want this"

    def test_skips_empty_content(self) -> None:
        events = [
            _transcript_event("agent", ""),
            _transcript_event("agent", "   "),
            _transcript_event("agent", "valid content"),
        ]
        result = _clean_transcript(events)
        assert len(result) == 1
        assert result[0]["content"] == "valid content"

    def test_skips_non_agent_operator_roles(self) -> None:
        events = [
            _transcript_event("tool", "tool scaffolding"),
            _transcript_event("system", "internal"),
            _transcript_event("agent", "real turn"),
        ]
        result = _clean_transcript(events)
        assert len(result) == 1

    def test_skips_consecutive_duplicates(self) -> None:
        events = [
            _transcript_event("agent", "same thing"),
            _transcript_event("agent", "same thing"),
            _transcript_event("agent", "different"),
        ]
        result = _clean_transcript(events)
        assert len(result) == 2
        assert result[0]["content"] == "same thing"
        assert result[1]["content"] == "different"

    def test_skips_global_duplicates(self) -> None:
        events = [
            _transcript_event("agent", "hello"),
            _transcript_event("operator", "do it"),
            _transcript_event("agent", "hello"),  # global dup
        ]
        result = _clean_transcript(events)
        assert len(result) == 2

    def test_empty_events_list(self) -> None:
        result = _clean_transcript([])
        assert result == []

    def test_preserves_timestamp(self) -> None:
        events = [_transcript_event("agent", "content", timestamp="2024-06-15T10:00:00Z")]
        result = _clean_transcript(events)
        assert result[0]["timestamp"] == "2024-06-15T10:00:00Z"

    def test_content_is_stripped(self) -> None:
        ev = _transcript_event("agent", "  spaced content  ")
        result = _clean_transcript([ev])
        assert result[0]["content"] == "spaced content"

    def test_none_content_treated_as_empty(self) -> None:
        ev = DomainEvent(
            event_id="evt-test",
            job_id="job-1",
            timestamp=datetime.now(UTC),
            kind=DomainEventKind.transcript_updated,
            payload={"role": "agent", "content": None},
        )
        result = _clean_transcript([ev])
        assert result == []

    def test_missing_role_skipped(self) -> None:
        ev = DomainEvent(
            event_id="evt-test",
            job_id="job-1",
            timestamp=datetime.now(UTC),
            kind=DomainEventKind.transcript_updated,
            payload={"content": "no role here"},
        )
        result = _clean_transcript([ev])
        assert result == []

    def test_same_content_different_roles_not_deduplicated(self) -> None:
        events = [
            _transcript_event("agent", "hello"),
            _transcript_event("operator", "hello"),
        ]
        result = _clean_transcript(events)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# _format_transcript
# ---------------------------------------------------------------------------


class TestFormatTranscript:
    """Transcript formatting into numbered text."""

    def test_formats_multiple_turns(self) -> None:
        turns = [
            {"role": "operator", "content": "Fix the bug", "timestamp": ""},
            {"role": "agent", "content": "Done", "timestamp": ""},
        ]
        result = _format_transcript(turns)
        assert "[1] OPERATOR: Fix the bug" in result
        assert "[2] AGENT: Done" in result
        assert "\n---\n" in result

    def test_empty_turns(self) -> None:
        assert _format_transcript([]) == "(no transcript recorded)"

    def test_single_turn(self) -> None:
        turns = [{"role": "agent", "content": "hello", "timestamp": ""}]
        result = _format_transcript(turns)
        assert result == "[1] AGENT: hello"
        assert "---" not in result


# ---------------------------------------------------------------------------
# _extract_changed_files
# ---------------------------------------------------------------------------


class TestExtractChangedFiles:
    """Extract file paths from diff_updated events."""

    def test_extracts_paths(self) -> None:
        events = [
            _diff_event([{"path": "src/a.py"}, {"path": "src/b.py"}]),
        ]
        result = _extract_changed_files(events)
        assert result == ["src/a.py", "src/b.py"]

    def test_deduplicates_paths(self) -> None:
        events = [
            _diff_event([{"path": "src/a.py"}]),
            _diff_event([{"path": "src/a.py"}]),
        ]
        result = _extract_changed_files(events)
        assert result == ["src/a.py"]

    def test_uses_new_path_fallback(self) -> None:
        events = [_diff_event([{"new_path": "renamed.py"}])]
        result = _extract_changed_files(events)
        assert result == ["renamed.py"]

    def test_skips_empty_paths(self) -> None:
        events = [_diff_event([{"path": ""}, {"path": "valid.py"}])]
        result = _extract_changed_files(events)
        assert result == ["valid.py"]

    def test_skips_missing_path_keys(self) -> None:
        events = [_diff_event([{"something_else": "x"}])]
        result = _extract_changed_files(events)
        assert result == []

    def test_empty_events(self) -> None:
        assert _extract_changed_files([]) == []

    def test_sorted_output(self) -> None:
        events = [_diff_event([{"path": "z.py"}, {"path": "a.py"}, {"path": "m.py"}])]
        result = _extract_changed_files(events)
        assert result == ["a.py", "m.py", "z.py"]


# ---------------------------------------------------------------------------
# _extract_json
# ---------------------------------------------------------------------------


class TestExtractJson:
    """JSON extraction from LLM responses."""

    def test_valid_json_returned_as_is(self) -> None:
        raw = '{"key": "value"}'
        result = _extract_json(raw, "job-1", "task", 1)
        assert json.loads(result) == {"key": "value"}

    def test_strips_markdown_fences(self) -> None:
        raw = '```json\n{"key": "value"}\n```'
        result = _extract_json(raw, "job-1", "task", 1)
        assert json.loads(result) == {"key": "value"}

    def test_strips_plain_fences(self) -> None:
        raw = '```\n{"key": "value"}\n```'
        result = _extract_json(raw, "job-1", "task", 1)
        assert json.loads(result) == {"key": "value"}

    def test_extracts_json_from_surrounding_text(self) -> None:
        raw = 'Here is the summary:\n{"key": "value"}\nThat\'s all.'
        result = _extract_json(raw, "job-1", "task", 1)
        assert json.loads(result) == {"key": "value"}

    def test_fallback_on_invalid_json(self) -> None:
        raw = "this is not json at all"
        result = _extract_json(raw, "job-1", "task", 1)
        parsed = json.loads(result)
        assert parsed["summarized"] is False
        assert parsed["original_task"] == "task"
        assert parsed["session_number"] == 1
        assert "this is not json at all" in parsed["raw_response"]

    def test_fallback_preserves_raw_response(self) -> None:
        raw = "random text output"
        result = _extract_json(raw, "job-1", "original task text", 2)
        parsed = json.loads(result)
        assert parsed["original_task"] == "original task text"
        assert parsed["session_number"] == 2
        assert parsed["raw_response"] == "random text output"

    def test_fallback_caps_raw_response_length(self) -> None:
        raw = "x" * 100_000
        result = _extract_json(raw, "job-1", "task", 1)
        parsed = json.loads(result)
        assert len(parsed["raw_response"]) == 50_000

    def test_fallback_includes_verification_state(self) -> None:
        raw = "not json"
        result = _extract_json(raw, "job-1", "task", 1)
        parsed = json.loads(result)
        vs = parsed["verification_state"]
        assert vs["tests_run"] is False
        assert vs["build_run"] is False
        assert "failed" in vs["notes"].lower()

    def test_whitespace_stripped_before_parse(self) -> None:
        raw = '  \n  {"key": "value"}  \n  '
        result = _extract_json(raw, "job-1", "task", 1)
        assert json.loads(result) == {"key": "value"}

    def test_handles_nested_json(self) -> None:
        obj = {"outer": {"inner": [1, 2, 3]}}
        raw = json.dumps(obj)
        result = _extract_json(raw, "job-1", "task", 1)
        assert json.loads(result) == obj

    def test_invalid_json_inside_braces_triggers_fallback(self) -> None:
        raw = "Some text {not: valid json, missing quotes} end"
        result = _extract_json(raw, "job-1", "task", 1)
        parsed = json.loads(result)
        assert parsed["summarized"] is False

    def test_empty_string_triggers_fallback(self) -> None:
        result = _extract_json("", "job-1", "task", 1)
        parsed = json.loads(result)
        assert parsed["summarized"] is False


# ---------------------------------------------------------------------------
# _build_resume_prompt
# ---------------------------------------------------------------------------


class TestBuildResumePrompt:
    """Resume prompt construction for session N+1."""

    def test_includes_all_sections(self) -> None:
        result = _build_resume_prompt(
            summary_text="Summary here",
            changed_files=["src/a.py", "src/b.py"],
            instruction="Continue the work",
            session_number=2,
            job_id="job-1",
            original_task="Fix the bug",
        )
        assert "RESUMED SESSION" in result
        assert "session 2" in result
        assert "job-1" in result
        assert "Fix the bug" in result
        assert "Summary here" in result
        assert "src/a.py" in result
        assert "src/b.py" in result
        assert "Continue the work" in result

    def test_no_summary_uses_fallback(self) -> None:
        result = _build_resume_prompt(
            summary_text=None,
            changed_files=[],
            instruction="do stuff",
            session_number=1,
            job_id="job-1",
            original_task="task",
        )
        assert "no summary available" in result

    def test_no_changed_files_shows_none(self) -> None:
        result = _build_resume_prompt(
            summary_text="summary",
            changed_files=[],
            instruction="do stuff",
            session_number=1,
            job_id="job-1",
            original_task="task",
        )
        assert "no file changes recorded" in result

    def test_includes_instructions_footer(self) -> None:
        result = _build_resume_prompt(
            summary_text="s",
            changed_files=[],
            instruction="inst",
            session_number=1,
            job_id="j",
            original_task="t",
        )
        assert "Do not re-describe the summary" in result
        assert "Act on the instruction directly" in result


# ---------------------------------------------------------------------------
# SummarizationService.summarize_and_store
# ---------------------------------------------------------------------------


class _FakeAsyncCtx:
    """Async context manager that yields a mock db session."""

    def __init__(self, mock_db_session: AsyncMock) -> None:
        self._s = mock_db_session

    async def __aenter__(self) -> AsyncMock:
        return self._s

    async def __aexit__(self, *args: object) -> None:
        pass


class TestSummarizeAndStore:
    """Integration-style tests for the main orchestration method."""

    def _make_service(self, adapter_response: str = "") -> tuple[SummarizationService, AsyncMock, AsyncMock]:
        adapter = AsyncMock()
        adapter.complete = AsyncMock(return_value=adapter_response)

        mock_db = AsyncMock()
        mock_db.commit = AsyncMock()
        session_factory = MagicMock(return_value=_FakeAsyncCtx(mock_db))
        svc = SummarizationService(session_factory=session_factory, adapter=adapter)
        return svc, adapter, mock_db

    @pytest.mark.asyncio
    async def test_with_pre_built_data(self) -> None:
        """When pre-built transcript & files are provided, skips event store."""
        valid_json = _valid_summary_json()
        svc, adapter, mock_db = self._make_service(adapter_response=valid_json)

        mock_artifact_svc = AsyncMock()
        mock_artifact_svc.store_session_summary = AsyncMock()

        with (
            patch("backend.persistence.event_repo.EventRepository"),
            patch("backend.persistence.artifact_repo.ArtifactRepository"),
            patch("backend.services.artifact_service.ArtifactService", return_value=mock_artifact_svc),
        ):
            result = await svc.summarize_and_store(
                job_id="job-1",
                session_number=1,
                original_task="Fix the bug",
                pre_built_transcript="[1] AGENT: Did the fix",
                pre_built_changed_files=["src/fix.py"],
            )

        parsed = json.loads(result)
        assert parsed["original_task"] == "Fix the bug"
        adapter.complete.assert_awaited_once()
        mock_artifact_svc.store_session_summary.assert_awaited_once_with("job-1", 1, result)
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reads_events_when_no_pre_built_data(self) -> None:
        """When no pre-built data, reads from EventRepository."""
        valid_json = _valid_summary_json()
        svc, adapter, mock_db = self._make_service(adapter_response=valid_json)

        transcript_events = [
            _transcript_event("agent", "I fixed the bug"),
            _transcript_event("operator", "Thanks"),
        ]
        diff_events = [
            _diff_event([{"path": "src/a.py"}]),
        ]

        mock_event_repo = AsyncMock()
        mock_event_repo.list_by_job = AsyncMock(side_effect=[transcript_events, diff_events])

        mock_artifact_svc = AsyncMock()
        mock_artifact_svc.store_session_summary = AsyncMock()

        with (
            patch("backend.persistence.event_repo.EventRepository", return_value=mock_event_repo),
            patch("backend.persistence.artifact_repo.ArtifactRepository"),
            patch("backend.services.artifact_service.ArtifactService", return_value=mock_artifact_svc),
        ):
            await svc.summarize_and_store(
                job_id="job-1",
                session_number=1,
                original_task="Fix the bug",
            )

        assert mock_event_repo.list_by_job.await_count == 2
        first_call = mock_event_repo.list_by_job.call_args_list[0]
        assert first_call[0][0] == "job-1"
        assert DomainEventKind.transcript_updated in first_call[1]["kinds"]
        second_call = mock_event_repo.list_by_job.call_args_list[1]
        assert DomainEventKind.diff_updated in second_call[1]["kinds"]

    @pytest.mark.asyncio
    async def test_adapter_receives_formatted_prompt(self) -> None:
        """Verify the prompt sent to the adapter contains transcript and task."""
        valid_json = _valid_summary_json()
        svc, adapter, _ = self._make_service(adapter_response=valid_json)

        with (
            patch("backend.persistence.event_repo.EventRepository"),
            patch("backend.persistence.artifact_repo.ArtifactRepository"),
            patch("backend.services.artifact_service.ArtifactService", return_value=AsyncMock()),
        ):
            await svc.summarize_and_store(
                job_id="job-1",
                session_number=3,
                original_task="Build the feature",
                pre_built_transcript="transcript text here",
                pre_built_changed_files=["file.py"],
            )

        prompt = adapter.complete.call_args[0][0]
        assert "transcript text here" in prompt
        assert "Build the feature" in prompt
        assert "file.py" in prompt
        assert "SESSION NUMBER: 3" in prompt

    @pytest.mark.asyncio
    async def test_none_changed_files_shows_none_recorded(self) -> None:
        """When no changed files, prompt shows 'None recorded'."""
        valid_json = _valid_summary_json()
        svc, adapter, _ = self._make_service(adapter_response=valid_json)

        with (
            patch("backend.persistence.event_repo.EventRepository"),
            patch("backend.persistence.artifact_repo.ArtifactRepository"),
            patch("backend.services.artifact_service.ArtifactService", return_value=AsyncMock()),
        ):
            await svc.summarize_and_store(
                job_id="job-1",
                session_number=1,
                original_task="task",
                pre_built_transcript="text",
                pre_built_changed_files=[],
            )

        prompt = adapter.complete.call_args[0][0]
        assert "None recorded" in prompt

    @pytest.mark.asyncio
    async def test_llm_returns_garbage_gets_fallback(self) -> None:
        """When LLM returns invalid JSON, fallback summary is stored."""
        svc, adapter, _ = self._make_service(adapter_response="I don't know how to JSON")

        mock_artifact_svc = AsyncMock()
        mock_artifact_svc.store_session_summary = AsyncMock()

        with (
            patch("backend.persistence.event_repo.EventRepository"),
            patch("backend.persistence.artifact_repo.ArtifactRepository"),
            patch("backend.services.artifact_service.ArtifactService", return_value=mock_artifact_svc),
        ):
            result = await svc.summarize_and_store(
                job_id="job-1",
                session_number=1,
                original_task="task",
                pre_built_transcript="text",
                pre_built_changed_files=[],
            )

        parsed = json.loads(result)
        assert parsed["summarized"] is False
        assert "I don't know how to JSON" in parsed["raw_response"]
        mock_artifact_svc.store_session_summary.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_json_string(self) -> None:
        """Result should always be a valid JSON string."""
        valid_json = _valid_summary_json()
        svc, _, _ = self._make_service(adapter_response=valid_json)

        with (
            patch("backend.persistence.event_repo.EventRepository"),
            patch("backend.persistence.artifact_repo.ArtifactRepository"),
            patch("backend.services.artifact_service.ArtifactService", return_value=AsyncMock()),
        ):
            result = await svc.summarize_and_store(
                job_id="job-1",
                session_number=1,
                original_task="task",
                pre_built_transcript="text",
                pre_built_changed_files=[],
            )

        json.loads(result)
