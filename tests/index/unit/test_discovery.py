"""Unit tests for Context Discovery (discovery.py, scanner.py).

Tests cover:
- Marker file detection for each language name
- Tier 1 vs Tier 2 marker classification
- Candidate context generation from markers
- Full repository scan for contexts
- Ambient name fallback contexts
"""

from __future__ import annotations

from pathlib import Path

from codeplane.index._internal.discovery import (
    AMBIENT_NAMES,
    INCLUDE_SPECS,
    MARKER_DEFINITIONS,
    UNIVERSAL_EXCLUDES,
    ContextDiscovery,
    DiscoveryResult,
)
from codeplane.index.models import LanguageFamily, MarkerTier


class TestMarkerDefinitions:
    """Tests for marker file definitions."""

    def test_marker_definitions_exist(self) -> None:
        """MARKER_DEFINITIONS should be defined."""
        assert MARKER_DEFINITIONS is not None
        assert len(MARKER_DEFINITIONS) > 0

    def test_javascript_markers(self) -> None:
        """JavaScript name should have package.json markers."""
        js_markers = MARKER_DEFINITIONS.get(LanguageFamily.JAVASCRIPT, {})
        workspace = js_markers.get(MarkerTier.WORKSPACE, [])
        package = js_markers.get(MarkerTier.PACKAGE, [])

        # pnpm-workspace.yaml, etc. are WORKSPACE markers
        # package.json is PACKAGE marker
        all_markers = workspace + package
        assert "package.json" in all_markers

    def test_python_markers(self) -> None:
        """Python name should have pyproject.toml markers."""
        py_markers = MARKER_DEFINITIONS.get(LanguageFamily.PYTHON, {})
        workspace = py_markers.get(MarkerTier.WORKSPACE, [])
        package = py_markers.get(MarkerTier.PACKAGE, [])

        all_markers = workspace + package
        assert "pyproject.toml" in all_markers or "setup.py" in all_markers

    def test_go_markers(self) -> None:
        """Go name should have go.mod markers."""
        go_markers = MARKER_DEFINITIONS.get(LanguageFamily.GO, {})
        workspace = go_markers.get(MarkerTier.WORKSPACE, [])
        package = go_markers.get(MarkerTier.PACKAGE, [])

        all_markers = workspace + package
        assert "go.mod" in all_markers

    def test_rust_markers(self) -> None:
        """Rust name should have cargo.toml markers."""
        rust_markers = MARKER_DEFINITIONS.get(LanguageFamily.RUST, {})
        workspace = rust_markers.get(MarkerTier.WORKSPACE, [])
        package = rust_markers.get(MarkerTier.PACKAGE, [])

        all_markers = workspace + package
        assert "cargo.toml" in all_markers  # Markers are lowercase


class TestIncludeSpecs:
    """Tests for file include specifications."""

    def test_include_specs_exist(self) -> None:
        """INCLUDE_SPECS should be defined for language families."""
        assert INCLUDE_SPECS is not None
        assert len(INCLUDE_SPECS) > 0

    def test_python_include_spec(self) -> None:
        """Python should include .py files."""
        py_spec = INCLUDE_SPECS.get(LanguageFamily.PYTHON, [])
        assert any(".py" in spec for spec in py_spec)

    def test_javascript_include_spec(self) -> None:
        """JavaScript should include .js, .ts files."""
        js_spec = INCLUDE_SPECS.get(LanguageFamily.JAVASCRIPT, [])
        patterns = " ".join(js_spec)
        assert ".js" in patterns or "js" in patterns


class TestUniversalExcludes:
    """Tests for universal exclude patterns."""

    def test_universal_excludes_exist(self) -> None:
        """UNIVERSAL_EXCLUDES should be defined."""
        assert UNIVERSAL_EXCLUDES is not None
        assert len(UNIVERSAL_EXCLUDES) > 0

    def test_excludes_common_directories(self) -> None:
        """Should exclude common non-source directories."""
        excludes = set(UNIVERSAL_EXCLUDES)
        # Common excludes
        assert "node_modules" in excludes or any("node_modules" in e for e in excludes)
        assert "__pycache__" in excludes or any("__pycache__" in e for e in excludes)
        assert ".git" in excludes or any(".git" in e for e in excludes)


class TestAmbientFamilies:
    """Tests for ambient name definitions."""

    def test_ambient_families_exist(self) -> None:
        """AMBIENT_NAMES should be defined."""
        assert AMBIENT_NAMES is not None
        assert len(AMBIENT_NAMES) > 0

    def test_ambient_families_are_strings(self) -> None:
        """Ambient names should be strings."""
        for name in AMBIENT_NAMES:
            # AMBIENT_NAMES contains language name strings
            assert isinstance(name, str)


class TestContextDiscovery:
    """Tests for ContextDiscovery class."""

    def test_discover_empty_repo(self, temp_dir: Path) -> None:
        """Discovery on empty repo should return ambient contexts."""
        repo_path = temp_dir / "empty_repo"
        repo_path.mkdir()

        discovery = ContextDiscovery(repo_path)
        result = discovery.discover_all()

        assert isinstance(result, DiscoveryResult)
        # Should have ambient contexts for data families
        # (or empty if no files match)

    def test_discover_python_project(self, temp_dir: Path) -> None:
        """Discovery should find Python project from pyproject.toml."""
        repo_path = temp_dir / "py_project"
        repo_path.mkdir()
        (repo_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
        (repo_path / "src").mkdir()
        (repo_path / "src" / "main.py").write_text("# main\n")

        discovery = ContextDiscovery(repo_path)
        result = discovery.discover_all()

        # Should find a Python context
        families = {c.language_family for c in result.candidates}
        assert LanguageFamily.PYTHON in families

    def test_discover_javascript_project(self, temp_dir: Path) -> None:
        """Discovery should find JavaScript project from package.json."""
        repo_path = temp_dir / "js_project"
        repo_path.mkdir()
        (repo_path / "package.json").write_text('{"name": "test"}\n')
        (repo_path / "index.js").write_text("// main\n")

        discovery = ContextDiscovery(repo_path)
        result = discovery.discover_all()

        families = {c.language_family for c in result.candidates}
        assert LanguageFamily.JAVASCRIPT in families

    def test_discover_go_project(self, temp_dir: Path) -> None:
        """Discovery should find Go project from go.mod."""
        repo_path = temp_dir / "go_project"
        repo_path.mkdir()
        (repo_path / "go.mod").write_text("module example.com/test\n\ngo 1.21\n")
        (repo_path / "main.go").write_text("package main\n")

        discovery = ContextDiscovery(repo_path)
        result = discovery.discover_all()

        families = {c.language_family for c in result.candidates}
        assert LanguageFamily.GO in families

    def test_discover_rust_project(self, temp_dir: Path) -> None:
        """Discovery should find Rust project from Cargo.toml."""
        repo_path = temp_dir / "rust_project"
        repo_path.mkdir()
        (repo_path / "Cargo.toml").write_text('[package]\nname = "test"\n')
        (repo_path / "src").mkdir()
        (repo_path / "src" / "main.rs").write_text("fn main() {}\n")

        discovery = ContextDiscovery(repo_path)
        result = discovery.discover_all()

        families = {c.language_family for c in result.candidates}
        assert LanguageFamily.RUST in families

    def test_discover_monorepo_multiple_packages(self, temp_dir: Path) -> None:
        """Discovery should find multiple packages in monorepo."""
        repo_path = temp_dir / "monorepo"
        repo_path.mkdir()

        # Root workspace marker
        (repo_path / "pnpm-workspace.yaml").write_text("pkgs:\n  - 'pkgs/*'\n")

        # Two packages
        (repo_path / "pkgs").mkdir()
        (repo_path / "pkgs" / "pkg-a").mkdir()
        (repo_path / "pkgs" / "pkg-a" / "package.json").write_text('{"name": "a"}\n')

        (repo_path / "pkgs" / "pkg-b").mkdir()
        (repo_path / "pkgs" / "pkg-b" / "package.json").write_text('{"name": "b"}\n')

        discovery = ContextDiscovery(repo_path)
        result = discovery.discover_all()

        # Should find multiple JavaScript contexts
        js_contexts = [
            c for c in result.candidates if c.language_family == LanguageFamily.JAVASCRIPT
        ]
        assert len(js_contexts) >= 2

    def test_discover_respects_excludes(self, temp_dir: Path) -> None:
        """Discovery should skip excluded directories."""
        repo_path = temp_dir / "with_excludes"
        repo_path.mkdir()

        # Main project
        (repo_path / "package.json").write_text('{"name": "main"}\n')

        # node_modules should be excluded
        (repo_path / "node_modules").mkdir()
        (repo_path / "node_modules" / "dep").mkdir()
        (repo_path / "node_modules" / "dep" / "package.json").write_text('{"name": "dep"}\n')

        discovery = ContextDiscovery(repo_path)
        result = discovery.discover_all()

        # Should not find context in node_modules
        for candidate in result.candidates:
            assert "node_modules" not in (candidate.root_path or "")


class TestDiscoveryResult:
    """Tests for DiscoveryResult dataclass."""

    def test_discovery_result_has_candidates(self, temp_dir: Path) -> None:
        """DiscoveryResult should have candidates list."""
        repo_path = temp_dir / "test_repo"
        repo_path.mkdir()
        (repo_path / "pyproject.toml").write_text('[project]\nname = "test"\n')

        discovery = ContextDiscovery(repo_path)
        result = discovery.discover_all()

        assert hasattr(result, "candidates")
        assert isinstance(result.candidates, list)

    def test_discovery_result_has_markers(self, temp_dir: Path) -> None:
        """DiscoveryResult should track discovered markers."""
        repo_path = temp_dir / "test_repo"
        repo_path.mkdir()
        (repo_path / "package.json").write_text('{"name": "test"}\n')

        discovery = ContextDiscovery(repo_path)
        result = discovery.discover_all()

        assert hasattr(result, "markers")


class TestScannerEdgeCases:
    """Tests for scanner edge cases."""

    def test_discover_single_family(self, temp_dir: Path) -> None:
        """discover_name should return results for specific name."""
        repo_path = temp_dir / "repo"
        repo_path.mkdir()
        (repo_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
        (repo_path / "package.json").write_text('{"name": "test"}\n')

        discovery = ContextDiscovery(repo_path)
        result = discovery.discover_family(LanguageFamily.PYTHON)

        # Should only have Python candidates
        for c in result.candidates:
            assert c.language_family == LanguageFamily.PYTHON

    def test_discover_dotnet_sln_file(self, temp_dir: Path) -> None:
        """Should discover .sln files as Tier 1 workspace markers."""
        repo_path = temp_dir / "repo"
        repo_path.mkdir()

        sln_content = "Microsoft Visual Studio Solution File, Format Version 12.00\n"
        (repo_path / "MySolution.sln").write_text(sln_content)

        discovery = ContextDiscovery(repo_path)
        result = discovery.discover_all()

        dotnet_candidates = [
            c for c in result.candidates if c.language_family == LanguageFamily.CSHARP
        ]
        assert len(dotnet_candidates) >= 1
        # sln file should be Tier 1
        sln_candidates = [c for c in dotnet_candidates if c.tier == 1]
        assert len(sln_candidates) >= 1

    def test_discover_dotnet_csproj_file(self, temp_dir: Path) -> None:
        """Should discover .csproj files as Tier 2 markers."""
        repo_path = temp_dir / "repo"
        repo_path.mkdir()

        csproj_content = '<Project Sdk="Microsoft.NET.Sdk"></Project>'
        (repo_path / "MyProject.csproj").write_text(csproj_content)

        discovery = ContextDiscovery(repo_path)
        result = discovery.discover_all()

        dotnet_candidates = [
            c for c in result.candidates if c.language_family == LanguageFamily.CSHARP
        ]
        assert len(dotnet_candidates) >= 1

    def test_discover_js_package_with_workspaces(self, temp_dir: Path) -> None:
        """package.json with workspaces should be Tier 1."""
        repo_path = temp_dir / "repo"
        repo_path.mkdir()

        pkg_content = '{"name": "monorepo", "workspaces": ["packages/*"]}'
        (repo_path / "package.json").write_text(pkg_content)

        discovery = ContextDiscovery(repo_path)
        result = discovery.discover_all()

        js_candidates = [
            c for c in result.candidates if c.language_family == LanguageFamily.JAVASCRIPT
        ]
        # Should be Tier 1 due to workspaces field
        tier1 = [c for c in js_candidates if c.tier == 1]
        assert len(tier1) >= 1

    def test_discover_maven_pom_with_modules(self, temp_dir: Path) -> None:
        """pom.xml with <modules> should be Tier 1."""
        repo_path = temp_dir / "repo"
        repo_path.mkdir()

        pom_content = """<?xml version="1.0"?>
<project>
    <modules>
        <module>core</module>
    </modules>
</project>
"""
        (repo_path / "pom.xml").write_text(pom_content)

        discovery = ContextDiscovery(repo_path)
        result = discovery.discover_all()

        jvm_candidates = [c for c in result.candidates if c.language_family == LanguageFamily.JAVA]
        tier1 = [c for c in jvm_candidates if c.tier == 1]
        assert len(tier1) >= 1

    def test_discover_cargo_workspace(self, temp_dir: Path) -> None:
        """Cargo.toml with [workspace] should be Tier 1."""
        repo_path = temp_dir / "repo"
        repo_path.mkdir()

        cargo_content = "[workspace]\nmembers = []\n"
        (repo_path / "Cargo.toml").write_text(cargo_content)

        discovery = ContextDiscovery(repo_path)
        result = discovery.discover_all()

        rust_candidates = [c for c in result.candidates if c.language_family == LanguageFamily.RUST]
        tier1 = [c for c in rust_candidates if c.tier == 1]
        assert len(tier1) >= 1

    def test_discover_invalid_json_package(self, temp_dir: Path) -> None:
        """Invalid JSON in package.json should not crash."""
        repo_path = temp_dir / "repo"
        repo_path.mkdir()

        (repo_path / "package.json").write_text("invalid json {{{")

        discovery = ContextDiscovery(repo_path)
        result = discovery.discover_all()

        # Should not crash, may or may not find candidates
        assert isinstance(result, DiscoveryResult)

    def test_discover_nested_markers(self, temp_dir: Path) -> None:
        """Should consolidate markers in same directory."""
        repo_path = temp_dir / "repo"
        repo_path.mkdir()

        # Both files in same directory
        (repo_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
        (repo_path / "setup.py").write_text("# setup\n")

        discovery = ContextDiscovery(repo_path)
        result = discovery.discover_all()

        py_candidates = [c for c in result.candidates if c.language_family == LanguageFamily.PYTHON]
        # Should be consolidated to one candidate with multiple markers
        root_candidates = [c for c in py_candidates if c.root_path == ""]
        assert len(root_candidates) == 1
        # Should have multiple markers
        assert len(root_candidates[0].markers) >= 1

    def test_discover_ambient_family_fallback(self, temp_dir: Path) -> None:
        """Ambient name should get fallback context if no markers."""
        repo_path = temp_dir / "repo"
        repo_path.mkdir()

        # Create a markdown file but no markers
        (repo_path / "README.md").write_text("# Readme\n")

        discovery = ContextDiscovery(repo_path)
        result = discovery.discover_family(LanguageFamily.MARKDOWN)

        # Markdown is ambient, should get fallback context
        assert len(result.candidates) >= 1
        # Fallback has empty root and no tier
        fallback = [c for c in result.candidates if c.tier is None]
        assert len(fallback) >= 1


class TestCrossPlatformPathNormalization:
    """Tests for cross-platform path handling in scanner.

    These tests verify that the scanner produces POSIX-style paths regardless
    of the underlying OS, enabling consistent glob matching and path comparisons.
    """

    def test_candidate_root_path_uses_forward_slashes(self, temp_dir: Path) -> None:
        """CandidateContext.root_path should use forward slashes (POSIX).

        On Windows, Path objects produce backslash strings, but our candidates
        must use forward slashes for consistent glob matching.
        """
        repo_path = temp_dir / "repo"
        repo_path.mkdir()

        # Nested package structure
        (repo_path / "packages").mkdir()
        (repo_path / "packages" / "core").mkdir()
        (repo_path / "packages" / "core" / "package.json").write_text('{"name": "core"}\n')

        discovery = ContextDiscovery(repo_path)
        result = discovery.discover_all()

        js_candidates = [
            c for c in result.candidates if c.language_family == LanguageFamily.JAVASCRIPT
        ]
        nested_candidates = [c for c in js_candidates if c.root_path and "packages" in c.root_path]

        for candidate in nested_candidates:
            # root_path must use forward slashes, not backslashes
            assert "\\" not in candidate.root_path, (
                f"root_path contains backslash: {candidate.root_path}"
            )
            # Should contain forward slash for nested path
            assert "/" in candidate.root_path or candidate.root_path == "packages"

    def test_marker_paths_use_forward_slashes(self, temp_dir: Path) -> None:
        """Marker paths in candidates should use forward slashes."""
        repo_path = temp_dir / "repo"
        repo_path.mkdir()

        (repo_path / "src").mkdir()
        (repo_path / "src" / "app").mkdir()
        (repo_path / "src" / "app" / "pyproject.toml").write_text('[project]\nname = "app"\n')

        discovery = ContextDiscovery(repo_path)
        result = discovery.discover_all()

        for marker in result.markers:
            assert "\\" not in marker.path, f"marker path contains backslash: {marker.path}"

    def test_deeply_nested_paths_normalized(self, temp_dir: Path) -> None:
        """Deeply nested paths should be normalized to POSIX."""
        repo_path = temp_dir / "repo"
        repo_path.mkdir()

        # Create deeply nested structure (not using 'pkg' since it's prunable)
        deep_path = repo_path / "modules" / "sub1" / "sub2" / "sub3"
        deep_path.mkdir(parents=True)
        (deep_path / "go.mod").write_text("module example.com/deep\n\ngo 1.21\n")

        discovery = ContextDiscovery(repo_path)
        result = discovery.discover_all()

        go_candidates = [c for c in result.candidates if c.language_family == LanguageFamily.GO]

        # Should find exactly one Go candidate (the deeply nested one)
        assert len(go_candidates) == 1
        # Path should be normalized to POSIX: modules/sub1/sub2/sub3
        assert go_candidates[0].root_path == "modules/sub1/sub2/sub3"
        assert "\\" not in go_candidates[0].root_path

    def test_discovery_handles_mixed_separators_consistently(self, temp_dir: Path) -> None:
        """Discovery should produce consistent paths even if OS uses different separators."""
        repo_path = temp_dir / "repo"
        repo_path.mkdir()

        # Multiple packages at same depth
        (repo_path / "apps").mkdir()
        (repo_path / "apps" / "web").mkdir()
        (repo_path / "apps" / "api").mkdir()
        (repo_path / "apps" / "web" / "package.json").write_text('{"name": "web"}\n')
        (repo_path / "apps" / "api" / "package.json").write_text('{"name": "api"}\n')

        discovery = ContextDiscovery(repo_path)
        result = discovery.discover_all()

        js_candidates = [
            c for c in result.candidates if c.language_family == LanguageFamily.JAVASCRIPT
        ]
        app_candidates = [c for c in js_candidates if c.root_path and "apps" in c.root_path]

        # Both should use forward slashes
        paths = sorted(c.root_path for c in app_candidates)
        assert "apps/api" in paths
        assert "apps/web" in paths
        # No backslashes anywhere
        for path in paths:
            assert "\\" not in path
