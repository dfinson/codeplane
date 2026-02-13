"""Integration tests for semantic diff MCP tool.

Tests cover:
- Agentic hint generation
- Result serialization
"""

from __future__ import annotations

from typing import Any

from codeplane.index._internal.diff.models import (
    AnalysisScope,
    FileChangeInfo,
    ImpactInfo,
    SemanticDiffResult,
    StructuralChange,
)
from codeplane.mcp.budget import BudgetAccumulator
from codeplane.mcp.tools.diff import (
    _build_agentic_hint,
    _DiffCache,
    _parse_cursor,
    _result_to_dict,
)

# ============================================================================
# Helpers
# ============================================================================


def _change(
    change: str = "added",
    structural_severity: str = "non_breaking",
    name: str = "foo",
    kind: str = "function",
    qualified_name: str | None = None,
    impact: ImpactInfo | None = None,
) -> StructuralChange:
    return StructuralChange(
        path="src/a.py",
        kind=kind,
        name=name,
        qualified_name=qualified_name,
        change=change,
        structural_severity=structural_severity,
        behavior_change_risk="unknown",
        risk_basis=None,
        old_sig="def old()",
        new_sig="def new()",
        impact=impact,
        nested_changes=None,
    )


def _file_change(
    path: str = "data/test.json",
    status: str = "modified",
    category: str = "config",
) -> FileChangeInfo:
    return FileChangeInfo(
        path=path,
        status=status,
        category=category,
        language=None,
    )


def _result(
    changes: list[StructuralChange] | None = None,
    non_structural: list[FileChangeInfo] | None = None,
    summary: str = "test",
    breaking: str | None = None,
) -> SemanticDiffResult:
    return SemanticDiffResult(
        structural_changes=changes or [],
        non_structural_changes=non_structural or [],
        summary=summary,
        breaking_summary=breaking,
        files_analyzed=1 if changes else 0,
        base_description="HEAD",
        target_description="working tree",
    )


# ============================================================================
# Tests: Agentic Hint Generation
# ============================================================================


class TestAgenticHint:
    """Tests for _build_agentic_hint."""

    def test_no_changes(self) -> None:
        hint = _build_agentic_hint(_result())
        assert "No actionable changes" in hint

    def test_signature_changed_with_refs(self) -> None:
        impact = ImpactInfo(
            reference_count=5,
            referencing_files=["src/a.py", "src/b.py"],
        )
        hint = _build_agentic_hint(
            _result(
                [
                    _change(
                        "signature_changed",
                        "breaking",
                        "connect",
                        "method",
                        "Client.connect",
                        impact,
                    ),
                ]
            )
        )
        assert "Signature of Client.connect" in hint
        assert "5 references" in hint

    def test_removed_hint(self) -> None:
        hint = _build_agentic_hint(_result([_change("removed", "breaking", "OldClass", "class")]))
        assert "OldClass was removed" in hint

    def test_body_changed_hint(self) -> None:
        hint = _build_agentic_hint(
            _result(
                [
                    _change("body_changed", "non_breaking", "foo"),
                    _change("body_changed", "non_breaking", "bar"),
                ]
            )
        )
        assert "2 function bodies changed" in hint

    def test_affected_tests_hint(self) -> None:
        impact = ImpactInfo(affected_test_files=["tests/test_a.py"])
        hint = _build_agentic_hint(_result([_change("removed", "breaking", "foo", impact=impact)]))
        assert "Affected test files:" in hint
        assert "tests/test_a.py" in hint


# ============================================================================
# Tests: Result Serialization
# ============================================================================


class TestResultSerialization:
    """Tests for _result_to_dict."""

    def test_empty_result(self) -> None:
        d = _result_to_dict(_result())
        assert d["summary"] == "test"
        assert d["structural_changes"] == []

    def test_with_impact(self) -> None:
        impact = ImpactInfo(reference_count=3, referencing_files=["a.py"])
        d = _result_to_dict(_result([_change("removed", "breaking", "foo", impact=impact)]))
        assert d["structural_changes"][0]["impact"]["reference_count"] == 3


# ============================================================================
# Tests: Pagination
# ============================================================================


class TestPagination:
    """Tests for _result_to_dict pagination."""

    def test_empty_pagination_when_no_changes(self) -> None:
        d = _result_to_dict(_result())
        assert "next_cursor" not in d["pagination"]
        assert d["pagination"].get("total_structural") == 0
        assert d["pagination"].get("total_non_structural") == 0

    def test_no_pagination_under_budget(self) -> None:
        changes = [_change(name=f"fn_{i}") for i in range(5)]
        d = _result_to_dict(_result(changes))
        assert len(d["structural_changes"]) == 5
        assert "next_cursor" not in d["pagination"]
        assert "truncated" not in d["pagination"]

    def test_pagination_triggers_over_budget(self, monkeypatch: Any) -> None:
        # Swap in a tiny-budget accumulator so even small items overflow
        class _TinyBudget(BudgetAccumulator):
            def __init__(self, budget: int = 500) -> None:
                super().__init__(budget=budget)

        monkeypatch.setattr("codeplane.mcp.tools.diff.BudgetAccumulator", _TinyBudget)
        changes = [_change(name=f"fn_{i}") for i in range(10)]
        d = _result_to_dict(_result(changes), cache_id=1)
        assert len(d["structural_changes"]) < 10
        assert d["pagination"]["truncated"] is True
        assert "next_cursor" in d["pagination"]
        # Cursor format: "<cache_id>:<structural_offset>:<non_structural_offset>"
        assert d["pagination"]["next_cursor"].startswith("1:")
        assert d["pagination"]["total_structural"] == 10

    def test_cursor_continues_from_offset(self) -> None:
        changes = [_change(name=f"fn_{i}") for i in range(10)]
        # cursor_offset=3 should skip first 3
        d = _result_to_dict(_result(changes), cursor_offset=3)
        assert d["structural_changes"][0]["name"] == "fn_3"

    def test_cursor_last_page(self) -> None:
        changes = [_change(name=f"fn_{i}") for i in range(10)]
        d = _result_to_dict(_result(changes), cursor_offset=9)
        assert len(d["structural_changes"]) == 1
        assert d["structural_changes"][0]["name"] == "fn_9"
        assert "next_cursor" not in d["pagination"]
        assert "truncated" not in d["pagination"]

    def test_agentic_hint_computed_from_all_changes(self, monkeypatch: Any) -> None:
        """agentic_hint reflects ALL changes, not just the paginated page."""

        class _TinyBudget(BudgetAccumulator):
            def __init__(self, budget: int = 500) -> None:
                super().__init__(budget=budget)

        monkeypatch.setattr("codeplane.mcp.tools.diff.BudgetAccumulator", _TinyBudget)
        changes = [_change("body_changed", "non_breaking", f"fn_{i}") for i in range(10)]
        d = _result_to_dict(_result(changes))
        # Hint should mention all 10, not just the items on this page
        assert "10 function bodies changed" in d["agentic_hint"]


# ============================================================================
# Tests: Non-structural Changes Pagination
# ============================================================================


class TestNonStructuralPagination:
    """Tests for non_structural_changes pagination in _result_to_dict."""

    def test_non_structural_paginated_after_structural_complete(self, monkeypatch: Any) -> None:
        """non_structural_changes only paginated when structural_changes is done."""

        class _TinyBudget(BudgetAccumulator):
            def __init__(self, budget: int = 2000) -> None:
                super().__init__(budget=budget)

        monkeypatch.setattr("codeplane.mcp.tools.diff.BudgetAccumulator", _TinyBudget)

        # 3 structural changes (small) + 10 non_structural (should overflow)
        structural = [_change(name=f"fn_{i}") for i in range(3)]
        non_structural = [_file_change(f"data/file_{i}.json") for i in range(10)]

        d = _result_to_dict(_result(structural, non_structural), cache_id=1)

        # All structural should fit
        assert len(d["structural_changes"]) == 3
        # Some non_structural should be on this page
        assert len(d["non_structural_changes"]) > 0
        # If all non_structural fit, no cursor; if not, cursor present
        total_non_structural = d["pagination"]["total_non_structural"]
        assert total_non_structural == 10

    def test_cursor_continues_non_structural(self, monkeypatch: Any) -> None:
        """Pagination cursor allows continuing non_structural_changes."""

        class _TinyBudget(BudgetAccumulator):
            def __init__(self, budget: int = 500) -> None:
                super().__init__(budget=budget)

        monkeypatch.setattr("codeplane.mcp.tools.diff.BudgetAccumulator", _TinyBudget)

        # No structural, all non_structural
        non_structural = [_file_change(f"data/file_{i}.json") for i in range(20)]

        # First page
        d1 = _result_to_dict(_result([], non_structural), cache_id=1)
        first_page_count = len(d1["non_structural_changes"])
        assert first_page_count < 20  # Should overflow
        assert d1["pagination"]["truncated"] is True

        # Parse cursor: "cache_id:structural_offset:non_structural_offset"
        cursor = d1["pagination"]["next_cursor"]
        assert cursor is not None
        parts = cursor.split(":")
        assert len(parts) == 3

        # Second page with offset
        non_structural_offset = int(parts[2])
        d2 = _result_to_dict(
            _result([], non_structural),
            cursor_offset=0,
            non_structural_offset=non_structural_offset,
            cache_id=1,
        )
        second_page_count = len(d2["non_structural_changes"])
        assert d2["non_structural_changes"][0]["path"] == f"data/file_{first_page_count}.json"
        assert second_page_count > 0

    def test_empty_non_structural_no_extra_pagination(self) -> None:
        """Empty non_structural_changes doesn't affect pagination."""
        # No structural, no non_structural
        d = _result_to_dict(_result())
        assert d["pagination"]["total_structural"] == 0
        assert d["pagination"]["total_non_structural"] == 0
        assert "next_cursor" not in d["pagination"]

    def test_mixed_pagination_structural_then_non_structural(self, monkeypatch: Any) -> None:
        """Pagination flows from structural to non_structural across pages."""

        class _TinyBudget(BudgetAccumulator):
            def __init__(self, budget: int = 400) -> None:
                super().__init__(budget=budget)

        monkeypatch.setattr("codeplane.mcp.tools.diff.BudgetAccumulator", _TinyBudget)

        # 5 structural + 5 non_structural - should span multiple pages
        structural = [_change(name=f"fn_{i}") for i in range(5)]
        non_structural = [_file_change(f"data/file_{i}.json") for i in range(5)]

        # Page 1: some structural
        d1 = _result_to_dict(_result(structural, non_structural), cache_id=1)
        structural_page1 = len(d1["structural_changes"])
        assert structural_page1 > 0
        assert d1["pagination"]["total_structural"] == 5
        assert d1["pagination"]["total_non_structural"] == 5

        if structural_page1 < 5:
            # Page 2: continue structural, maybe start non_structural
            assert d1["pagination"]["truncated"] is True
            cursor = d1["pagination"]["next_cursor"]
            parts = cursor.split(":")
            d2 = _result_to_dict(
                _result(structural, non_structural),
                cursor_offset=int(parts[1]),
                non_structural_offset=int(parts[2]),
                cache_id=1,
            )

            # Eventually all items should be seen across all pages
            total_structural_seen = structural_page1 + len(d2["structural_changes"])
            total_non_structural_seen = len(d1["non_structural_changes"]) + len(
                d2["non_structural_changes"]
            )

            # Either we've seen all, or there's more to paginate
            if "next_cursor" not in d2["pagination"]:
                assert total_structural_seen == 5
                assert total_non_structural_seen == 5


# ============================================================================
# Tests: Cursor Parsing
# ============================================================================


class TestParseCursor:
    """Tests for _parse_cursor."""

    def test_none_returns_no_cache_zero_offsets(self) -> None:
        cache_id, structural_offset, non_structural_offset = _parse_cursor(None)
        assert cache_id is None
        assert structural_offset == 0
        assert non_structural_offset == 0

    def test_valid_three_part_cursor(self) -> None:
        cache_id, structural_offset, non_structural_offset = _parse_cursor("42:17:5")
        assert cache_id == 42
        assert structural_offset == 17
        assert non_structural_offset == 5

    def test_legacy_two_part_cursor(self) -> None:
        """Legacy 2-part cursors assume structural offset only."""
        cache_id, structural_offset, non_structural_offset = _parse_cursor("42:17")
        assert cache_id == 42
        assert structural_offset == 17
        assert non_structural_offset == 0

    def test_legacy_plain_offset(self) -> None:
        """Legacy cursors (plain int) are treated as offset-only."""
        cache_id, structural_offset, non_structural_offset = _parse_cursor("5")
        assert cache_id is None
        assert structural_offset == 5
        assert non_structural_offset == 0

    def test_malformed_returns_defaults(self) -> None:
        cache_id, structural_offset, non_structural_offset = _parse_cursor("garbage")
        assert cache_id is None
        assert structural_offset == 0
        assert non_structural_offset == 0


# ============================================================================
# Tests: DiffCache
# ============================================================================


class TestDiffCache:
    """Tests for _DiffCache."""

    def test_store_and_retrieve(self) -> None:
        cache = _DiffCache(max_entries=3)
        r = _result([_change(name="a")])
        cid = cache.store(r)
        assert cache.get(cid) is r

    def test_cache_miss(self) -> None:
        cache = _DiffCache(max_entries=3)
        assert cache.get(999) is None

    def test_eviction_on_overflow(self) -> None:
        cache = _DiffCache(max_entries=2)
        r1 = _result([_change(name="a")])
        r2 = _result([_change(name="b")])
        r3 = _result([_change(name="c")])
        id1 = cache.store(r1)
        cache.store(r2)
        cache.store(r3)
        # r1 should be evicted
        assert cache.get(id1) is None

    def test_ttl_expiration(self) -> None:
        import codeplane.mcp.tools.diff as diff_mod

        cache = _DiffCache(max_entries=5)
        r = _result([_change(name="a")])
        cid = cache.store(r)

        # Expire the entry by rewinding its creation timestamp
        entry = cache._entries[cid]
        original_ttl = diff_mod._CACHE_TTL_SECONDS
        entry.created_at -= original_ttl + 1

        assert cache.get(cid) is None

    def test_clear(self) -> None:
        cache = _DiffCache(max_entries=3)
        cid = cache.store(_result())
        cache.clear()
        assert cache.get(cid) is None


# ============================================================================
# Tests: Scope Serialization
# ============================================================================


class TestScopeSerialization:
    """Tests for AnalysisScope serialization in _result_to_dict."""

    def test_scope_included_when_present(self) -> None:
        scope = AnalysisScope(
            base_sha="abc123",
            target_sha="def456",
            worktree_dirty=False,
            mode="git",
            files_parsed=10,
            files_no_grammar=3,
            languages_analyzed=["python", "typescript"],
        )
        r = _result([_change(name="a")])
        r.scope = scope
        d = _result_to_dict(r)
        assert "scope" in d
        assert d["scope"]["base_sha"] == "abc123"
        assert d["scope"]["target_sha"] == "def456"
        assert d["scope"]["worktree_dirty"] is False
        assert d["scope"]["mode"] == "git"
        assert d["scope"]["files_parsed"] == 10
        assert d["scope"]["files_no_grammar"] == 3
        assert d["scope"]["languages_analyzed"] == ["python", "typescript"]
        assert d["scope"]["entity_id_scheme"] == "def_uid_v1"

    def test_scope_omitted_when_none(self) -> None:
        d = _result_to_dict(_result())
        assert "scope" not in d

    def test_scope_drops_none_values(self) -> None:
        """None values in scope are not serialized."""
        scope = AnalysisScope(
            base_sha=None,
            target_sha=None,
            worktree_dirty=None,
            mode="epoch",
            files_parsed=5,
        )
        r = _result([_change(name="a")])
        r.scope = scope
        d = _result_to_dict(r)
        assert "base_sha" not in d["scope"]
        assert "target_sha" not in d["scope"]
        assert "worktree_dirty" not in d["scope"]
        assert d["scope"]["mode"] == "epoch"


# ============================================================================
# Tests: Risk Basis Serialization
# ============================================================================


class TestRiskBasisSerialization:
    """Tests for risk_basis serialization in _result_to_dict."""

    def test_risk_basis_included_when_present(self) -> None:
        c = StructuralChange(
            path="src/a.py",
            kind="function",
            name="foo",
            qualified_name=None,
            change="removed",
            structural_severity="breaking",
            behavior_change_risk="high",
            risk_basis="symbol_removed",
            old_sig="def foo()",
            new_sig=None,
            impact=None,
        )
        d = _result_to_dict(_result([c]))
        assert d["structural_changes"][0]["risk_basis"] == "symbol_removed"

    def test_risk_basis_fallback_when_risk_not_low(self) -> None:
        """Schema invariant: risk != low and no basis → unclassified_change."""
        d = _result_to_dict(_result([_change(name="bar")]))
        # _change() sets behavior_change_risk="unknown" and risk_basis=None
        assert d["structural_changes"][0]["risk_basis"] == "unclassified_change"

    def test_risk_basis_omitted_when_risk_low(self) -> None:
        c = StructuralChange(
            path="src/a.py",
            kind="function",
            name="bar",
            qualified_name=None,
            change="added",
            structural_severity="non_breaking",
            behavior_change_risk="low",
            risk_basis=None,
            old_sig=None,
            new_sig=None,
            impact=None,
        )
        d = _result_to_dict(_result([c]))
        assert "risk_basis" not in d["structural_changes"][0]


# ============================================================================
# Tests: Import Count Serialization
# ============================================================================


class TestImportCountSerialization:
    """Tests for import_count in ImpactInfo serialization."""

    def test_import_count_separate_from_reference_count(self) -> None:
        impact = ImpactInfo(
            reference_count=5,
            import_count=2,
            referencing_files=["a.py", "b.py"],
            importing_files=["c.py", "d.py"],
        )
        d = _result_to_dict(_result([_change(name="fn", impact=impact)]))
        impact_d = d["structural_changes"][0]["impact"]
        assert impact_d["reference_count"] == 5
        assert impact_d["import_count"] == 2

    def test_import_count_omitted_when_none(self) -> None:
        impact = ImpactInfo(reference_count=3)
        d = _result_to_dict(_result([_change(name="fn", impact=impact)]))
        impact_d = d["structural_changes"][0]["impact"]
        assert "import_count" not in impact_d


# ============================================================================
# Tests: Schema Refinements (classification_confidence, invariants, renames)
# ============================================================================


class TestClassificationConfidence:
    """Tests for classification_confidence always present in serialized output."""

    def test_classification_confidence_always_emitted(self) -> None:
        d = _result_to_dict(_result([_change(name="fn")]))
        assert d["structural_changes"][0]["classification_confidence"] == "high"

    def test_classification_confidence_value_propagated(self) -> None:
        c = StructuralChange(
            path="src/a.py",
            kind="function",
            name="fn",
            qualified_name=None,
            change="added",
            structural_severity="non_breaking",
            behavior_change_risk="low",
            old_sig=None,
            new_sig=None,
            impact=None,
            classification_confidence="low",
        )
        d = _result_to_dict(_result([c]))
        assert d["structural_changes"][0]["classification_confidence"] == "low"


class TestRenameFields:
    """Tests for old_name and previous_entity_id on renames."""

    def test_rename_includes_old_name(self) -> None:
        c = StructuralChange(
            path="src/a.py",
            kind="function",
            name="new_fn",
            qualified_name=None,
            change="renamed",
            structural_severity="breaking",
            behavior_change_risk="high",
            old_sig=None,
            new_sig=None,
            impact=None,
            old_name="old_fn",
        )
        d = _result_to_dict(_result([c]))
        assert d["structural_changes"][0]["old_name"] == "old_fn"

    def test_rename_includes_previous_entity_id(self) -> None:
        c = StructuralChange(
            path="src/a.py",
            kind="function",
            name="new_fn",
            qualified_name=None,
            change="renamed",
            structural_severity="breaking",
            behavior_change_risk="high",
            old_sig=None,
            new_sig=None,
            impact=None,
            previous_entity_id="some-old-uid",
        )
        d = _result_to_dict(_result([c]))
        assert d["structural_changes"][0]["previous_entity_id"] == "some-old-uid"

    def test_rename_fields_absent_on_non_rename(self) -> None:
        d = _result_to_dict(_result([_change(change="added")]))
        ch = d["structural_changes"][0]
        assert "old_name" not in ch
        assert "previous_entity_id" not in ch


class TestSchemaInvariants:
    """Tests for mandatory field invariants in serializer."""

    def test_signature_changed_emits_both_sigs(self) -> None:
        c = StructuralChange(
            path="src/a.py",
            kind="function",
            name="fn",
            qualified_name=None,
            change="signature_changed",
            structural_severity="breaking",
            behavior_change_risk="high",
            old_sig="def fn(x)",
            new_sig=None,
            impact=None,
        )
        d = _result_to_dict(_result([c]))
        ch = d["structural_changes"][0]
        assert ch["old_signature"] == "def fn(x)"
        assert ch["new_signature"] == ""  # Falls back to empty string

    def test_body_changed_emits_lines_changed(self) -> None:
        c = StructuralChange(
            path="src/a.py",
            kind="function",
            name="fn",
            qualified_name=None,
            change="body_changed",
            structural_severity="non_breaking",
            behavior_change_risk="unknown",
            old_sig=None,
            new_sig=None,
            impact=None,
            lines_changed=None,
        )
        d = _result_to_dict(_result([c]))
        assert d["structural_changes"][0]["lines_changed"] == 0  # Default

    def test_body_changed_preserves_actual_lines(self) -> None:
        c = StructuralChange(
            path="src/a.py",
            kind="function",
            name="fn",
            qualified_name=None,
            change="body_changed",
            structural_severity="non_breaking",
            behavior_change_risk="unknown",
            old_sig=None,
            new_sig=None,
            impact=None,
            lines_changed=42,
        )
        d = _result_to_dict(_result([c]))
        assert d["structural_changes"][0]["lines_changed"] == 42


class TestPaginationTotal:
    """Tests for 'total' field in pagination (replaces total_estimate)."""

    def test_pagination_uses_total_not_total_estimate(self) -> None:
        d = _result_to_dict(_result([_change()]))
        assert "total_structural" in d["pagination"]
        assert "total_non_structural" in d["pagination"]
        assert "total_estimate" not in d["pagination"]
