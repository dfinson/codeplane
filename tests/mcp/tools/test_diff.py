"""Integration tests for semantic diff MCP tool.

Tests cover:
- Agentic hint generation
- Result serialization
"""

from __future__ import annotations

from codeplane.index._internal.diff.models import (
    ImpactInfo,
    SemanticDiffResult,
    StructuralChange,
)
from codeplane.mcp.tools.diff import _build_agentic_hint, _result_to_dict

# ============================================================================
# Helpers
# ============================================================================


def _change(
    change: str = "added",
    severity: str = "non_breaking",
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
        severity=severity,
        old_sig="def old()",
        new_sig="def new()",
        impact=impact,
        nested_changes=None,
    )


def _result(
    changes: list[StructuralChange] | None = None,
    summary: str = "test",
    breaking: str | None = None,
) -> SemanticDiffResult:
    return SemanticDiffResult(
        structural_changes=changes or [],
        non_structural_changes=[],
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
        assert d["pagination"] == {}

    def test_no_pagination_under_limit(self) -> None:
        changes = [_change(name=f"fn_{i}") for i in range(5)]
        d = _result_to_dict(_result(changes))
        assert len(d["structural_changes"]) == 5
        assert d["pagination"] == {}

    def test_pagination_triggers_over_limit(self) -> None:
        changes = [_change(name=f"fn_{i}") for i in range(10)]
        d = _result_to_dict(_result(changes), limit=3)
        assert len(d["structural_changes"]) == 3
        assert d["pagination"]["next_cursor"] == "3"
        assert d["pagination"]["total_estimate"] == 10

    def test_cursor_continues_from_offset(self) -> None:
        changes = [_change(name=f"fn_{i}") for i in range(10)]
        d = _result_to_dict(_result(changes), cursor="3", limit=3)
        assert len(d["structural_changes"]) == 3
        assert d["structural_changes"][0]["name"] == "fn_3"
        assert d["pagination"]["next_cursor"] == "6"

    def test_cursor_last_page(self) -> None:
        changes = [_change(name=f"fn_{i}") for i in range(10)]
        d = _result_to_dict(_result(changes), cursor="9", limit=3)
        assert len(d["structural_changes"]) == 1
        assert d["structural_changes"][0]["name"] == "fn_9"
        assert d["pagination"] == {}

    def test_agentic_hint_computed_from_all_changes(self) -> None:
        """agentic_hint reflects ALL changes, not just the paginated page."""
        changes = [_change("body_changed", "non_breaking", f"fn_{i}") for i in range(10)]
        d = _result_to_dict(_result(changes), limit=3)
        # Hint should mention all 10, not just the 3 on this page
        assert "10 function bodies changed" in d["agentic_hint"]
