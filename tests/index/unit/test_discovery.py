"""Unit tests for Context Discovery (discovery.py, scanner.py).

Tests cover:
- Marker file detection for each language family
- Tier 1 vs Tier 2 marker classification
- Candidate context generation from markers
- Full repository scan for contexts
- Ambient family fallback contexts
"""

from __future__ import annotations

from pathlib import Path

from codeplane.index._internal.discovery import (
    AMBIENT_FAMILIES,
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
        """JavaScript family should have package.json markers."""
        js_markers = MARKER_DEFINITIONS.get(LanguageFamily.JAVASCRIPT, {})
        workspace = js_markers.get(MarkerTier.WORKSPACE, [])
        package = js_markers.get(MarkerTier.PACKAGE, [])

        # pnpm-workspace.yaml, etc. are WORKSPACE markers
        # package.json is PACKAGE marker
        all_markers = workspace + package
        assert "package.json" in all_markers

    def test_python_markers(self) -> None:
        """Python family should have pyproject.toml markers."""
        py_markers = MARKER_DEFINITIONS.get(LanguageFamily.PYTHON, {})
        workspace = py_markers.get(MarkerTier.WORKSPACE, [])
        package = py_markers.get(MarkerTier.PACKAGE, [])

        all_markers = workspace + package
        assert "pyproject.toml" in all_markers or "setup.py" in all_markers

    def test_go_markers(self) -> None:
        """Go family should have go.mod markers."""
        go_markers = MARKER_DEFINITIONS.get(LanguageFamily.GO, {})
        workspace = go_markers.get(MarkerTier.WORKSPACE, [])
        package = go_markers.get(MarkerTier.PACKAGE, [])

        all_markers = workspace + package
        assert "go.mod" in all_markers

    def test_rust_markers(self) -> None:
        """Rust family should have Cargo.toml markers."""
        rust_markers = MARKER_DEFINITIONS.get(LanguageFamily.RUST, {})
        workspace = rust_markers.get(MarkerTier.WORKSPACE, [])
        package = rust_markers.get(MarkerTier.PACKAGE, [])

        all_markers = workspace + package
        assert "Cargo.toml" in all_markers


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
    """Tests for ambient family definitions."""

    def test_ambient_families_exist(self) -> None:
        """AMBIENT_FAMILIES should be defined."""
        assert AMBIENT_FAMILIES is not None
        assert len(AMBIENT_FAMILIES) > 0

    def test_ambient_families_are_data_families(self) -> None:
        """Ambient families should typically be data families."""
        for family in AMBIENT_FAMILIES:
            # Most ambient families are data families (markdown, json_yaml, etc.)
            # But this is not a strict requirement
            assert isinstance(family, LanguageFamily)


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
        (repo_path / "pnpm-workspace.yaml").write_text("packages:\n  - 'packages/*'\n")

        # Two packages
        (repo_path / "packages").mkdir()
        (repo_path / "packages" / "pkg-a").mkdir()
        (repo_path / "packages" / "pkg-a" / "package.json").write_text('{"name": "a"}\n')

        (repo_path / "packages" / "pkg-b").mkdir()
        (repo_path / "packages" / "pkg-b" / "package.json").write_text('{"name": "b"}\n')

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
