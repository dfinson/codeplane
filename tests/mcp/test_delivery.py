"""Tests for MCP delivery envelope, resource cache, and client profiles.

Covers:
- ResourceCache: disk-backed store/retrieve
- ClientProfile + resolve_profile: profile selection logic
- build_envelope: inline/resource delivery decisions
"""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

import codeplane.mcp.delivery as _delivery_mod
from codeplane.mcp.delivery import (
    ClientProfile,
    ResourceCache,
    ScopeBudget,
    ScopeManager,
    build_envelope,
    resolve_profile,
)


class TestResourceCache:
    """Tests for disk-backed ResourceCache."""

    @pytest.fixture(autouse=True)
    def _setup_cache_dir(self, tmp_path: Path) -> Generator[None, None, None]:
        """Point _cache_dir at a temp directory for each test."""
        old = _delivery_mod._cache_dir
        _delivery_mod._cache_dir = tmp_path / ".codeplane" / "cache"
        yield
        _delivery_mod._cache_dir = old

    def test_store_writes_to_disk(self) -> None:
        """Store payload, verify file exists on disk with correct content."""
        cache = ResourceCache()
        resource_id, byte_size = cache.store(b"hello world", "source")
        assert _delivery_mod._cache_dir is not None
        disk_path = _delivery_mod._cache_dir / "source" / f"{resource_id}.json"
        assert disk_path.exists()
        assert disk_path.read_bytes() == b"hello world"
        assert byte_size == len(b"hello world")

    def test_immutability(self) -> None:
        """Store, read twice from disk, verify identical bytes."""
        cache = ResourceCache()
        payload = b"immutable data 12345"
        resource_id, _ = cache.store(payload, "source")
        assert _delivery_mod._cache_dir is not None
        disk_path = _delivery_mod._cache_dir / "source" / f"{resource_id}.json"
        r1 = disk_path.read_bytes()
        r2 = disk_path.read_bytes()
        assert r1 == r2 == payload

    def test_byte_size_returned(self) -> None:
        """Verify byte_size matches payload size."""
        cache = ResourceCache()
        payload = b"exactly 25 bytes of data!"
        _, byte_size = cache.store(payload, "source")
        assert byte_size == len(payload)

    def test_dict_payload_stored_pretty(self) -> None:
        """Dict payloads are serialized as pretty-printed JSON on disk."""
        cache = ResourceCache()
        payload = {"key": "value", "nested": [1, 2, 3]}
        resource_id, _ = cache.store(payload, "test")
        assert _delivery_mod._cache_dir is not None
        disk_path = _delivery_mod._cache_dir / "test" / f"{resource_id}.json"
        raw = disk_path.read_bytes()
        text = raw.decode("utf-8")
        # Pretty-printed with indent=2 for agent readability
        assert "\n" in text
        assert text.startswith("{")
        assert json.loads(text) == payload

    def test_default_profile(self) -> None:
        """No clientInfo -> default profile."""
        profile = resolve_profile(None, None)
        assert profile.name == "default"

    def test_exact_name_match(self) -> None:
        """clientInfo.name='copilot_coding_agent' -> correct profile."""
        profile = resolve_profile({"name": "copilot_coding_agent"}, None)
        assert profile.name == "copilot_coding_agent"

    def test_unknown_name_falls_to_default(self) -> None:
        """Unknown name -> default profile."""
        profile = resolve_profile({"name": "unknown_client"}, None)
        assert profile.name == "default"

    def test_config_override(self) -> None:
        """Explicit config override takes priority."""
        profile = resolve_profile(
            {"name": "Visual Studio Code"},
            None,
            config_override="copilot_coding_agent",
        )
        assert profile.name == "copilot_coding_agent"


class TestBuildEnvelope:
    """Tests for build_envelope."""

    @pytest.fixture(autouse=True)
    def _setup_cache_dir(self, tmp_path: Path) -> Generator[None, None, None]:
        """Point disk cache at tmp for resource delivery tests."""
        old = _delivery_mod._cache_dir
        _delivery_mod._cache_dir = tmp_path / ".codeplane" / "cache"
        _delivery_mod._cache_dir.mkdir(parents=True, exist_ok=True)
        yield
        _delivery_mod._cache_dir = old

    def _profile(self, inline_cap: int = 8000) -> ClientProfile:
        return ClientProfile(name="test", inline_cap_bytes=inline_cap)

    def test_small_payload_inline(self) -> None:
        """Small payload -> delivery='inline', no resource_uri."""
        payload = {"data": "x" * 500}
        env = build_envelope(payload, resource_kind="source", client_profile=self._profile())
        assert env["delivery"] == "inline"
        assert "resource_uri" not in env

    def test_large_payload_resource(self) -> None:
        """Large payload -> delivery='resource', written to disk."""
        payload = {"data": "x" * 20000}
        env = build_envelope(payload, resource_kind="source", client_profile=self._profile())
        assert env["delivery"] == "resource"
        assert "resource_uri" not in env
        assert "agentic_hint" in env
        assert "cached at" in env["agentic_hint"]

    def test_inline_budget_fields(self) -> None:
        """Verify inline_budget_bytes_used and _limit present."""
        payload = {"data": "small"}
        env = build_envelope(payload, resource_kind="source", client_profile=self._profile())
        assert "inline_budget_bytes_used" in env
        assert "inline_budget_bytes_limit" in env

    def test_resource_kind_set(self) -> None:
        """Verify resource_kind matches the tool type."""
        payload = {"data": "x"}
        env = build_envelope(payload, resource_kind="search_hits", client_profile=self._profile())
        assert env["resource_kind"] == "search_hits"

    def test_scope_id_echoed(self) -> None:
        """Pass scope_id, verify echoed in response."""
        payload = {"data": "x"}
        env = build_envelope(
            payload,
            resource_kind="source",
            client_profile=self._profile(),
            scope_id="s-123",
        )
        assert env["scope_id"] == "s-123"


class TestScopeBudget:
    """Tests for ScopeBudget and ScopeManager."""

    def test_scope_creation(self) -> None:
        """First call with scope_id creates scope."""
        mgr = ScopeManager()
        budget = mgr.get_or_create("test-scope")
        assert isinstance(budget, ScopeBudget)

    def test_scope_usage_tracked(self) -> None:
        """Multiple reads increment read_bytes."""
        mgr = ScopeManager()
        budget = mgr.get_or_create("test-scope")
        budget.increment_read(1000)
        budget.increment_read(2000)
        usage = budget.to_usage_dict()
        assert usage["read_bytes"] == 3000

    def test_full_read_counter(self) -> None:
        """read_file_full increments full_reads counter."""
        mgr = ScopeManager()
        budget = mgr.get_or_create("test-scope")
        budget.increment_full_read("a.py", 100)
        budget.increment_full_read("b.py", 200)
        usage = budget.to_usage_dict()
        assert usage["full_reads"] == 2

    def test_search_call_counter(self) -> None:
        """Search increments search_calls counter."""
        mgr = ScopeManager()
        budget = mgr.get_or_create("test-scope")
        budget.increment_search(10)
        usage = budget.to_usage_dict()
        assert usage["search_calls"] == 1
        assert usage["search_hits"] == 10

    def test_multiple_scopes_independent(self) -> None:
        """Two scope_ids track independently."""
        mgr = ScopeManager()
        b1 = mgr.get_or_create("scope-a")
        b2 = mgr.get_or_create("scope-b")
        b1.increment_read(1000)
        b2.increment_read(5000)
        assert b1.to_usage_dict()["read_bytes"] == 1000
        assert b2.to_usage_dict()["read_bytes"] == 5000

    def test_duplicate_read_detection(self) -> None:
        """Same full read twice, same scope -> warning."""
        mgr = ScopeManager()
        budget = mgr.get_or_create("test-scope")
        budget.increment_full_read("same.py", 100)
        budget.increment_full_read("same.py", 100)
        warning = budget.check_duplicate_read("same.py")
        assert warning is not None
        assert warning["code"] == "DUPLICATE_FULL_READ"
        assert warning["count"] == 2

    def test_no_warning_after_mutation(self) -> None:
        """read -> mutation -> read -> no warning."""
        mgr = ScopeManager()
        budget = mgr.get_or_create("test-scope")
        budget.increment_full_read("file.py", 100)
        budget.record_mutation()
        budget.increment_full_read("file.py", 100)
        warning = budget.check_duplicate_read("file.py")
        assert warning is None


class TestFetchHints:
    """Tests for disk-cache fetch hints with jq extraction commands.

    Only non-paginated resource kinds hit disk: semantic_diff, diff,
    test_output, refactor_preview, log, commit, blame, repo_map.
    source and search_hits are paginated inline — no hint tests for those.
    """

    def test_diff_hint_has_jq_commands(self) -> None:
        """Diff hint includes file count summary and jq extraction."""
        from codeplane.mcp.delivery import _build_fetch_hint

        diff_text = "diff --git a/x.py b/x.py\n+hello\ndiff --git a/y.py b/y.py\n-bye"
        payload = {"diff": diff_text}
        hint = _build_fetch_hint("abc123", 2000, "diff", payload)
        assert "2 file(s) changed" in hint
        assert "cached at" in hint
        assert "jq -r '.diff'" in hint
        assert ".codeplane/cache/diff/abc123.json" in hint

    def test_log_hint_has_jq_commands(self) -> None:
        """Log hint includes commit summary and jq extraction."""
        from codeplane.mcp.delivery import _build_fetch_hint

        payload = {
            "results": [
                {"short_sha": "abc1234", "message": "fix bug", "sha": "abc1234full"},
                {"short_sha": "def5678", "message": "add feature", "sha": "def5678full"},
            ],
        }
        hint = _build_fetch_hint("abc123", 4000, "log", payload)
        assert "2 commit(s)" in hint
        assert "abc1234" in hint
        assert "jq" in hint
        assert ".codeplane/cache/log/abc123.json" in hint

    def test_blame_hint_has_jq_commands(self) -> None:
        """Blame hint includes author summary and jq for hunk details."""
        from codeplane.mcp.delivery import _build_fetch_hint

        payload = {
            "path": "src/foo.py",
            "results": [
                {"author": "alice", "commit_sha": "aaa", "start_line": 1, "end_line": 10},
                {"author": "bob", "commit_sha": "bbb", "start_line": 11, "end_line": 15},
            ],
        }
        hint = _build_fetch_hint("abc123", 3000, "blame", payload)
        assert "2 hunk(s)" in hint
        assert "2 author(s)" in hint
        assert "src/foo.py" in hint
        assert "jq" in hint
        assert "group_by" in hint  # author aggregation command

    def test_test_output_hint_failures_jq(self) -> None:
        """Test output hint with failures includes jq to extract failed tests."""
        from codeplane.mcp.delivery import _build_fetch_hint

        payload = {
            "passed": 10,
            "failed": 2,
            "total": 12,
            "results": [
                {"name": "test_a", "status": "passed"},
                {"name": "test_b", "status": "failed", "message": "assertion error"},
            ],
        }
        hint = _build_fetch_hint("abc123", 5000, "test_output", payload)
        assert "10 passed" in hint
        assert "2 failed" in hint
        assert "jq" in hint
        assert "failed" in hint

    def test_test_output_hint_all_pass(self) -> None:
        """Test output with 0 failures gives simple summary jq."""
        from codeplane.mcp.delivery import _build_fetch_hint

        payload = {"passed": 10, "failed": 0, "total": 10}
        hint = _build_fetch_hint("abc123", 2000, "test_output", payload)
        assert "10 passed" in hint
        assert "0 failed" in hint
        assert "jq" in hint

    def test_semantic_diff_hint_with_summary(self) -> None:
        """Semantic diff hint uses summary field and adds per-type jq filters."""
        from codeplane.mcp.delivery import _build_fetch_hint

        payload = {
            "structural_changes": [
                {"change": "added", "name": "foo", "path": "a.py"},
                {"change": "added", "name": "bar", "path": "a.py"},
                {"change": "removed", "name": "baz", "path": "b.py"},
            ],
            "summary": "2 added, 1 removed",
        }
        hint = _build_fetch_hint("abc123", 4000, "semantic_diff", payload)
        assert "2 added, 1 removed" in hint
        # Should have jq filter commands for each change type
        assert '"added"' in hint
        assert '"removed"' in hint
        assert "jq" in hint

    def test_semantic_diff_hint_without_summary(self) -> None:
        """Semantic diff hint tallies change types and builds jq filters."""
        from codeplane.mcp.delivery import _build_fetch_hint

        payload = {
            "structural_changes": [
                {"change": "added", "name": "foo", "path": "a.py"},
                {"change": "removed", "name": "bar", "path": "b.py"},
            ],
        }
        hint = _build_fetch_hint("abc123", 4000, "semantic_diff", payload)
        assert "2 change(s)" in hint
        assert "1 added" in hint
        assert "1 removed" in hint
        assert "jq" in hint
        assert ".structural_changes" in hint

    def test_semantic_diff_detects_changes_key(self) -> None:
        """Semantic diff uses 'changes' key when 'structural_changes' absent."""
        from codeplane.mcp.delivery import _build_fetch_hint

        payload = {
            "changes": [
                {"change_type": "body_changed", "name": "foo", "path": "a.py"},
            ],
        }
        hint = _build_fetch_hint("abc123", 4000, "semantic_diff", payload)
        assert ".changes" in hint
        assert "body_changed" in hint

    def test_refactor_preview_hint(self) -> None:
        """Refactor preview shows match count and jq listing."""
        from codeplane.mcp.delivery import _build_fetch_hint

        payload = {
            "matches": [
                {"path": "a.py", "line": 10, "certainty": "high", "text": "foo"},
                {"path": "b.py", "line": 20, "certainty": "low", "text": "foo"},
            ],
        }
        hint = _build_fetch_hint("abc123", 3000, "refactor_preview", payload)
        assert "2 match(es) across 2 file(s)" in hint
        assert "jq" in hint
        # Low-certainty match triggers additional filter
        assert "low" in hint or "medium" in hint

    def test_commit_hint(self) -> None:
        """Commit hint shows sha/message and jq for details."""
        from codeplane.mcp.delivery import _build_fetch_hint

        payload = {
            "short_sha": "abc1234",
            "message": "fix regression",
            "author": "alice",
            "files": [{"path": "src/x.py"}, {"path": "src/y.py"}],
        }
        hint = _build_fetch_hint("abc123", 2000, "commit", payload)
        assert "abc1234" in hint
        assert "fix regression" in hint
        assert "jq" in hint
        assert ".files" in hint  # command to list changed files

    def test_repo_map_hint(self) -> None:
        """Repo map hint lists sections and jq to explore each."""
        from codeplane.mcp.delivery import _build_fetch_hint

        payload: dict[str, Any] = {
            "structure": {"files": []},
            "dependencies": {"packages": []},
            "test_layout": {"suites": []},
        }
        hint = _build_fetch_hint("abc123", 5000, "repo_map", payload)
        assert "structure" in hint
        assert "dependencies" in hint
        assert "jq" in hint

    def test_unknown_kind_no_commands(self) -> None:
        """Unknown kind gets path and size but no extraction commands."""
        from codeplane.mcp.delivery import _build_fetch_hint

        hint = _build_fetch_hint("abc123", 1000, "unknown_kind", {"data": "x"})
        assert "cached at" in hint
        assert "1,000 bytes" in hint
        # No cat, no type, no jq — just the header
        assert "cat" not in hint
        assert "type" not in hint
        assert "jq" not in hint

    def test_hint_without_payload(self) -> None:
        """No payload -> header only, no commands."""
        from codeplane.mcp.delivery import _build_fetch_hint

        hint = _build_fetch_hint("abc123", 1000, "semantic_diff")
        assert "cached at" in hint
        assert "jq" not in hint

    def test_semantic_diff_text_format(self) -> None:
        """Text format structural_changes (list[str]) must not crash."""
        from codeplane.mcp.delivery import _build_fetch_hint

        payload = {
            "structural_changes": [
                "added function foo  src/a.py:10-20  Δ5",
                "added function bar  src/a.py:30-40  Δ8",
                "removed class Baz  src/b.py:1-50  Δ50",
            ],
            "summary": "2 added, 1 removed",
        }
        hint = _build_fetch_hint("abc123", 4000, "semantic_diff", payload)
        assert "2 added, 1 removed" in hint
        assert "jq" in hint
        # Text format should NOT produce dict-based jq filters
        assert ".change //" not in hint
        assert ".structural_changes[]" in hint

    def test_semantic_diff_text_format_no_summary(self) -> None:
        """Text format without summary tallies change types from line prefixes."""
        from codeplane.mcp.delivery import _build_fetch_hint

        payload = {
            "structural_changes": [
                "added function foo  src/a.py:10-20",
                "removed class Bar  src/b.py:1-50",
            ],
        }
        hint = _build_fetch_hint("abc123", 4000, "semantic_diff", payload)
        assert "2 change(s)" in hint
        assert "1 added" in hint
        assert "1 removed" in hint


class TestCursorPagination:
    """Tests for cursor-based pagination of oversized responses."""

    def test_fit_items_all_fit(self) -> None:
        """When all items fit, return count of all items."""
        from codeplane.mcp.delivery import _fit_items

        items = [{"path": "a.py"}, {"path": "b.py"}]
        count = _fit_items(items, 0, 10_000, 200)
        assert count == 2

    def test_fit_items_partial(self) -> None:
        """When only some items fit, return partial count."""
        from codeplane.mcp.delivery import _fit_items

        # Each item ~20 bytes JSON. Cap = 60 with 10 overhead => 50 available => fits ~2
        items = [{"k": "x"} for _ in range(5)]
        count = _fit_items(items, 0, 60, 10)
        assert 1 <= count < 5

    def test_fit_items_minimum_one(self) -> None:
        """Even an oversized single item returns count=1."""
        from codeplane.mcp.delivery import _fit_items

        items = [{"content": "x" * 5000}]
        count = _fit_items(items, 0, 100, 50)
        assert count == 1

    def test_try_paginate_non_paginated_kind(self) -> None:
        """Non-paginated kinds return None."""
        from codeplane.mcp.delivery import _try_paginate

        result = _try_paginate({"diff": "hello"}, "diff", 8000)
        assert result is None

    def test_try_paginate_single_item_content_split(self) -> None:
        """Single oversized item gets content-split across pages, not rejected."""
        from codeplane.mcp.delivery import _CURSOR_STORE, _try_paginate

        # Single file with ~10KB content (exceeds 8KB cap)
        big_content = "\n".join(f"line {i}: {'x' * 80}" for i in range(120))
        result = _try_paginate(
            {"files": [{"path": "a.py", "content": big_content, "line_count": 120}]},
            "source",
            4000,
        )
        assert result is not None
        assert result["has_more"] is True
        assert "cursor" in result
        # Content should be truncated (not the full 120 lines)
        delivered = result["files"][0]
        assert delivered.get("content_truncated") is True
        assert delivered["content_lines_delivered"] < 120
        assert delivered["content_lines_total"] == 120

        # Clean up
        _CURSOR_STORE.pop(result["cursor"], None)

    def test_try_paginate_returns_first_page(self) -> None:
        """Multiple items that overflow get paginated; first page returned."""
        from codeplane.mcp.delivery import _CURSOR_STORE, _try_paginate

        items = [{"path": f"file{i}.py", "content": "x" * 3000} for i in range(5)]
        payload = {"files": items, "summary": "5 files"}
        page = _try_paginate(payload, "source", 8000, "5 files")

        assert page is not None
        assert page["resource_kind"] == "source"
        assert page["delivery"] == "inline"
        assert "files" in page
        assert len(page["files"]) < 5  # not all items fit
        assert page["has_more"] is True
        assert "cursor" in page
        assert page["page_info"]["total"] == 5

        # Clean up cursor store
        cursor_id = page["cursor"]
        _CURSOR_STORE.pop(cursor_id, None)

    def test_resume_cursor_returns_next_page(self) -> None:
        """Resuming a cursor returns the next page of items."""
        from codeplane.mcp.delivery import _CURSOR_STORE, _try_paginate, resume_cursor

        items = [{"path": f"file{i}.py", "content": "x" * 3000} for i in range(5)]
        payload = {"files": items, "summary": "5 files"}
        page1 = _try_paginate(payload, "source", 8000)
        assert page1 is not None
        assert page1["has_more"] is True

        page2 = resume_cursor(page1["cursor"])
        assert page2 is not None
        assert "files" in page2
        assert page2["page_info"]["total"] == 5
        first_page_count = page1["page_info"]["returned"]
        assert (
            page2["page_info"]["remaining"] == 5 - first_page_count - page2["page_info"]["returned"]
        )

        # Clean up
        if "cursor" in page2:
            _CURSOR_STORE.pop(page2["cursor"], None)

    def test_cursor_expiry(self) -> None:
        """Expired cursor returns None."""
        import time as _time

        from codeplane.mcp.delivery import _CURSOR_STORE, _try_paginate, resume_cursor

        items = [{"path": f"file{i}.py", "content": "x" * 3000} for i in range(5)]
        page1 = _try_paginate({"files": items}, "source", 8000)
        assert page1 is not None

        # Force expiry by backdating
        cursor_id = page1["cursor"]
        _CURSOR_STORE[cursor_id].created_at = _time.monotonic() - 600

        page2 = resume_cursor(cursor_id)
        assert page2 is None

    def test_final_page_removes_cursor(self) -> None:
        """When the last page is returned, cursor is removed from store."""
        from codeplane.mcp.delivery import _CURSOR_STORE, _try_paginate, resume_cursor

        # 2 items, cap so small only 1 fits per page
        items = [{"path": "a.py", "content": "x" * 4000}, {"path": "b.py", "content": "y" * 4000}]
        page1 = _try_paginate({"files": items}, "source", 5800)
        assert page1 is not None
        assert page1["has_more"] is True
        cursor_id = page1["cursor"]

        page2 = resume_cursor(cursor_id)
        assert page2 is not None
        assert page2["has_more"] is False
        assert "cursor" not in page2
        assert cursor_id not in _CURSOR_STORE

    def test_extra_fields_echoed_every_page(self) -> None:
        """Non-paginated fields (summary etc.) appear in every page."""
        from codeplane.mcp.delivery import _CURSOR_STORE, _try_paginate, resume_cursor

        items = [{"path": f"f{i}.py", "content": "x" * 4000} for i in range(3)]
        payload = {"files": items, "summary": "3 files", "not_found": ["missing.py"]}
        page1 = _try_paginate(payload, "source", 5000, "3 files")
        assert page1 is not None
        assert page1.get("not_found") == ["missing.py"]
        assert page1.get("summary") == "3 files"

        if page1.get("has_more"):
            page2 = resume_cursor(page1["cursor"])
            assert page2 is not None
            assert page2.get("not_found") == ["missing.py"]
            # Clean up
            if "cursor" in page2:
                _CURSOR_STORE.pop(page2["cursor"], None)

    def test_search_hits_paginated(self) -> None:
        """Search results (search_hits kind) are also paginated."""
        from codeplane.mcp.delivery import _CURSOR_STORE, _try_paginate

        items = [
            {
                "hit_id": f"h{i}",
                "path": f"file{i}.py",
                "span": {"start_line": 1, "end_line": 50},
                "extra": "x" * 500,
            }
            for i in range(10)
        ]
        payload = {"results": items, "query_time_ms": 5, "summary": "10 hits"}
        page = _try_paginate(payload, "search_hits", 2000)

        assert page is not None
        assert "results" in page
        assert len(page["results"]) < 10
        assert page["has_more"] is True
        assert page["page_info"]["total"] == 10

        # Clean up
        _CURSOR_STORE.pop(page["cursor"], None)

    def test_build_envelope_paginates_before_disk(self) -> None:
        """build_envelope tries pagination before falling back to disk."""
        # A source payload with many files that exceeds inline cap
        items = [
            {"path": f"f{i}.py", "content": "x" * 2000, "range": [1, 50], "line_count": 50}
            for i in range(10)
        ]
        payload = {"files": items, "summary": "10 files"}
        result = build_envelope(payload, resource_kind="source", inline_summary="10 files")

        # Should get paginated delivery, not resource
        assert result["delivery"] == "inline"
        assert "cursor" in result
        assert result["has_more"] is True
        assert "files" in result
        assert len(result["files"]) < 10

        # Clean up
        from codeplane.mcp.delivery import _CURSOR_STORE

        _CURSOR_STORE.pop(result["cursor"], None)

    def test_split_content_item_basic(self) -> None:
        """_split_content_item splits oversized content by lines."""
        from codeplane.mcp.delivery import _split_content_item

        content = "\n".join(f"line {i}" for i in range(100))
        item = {"path": "big.py", "content": content, "line_count": 100}

        partial, new_offset, complete = _split_content_item(item, 0, 200)
        assert not complete
        assert new_offset > 0
        assert new_offset < 100
        assert partial["content_truncated"] is True
        assert partial["content_lines_total"] == 100
        assert partial["content_lines_delivered"] == new_offset
        assert partial["content_offset"] == 0

    def test_split_content_item_resume_offset(self) -> None:
        """Resuming from a line offset delivers subsequent lines."""
        from codeplane.mcp.delivery import _split_content_item

        content = "\n".join(f"line {i}" for i in range(50))
        item = {"path": "big.py", "content": content, "line_count": 50}

        # First chunk
        _, offset1, _ = _split_content_item(item, 0, 300)
        # Second chunk from offset
        partial2, offset2, _ = _split_content_item(item, offset1, 300)
        assert partial2["content_offset"] == offset1
        assert offset2 > offset1
        # Content starts with the right line
        assert partial2["content"].startswith(f"line {offset1}")

    def test_split_content_item_completes(self) -> None:
        """When budget is large enough for remaining lines, item_complete is True."""
        from codeplane.mcp.delivery import _split_content_item

        content = "\n".join(f"line {i}" for i in range(5))
        item = {"path": "small.py", "content": content, "line_count": 5}

        partial, new_offset, complete = _split_content_item(item, 0, 10_000)
        assert complete
        assert new_offset == 5
        assert "content_truncated" not in partial

    def test_split_content_item_adjusts_range(self) -> None:
        """Range field is adjusted to reflect the delivered chunk."""
        from codeplane.mcp.delivery import _split_content_item

        content = "\n".join(f"line {i}" for i in range(100))
        item = {"path": "big.py", "content": content, "line_count": 100, "range": [50, 149]}

        partial, new_offset, _ = _split_content_item(item, 0, 200)
        assert partial["range"][0] == 50  # original start
        assert partial["range"][1] == 50 + new_offset - 1

        # Resume from offset
        partial2, _, _ = _split_content_item(item, new_offset, 200)
        assert partial2["range"][0] == 50 + new_offset

    def test_oversized_single_file_full_pagination(self) -> None:
        """A single oversized file gets content-split across multiple cursor pages."""
        from codeplane.mcp.delivery import _CURSOR_STORE, _try_paginate, resume_cursor

        big_content = "\n".join(f"line {i}: {'x' * 80}" for i in range(200))
        payload = {"files": [{"path": "huge.py", "content": big_content, "line_count": 200}]}
        page1 = _try_paginate(payload, "source", 4000)
        assert page1 is not None
        assert page1["has_more"] is True
        assert page1["files"][0].get("content_truncated") is True

        # Collect all lines across pages
        all_lines: list[str] = []
        all_lines.extend(page1["files"][0]["content"].split("\n"))
        cursor_id = page1["cursor"]

        pages = 1
        while True:
            page = resume_cursor(cursor_id)
            assert page is not None
            pages += 1
            all_lines.extend(page["files"][0]["content"].split("\n"))
            if not page.get("has_more"):
                break
            cursor_id = page["cursor"]

        assert pages > 2  # should take multiple pages
        # All original lines should be recovered
        assert len(all_lines) == 200
        assert all_lines[0] == "line 0: " + "x" * 80
        assert all_lines[-1] == "line 199: " + "x" * 80
        # Cursor should be cleaned up
        assert cursor_id not in _CURSOR_STORE

    def test_multi_item_with_oversized_first(self) -> None:
        """Multiple items where the first is oversized: splits first, then delivers rest."""
        from codeplane.mcp.delivery import _CURSOR_STORE, _try_paginate, resume_cursor

        big = {
            "path": "big.py",
            "content": "\n".join(f"line {i}: {'x' * 80}" for i in range(100)),
            "line_count": 100,
        }
        small = {"path": "small.py", "content": "hello", "line_count": 1}
        payload = {"files": [big, small]}
        page1 = _try_paginate(payload, "source", 4000)
        assert page1 is not None
        assert page1["has_more"] is True

        # Drain all pages
        big_lines: list[str] = []
        small_content = None
        cursor_id = page1["cursor"]
        current = page1

        while True:
            for f in current["files"]:
                if f["path"] == "big.py":
                    big_lines.extend(f["content"].split("\n"))
                elif f["path"] == "small.py":
                    small_content = f["content"]
            if not current.get("has_more"):
                break
            next_page = resume_cursor(cursor_id)
            assert next_page is not None
            current = next_page
            assert current is not None
            cursor_id = current.get("cursor", cursor_id)

        assert len(big_lines) == 100
        assert small_content == "hello"
        assert cursor_id not in _CURSOR_STORE
