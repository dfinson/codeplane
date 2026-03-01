"""Tests for refactor_edit tool (edit.py).

Covers:
- FindReplaceEdit model validation
- _find_all_occurrences
- _offset_to_line
- _fuzzy_find
- _resolve_edit (exact, disambiguated, fuzzy, ambiguous, no-match)
- _summarize_edit
- refactor_edit handler integration tests
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastmcp import FastMCP

from codeplane.mcp._compat import get_tools_sync
from codeplane.mcp.errors import MCPError, MCPErrorCode
from codeplane.mcp.tools.edit import (
    FindReplaceEdit,
    _find_all_occurrences,
    _fuzzy_find,
    _offset_to_line,
    _resolve_edit,
    _summarize_edit,
    register_tools,
)

# =============================================================================
# FindReplaceEdit Model
# =============================================================================


class TestFindReplaceEdit:
    """Tests for FindReplaceEdit Pydantic model."""

    def test_minimal_update(self) -> None:
        """Minimal update edit."""
        e = FindReplaceEdit(
            path="foo.py",
            old_content="hello",
            new_content="world",
        )
        assert e.path == "foo.py"
        assert e.old_content == "hello"
        assert e.new_content == "world"
        assert e.delete is False
        assert e.expected_file_sha256 is None
        assert e.start_line is None
        assert e.end_line is None

    def test_create_edit(self) -> None:
        """File creation edit (old_content=None)."""
        e = FindReplaceEdit(
            path="new.py",
            old_content=None,
            new_content="print('hi')\n",
        )
        assert e.old_content is None
        assert e.new_content == "print('hi')\n"

    def test_delete_edit(self) -> None:
        """File deletion edit."""
        e = FindReplaceEdit(
            path="dead.py",
            old_content=None,
            new_content=None,
            delete=True,
        )
        assert e.delete is True

    def test_with_sha(self) -> None:
        """Edit with sha256 for staleness check."""
        e = FindReplaceEdit(
            path="bar.py",
            old_content="a",
            new_content="b",
            expected_file_sha256="abc123",
        )
        assert e.expected_file_sha256 == "abc123"

    def test_with_span_hints(self) -> None:
        """Edit with line hints for disambiguation."""
        e = FindReplaceEdit(
            path="baz.py",
            old_content="x",
            new_content="y",
            start_line=10,
            end_line=20,
        )
        assert e.start_line == 10
        assert e.end_line == 20

    def test_extra_fields_forbidden(self) -> None:
        """Extra fields raise validation error."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="extra"):
            FindReplaceEdit(
                path="foo.py",
                old_content="a",
                new_content="b",
                bogus_field="nope",  # type: ignore[call-arg]
            )


# =============================================================================
# _find_all_occurrences
# =============================================================================


class TestFindAllOccurrences:
    """Tests for _find_all_occurrences helper."""

    def test_no_match(self) -> None:
        assert _find_all_occurrences("hello world", "xyz") == []

    def test_single_match(self) -> None:
        positions = _find_all_occurrences("hello world", "world")
        assert positions == [6]

    def test_multiple_matches(self) -> None:
        positions = _find_all_occurrences("abcabc", "abc")
        assert positions == [0, 3]

    def test_overlapping_matches(self) -> None:
        positions = _find_all_occurrences("aaa", "aa")
        assert positions == [0, 1]

    def test_empty_content(self) -> None:
        assert _find_all_occurrences("", "foo") == []

    def test_empty_needle(self) -> None:
        # Empty string matches at every position
        positions = _find_all_occurrences("ab", "")
        assert len(positions) == 3  # positions 0, 1, 2

    def test_multiline(self) -> None:
        content = "line1\nline2\nline1\n"
        positions = _find_all_occurrences(content, "line1")
        assert len(positions) == 2


# =============================================================================
# _offset_to_line
# =============================================================================


class TestOffsetToLine:
    """Tests for _offset_to_line helper."""

    def test_first_line(self) -> None:
        assert _offset_to_line("hello\nworld", 0) == 1

    def test_second_line(self) -> None:
        assert _offset_to_line("hello\nworld", 6) == 2

    def test_third_line(self) -> None:
        content = "a\nb\nc\n"
        assert _offset_to_line(content, 4) == 3

    def test_offset_at_newline(self) -> None:
        assert _offset_to_line("hello\nworld", 5) == 1


# =============================================================================
# _fuzzy_find
# =============================================================================


class TestFuzzyFind:
    """Tests for _fuzzy_find whitespace-normalized search."""

    def test_exact_match_also_fuzzy(self) -> None:
        positions = _fuzzy_find("hello world", "hello world")
        assert len(positions) == 1

    def test_extra_whitespace(self) -> None:
        positions = _fuzzy_find("hello    world", "hello world")
        assert len(positions) == 1

    def test_no_match(self) -> None:
        positions = _fuzzy_find("hello world", "goodbye")
        assert len(positions) == 0

    def test_multiple_fuzzy_matches(self) -> None:
        content = "foo  bar baz foo bar baz"
        positions = _fuzzy_find(content, "foo bar")
        assert len(positions) == 2

    def test_empty_needle(self) -> None:
        positions = _fuzzy_find("some content", "   ")
        assert positions == []


# =============================================================================
# _resolve_edit
# =============================================================================


class TestResolveEdit:
    """Tests for _resolve_edit — the core resolution logic."""

    def test_exact_single_match(self) -> None:
        """Single exact match → replace in place."""
        content = "hello world"
        result, meta = _resolve_edit(content, "world", "planet")
        assert result == "hello planet"
        assert meta["match_kind"] == "exact"
        assert meta["match_line"] == 1

    def test_exact_multiline(self) -> None:
        """Exact match spanning multiple lines."""
        content = "line1\nline2\nline3\n"
        result, meta = _resolve_edit(content, "line2\nline3", "replaced")
        assert result == "line1\nreplaced\n"
        assert meta["match_kind"] == "exact"

    def test_exact_disambiguated_by_span(self) -> None:
        """Multiple exact matches + start_line hint selects correct one."""
        content = "foo\nbar\nfoo\nbaz\n"
        result, meta = _resolve_edit(content, "foo", "qux", start_line=3)
        assert result == "foo\nbar\nqux\nbaz\n"
        assert meta["match_kind"] == "exact_span_disambiguated"
        assert meta["match_line"] == 3

    def test_exact_disambiguated_first_occurrence(self) -> None:
        """Span hint selects first occurrence."""
        content = "foo\nbar\nfoo\nbaz\n"
        result, meta = _resolve_edit(content, "foo", "qux", start_line=1)
        assert result == "qux\nbar\nfoo\nbaz\n"
        assert meta["match_line"] == 1

    def test_ambiguous_no_span(self) -> None:
        """Multiple matches + no span hint → AMBIGUOUS_MATCH error."""
        content = "foo\nbar\nfoo\n"
        with pytest.raises(MCPError) as exc_info:
            _resolve_edit(content, "foo", "qux")
        assert exc_info.value.code == MCPErrorCode.AMBIGUOUS_MATCH

    def test_fuzzy_whitespace_match(self) -> None:
        """No exact match, but fuzzy whitespace-normalized match exists."""
        content = "def  foo(  x ):\n    pass\n"
        result, meta = _resolve_edit(content, "def foo( x ):\n    pass", "def bar():\n    pass")
        assert "bar" in result
        assert meta["match_kind"] == "fuzzy_whitespace"

    def test_multiple_fuzzy_matches_error(self) -> None:
        """Multiple fuzzy matches → AMBIGUOUS_MATCH."""
        content = "foo  bar\nbaz\nfoo   bar\n"
        with pytest.raises(MCPError) as exc_info:
            _resolve_edit(content, "foo bar", "qux")
        assert exc_info.value.code == MCPErrorCode.AMBIGUOUS_MATCH

    def test_no_match_at_all(self) -> None:
        """No exact or fuzzy match → CONTENT_MISMATCH."""
        content = "hello world\n"
        with pytest.raises(MCPError) as exc_info:
            _resolve_edit(content, "completely_unrelated_text", "replacement")
        assert exc_info.value.code == MCPErrorCode.CONTENT_MISMATCH

    def test_exact_match_at_end_of_file(self) -> None:
        """Match at the very end of file content."""
        content = "prefix\nsuffix"
        result, meta = _resolve_edit(content, "suffix", "end")
        assert result == "prefix\nend"
        assert meta["match_kind"] == "exact"

    def test_replace_with_empty_string(self) -> None:
        """Replace match with empty string (deletion of block)."""
        content = "keep\ndelete_me\nkeep"
        result, meta = _resolve_edit(content, "delete_me\n", "")
        assert result == "keep\nkeep"

    def test_replace_with_longer_content(self) -> None:
        """Replace with more lines than original."""
        content = "a\nb\nc\n"
        result, meta = _resolve_edit(content, "b", "b1\nb2\nb3")
        assert result == "a\nb1\nb2\nb3\nc\n"


# =============================================================================
# _summarize_edit
# =============================================================================


class TestSummarizeEdit:
    """Tests for _summarize_edit helper."""

    def test_empty_results(self) -> None:
        assert _summarize_edit([]) == "no changes"

    def test_single_result(self) -> None:
        results = [{"path": "src/foo.py", "action": "updated"}]
        summary = _summarize_edit(results)
        assert "updated" in summary
        assert "foo.py" in summary

    def test_multiple_results(self) -> None:
        results = [
            {"path": "a.py", "action": "created"},
            {"path": "b.py", "action": "updated"},
            {"path": "c.py", "action": "updated"},
            {"path": "d.py", "action": "deleted"},
        ]
        summary = _summarize_edit(results)
        assert "1 created" in summary
        assert "2 updated" in summary
        assert "1 deleted" in summary


# =============================================================================
# refactor_edit handler integration
# =============================================================================


class TestRefactorEditHandler:
    """Integration tests for refactor_edit tool handler."""

    @pytest.fixture
    def mcp_app(self) -> FastMCP:
        return FastMCP("test")

    @pytest.fixture
    def app_ctx(self, tmp_path: Path) -> MagicMock:
        ctx = MagicMock()
        ctx.coordinator.repo_root = tmp_path
        session = MagicMock()
        session.counters = {"recon_called": 1, "resolved_files": {}}
        session.edit_tickets = {}
        session.edits_since_checkpoint = 0

        from codeplane.mcp.session import RefactorPlan

        session.active_plan = RefactorPlan(
            plan_id="test-plan-1",
            recon_id="r1",
            description="test plan for unit tests",
        )
        ctx.session_manager.get_or_create.return_value = session
        ctx.mutation_ops.notify_mutation = MagicMock()
        return ctx

    @pytest.fixture
    def fastmcp_ctx(self) -> MagicMock:
        ctx = MagicMock(spec=["session_id"])
        ctx.session_id = "test-session"
        return ctx

    def _write_file(self, repo_root: Path, rel_path: str, content: str) -> str:
        fp = repo_root / rel_path
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    @pytest.mark.asyncio
    async def test_basic_update(
        self, mcp_app: FastMCP, app_ctx: MagicMock, fastmcp_ctx: MagicMock, tmp_path: Path
    ) -> None:
        """Basic find-and-replace update via edit_ticket."""
        sha = self._write_file(tmp_path, "hello.py", "print('hello')\n")
        session = app_ctx.session_manager.get_or_create.return_value
        session.counters["resolved_files"] = {"hello.py": sha}

        from codeplane.mcp.session import EditTicket

        ticket_id = "recon1:0:" + sha[:8]
        session.edit_tickets[ticket_id] = EditTicket(
            ticket_id=ticket_id,
            path="hello.py",
            sha256=sha,
            candidate_id="recon1:0",
            issued_by="resolve",
        )

        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        edit_fn = tools["refactor_edit"].fn

        result: dict[str, Any] = await edit_fn(
            ctx=fastmcp_ctx,
            edits=[
                FindReplaceEdit(
                    edit_ticket=ticket_id,
                    old_content="print('hello')",
                    new_content="print('world')",
                )
            ],
            plan_id="test-plan-1",
        )

        assert result["applied"] is True
        assert result["delta"]["files_changed"] == 1
        assert result["delta"]["files"][0]["action"] == "updated"
        assert "agentic_hint" in result
        assert "checkpoint" in result["agentic_hint"]
        # Verify file was actually written
        assert (tmp_path / "hello.py").read_text() == "print('world')\n"
        # Verify continuation ticket was issued
        assert "continuation_tickets" in result
        assert len(result["continuation_tickets"]) == 1
        assert result["continuation_tickets"][0]["path"] == "hello.py"

    @pytest.mark.asyncio
    async def test_sha256_mismatch(
        self, mcp_app: FastMCP, app_ctx: MagicMock, fastmcp_ctx: MagicMock, tmp_path: Path
    ) -> None:
        """SHA mismatch via stale ticket raises FileHashMismatchError."""
        self._write_file(tmp_path, "stale.py", "old content\n")
        session = app_ctx.session_manager.get_or_create.return_value

        from codeplane.mcp.session import EditTicket

        # Ticket with wrong sha256
        ticket_id = "r:0:deadbeef"
        session.edit_tickets[ticket_id] = EditTicket(
            ticket_id=ticket_id,
            path="stale.py",
            sha256="wrong_sha_value",
            candidate_id="r:0",
            issued_by="resolve",
        )

        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        edit_fn = tools["refactor_edit"].fn

        from codeplane.mcp.errors import FileHashMismatchError

        with pytest.raises(FileHashMismatchError):
            await edit_fn(
                ctx=fastmcp_ctx,
                edits=[
                    FindReplaceEdit(
                        edit_ticket=ticket_id,
                        old_content="old content",
                        new_content="new content",
                    )
                ],
                plan_id="test-plan-1",
            )

    @pytest.mark.asyncio
    async def test_update_without_ticket_raises(
        self, mcp_app: FastMCP, app_ctx: MagicMock, fastmcp_ctx: MagicMock
    ) -> None:
        """Update without edit_ticket raises MCPError."""
        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        edit_fn = tools["refactor_edit"].fn

        with pytest.raises(MCPError) as exc_info:
            await edit_fn(
                ctx=fastmcp_ctx,
                edits=[
                    FindReplaceEdit(
                        path="foo.py",
                        old_content="x",
                        new_content="y",
                    )
                ],
                plan_id="test-plan-1",
            )
        assert exc_info.value.code == MCPErrorCode.INVALID_PARAMS
        assert "edit_ticket" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_create_file_via_mutation_ops(
        self, mcp_app: FastMCP, app_ctx: MagicMock, fastmcp_ctx: MagicMock
    ) -> None:
        """File creation delegates to mutation_ops.write_source."""
        mock_delta = MagicMock()
        mock_delta.files = [
            MagicMock(
                path="new.py",
                action="created",
                old_hash="0000",
                new_hash="abcd",
                insertions=3,
                deletions=0,
            )
        ]
        mock_result = MagicMock()
        mock_result.delta = mock_delta
        app_ctx.mutation_ops.write_source.return_value = mock_result

        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        edit_fn = tools["refactor_edit"].fn

        result: dict[str, Any] = await edit_fn(
            ctx=fastmcp_ctx,
            edits=[
                FindReplaceEdit(
                    path="new.py",
                    old_content=None,
                    new_content="print('new')\n",
                )
            ],
            plan_id="test-plan-1",
        )

        assert result["applied"] is True
        assert result["delta"]["files"][0]["action"] == "created"

    @pytest.mark.asyncio
    async def test_delete_file_via_mutation_ops(
        self, mcp_app: FastMCP, app_ctx: MagicMock, fastmcp_ctx: MagicMock
    ) -> None:
        """File deletion delegates to mutation_ops.write_source."""
        mock_delta = MagicMock()
        mock_delta.files = [
            MagicMock(
                path="doomed.py",
                action="deleted",
                old_hash="abcd",
                new_hash="0000",
                insertions=0,
                deletions=5,
            )
        ]
        mock_result = MagicMock()
        mock_result.delta = mock_delta
        app_ctx.mutation_ops.write_source.return_value = mock_result

        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        edit_fn = tools["refactor_edit"].fn

        result: dict[str, Any] = await edit_fn(
            ctx=fastmcp_ctx,
            edits=[
                FindReplaceEdit(
                    path="doomed.py",
                    delete=True,
                )
            ],
            plan_id="test-plan-1",
        )

        assert result["applied"] is True
        assert result["delta"]["files"][0]["action"] == "deleted"

    @pytest.mark.asyncio
    async def test_create_file_not_found_error(
        self, mcp_app: FastMCP, app_ctx: MagicMock, fastmcp_ctx: MagicMock
    ) -> None:
        """FileNotFoundError from mutation_ops becomes MCPError."""
        app_ctx.mutation_ops.write_source.side_effect = FileNotFoundError("missing dir")

        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        edit_fn = tools["refactor_edit"].fn

        with pytest.raises(MCPError) as exc_info:
            await edit_fn(
                ctx=fastmcp_ctx,
                edits=[FindReplaceEdit(path="deep/new.py", old_content=None, new_content="x")],
                plan_id="test-plan-1",
            )
        assert exc_info.value.code == MCPErrorCode.FILE_NOT_FOUND

    @pytest.mark.asyncio
    async def test_create_file_exists_error(
        self, mcp_app: FastMCP, app_ctx: MagicMock, fastmcp_ctx: MagicMock
    ) -> None:
        """FileExistsError from mutation_ops becomes MCPError."""
        app_ctx.mutation_ops.write_source.side_effect = FileExistsError("already exists")

        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        edit_fn = tools["refactor_edit"].fn

        with pytest.raises(MCPError) as exc_info:
            await edit_fn(
                ctx=fastmcp_ctx,
                edits=[FindReplaceEdit(path="exists.py", old_content=None, new_content="x")],
                plan_id="test-plan-1",
            )
        assert exc_info.value.code == MCPErrorCode.FILE_EXISTS

    @pytest.mark.asyncio
    async def test_session_tracks_edited_files(
        self, mcp_app: FastMCP, app_ctx: MagicMock, fastmcp_ctx: MagicMock, tmp_path: Path
    ) -> None:
        """Edited files are tracked in session counters."""
        sha = self._write_file(tmp_path, "tracked.py", "old\n")
        session = app_ctx.session_manager.get_or_create.return_value
        session.counters = {"recon_called": 1, "resolved_files": {"tracked.py": sha}}

        from codeplane.mcp.session import EditTicket

        ticket_id = f"r:0:{sha[:8]}"
        session.edit_tickets[ticket_id] = EditTicket(
            ticket_id=ticket_id,
            path="tracked.py",
            sha256=sha,
            candidate_id="r:0",
            issued_by="resolve",
        )

        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        edit_fn = tools["refactor_edit"].fn

        await edit_fn(
            ctx=fastmcp_ctx,
            edits=[FindReplaceEdit(edit_ticket=ticket_id, old_content="old", new_content="new")],
            plan_id="test-plan-1",
        )

        edited = session.counters.get("edited_files", set())
        assert "tracked.py" in edited

    @pytest.mark.asyncio
    async def test_unknown_ticket_raises(
        self, mcp_app: FastMCP, app_ctx: MagicMock, fastmcp_ctx: MagicMock
    ) -> None:
        """Unknown edit_ticket raises MCPError."""
        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        edit_fn = tools["refactor_edit"].fn

        with pytest.raises(MCPError) as exc_info:
            await edit_fn(
                ctx=fastmcp_ctx,
                edits=[
                    FindReplaceEdit(
                        edit_ticket="bogus:0:deadbeef",
                        old_content="x",
                        new_content="y",
                    )
                ],
                plan_id="test-plan-1",
            )
        assert exc_info.value.code == MCPErrorCode.INVALID_PARAMS
        assert "Unknown edit_ticket" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_used_ticket_raises(
        self, mcp_app: FastMCP, app_ctx: MagicMock, fastmcp_ctx: MagicMock, tmp_path: Path
    ) -> None:
        """Already-used ticket raises MCPError."""
        sha = self._write_file(tmp_path, "used.py", "old\n")
        session = app_ctx.session_manager.get_or_create.return_value

        from codeplane.mcp.session import EditTicket

        ticket_id = f"r:0:{sha[:8]}"
        session.edit_tickets[ticket_id] = EditTicket(
            ticket_id=ticket_id,
            path="used.py",
            sha256=sha,
            candidate_id="r:0",
            issued_by="resolve",
            used=True,
        )

        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        edit_fn = tools["refactor_edit"].fn

        with pytest.raises(MCPError) as exc_info:
            await edit_fn(
                ctx=fastmcp_ctx,
                edits=[
                    FindReplaceEdit(edit_ticket=ticket_id, old_content="old", new_content="new")
                ],
                plan_id="test-plan-1",
            )
        assert exc_info.value.code == MCPErrorCode.INVALID_PARAMS
        assert "already used" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_batch_limit_raises(
        self, mcp_app: FastMCP, app_ctx: MagicMock, fastmcp_ctx: MagicMock
    ) -> None:
        """Exceeding batch limit raises MCPError."""
        session = app_ctx.session_manager.get_or_create.return_value
        session.edits_since_checkpoint = 2  # Already at limit

        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        edit_fn = tools["refactor_edit"].fn

        with pytest.raises(MCPError) as exc_info:
            await edit_fn(
                ctx=fastmcp_ctx,
                edits=[FindReplaceEdit(path="new.py", old_content=None, new_content="x")],
                plan_id="test-plan-1",
            )
        assert exc_info.value.code == MCPErrorCode.INVALID_PARAMS
        assert "batch limit" in exc_info.value.message.lower()

    @pytest.mark.asyncio
    async def test_create_without_plan_raises(
        self, mcp_app: FastMCP, app_ctx: MagicMock, fastmcp_ctx: MagicMock
    ) -> None:
        """Gap 3: Create-only call without active plan raises MCPError."""
        session = app_ctx.session_manager.get_or_create.return_value
        session.active_plan = None

        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        edit_fn = tools["refactor_edit"].fn

        with pytest.raises(MCPError) as exc_info:
            await edit_fn(
                ctx=fastmcp_ctx,
                edits=[FindReplaceEdit(path="new.py", old_content=None, new_content="x")],
                plan_id="test-plan-1",
            )
        assert exc_info.value.code == MCPErrorCode.INVALID_PARAMS
        assert "No active refactor plan" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_delete_without_plan_raises(
        self, mcp_app: FastMCP, app_ctx: MagicMock, fastmcp_ctx: MagicMock
    ) -> None:
        """Gap 3: Delete-only call without active plan raises MCPError."""
        session = app_ctx.session_manager.get_or_create.return_value
        session.active_plan = None

        register_tools(mcp_app, app_ctx)
        tools = get_tools_sync(mcp_app)
        edit_fn = tools["refactor_edit"].fn

        with pytest.raises(MCPError) as exc_info:
            await edit_fn(
                ctx=fastmcp_ctx,
                edits=[FindReplaceEdit(path="doomed.py", delete=True)],
                plan_id="test-plan-1",
            )
        assert exc_info.value.code == MCPErrorCode.INVALID_PARAMS
        assert "No active refactor plan" in exc_info.value.message
