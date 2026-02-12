"""Unit tests for diff enrichment (enrichment.py).

Tests cover:
- Summary generation
- Breaking summary generation
- Test file detection
- Confidence levels
"""

from __future__ import annotations

from codeplane.index._internal.diff.enrichment import (
    _build_breaking_summary,
    _build_summary,
    _enrich_test_files,
    _get_confidence,
)
from codeplane.index._internal.diff.models import StructuralChange

# ============================================================================
# Tests: Test file enrichment
# ============================================================================


class TestEnrichTestFiles:
    """Tests for _enrich_test_files."""

    def test_finds_test_files(self) -> None:
        result = _enrich_test_files(
            referencing_files=["src/main.py", "tests/test_main.py"],
            importing_files=["src/utils.py", "tests/test_utils.py"],
        )
        assert result is not None
        assert "tests/test_main.py" in result
        assert "tests/test_utils.py" in result

    def test_no_test_files(self) -> None:
        result = _enrich_test_files(
            referencing_files=["src/main.py"],
            importing_files=["src/utils.py"],
        )
        assert result is None

    def test_none_inputs(self) -> None:
        result = _enrich_test_files(None, None)
        assert result is None

    def test_deduplication(self) -> None:
        result = _enrich_test_files(
            referencing_files=["tests/test_main.py"],
            importing_files=["tests/test_main.py"],
        )
        assert result is not None
        assert len(result) == 1


# ============================================================================
# Tests: Confidence
# ============================================================================


class TestConfidence:
    """Tests for _get_confidence."""

    def test_python_is_high(self) -> None:
        assert _get_confidence("src/main.py") == "high"

    def test_unknown_extension_is_low(self) -> None:
        assert _get_confidence("src/main.xyz") == "low"


# ============================================================================
# Tests: Summary Generation
# ============================================================================


def _change(
    change: str = "added",
    structural_severity: str = "non_breaking",
    name: str = "foo",
    kind: str = "function",
) -> StructuralChange:
    return StructuralChange(
        path="src/a.py",
        kind=kind,
        name=name,
        qualified_name=None,
        change=change,
        structural_severity=structural_severity,
        behavior_change_risk="unknown",
        old_sig=None,
        new_sig=None,
        impact=None,
        nested_changes=None,
    )


class TestBuildSummary:
    """Tests for _build_summary."""

    def test_empty(self) -> None:
        assert _build_summary([]) == "No changes detected"

    def test_single_change(self) -> None:
        result = _build_summary([_change("added")])
        assert "added" in result

    def test_multiple_types(self) -> None:
        result = _build_summary(
            [
                _change("added"),
                _change("removed"),
                _change("signature_changed"),
            ]
        )
        assert "added" in result
        assert "removed" in result
        assert "signature changed" in result


class TestBuildBreakingSummary:
    """Tests for _build_breaking_summary."""

    def test_no_breaking(self) -> None:
        assert _build_breaking_summary([_change("added")]) is None

    def test_has_breaking(self) -> None:
        result = _build_breaking_summary(
            [
                _change("removed", "breaking", "foo"),
            ]
        )
        assert result is not None
        assert "1 breaking change" in result
        assert "foo" in result

    def test_multiple_breaking(self) -> None:
        result = _build_breaking_summary(
            [
                _change("removed", "breaking", "foo"),
                _change("signature_changed", "breaking", "bar"),
            ]
        )
        assert result is not None
        assert "2 breaking changes" in result
