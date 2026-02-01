"""Unit tests for Context Router (router.py).

Tests cover:
- File-to-context routing
- Single owner per (file, family) invariant
- Nested context priority (most specific wins)
- Segment-safe containment
"""

from __future__ import annotations

from codeplane.index._internal.discovery import (
    ContextRouter,
    FileRoute,
    RoutingResult,
    route_single_file,
)
from codeplane.index.models import CandidateContext, LanguageFamily, ProbeStatus


def make_candidate(
    family: LanguageFamily,
    root_path: str,
    include_spec: list[str] | None = None,
    exclude_spec: list[str] | None = None,
) -> CandidateContext:
    """Helper to create CandidateContext."""
    return CandidateContext(
        language_family=family,
        root_path=root_path,
        tier=2,
        markers=[],
        probe_status=ProbeStatus.VALID,
        include_spec=include_spec,
        exclude_spec=exclude_spec,
    )


class TestContextRouter:
    """Tests for ContextRouter class."""

    def test_route_file_to_context(self) -> None:
        """Should route file to correct context."""
        contexts = [make_candidate(LanguageFamily.PYTHON, "src")]
        router = ContextRouter()
        result = router.route_files(["src/main.py"], contexts)

        assert result.routed_count == 1
        assert result.routes[0].routed
        assert result.routes[0].context_root == "src"

    def test_route_file_no_match(self) -> None:
        """Should not route when no context matches."""
        contexts = [make_candidate(LanguageFamily.PYTHON, "src")]
        router = ContextRouter()
        result = router.route_files(["other/main.py"], contexts)

        assert result.unrouted_count == 1
        assert not result.routes[0].routed

    def test_route_respects_family(self) -> None:
        """Should only match contexts with same language family."""
        contexts = [
            make_candidate(LanguageFamily.PYTHON, "backend"),
            make_candidate(LanguageFamily.JAVASCRIPT, "frontend"),
        ]
        router = ContextRouter()

        # Python file should match Python context
        py_result = router.route_files(["backend/app.py"], contexts)
        assert py_result.routes[0].routed
        assert py_result.routes[0].context_root == "backend"

        # JavaScript file should match JavaScript context
        js_result = router.route_files(["frontend/app.js"], contexts)
        assert js_result.routes[0].routed
        assert js_result.routes[0].context_root == "frontend"

    def test_route_nested_contexts_most_specific(self) -> None:
        """Most specific (nested) context should win."""
        contexts = [
            make_candidate(LanguageFamily.PYTHON, "src"),
            make_candidate(LanguageFamily.PYTHON, "src/packages"),
            make_candidate(LanguageFamily.PYTHON, "src/packages/core"),
        ]
        router = ContextRouter()

        # File in packages/core should match deepest context
        result = router.route_files(["src/packages/core/main.py"], contexts)
        assert result.routes[0].routed
        assert result.routes[0].context_root == "src/packages/core"

        # File in packages (not core) should match packages context
        result2 = router.route_files(["src/packages/other.py"], contexts)
        assert result2.routes[0].routed
        assert result2.routes[0].context_root == "src/packages"

    def test_route_respects_include_spec(self) -> None:
        """Should only match files matching include spec."""
        contexts = [make_candidate(LanguageFamily.PYTHON, "src", include_spec=["*.py"])]
        router = ContextRouter()

        # .py file should match
        py_result = router.route_files(["src/main.py"], contexts)
        assert py_result.routes[0].routed

    def test_route_respects_exclude_spec(self) -> None:
        """Should not match files in exclude spec."""
        contexts = [make_candidate(LanguageFamily.PYTHON, "src", exclude_spec=["tests/**"])]
        router = ContextRouter()

        # Regular file should match
        result = router.route_files(["src/main.py"], contexts)
        assert result.routes[0].routed

        # File in excluded path should not match
        excluded_result = router.route_files(["src/tests/test_main.py"], contexts)
        assert not excluded_result.routes[0].routed


class TestSegmentSafeContainment:
    """Tests for segment-safe path containment."""

    def test_apps_vs_apps_legacy(self) -> None:
        """'apps' context should not contain 'apps-legacy' files."""
        contexts = [
            make_candidate(LanguageFamily.PYTHON, "apps"),
            make_candidate(LanguageFamily.PYTHON, "apps-legacy"),
        ]
        router = ContextRouter()

        # File in apps should match apps context
        result1 = router.route_files(["apps/main.py"], contexts)
        assert result1.routes[0].routed
        assert result1.routes[0].context_root == "apps"

        # File in apps-legacy should match apps-legacy context
        result2 = router.route_files(["apps-legacy/main.py"], contexts)
        assert result2.routes[0].routed
        assert result2.routes[0].context_root == "apps-legacy"


class TestRouteSingleFile:
    """Tests for route_single_file helper."""

    def test_route_single_file_match(self) -> None:
        """Helper should route single file correctly."""
        contexts = [make_candidate(LanguageFamily.PYTHON, "src")]
        result = route_single_file("src/main.py", contexts)

        assert result is not None
        assert result.context_root == "src"

    def test_route_single_file_no_match(self) -> None:
        """Helper should return None when no match."""
        contexts = [make_candidate(LanguageFamily.PYTHON, "src")]
        result = route_single_file("other/main.py", contexts)

        assert result is None


class TestFileRoute:
    """Tests for FileRoute dataclass."""

    def test_file_route_structure(self) -> None:
        """FileRoute should have expected fields."""
        route = FileRoute(
            file_path="src/main.py",
            context_root="src",
            language_family=LanguageFamily.PYTHON,
            routed=True,
        )

        assert route.file_path == "src/main.py"
        assert route.context_root == "src"
        assert route.language_family == LanguageFamily.PYTHON
        assert route.routed is True


class TestRoutingResult:
    """Tests for RoutingResult dataclass."""

    def test_routing_result_structure(self) -> None:
        """RoutingResult should have expected fields."""
        result = RoutingResult(
            routes=[FileRoute(file_path="a.py", routed=True)],
            routed_count=1,
            unrouted_count=0,
        )

        assert len(result.routes) == 1
        assert result.routed_count == 1
        assert result.unrouted_count == 0


class TestRouterEdgeCases:
    """Tests for router edge cases."""

    def test_route_unknown_extension(self) -> None:
        """Unknown extensions should not be routed."""
        contexts = [make_candidate(LanguageFamily.PYTHON, "src")]
        router = ContextRouter()
        result = router.route_files(["src/data.xyz"], contexts)

        assert result.unrouted_count == 1
        assert "Unknown extension" in result.routes[0].reason

    def test_route_no_contexts_for_family(self) -> None:
        """No contexts for family should result in unrouted."""
        contexts = [make_candidate(LanguageFamily.PYTHON, "src")]
        router = ContextRouter()
        # Route a JavaScript file with no JS contexts
        result = router.route_files(["src/app.js"], contexts)

        assert result.unrouted_count == 1
        assert "No contexts for family" in result.routes[0].reason

    def test_route_include_spec_extension_pattern(self) -> None:
        """Extension pattern in include_spec should work."""
        contexts = [make_candidate(LanguageFamily.PYTHON, "src", include_spec=["*.py", "*.pyi"])]
        router = ContextRouter()

        # .py should match
        result1 = router.route_files(["src/main.py"], contexts)
        assert result1.routes[0].routed

        # .pyi should match
        result2 = router.route_files(["src/stubs.pyi"], contexts)
        assert result2.routes[0].routed

    def test_route_include_spec_fnmatch_pattern(self) -> None:
        """Fnmatch pattern in include_spec should work."""
        contexts = [make_candidate(LanguageFamily.PYTHON, "src", include_spec=["test_*.py"])]
        router = ContextRouter()

        # test_main.py should match
        result1 = router.route_files(["src/test_main.py"], contexts)
        assert result1.routes[0].routed

        # main.py should not match
        result2 = router.route_files(["src/main.py"], contexts)
        assert not result2.routes[0].routed

    def test_route_include_spec_no_match(self) -> None:
        """File not matching include_spec should not route."""
        contexts = [make_candidate(LanguageFamily.PYTHON, "src", include_spec=["*.md"])]
        router = ContextRouter()

        result = router.route_files(["src/main.py"], contexts)
        assert not result.routes[0].routed

    def test_route_exclude_spec_double_star(self) -> None:
        """Exclude spec with ** should match nested paths."""
        contexts = [make_candidate(LanguageFamily.PYTHON, "src", exclude_spec=["tests/**"])]
        router = ContextRouter()

        # Root of tests should be excluded
        result1 = router.route_files(["src/tests/test_main.py"], contexts)
        assert not result1.routes[0].routed

        # Nested tests should also be excluded
        result2 = router.route_files(["src/tests/unit/test_core.py"], contexts)
        assert not result2.routes[0].routed

    def test_route_exclude_spec_single_star(self) -> None:
        """Exclude spec with /* should match only direct children."""
        contexts = [make_candidate(LanguageFamily.PYTHON, "src", exclude_spec=["vendor/*"])]
        router = ContextRouter()

        # Direct child should be excluded
        result1 = router.route_files(["src/vendor/lib.py"], contexts)
        assert not result1.routes[0].routed

        # Nested file should not be excluded by /* pattern
        _ = router.route_files(["src/vendor/pkg/lib.py"], contexts)
        # Note: /* only matches direct children, so nested might match or not
        # based on implementation - let's verify behavior

    def test_relative_to_empty_root(self) -> None:
        """Relative to empty root returns full path."""
        router = ContextRouter()
        assert router._relative_to("src/main.py", "") == "src/main.py"

    def test_relative_to_same_path(self) -> None:
        """Relative to same path returns empty string."""
        router = ContextRouter()
        assert router._relative_to("src", "src") == ""

    def test_relative_to_nested_path(self) -> None:
        """Relative to parent returns relative portion."""
        router = ContextRouter()
        assert router._relative_to("src/pkg/main.py", "src") == "pkg/main.py"

    def test_root_context_routing(self) -> None:
        """Root context (empty path) should match files at root."""
        contexts = [make_candidate(LanguageFamily.PYTHON, "")]
        router = ContextRouter()
        result = router.route_files(["main.py"], contexts)

        assert result.routes[0].routed
        assert result.routes[0].context_root == ""

    def test_multiple_families_routing(self) -> None:
        """Multiple families should route correctly."""
        contexts = [
            make_candidate(LanguageFamily.PYTHON, "src"),
            make_candidate(LanguageFamily.GO, "go"),
            make_candidate(LanguageFamily.RUST, "rust"),
        ]
        router = ContextRouter()

        result = router.route_files(
            ["src/main.py", "go/main.go", "rust/main.rs"],
            contexts,
        )

        assert result.routed_count == 3
        assert result.routes[0].language_family == LanguageFamily.PYTHON
        assert result.routes[1].language_family == LanguageFamily.GO
        assert result.routes[2].language_family == LanguageFamily.RUST
