"""Unit tests for Tier 1 Authority Filter (authority.py).

Tests cover:
- JavaScript pnpm-workspace.yaml authority
- JavaScript npm workspaces authority
- Go workspace authority (go.work)
- Rust workspace authority (Cargo.toml workspace)
- JVM multi-module authority
- Detached context detection
"""

from __future__ import annotations

from pathlib import Path

from codeplane.index._internal.discovery import (
    AuthorityResult,
    Tier1AuthorityFilter,
)
from codeplane.index.models import CandidateContext, LanguageFamily, ProbeStatus


def make_candidate(
    family: LanguageFamily,
    root_path: str,
    tier: int,
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


class TestTier1AuthorityFilter:
    """Tests for Tier1AuthorityFilter class."""

    def test_no_tier1_markers_passes_all(self, temp_dir: Path) -> None:
        """Without Tier 1 markers, all candidates pass through."""
        repo_path = temp_dir / "repo"
        repo_path.mkdir()

        candidates = [
            make_candidate(LanguageFamily.PYTHON, "pkg-a", 2),
            make_candidate(LanguageFamily.PYTHON, "pkg-b", 2),
        ]

        authority = Tier1AuthorityFilter(repo_path)
        result = authority.apply(candidates)

        assert isinstance(result, AuthorityResult)
        assert len(result.pending) == 2
        assert len(result.detached) == 0

    def test_pnpm_workspace_authority(self, temp_dir: Path) -> None:
        """pnpm-workspace.yaml should define authority."""
        repo_path = temp_dir / "repo"
        repo_path.mkdir()

        # Create pnpm workspace config
        workspace_yaml = """packages:
  - 'packages/*'
"""
        (repo_path / "pnpm-workspace.yaml").write_text(workspace_yaml)

        # Create packages directory
        (repo_path / "packages").mkdir()
        (repo_path / "packages" / "included").mkdir()
        (repo_path / "packages" / "included" / "package.json").write_text("{}")

        # Create detached package (not in workspace)
        (repo_path / "other").mkdir()
        (repo_path / "other" / "package.json").write_text("{}")

        candidates = [
            # Tier 1 workspace root (required for authority filtering)
            make_candidate(
                LanguageFamily.JAVASCRIPT,
                "",
                1,
                ["pnpm-workspace.yaml"],
            ),
            make_candidate(
                LanguageFamily.JAVASCRIPT,
                "packages/included",
                2,
                ["packages/included/package.json"],
            ),
            make_candidate(LanguageFamily.JAVASCRIPT, "other", 2, ["other/package.json"]),
        ]

        authority = Tier1AuthorityFilter(repo_path)
        result = authority.apply(candidates)

        # packages/included should be pending (in workspace)
        # other should be detached (not in workspace)
        pending_roots = {c.root_path for c in result.pending}
        detached_roots = {c.root_path for c in result.detached}

        assert "packages/included" in pending_roots
        assert "other" in detached_roots

    def test_npm_workspaces_authority(self, temp_dir: Path) -> None:
        """package.json workspaces field should define authority."""
        repo_path = temp_dir / "repo"
        repo_path.mkdir()

        # Create root package.json with workspaces
        root_package = """{
  "name": "monorepo",
  "workspaces": ["packages/*"]
}"""
        (repo_path / "package.json").write_text(root_package)

        (repo_path / "packages").mkdir()
        (repo_path / "packages" / "core").mkdir()
        (repo_path / "packages" / "core" / "package.json").write_text("{}")

        candidates = [
            make_candidate(
                LanguageFamily.JAVASCRIPT,
                "packages/core",
                2,
                ["packages/core/package.json"],
            ),
        ]

        authority = Tier1AuthorityFilter(repo_path)
        result = authority.apply(candidates)

        # Should be pending (in workspace)
        assert len(result.pending) == 1
        assert result.pending[0].root_path == "packages/core"

    def test_go_work_authority(self, temp_dir: Path) -> None:
        """go.work should define authority for Go modules."""
        repo_path = temp_dir / "repo"
        repo_path.mkdir()

        # Create go.work file
        go_work = """go 1.21

use (
    ./cmd
    ./pkg
)
"""
        (repo_path / "go.work").write_text(go_work)

        (repo_path / "cmd").mkdir()
        (repo_path / "cmd" / "go.mod").write_text("module example.com/cmd\n")

        (repo_path / "pkg").mkdir()
        (repo_path / "pkg" / "go.mod").write_text("module example.com/pkg\n")

        (repo_path / "orphan").mkdir()
        (repo_path / "orphan" / "go.mod").write_text("module example.com/orphan\n")

        candidates = [
            # Tier 1 workspace root (required for authority filtering)
            make_candidate(LanguageFamily.GO, "", 1, ["go.work"]),
            make_candidate(LanguageFamily.GO, "cmd", 2, ["cmd/go.mod"]),
            make_candidate(LanguageFamily.GO, "pkg", 2, ["pkg/go.mod"]),
            make_candidate(LanguageFamily.GO, "orphan", 2, ["orphan/go.mod"]),
        ]

        authority = Tier1AuthorityFilter(repo_path)
        result = authority.apply(candidates)

        pending_roots = {c.root_path for c in result.pending}
        detached_roots = {c.root_path for c in result.detached}

        assert "cmd" in pending_roots
        assert "pkg" in pending_roots
        assert "orphan" in detached_roots

    def test_cargo_workspace_authority(self, temp_dir: Path) -> None:
        """Cargo.toml workspace should define authority."""
        repo_path = temp_dir / "repo"
        repo_path.mkdir()

        # Create workspace Cargo.toml
        workspace_toml = """[workspace]
members = [
    "crates/*"
]
"""
        (repo_path / "Cargo.toml").write_text(workspace_toml)

        (repo_path / "crates").mkdir()
        (repo_path / "crates" / "lib-a").mkdir()
        (repo_path / "crates" / "lib-a" / "Cargo.toml").write_text('[package]\nname = "lib-a"\n')

        candidates = [
            make_candidate(
                LanguageFamily.RUST,
                "crates/lib-a",
                2,
                ["crates/lib-a/Cargo.toml"],
            ),
        ]

        authority = Tier1AuthorityFilter(repo_path)
        result = authority.apply(candidates)

        assert len(result.pending) == 1
        assert result.pending[0].root_path == "crates/lib-a"

    def test_non_code_families_pass_through(self, temp_dir: Path) -> None:
        """Non-code families should pass through without authority check."""
        repo_path = temp_dir / "repo"
        repo_path.mkdir()

        candidates = [
            make_candidate(LanguageFamily.MARKDOWN, "", None),
            make_candidate(LanguageFamily.JSON_YAML, "", None),
        ]

        authority = Tier1AuthorityFilter(repo_path)
        result = authority.apply(candidates)

        # Data families don't need authority
        assert len(result.pending) == 2
        assert len(result.detached) == 0


class TestAuthorityResult:
    """Tests for AuthorityResult dataclass."""

    def test_authority_result_structure(self) -> None:
        """AuthorityResult should have pending and detached lists."""
        result = AuthorityResult(pending=[], detached=[])

        assert hasattr(result, "pending")
        assert hasattr(result, "detached")
        assert isinstance(result.pending, list)
        assert isinstance(result.detached, list)
