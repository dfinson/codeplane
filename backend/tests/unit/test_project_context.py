"""Tests for action_policy.project_context — manifest parsing and host extraction."""

from __future__ import annotations

import json
from pathlib import Path  # noqa: TC003

import pytest

from backend.services.action_policy.project_context import (
    ProjectContext,
    is_manifest_file,
)


@pytest.fixture
def tmp_worktree(tmp_path: Path) -> Path:
    return tmp_path


# ---------------------------------------------------------------------------
# is_manifest_file (module-level helper)
# ---------------------------------------------------------------------------


class TestIsManifestFile:
    def test_package_json(self) -> None:
        assert is_manifest_file("package.json") is True

    def test_pyproject_toml(self) -> None:
        assert is_manifest_file("pyproject.toml") is True

    def test_requirements_txt(self) -> None:
        assert is_manifest_file("requirements.txt") is True

    def test_cargo_toml(self) -> None:
        assert is_manifest_file("Cargo.toml") is True

    def test_go_mod(self) -> None:
        assert is_manifest_file("go.mod") is True

    def test_random_file(self) -> None:
        assert is_manifest_file("README.md") is False

    def test_nested_path(self) -> None:
        assert is_manifest_file("src/package.json") is True


# ---------------------------------------------------------------------------
# ProjectContext.__init__
# ---------------------------------------------------------------------------


class TestProjectContextInit:
    def test_initial_state(self, tmp_worktree: Path) -> None:
        ctx = ProjectContext(str(tmp_worktree))
        assert ctx.worktree == str(tmp_worktree)
        assert ctx.built is False
        assert len(ctx.dependencies) == 0
        assert len(ctx.configured_hosts) == 0


# ---------------------------------------------------------------------------
# _parse_package_json
# ---------------------------------------------------------------------------


class TestParsePackageJson:
    @pytest.mark.asyncio
    async def test_extracts_dependencies(self, tmp_worktree: Path) -> None:
        pkg = {
            "dependencies": {"react": "^18.0.0", "axios": "^1.0.0"},
            "devDependencies": {"vitest": "^1.0.0"},
        }
        (tmp_worktree / "package.json").write_text(json.dumps(pkg))
        ctx = ProjectContext(str(tmp_worktree))
        await ctx.build()
        assert "react" in ctx.dependencies
        assert "axios" in ctx.dependencies
        assert "vitest" in ctx.dependencies

    @pytest.mark.asyncio
    async def test_handles_invalid_json(self, tmp_worktree: Path) -> None:
        (tmp_worktree / "package.json").write_text("{invalid json")
        ctx = ProjectContext(str(tmp_worktree))
        await ctx.build()
        assert ctx.built is True
        assert len(ctx.dependencies) == 0


# ---------------------------------------------------------------------------
# _parse_pyproject_toml
# ---------------------------------------------------------------------------


class TestParsePyprojectToml:
    @pytest.mark.asyncio
    async def test_extracts_deps_from_dependencies_section(self, tmp_worktree: Path) -> None:
        content = """
[project]
name = "myproject"

[project.dependencies]
fastapi = ">=0.100"
sqlalchemy = ">=2.0"
"""
        (tmp_worktree / "pyproject.toml").write_text(content)
        ctx = ProjectContext(str(tmp_worktree))
        await ctx.build()
        assert "fastapi" in ctx.dependencies
        assert "sqlalchemy" in ctx.dependencies


# ---------------------------------------------------------------------------
# _parse_requirements_txt
# ---------------------------------------------------------------------------


class TestParseRequirementsTxt:
    @pytest.mark.asyncio
    async def test_extracts_packages(self, tmp_worktree: Path) -> None:
        content = """
requests>=2.28
flask==2.3.0
# comment
-e ./local_pkg
"""
        (tmp_worktree / "requirements.txt").write_text(content)
        ctx = ProjectContext(str(tmp_worktree))
        await ctx.build()
        assert "requests" in ctx.dependencies
        assert "flask" in ctx.dependencies


# ---------------------------------------------------------------------------
# _parse_cargo_toml
# ---------------------------------------------------------------------------


class TestParseCargoToml:
    @pytest.mark.asyncio
    async def test_extracts_dependencies(self, tmp_worktree: Path) -> None:
        content = """
[package]
name = "myapp"

[dependencies]
serde = "1.0"
tokio = { version = "1", features = ["full"] }
"""
        (tmp_worktree / "Cargo.toml").write_text(content)
        ctx = ProjectContext(str(tmp_worktree))
        await ctx.build()
        assert "serde" in ctx.dependencies
        assert "tokio" in ctx.dependencies


# ---------------------------------------------------------------------------
# _parse_go_mod
# ---------------------------------------------------------------------------


class TestParseGoMod:
    @pytest.mark.asyncio
    async def test_extracts_module_deps(self, tmp_worktree: Path) -> None:
        content = """
module example.com/myapp

go 1.21

require (
\tgithub.com/gin-gonic/gin v1.9.1
\tgithub.com/stripe/stripe-go v74.0.0
)
"""
        (tmp_worktree / "go.mod").write_text(content)
        ctx = ProjectContext(str(tmp_worktree))
        await ctx.build()
        assert "gin" in ctx.dependencies
        assert "stripe-go" in ctx.dependencies


# ---------------------------------------------------------------------------
# _parse_composer_json
# ---------------------------------------------------------------------------


class TestParseComposerJson:
    @pytest.mark.asyncio
    async def test_extracts_composer_deps(self, tmp_worktree: Path) -> None:
        data = {"require": {"laravel/framework": "^10.0", "guzzlehttp/guzzle": "^7.0"}}
        (tmp_worktree / "composer.json").write_text(json.dumps(data))
        ctx = ProjectContext(str(tmp_worktree))
        await ctx.build()
        assert "framework" in ctx.dependencies
        assert "guzzle" in ctx.dependencies


# ---------------------------------------------------------------------------
# Config file parsing — hosts
# ---------------------------------------------------------------------------


class TestConfigParsing:
    @pytest.mark.asyncio
    async def test_extracts_hosts_from_env(self, tmp_worktree: Path) -> None:
        content = """
DATABASE_URL=postgres://db.myhost.com:5432/mydb
API_URL=https://api.stripe.com/v1
"""
        (tmp_worktree / ".env").write_text(content)
        ctx = ProjectContext(str(tmp_worktree))
        await ctx.build()
        assert "db.myhost.com" in ctx.configured_hosts
        assert "api.stripe.com" in ctx.configured_hosts

    @pytest.mark.asyncio
    async def test_ignores_localhost(self, tmp_worktree: Path) -> None:
        content = "API_URL=http://localhost:3000\n"
        (tmp_worktree / ".env").write_text(content)
        ctx = ProjectContext(str(tmp_worktree))
        await ctx.build()
        assert "localhost" not in ctx.configured_hosts

    @pytest.mark.asyncio
    async def test_docker_compose_services(self, tmp_worktree: Path) -> None:
        content = """
services:
  postgres:
    image: postgres:15
  redis:
    image: redis:7
"""
        (tmp_worktree / "docker-compose.yml").write_text(content)
        ctx = ProjectContext(str(tmp_worktree))
        await ctx.build()
        assert "postgres" in ctx.services
        assert "redis" in ctx.services


# ---------------------------------------------------------------------------
# Initial snapshot freezing
# ---------------------------------------------------------------------------


class TestInitialSnapshot:
    @pytest.mark.asyncio
    async def test_lazy_build_never_claims_an_initial_trusted_snapshot(self, tmp_worktree: Path) -> None:
        (tmp_worktree / "package.json").write_text(json.dumps({"dependencies": {"late-added": "1"}}))
        ctx = ProjectContext(str(tmp_worktree))
        await ctx.build()
        assert ctx.has_dependency("late-added")
        assert not ctx.has_initial_dependency("late-added")

    @pytest.mark.asyncio
    async def test_initial_deps_frozen(self, tmp_worktree: Path) -> None:
        pkg = {"dependencies": {"react": "^18"}}
        (tmp_worktree / "package.json").write_text(json.dumps(pkg))
        ctx = ProjectContext(str(tmp_worktree))
        await ctx.capture_initial()
        assert ctx.has_initial_dependency("react") is True
        assert ctx.has_dependency("react") is True

    @pytest.mark.asyncio
    async def test_dependency_added_after_build_not_in_initial(self, tmp_worktree: Path) -> None:
        pkg = {"dependencies": {"react": "^18"}}
        (tmp_worktree / "package.json").write_text(json.dumps(pkg))
        ctx = ProjectContext(str(tmp_worktree))
        await ctx.capture_initial()

        # Simulate agent adding a new dependency
        ctx.dependencies.add("malware")
        assert ctx.has_dependency("malware") is True
        assert ctx.has_initial_dependency("malware") is False

    @pytest.mark.asyncio
    async def test_initial_host_frozen(self, tmp_worktree: Path) -> None:
        (tmp_worktree / ".env").write_text("API=https://api.stripe.com/v1\n")
        ctx = ProjectContext(str(tmp_worktree))
        await ctx.capture_initial()
        assert ctx.has_initial_host("api.stripe.com") is True

    def test_has_initial_dependency_before_build(self, tmp_worktree: Path) -> None:
        ctx = ProjectContext(str(tmp_worktree))
        assert ctx.has_initial_dependency("react") is False

    def test_has_initial_host_before_build(self, tmp_worktree: Path) -> None:
        ctx = ProjectContext(str(tmp_worktree))
        assert ctx.has_initial_host("example.com") is False


# ---------------------------------------------------------------------------
# Invalidation
# ---------------------------------------------------------------------------


class TestInvalidation:
    @pytest.mark.asyncio
    async def test_invalidate_resets_built(self, tmp_worktree: Path) -> None:
        ctx = ProjectContext(str(tmp_worktree))
        await ctx.build()
        assert ctx.built is True
        ctx.invalidate()
        assert ctx.built is False

    @pytest.mark.asyncio
    async def test_initial_services_property(self, tmp_worktree: Path) -> None:
        content = """
services:
  postgres:
    image: postgres:15
"""
        (tmp_worktree / "docker-compose.yml").write_text(content)
        ctx = ProjectContext(str(tmp_worktree))
        await ctx.capture_initial()
        assert "postgres" in ctx.initial_services

    def test_initial_services_before_build(self, tmp_worktree: Path) -> None:
        ctx = ProjectContext(str(tmp_worktree))
        assert ctx.initial_services == frozenset()
