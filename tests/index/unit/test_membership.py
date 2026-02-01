"""Unit tests for Membership Resolver (membership.py).

Tests cover:
- Include spec assignment per language family
- Exclude spec assignment (universal excludes + hole-punches)
- Hole-punch rule: nested contexts excluded from parent
- Segment-safe containment ("apps" doesn't contain "apps-legacy")
- is_inside helper function
"""

from __future__ import annotations

from codeplane.index._internal.discovery import (
    MembershipResolver,
    MembershipResult,
    is_inside,
)
from codeplane.index.models import CandidateContext, LanguageFamily, ProbeStatus


def make_candidate(
    family: LanguageFamily,
    root_path: str,
    tier: int | None = None,
    markers: list[str] | None = None,
) -> CandidateContext:
    """Helper to create CandidateContext."""
    return CandidateContext(
        language_family=family,
        root_path=root_path,
        tier=tier,
        markers=markers or [],
        probe_status=ProbeStatus.PENDING,
    )


class TestIsInside:
    """Tests for is_inside containment helper."""

    def test_same_path_is_inside(self) -> None:
        """Same path should be considered inside."""
        assert is_inside("pkg/foo", "pkg/foo")

    def test_child_is_inside_parent(self) -> None:
        """Child path should be inside parent."""
        assert is_inside("pkg/foo/bar", "pkg/foo")

    def test_parent_not_inside_child(self) -> None:
        """Parent path should not be inside child."""
        assert not is_inside("pkg/foo", "pkg/foo/bar")

    def test_sibling_not_inside(self) -> None:
        """Sibling paths should not be inside each other."""
        assert not is_inside("pkg/foo", "pkg/bar")
        assert not is_inside("pkg/bar", "pkg/foo")

    def test_segment_safe_apps_vs_apps_legacy(self) -> None:
        """'apps' should not contain 'apps-legacy' (segment-safe)."""
        assert not is_inside("apps-legacy", "apps")
        assert not is_inside("apps-legacy/foo", "apps")

    def test_segment_safe_lib_vs_libs(self) -> None:
        """'lib' should not contain 'libs' (segment-safe)."""
        assert not is_inside("libs", "lib")
        assert not is_inside("libs/core", "lib")

    def test_root_contains_all(self) -> None:
        """Empty root path should contain all paths."""
        assert is_inside("any/path/here", "")
        assert is_inside("single", "")

    def test_empty_path_inside_root(self) -> None:
        """Empty path should be inside empty root."""
        assert is_inside("", "")


class TestMembershipResolver:
    """Tests for MembershipResolver class."""

    def test_assigns_include_spec_for_python(self) -> None:
        """Should assign .py include spec for Python."""
        candidates = [make_candidate(LanguageFamily.PYTHON, "src")]

        resolver = MembershipResolver()
        result = resolver.resolve(candidates)

        assert len(result.contexts) == 1
        ctx = result.contexts[0]
        assert ctx.include_spec is not None
        assert ".py" in str(ctx.include_spec)

    def test_assigns_include_spec_for_javascript(self) -> None:
        """Should assign .js/.ts include spec for JavaScript."""
        candidates = [make_candidate(LanguageFamily.JAVASCRIPT, "src")]

        resolver = MembershipResolver()
        result = resolver.resolve(candidates)

        assert len(result.contexts) == 1
        ctx = result.contexts[0]
        assert ctx.include_spec is not None
        include_str = str(ctx.include_spec)
        assert ".js" in include_str or ".ts" in include_str

    def test_assigns_universal_excludes(self) -> None:
        """Should assign universal exclude patterns."""
        candidates = [make_candidate(LanguageFamily.PYTHON, "src")]

        resolver = MembershipResolver()
        result = resolver.resolve(candidates)

        ctx = result.contexts[0]
        assert ctx.exclude_spec is not None
        exclude_str = str(ctx.exclude_spec)
        # Should exclude common directories
        assert "node_modules" in exclude_str or "__pycache__" in exclude_str

    def test_hole_punch_nested_contexts(self) -> None:
        """Nested contexts should be excluded from parent."""
        # Parent context at "packages"
        # Child context at "packages/core"
        candidates = [
            make_candidate(LanguageFamily.JAVASCRIPT, "packages", 1),
            make_candidate(LanguageFamily.JAVASCRIPT, "packages/core", 2),
        ]

        resolver = MembershipResolver()
        result = resolver.resolve(candidates)

        # Find parent context
        parent = next(c for c in result.contexts if c.root_path == "packages")

        # Parent should exclude the nested context via relative path
        # The hole-punch uses relative paths like "core/**"
        assert parent.exclude_spec is not None
        # Check for the relative path pattern (core is relative to packages)
        assert "core/**" in str(parent.exclude_spec)

    def test_hole_punch_multiple_nested(self) -> None:
        """Multiple nested contexts should all be excluded."""
        candidates = [
            make_candidate(LanguageFamily.JAVASCRIPT, "", 1),  # Root
            make_candidate(LanguageFamily.JAVASCRIPT, "apps/web", 2),
            make_candidate(LanguageFamily.JAVASCRIPT, "apps/mobile", 2),
            make_candidate(LanguageFamily.JAVASCRIPT, "packages/shared", 2),
        ]

        resolver = MembershipResolver()
        result = resolver.resolve(candidates)

        # Find root context
        root = next(c for c in result.contexts if c.root_path == "")

        # Root should exclude all nested contexts
        exclude_str = str(root.exclude_spec)
        assert "apps/web" in exclude_str
        assert "apps/mobile" in exclude_str
        assert "packages/shared" in exclude_str

    def test_different_families_no_hole_punch(self) -> None:
        """Different language families should not hole-punch each other."""
        candidates = [
            make_candidate(LanguageFamily.PYTHON, "backend", 2),
            make_candidate(LanguageFamily.JAVASCRIPT, "backend/api", 2),
        ]

        resolver = MembershipResolver()
        result = resolver.resolve(candidates)

        # Python context at "backend" should NOT exclude "backend/api"
        # because they're different families
        python_ctx = next(c for c in result.contexts if c.language_family == LanguageFamily.PYTHON)

        if python_ctx.exclude_spec:
            # Should not exclude the JS path
            assert "backend/api" not in str(python_ctx.exclude_spec)


class TestMembershipResult:
    """Tests for MembershipResult dataclass."""

    def test_membership_result_structure(self) -> None:
        """MembershipResult should have contexts list."""
        result = MembershipResult(contexts=[])

        assert hasattr(result, "contexts")
        assert isinstance(result.contexts, list)
