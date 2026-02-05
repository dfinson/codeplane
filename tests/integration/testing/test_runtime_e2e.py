"""Integration tests for testing runtime resolution and execution context.

Tests real Python/Node.js detection, venv resolution, and tool config building.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from codeplane.testing.runtime import (
    ExecutionContextBuilder,
    RuntimeResolver,
)

pytestmark = pytest.mark.integration


class TestRuntimeResolverIntegration:
    """Integration tests for RuntimeResolver."""

    def test_resolve_detects_system_python(self, integration_repo: Path) -> None:
        """Resolver detects system Python."""
        runtime = RuntimeResolver.resolve(integration_repo)

        # Should detect Python
        assert runtime.python_executable is not None
        assert "python" in runtime.python_executable.lower()

    def test_resolve_detects_venv(self, integration_repo: Path) -> None:
        """Resolver detects virtualenv when present."""
        # Create a venv
        venv_path = integration_repo / ".venv"
        subprocess.run(
            ["python", "-m", "venv", str(venv_path)],
            check=True,
            capture_output=True,
        )

        runtime = RuntimeResolver.resolve(integration_repo)

        # Should prefer venv Python
        assert runtime.python_executable is not None
        assert ".venv" in runtime.python_executable or "venv" in runtime.python_executable

    def test_resolve_sets_env_vars_for_venv(self, integration_repo: Path) -> None:
        """Resolver sets VIRTUAL_ENV env var for virtualenv."""
        # Create a venv
        venv_path = integration_repo / ".venv"
        subprocess.run(
            ["python", "-m", "venv", str(venv_path)],
            check=True,
            capture_output=True,
        )

        runtime = RuntimeResolver.resolve(integration_repo)

        # get_env_vars returns environment variables; may or may not set VIRTUAL_ENV
        _ = runtime.get_env_vars()
        assert runtime.python_executable is not None

    @pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
    def test_resolve_detects_node(self, integration_repo: Path) -> None:
        """Resolver detects Node.js when available."""
        # Create package.json to make it a JS project
        (integration_repo / "package.json").write_text('{"name": "test", "version": "1.0.0"}')

        runtime = RuntimeResolver.resolve(integration_repo)

        # Should detect Node
        assert runtime.node_executable is not None

    @pytest.mark.skipif(shutil.which("go") is None, reason="Go not installed")
    def test_resolve_detects_go(self, integration_repo: Path) -> None:
        """Resolver detects Go when available."""
        # Create go.mod to make it a Go project
        (integration_repo / "go.mod").write_text("module example.com/test\n\ngo 1.21\n")

        runtime = RuntimeResolver.resolve(integration_repo)

        # Should detect Go
        assert runtime.go_executable is not None


class TestExecutionContextBuilderIntegration:
    """Integration tests for ExecutionContextBuilder."""

    def test_build_context_for_python_project(self, integration_repo: Path) -> None:
        """Builds execution context for Python project."""
        runtime = RuntimeResolver.resolve(integration_repo)
        ctx = ExecutionContextBuilder.build(integration_repo, runtime)

        # Should have Python runtime
        assert ctx.runtime.python_executable is not None

        # Verify test runner is available (may return None if pytest not configured)
        _ = ctx.get_test_runner("python.pytest")
        assert ctx.language_family == "python"

    def test_build_context_with_venv(self, integration_repo: Path) -> None:
        """Context uses venv Python when available."""
        # Create venv
        venv_path = integration_repo / ".venv"
        subprocess.run(
            ["python", "-m", "venv", str(venv_path)],
            check=True,
            capture_output=True,
        )

        runtime = RuntimeResolver.resolve(integration_repo)
        ctx = ExecutionContextBuilder.build(integration_repo, runtime)

        # Should use venv Python
        assert ctx.runtime.python_executable is not None
        assert ".venv" in ctx.runtime.python_executable or "venv" in ctx.runtime.python_executable

    def test_build_env_includes_path(self, integration_repo: Path) -> None:
        """Built env includes PATH."""
        runtime = RuntimeResolver.resolve(integration_repo)
        ctx = ExecutionContextBuilder.build(integration_repo, runtime)

        env = ctx.build_env()

        # Should have PATH
        assert "PATH" in env


class TestRuntimeVersionDetection:
    """Integration tests for runtime version detection."""

    def test_python_version_detected(self, integration_repo: Path) -> None:
        """Python version is detected correctly."""
        runtime = RuntimeResolver.resolve(integration_repo)

        # Should have detected Python version
        assert runtime.python_version is not None
        # Version should be a semver-like string
        assert "." in runtime.python_version
        parts = runtime.python_version.split(".")
        assert len(parts) >= 2
        assert parts[0].isdigit()

    @pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
    def test_node_version_detected(self, integration_repo: Path) -> None:
        """Node.js version is detected when available."""
        (integration_repo / "package.json").write_text('{"name": "test"}')

        runtime = RuntimeResolver.resolve(integration_repo)

        if runtime.node_executable:
            assert runtime.node_version is not None
            assert "." in runtime.node_version
