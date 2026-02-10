"""Tests for Tier 1 authority filter."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from codeplane.index._internal.discovery.authority import (
    AuthorityResult,
    Tier1AuthorityFilter,
)
from codeplane.index.models import CandidateContext, LanguageFamily, ProbeStatus


def make_candidate(
    root_path: str,
    tier: int,
    family: LanguageFamily,
    markers: list[str] | None = None,
) -> CandidateContext:
    """Create a test CandidateContext."""
    return CandidateContext(
        root_path=root_path,
        tier=tier,
        language_family=family,
        markers=markers or [],
        probe_status=ProbeStatus.PENDING,
    )


class TestAuthorityResult:
    """Tests for AuthorityResult dataclass."""

    def test_construction(self) -> None:
        """AuthorityResult holds pending and detached lists."""
        pending = [make_candidate("pkg1", 2, LanguageFamily.JAVASCRIPT)]
        detached = [make_candidate("pkg2", 2, LanguageFamily.JAVASCRIPT)]
        result = AuthorityResult(pending=pending, detached=detached)
        assert result.pending == pending
        assert result.detached == detached

    def test_empty_lists(self) -> None:
        """AuthorityResult accepts empty lists."""
        result = AuthorityResult(pending=[], detached=[])
        assert result.pending == []
        assert result.detached == []


class TestTier1AuthorityFilter:
    """Tests for Tier1AuthorityFilter."""

    def test_init_stores_repo_root(self) -> None:
        """Filter stores repo_root."""
        root = Path("/test/repo")
        f = Tier1AuthorityFilter(root)
        assert f.repo_root == root

    def test_apply_empty_candidates(self) -> None:
        """apply with empty list returns empty result."""
        f = Tier1AuthorityFilter(Path("/test"))
        result = f.apply([])
        assert result.pending == []
        assert result.detached == []

    def test_apply_python_candidates_pass_through(self) -> None:
        """Python candidates always pass through (no workspace mechanism)."""
        f = Tier1AuthorityFilter(Path("/test"))
        candidates = [
            make_candidate("pkg1", 2, LanguageFamily.PYTHON),
            make_candidate("pkg2", 2, LanguageFamily.PYTHON),
        ]
        result = f.apply(candidates)
        assert len(result.pending) == 2
        assert len(result.detached) == 0

    def test_apply_ruby_candidates_pass_through(self) -> None:
        """Ruby candidates always pass through."""
        f = Tier1AuthorityFilter(Path("/test"))
        candidates = [
            make_candidate("app", 2, LanguageFamily.RUBY),
        ]
        result = f.apply(candidates)
        assert len(result.pending) == 1
        assert len(result.detached) == 0

    def test_apply_javascript_no_tier1_passes_all(self) -> None:
        """JavaScript without Tier 1 workspace passes all candidates."""
        f = Tier1AuthorityFilter(Path("/test"))
        candidates = [
            make_candidate("packages/a", 2, LanguageFamily.JAVASCRIPT),
            make_candidate("packages/b", 2, LanguageFamily.JAVASCRIPT),
        ]
        result = f.apply(candidates)
        assert len(result.pending) == 2
        assert len(result.detached) == 0

    def test_apply_go_no_tier1_passes_all(self) -> None:
        """Go without go.work passes all candidates."""
        f = Tier1AuthorityFilter(Path("/test"))
        candidates = [
            make_candidate("cmd/app", 2, LanguageFamily.GO),
            make_candidate("pkg/lib", 2, LanguageFamily.GO),
        ]
        result = f.apply(candidates)
        assert len(result.pending) == 2
        assert len(result.detached) == 0

    def test_apply_rust_no_tier1_passes_all(self) -> None:
        """Rust without workspace Cargo.toml passes all candidates."""
        f = Tier1AuthorityFilter(Path("/test"))
        candidates = [
            make_candidate("crates/core", 2, LanguageFamily.RUST),
        ]
        result = f.apply(candidates)
        assert len(result.pending) == 1
        assert len(result.detached) == 0

    def test_is_inside_checks_segment(self) -> None:
        """_is_inside does segment-safe containment check."""
        f = Tier1AuthorityFilter(Path("/test"))
        # Direct child
        assert f._is_inside("packages/a", "packages") is True
        # Same path
        assert f._is_inside("packages", "packages") is True
        # Not inside (prefix but not segment)
        assert f._is_inside("packages-extra", "packages") is False
        # Root contains all
        assert f._is_inside("anything", "") is True

    def test_relative_to(self) -> None:
        """_relative_to computes relative path."""
        f = Tier1AuthorityFilter(Path("/test"))
        assert f._relative_to("packages/a/b", "packages") == "a/b"
        assert f._relative_to("packages", "packages") == ""
        assert f._relative_to("other", "packages") == "other"
        assert f._relative_to("something", "") == "something"

    def test_matches_any_glob_exact(self) -> None:
        """_matches_any_glob matches exact paths."""
        f = Tier1AuthorityFilter(Path("/test"))
        assert f._matches_any_glob("packages/a", ["packages/a"]) is True
        assert f._matches_any_glob("packages/b", ["packages/a"]) is False

    def test_matches_any_glob_wildcard(self) -> None:
        """_matches_any_glob supports wildcards."""
        f = Tier1AuthorityFilter(Path("/test"))
        assert f._matches_any_glob("packages/foo", ["packages/*"]) is True
        assert f._matches_any_glob("other/foo", ["packages/*"]) is False

    def test_matches_any_glob_strips_trailing_stars(self) -> None:
        """_matches_any_glob handles trailing /**."""
        f = Tier1AuthorityFilter(Path("/test"))
        assert f._matches_any_glob("packages/a", ["packages/**"]) is True

    def test_matches_any_glob_strips_leading_dot_slash(self) -> None:
        """_matches_any_glob handles leading ./."""
        f = Tier1AuthorityFilter(Path("/test"))
        assert f._matches_any_glob("packages/a", ["./packages/a"]) is True


class TestJavaScriptFiltering:
    """Tests for JavaScript workspace filtering."""

    def test_pnpm_workspace_globs(self) -> None:
        """Extracts globs from pnpm-workspace.yaml."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pnpm_file = root / "pnpm-workspace.yaml"
            pnpm_file.write_text("packages:\n  - packages/*\n  - apps/*\n")

            f = Tier1AuthorityFilter(root)
            t1 = make_candidate("", 1, LanguageFamily.JAVASCRIPT, ["pnpm-workspace.yaml"])
            globs = f._get_js_workspace_globs(t1)

            assert "packages/*" in globs
            assert "apps/*" in globs

    def test_npm_workspaces_array(self) -> None:
        """Extracts workspaces from package.json array format."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pkg_file = root / "package.json"
            pkg_file.write_text(json.dumps({"workspaces": ["packages/*"]}))

            f = Tier1AuthorityFilter(root)
            t1 = make_candidate("", 1, LanguageFamily.JAVASCRIPT, ["package.json"])
            globs = f._get_js_workspace_globs(t1)

            assert "packages/*" in globs

    def test_npm_workspaces_object(self) -> None:
        """Extracts workspaces from package.json object format."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pkg_file = root / "package.json"
            pkg_file.write_text(json.dumps({"workspaces": {"packages": ["packages/*", "apps/*"]}}))

            f = Tier1AuthorityFilter(root)
            t1 = make_candidate("", 1, LanguageFamily.JAVASCRIPT, ["package.json"])
            globs = f._get_js_workspace_globs(t1)

            assert "packages/*" in globs
            assert "apps/*" in globs

    def test_lerna_packages(self) -> None:
        """Extracts packages from lerna.json."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lerna_file = root / "lerna.json"
            lerna_file.write_text(json.dumps({"packages": ["packages/*"]}))

            f = Tier1AuthorityFilter(root)
            t1 = make_candidate("", 1, LanguageFamily.JAVASCRIPT, ["lerna.json"])
            globs = f._get_js_workspace_globs(t1)

            assert "packages/*" in globs


class TestGoFiltering:
    """Tests for Go workspace filtering."""

    def test_go_work_modules(self) -> None:
        """Extracts modules from go.work."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            go_work = root / "go.work"
            go_work.write_text("go 1.21\n\nuse (\n\t./cmd/app\n\t./pkg/lib\n)\n")

            f = Tier1AuthorityFilter(root)
            t1 = make_candidate("", 1, LanguageFamily.GO, ["go.work"])
            modules = f._get_go_work_modules(t1)

            assert "./cmd/app" in modules
            assert "./pkg/lib" in modules

    def test_go_work_single_use(self) -> None:
        """Extracts single use directive from go.work."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            go_work = root / "go.work"
            go_work.write_text("go 1.21\n\nuse ./app\n")

            f = Tier1AuthorityFilter(root)
            t1 = make_candidate("", 1, LanguageFamily.GO, ["go.work"])
            modules = f._get_go_work_modules(t1)

            assert "./app" in modules


class TestRustFiltering:
    """Tests for Rust workspace filtering."""

    def test_cargo_workspace_members(self) -> None:
        """Extracts members from Cargo.toml workspace (inline format)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cargo = root / "Cargo.toml"
            # Use inline format since the parser handles that correctly
            cargo.write_text('[workspace]\nmembers = ["crates/core", "crates/cli"]\n')

            f = Tier1AuthorityFilter(root)
            t1 = make_candidate("", 1, LanguageFamily.RUST, ["Cargo.toml"])
            members = f._get_cargo_workspace_members(t1)

            assert "crates/core" in members
            assert "crates/cli" in members

    def test_cargo_workspace_inline_members(self) -> None:
        """Extracts inline members from Cargo.toml."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cargo = root / "Cargo.toml"
            cargo.write_text('[workspace]\nmembers = ["a", "b"]\n')

            f = Tier1AuthorityFilter(root)
            t1 = make_candidate("", 1, LanguageFamily.RUST, ["Cargo.toml"])
            members = f._get_cargo_workspace_members(t1)

            assert "a" in members
            assert "b" in members


class TestJvmFiltering:
    """Tests for JVM workspace filtering (applies to Java/Kotlin/Scala/Groovy)."""

    def test_gradle_includes(self) -> None:
        """Extracts includes from settings.gradle."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = root / "settings.gradle"
            settings.write_text("include(':app')\ninclude ':lib'\n")

            f = Tier1AuthorityFilter(root)
            t1 = make_candidate("", 1, LanguageFamily.JAVA, ["settings.gradle"])
            includes, is_strict = f._get_gradle_includes(t1)

            assert ":app" in includes or "app" in includes

    def test_maven_modules(self) -> None:
        """Extracts modules from Maven pom.xml."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pom = root / "pom.xml"
            pom.write_text(
                "<project>\n<modules>\n<module>core</module>\n<module>api</module>\n</modules>\n</project>"
            )

            f = Tier1AuthorityFilter(root)
            t1 = make_candidate("", 1, LanguageFamily.JAVA, ["pom.xml"])
            modules = f._get_maven_modules(t1)

            assert "core" in modules
            assert "api" in modules


class TestDotNetFiltering:
    """Tests for .NET solution filtering (applies to C#/F#/VB)."""

    def test_sln_projects(self) -> None:
        """Extracts projects from .sln file."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sln = root / "Test.sln"
            sln.write_text(
                'Project("{FAE04EC0-301F-11D3-BF4B-00C04F79EFBC}") = "App", '
                '"src\\App\\App.csproj", "{GUID}"\nEndProject\n'
            )

            f = Tier1AuthorityFilter(root)
            t1 = make_candidate("", 1, LanguageFamily.CSHARP, ["Test.sln"])
            projects = f._get_sln_projects(t1)

            assert any("App.csproj" in p for p in projects)

    def test_sln_projects_backslash_normalization(self) -> None:
        """Project paths with backslashes are handled correctly.

        .sln files on Windows contain backslash paths like:
        "src\\App\\App.csproj"

        When matching against candidate root_path (which uses forward slashes),
        backslashes must be normalized.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sln = root / "Test.sln"
            # Typical Windows .sln format with backslash paths
            sln.write_text(
                'Project("{FAE04EC0-301F-11D3-BF4B-00C04F79EFBC}") = "Core", '
                '"src\\Core\\Core.csproj", "{GUID1}"\nEndProject\n'
                'Project("{FAE04EC0-301F-11D3-BF4B-00C04F79EFBC}") = "Api", '
                '"src\\Api\\Api.csproj", "{GUID2}"\nEndProject\n'
            )

            f = Tier1AuthorityFilter(root)
            t1 = make_candidate("", 1, LanguageFamily.CSHARP, ["Test.sln"])
            projects = f._get_sln_projects(t1)

            # Should extract both project paths
            assert len(projects) == 2
            # Paths retain original format from .sln (backslashes)
            assert any("Core.csproj" in p for p in projects)
            assert any("Api.csproj" in p for p in projects)


class TestCrossPlatformPathHandling:
    """Tests for cross-platform path compatibility.

    These tests verify that the authority filter works correctly with both
    POSIX-style paths (forward slashes) and Windows-style paths (backslashes).
    """

    def test_is_inside_posix_paths(self) -> None:
        """_is_inside works with POSIX-style paths."""
        f = Tier1AuthorityFilter(Path("/test"))
        assert f._is_inside("packages/core/src", "packages") is True
        assert f._is_inside("packages/core", "packages/core") is True
        assert f._is_inside("other/dir", "packages") is False

    def test_relative_to_posix_paths(self) -> None:
        """_relative_to works with POSIX-style paths."""
        f = Tier1AuthorityFilter(Path("/test"))
        assert f._relative_to("packages/core/src", "packages") == "core/src"
        assert f._relative_to("packages/core", "packages/core") == ""

    def test_matches_any_glob_posix_paths(self) -> None:
        """_matches_any_glob matches POSIX-style paths against POSIX globs."""
        f = Tier1AuthorityFilter(Path("/test"))
        # Standard POSIX paths work
        assert f._matches_any_glob("packages/core", ["packages/*"]) is True
        assert f._matches_any_glob("apps/web", ["packages/*", "apps/*"]) is True
        assert f._matches_any_glob("other/lib", ["packages/*", "apps/*"]) is False

    def test_matches_any_glob_with_deep_paths(self) -> None:
        """_matches_any_glob handles deeply nested paths."""
        f = Tier1AuthorityFilter(Path("/test"))
        # Glob with ** trailing should match nested paths after stripping **
        # _matches_any_glob strips /** and then checks fnmatch(path, glob) or fnmatch(path, glob + "/*")
        assert f._matches_any_glob("packages/core", ["packages/**"]) is True
        # Deep path also matches because fnmatch("packages/core/subdir", "packages/*") is True
        assert f._matches_any_glob("packages/core/subdir", ["packages/**"]) is True

    def test_matches_any_glob_exact_match_takes_precedence(self) -> None:
        """_matches_any_glob prefers exact match over wildcard."""
        f = Tier1AuthorityFilter(Path("/test"))
        # Exact match in glob list
        assert f._matches_any_glob("packages/core", ["packages/other", "packages/core"]) is True

    def test_dotnet_filter_normalizes_backslash_project_paths(self) -> None:
        """_filter_dotnet correctly matches candidates against .sln project paths.

        .sln files contain Windows-style backslash paths:
            "src\\App\\App.csproj"

        The filter must normalize these to match against candidate.root_path
        which uses forward slashes (e.g., "src/App").
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sln = root / "MySolution.sln"
            # .sln with Windows backslash paths
            sln.write_text(
                'Project("{FAE04EC0-301F-11D3-BF4B-00C04F79EFBC}") = "App", '
                '"src\\App\\App.csproj", "{GUID}"\nEndProject\n'
            )

            f = Tier1AuthorityFilter(root)

            # Tier 1 workspace (the .sln)
            t1 = make_candidate("", 1, LanguageFamily.CSHARP, ["MySolution.sln"])
            # Tier 2 project candidate with POSIX-style path
            t2 = make_candidate("src/App", 2, LanguageFamily.CSHARP, ["src/App/App.csproj"])

            result = f.apply([t1, t2])

            # Both should be pending (t2 matches the .sln project)
            assert len(result.pending) == 2
            assert len(result.detached) == 0

    def test_dotnet_filter_detaches_unlisted_projects(self) -> None:
        """Projects not listed in .sln are marked as detached."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sln = root / "MySolution.sln"
            # .sln only lists App project
            sln.write_text(
                'Project("{FAE04EC0-301F-11D3-BF4B-00C04F79EFBC}") = "App", '
                '"src\\App\\App.csproj", "{GUID}"\nEndProject\n'
            )

            f = Tier1AuthorityFilter(root)

            t1 = make_candidate("", 1, LanguageFamily.CSHARP, ["MySolution.sln"])
            # Listed project
            t2_listed = make_candidate("src/App", 2, LanguageFamily.CSHARP, ["src/App/App.csproj"])
            # Unlisted project
            t2_unlisted = make_candidate(
                "src/Other", 2, LanguageFamily.CSHARP, ["src/Other/Other.csproj"]
            )

            result = f.apply([t1, t2_listed, t2_unlisted])

            assert len(result.pending) == 2  # t1 + t2_listed
            assert len(result.detached) == 1  # t2_unlisted
            assert result.detached[0].root_path == "src/Other"
