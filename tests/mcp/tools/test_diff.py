"""Integration tests for semantic diff MCP tool.

Tests cover:
- Agentic hint generation
- Result serialization
"""

from __future__ import annotations

from codeplane.index._internal.diff.models import (
    AnalysisScope,
    FileChangeInfo,
    ImpactInfo,
    SemanticDiffResult,
    StructuralChange,
)
from codeplane.mcp.tools.diff import (
    _build_agentic_hint,
    _classify_domains,
    _detect_cross_domain_edges,
    _domain_key,
    _result_to_dict,
    _result_to_text,
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
    behavior_risk: str = "unknown",
    path: str = "src/a.py",
) -> StructuralChange:
    return StructuralChange(
        path=path,
        kind=kind,
        name=name,
        qualified_name=qualified_name,
        change=change,
        structural_severity=structural_severity,
        behavior_change_risk=behavior_risk,
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
    """Tests for _build_agentic_hint.

    The hint is intentionally compact - just counts, no symbol names.
    Full details are in structural_changes.
    """

    def test_no_changes(self) -> None:
        hint = _build_agentic_hint(_result())
        assert "No structural changes" in hint

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
        assert "1 signature changes" in hint

    def test_removed_hint(self) -> None:
        hint = _build_agentic_hint(_result([_change("removed", "breaking", "OldClass", "class")]))
        assert "1 removals" in hint

    def test_body_changed_hint(self) -> None:
        hint = _build_agentic_hint(
            _result(
                [
                    _change("body_changed", "non_breaking", "foo"),
                    _change("body_changed", "non_breaking", "bar"),
                ]
            )
        )
        assert "2 body changes" in hint

    def test_affected_tests_hint(self) -> None:
        impact = ImpactInfo(affected_test_files=["tests/test_a.py"])
        hint = _build_agentic_hint(_result([_change("removed", "breaking", "foo", impact=impact)]))
        assert "1 affected test files" in hint

    def test_high_risk_noted(self) -> None:
        hint = _build_agentic_hint(
            _result(
                [
                    _change("body_changed", "non_breaking", "foo", behavior_risk="high"),
                    _change("body_changed", "non_breaking", "bar", behavior_risk="low"),
                ]
            )
        )
        assert "2 body changes (1 high-risk)" in hint


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


# ============================================================================
# Tests: Text Format Serialization
# ============================================================================


class TestResultToText:
    """Tests for _result_to_text."""

    def test_empty_result(self) -> None:
        d = _result_to_text(_result())
        assert d["summary"] == "test"
        assert d["structural_changes"] == []
        assert d["non_structural_changes"] == []
        assert d["files_analyzed"] == 0
        assert d["base"] == "HEAD"
        assert d["target"] == "working tree"

    def test_structural_changes_as_text_lines(self) -> None:
        c = _change(change="added", name="new_func", kind="function")
        d = _result_to_text(_result([c]))
        lines = d["structural_changes"]
        assert isinstance(lines, list)
        # First line is the risk header comment
        assert lines[0].startswith("#")
        content_lines = [ln for ln in lines if not ln.startswith("#")]
        assert len(content_lines) == 1
        assert "added function new_func" in content_lines[0]

    def test_non_structural_changes_as_text(self) -> None:
        fc = _file_change(path="data/config.json", status="modified", category="config")
        d = _result_to_text(_result(non_structural=[fc]))
        lines = d["non_structural_changes"]
        assert len(lines) == 1
        assert "modified data/config.json" in lines[0]
        assert "config" in lines[0]

    def test_non_structural_with_language(self) -> None:
        fc = FileChangeInfo(path="a.rs", status="added", category="prod", language="rust")
        d = _result_to_text(_result(non_structural=[fc]))
        assert "rust" in d["non_structural_changes"][0]

    def test_breaking_summary(self) -> None:
        d = _result_to_text(_result(breaking="2 breaking: foo, bar"))
        assert d["breaking_summary"] == "2 breaking: foo, bar"

    def test_scope_included(self) -> None:
        r = _result()
        r.scope = AnalysisScope(base_sha="abc123", mode="git")
        d = _result_to_text(r)
        assert "scope" in d
        assert d["scope"]["base_sha"] == "abc123"

    def test_scope_drops_none_values(self) -> None:
        r = _result()
        r.scope = AnalysisScope(base_sha="abc", target_sha=None)
        d = _result_to_text(r)
        assert "target_sha" not in d["scope"]

    def test_scope_omitted_when_none(self) -> None:
        d = _result_to_text(_result())
        assert "scope" not in d

    def test_agentic_hint_present(self) -> None:
        d = _result_to_text(_result())
        assert "agentic_hint" in d

    def test_multiple_structural_changes(self) -> None:
        changes = [
            _change(change="added", name="a"),
            _change(change="removed", name="b"),
            _change(change="body_changed", name="c"),
        ]
        d = _result_to_text(_result(changes))
        content_lines = [ln for ln in d["structural_changes"] if not ln.startswith("#")]
        assert len(content_lines) == 3

    def test_signature_change_shows_sigs(self) -> None:
        c = _change(change="signature_changed", name="func")
        d = _result_to_text(_result([c]))
        content_lines = [ln for ln in d["structural_changes"] if not ln.startswith("#")]
        assert "old:" in content_lines[0]
        assert "new:" in content_lines[0]

    def test_risk_unknown_header_comment(self) -> None:
        """risk:unknown is omitted; header comment documents convention."""
        c = _change(change="added", name="foo", behavior_risk="unknown")
        d = _result_to_text(_result([c]))
        headers = [ln for ln in d["structural_changes"] if ln.startswith("#")]
        assert any("entries without risk:" in h for h in headers)
        content = [ln for ln in d["structural_changes"] if not ln.startswith("#")]
        assert "risk:" not in content[0]

    def test_no_headers_when_empty(self) -> None:
        """Empty result produces no header comments."""
        d = _result_to_text(_result())
        assert d["structural_changes"] == []

    def test_nested_path_dedup(self) -> None:
        """Nested entries omit path, showing only :start-end."""
        inner = StructuralChange(
            path="src/a.py",
            kind="method",
            name="inner_fn",
            qualified_name=None,
            change="body_changed",
            structural_severity="non_breaking",
            behavior_change_risk="low",
            old_sig=None,
            new_sig=None,
            impact=None,
            start_line=30,
            end_line=40,
        )
        outer = StructuralChange(
            path="src/a.py",
            kind="class",
            name="MyClass",
            qualified_name=None,
            change="body_changed",
            structural_severity="non_breaking",
            behavior_change_risk="low",
            old_sig=None,
            new_sig=None,
            impact=None,
            start_line=10,
            end_line=50,
            nested_changes=[inner],
        )
        d = _result_to_text(_result([outer]))
        content = [ln for ln in d["structural_changes"] if not ln.startswith("#")]
        assert len(content) == 2
        # Parent has full path
        assert "src/a.py:10-50" in content[0]
        # Nested has only :start-end (no path)
        assert ":30-40" in content[1]
        assert "src/a.py" not in content[1]

    def test_test_aliases(self) -> None:
        """Test paths appearing 3+ times get aliased."""
        impact = ImpactInfo(
            affected_test_files=["tests/very/long/path/test_module.py"],
        )
        # Create 4 changes all referencing the same test file
        changes = [_change(change="body_changed", name=f"fn{i}", impact=impact) for i in range(4)]
        d = _result_to_text(_result(changes))
        headers = [ln for ln in d["structural_changes"] if ln.startswith("#")]
        alias_headers = [h for h in headers if "test aliases:" in h]
        assert len(alias_headers) == 1
        assert "t1=tests/very/long/path/test_module.py" in alias_headers[0]
        # Content uses alias
        content = [ln for ln in d["structural_changes"] if not ln.startswith("#")]
        assert all("tests/very/long/path/test_module.py" not in ln for ln in content)
        assert any("t1" in ln for ln in content)

    def test_no_alias_header_when_no_aliases(self) -> None:
        """No test alias header when no test paths repeat enough."""
        c = _change(change="added", name="foo")
        d = _result_to_text(_result([c]))
        headers = [ln for ln in d["structural_changes"] if ln.startswith("#")]
        assert not any("test aliases:" in h for h in headers)


# =============================================================================
# Domain Classification
# =============================================================================


class TestDomainKey:
    """Unit tests for _domain_key."""

    def test_deep_path(self) -> None:
        assert _domain_key("src/codeplane/mcp/tools/diff.py") == "src/codeplane"

    def test_two_segments(self) -> None:
        assert _domain_key("tests/mcp/test_foo.py") == "tests/mcp"

    def test_shallow_path(self) -> None:
        assert _domain_key("src/foo.py") == "src"

    def test_root_file(self) -> None:
        assert _domain_key("README.md") == "(root)"


class TestClassifyDomains:
    """Unit tests for _classify_domains."""

    def test_single_domain(self) -> None:
        changes = [
            _change(change="added", name="foo", path="src/codeplane/a.py"),
            _change(change="removed", name="bar", path="src/codeplane/b.py"),
        ]
        domains = _classify_domains(changes)
        assert len(domains) == 1
        assert domains[0]["name"] == "src/codeplane"
        assert domains[0]["change_count"] == 2
        assert domains[0]["review_priority"] == 1

    def test_multi_domain_sorted_by_risk(self) -> None:
        changes = [
            _change(change="added", name="foo", path="src/codeplane/a.py"),
            _change(
                change="removed",
                name="bar",
                structural_severity="breaking",
                path="tests/mcp/test_a.py",
            ),
        ]
        domains = _classify_domains(changes)
        assert len(domains) == 2
        # Breaking domain should be priority 1
        assert domains[0]["name"] == "tests/mcp"
        assert domains[0]["breaking_count"] == 1
        assert domains[0]["review_priority"] == 1
        assert domains[1]["review_priority"] == 2

    def test_high_risk_body_changes_counted(self) -> None:
        changes = [
            _change(
                change="body_changed",
                name="fn",
                path="src/codeplane/x.py",
                behavior_risk="high",
            ),
        ]
        domains = _classify_domains(changes)
        assert domains[0]["high_risk_count"] == 1

    def test_non_structural_included_in_files(self) -> None:
        changes = [_change(change="added", name="foo", path="src/codeplane/a.py")]
        non_structural = [
            FileChangeInfo(path="src/codeplane/b.py", status="modified", category="config")
        ]
        domains = _classify_domains(changes, non_structural)
        assert "src/codeplane/b.py" in domains[0]["files"]


class TestDetectCrossDomainEdges:
    """Unit tests for _detect_cross_domain_edges."""

    def test_no_edges_single_domain(self) -> None:
        changes = [
            _change(change="added", name="foo", path="src/codeplane/a.py"),
            _change(change="added", name="bar", path="src/codeplane/b.py"),
        ]
        domains = _classify_domains(changes)
        edges = _detect_cross_domain_edges(changes, domains)
        assert edges == []

    def test_cross_domain_edge_detected(self) -> None:
        imp = ImpactInfo(importing_files=["tests/mcp/test_a.py"])
        changes = [
            _change(change="removed", name="foo", path="src/codeplane/a.py", impact=imp),
            _change(change="added", name="bar", path="tests/mcp/test_a.py"),
        ]
        domains = _classify_domains(changes)
        edges = _detect_cross_domain_edges(changes, domains)
        assert len(edges) == 1
        assert edges[0]["from_domain"] == "src/codeplane"
        assert edges[0]["to_domain"] == "tests/mcp"

    def test_no_edge_for_same_domain_imports(self) -> None:
        imp = ImpactInfo(importing_files=["src/codeplane/b.py"])
        changes = [
            _change(change="added", name="foo", path="src/codeplane/a.py", impact=imp),
            _change(change="added", name="bar", path="src/codeplane/b.py"),
        ]
        domains = _classify_domains(changes)
        edges = _detect_cross_domain_edges(changes, domains)
        assert edges == []


class TestMultiDomainHint:
    """Test _build_agentic_hint with multi-domain changes."""

    def test_review_plan_emitted(self) -> None:
        changes = [
            _change(
                change="removed",
                structural_severity="breaking",
                name="foo",
                path="src/codeplane/a.py",
            ),
            _change(change="added", name="bar", path="tests/mcp/test_a.py"),
        ]
        domains = _classify_domains(changes)
        cross_edges = _detect_cross_domain_edges(changes, domains)
        hint = _build_agentic_hint(_result(changes), domains, cross_edges)
        assert "REVIEW PLAN" in hint
        assert "2 domains" in hint

    def test_single_domain_no_review_plan(self) -> None:
        changes = [
            _change(change="added", name="foo", path="src/codeplane/a.py"),
            _change(change="added", name="bar", path="src/codeplane/b.py"),
        ]
        domains = _classify_domains(changes)
        hint = _build_agentic_hint(_result(changes), domains)
        assert "REVIEW PLAN" not in hint
        assert "2 additions" in hint
