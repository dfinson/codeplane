"""Tests for testing.runtime module.

Tests the context runtime models, resolution, and execution context building.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from codeplane.testing.runtime import (
    ContextRuntime,
    ExecutionContextBuilder,
    RuntimeExecutionContext,
    RuntimeResolutionResult,
    RuntimeResolver,
    ToolConfig,
)

# =============================================================================
# Tests for ContextRuntime
# =============================================================================


class TestContextRuntime:
    """Tests for ContextRuntime SQLModel."""

    def test_default_values(self) -> None:
        """ContextRuntime has sensible defaults."""
        runtime = ContextRuntime(context_id=1)
        assert runtime.context_id == 1
        assert runtime.python_executable is None
        assert runtime.node_executable is None
        assert runtime.env_vars_json is None

    def test_get_env_vars_empty(self) -> None:
        """get_env_vars returns empty dict when env_vars_json is None."""
        runtime = ContextRuntime(context_id=1)
        assert runtime.get_env_vars() == {}

    def test_get_env_vars_parses_json(self) -> None:
        """get_env_vars parses env_vars_json."""
        runtime = ContextRuntime(context_id=1)
        runtime.env_vars_json = json.dumps({"VIRTUAL_ENV": "/path/to/venv", "FOO": "bar"})
        env = runtime.get_env_vars()
        assert env["VIRTUAL_ENV"] == "/path/to/venv"
        assert env["FOO"] == "bar"

    def test_set_env_vars_stores_json(self) -> None:
        """set_env_vars stores as JSON."""
        runtime = ContextRuntime(context_id=1)
        runtime.set_env_vars({"KEY": "value"})
        assert runtime.env_vars_json == json.dumps({"KEY": "value"})

    def test_set_env_vars_empty_dict_sets_none(self) -> None:
        """set_env_vars with empty dict sets None."""
        runtime = ContextRuntime(context_id=1)
        runtime.set_env_vars({})
        assert runtime.env_vars_json is None

    def test_roundtrip_env_vars(self) -> None:
        """Env vars can be set and retrieved."""
        runtime = ContextRuntime(context_id=1)
        original = {"PATH": "/bin", "HOME": "/home/user"}
        runtime.set_env_vars(original)
        retrieved = runtime.get_env_vars()
        assert retrieved == original


# =============================================================================
# Tests for ToolConfig
# =============================================================================


class TestToolConfig:
    """Tests for ToolConfig dataclass."""

    def test_default_values(self) -> None:
        """ToolConfig has sensible defaults."""
        config = ToolConfig(
            tool_id="python.pytest",
            executable="/usr/bin/python",
        )
        assert config.tool_id == "python.pytest"
        assert config.executable == "/usr/bin/python"
        assert config.base_args == []
        assert config.env_overrides == {}
        assert config.available is True
        assert config.version is None

    def test_with_all_fields(self) -> None:
        """ToolConfig accepts all fields."""
        config = ToolConfig(
            tool_id="python.ruff",
            executable="/venv/bin/ruff",
            base_args=["check", "--fix"],
            env_overrides={"NO_COLOR": "1"},
            available=True,
            version="0.1.9",
        )
        assert config.base_args == ["check", "--fix"]
        assert config.env_overrides == {"NO_COLOR": "1"}
        assert config.version == "0.1.9"


# =============================================================================
# Tests for RuntimeExecutionContext
# =============================================================================


class TestRuntimeExecutionContext:
    """Tests for RuntimeExecutionContext dataclass."""

    def _create_context(self) -> RuntimeExecutionContext:
        """Create a test RuntimeExecutionContext."""
        runtime = ContextRuntime(context_id=1)
        runtime.python_executable = "/venv/bin/python"
        runtime.set_env_vars({"VIRTUAL_ENV": "/venv"})

        return RuntimeExecutionContext(
            context_id=1,
            language_family="python",
            root_path=Path("/repo"),
            runtime=runtime,
            test_runners={
                "python.pytest": ToolConfig(
                    tool_id="python.pytest",
                    executable="/venv/bin/python",
                    base_args=["-m", "pytest"],
                )
            },
            linters={
                "python.ruff": ToolConfig(
                    tool_id="python.ruff",
                    executable="/venv/bin/ruff",
                    base_args=["check"],
                )
            },
            env_vars={"CUSTOM": "value"},
        )

    def test_get_test_runner(self) -> None:
        """get_test_runner returns correct config."""
        ctx = self._create_context()
        runner = ctx.get_test_runner("python.pytest")
        assert runner is not None
        assert runner.tool_id == "python.pytest"

    def test_get_test_runner_not_found(self) -> None:
        """get_test_runner returns None for unknown runner."""
        ctx = self._create_context()
        assert ctx.get_test_runner("python.unknown") is None

    def test_get_linter(self) -> None:
        """get_linter returns correct config."""
        ctx = self._create_context()
        linter = ctx.get_linter("python.ruff")
        assert linter is not None
        assert linter.tool_id == "python.ruff"

    def test_get_linter_not_found(self) -> None:
        """get_linter returns None for unknown linter."""
        ctx = self._create_context()
        assert ctx.get_linter("python.unknown") is None

    def test_build_env_merges_correctly(self) -> None:
        """build_env merges environment in correct order."""
        ctx = self._create_context()
        tool_config = ToolConfig(
            tool_id="test",
            executable="/bin/test",
            env_overrides={"TOOL_VAR": "tool_value"},
        )

        env = ctx.build_env(tool_config)

        # Should include process env vars
        assert "PATH" in env or "HOME" in env  # At least some system vars

        # Should include runtime env vars
        assert env.get("VIRTUAL_ENV") == "/venv"

        # Should include context env vars
        assert env.get("CUSTOM") == "value"

        # Should include tool env overrides
        assert env.get("TOOL_VAR") == "tool_value"

    def test_build_env_without_tool_config(self) -> None:
        """build_env works without tool config."""
        ctx = self._create_context()
        env = ctx.build_env()
        assert "VIRTUAL_ENV" in env
        assert "CUSTOM" in env


# =============================================================================
# Tests for RuntimeResolver
# =============================================================================


class TestRuntimeResolver:
    """Tests for RuntimeResolver class."""

    def test_resolve_creates_context_runtime(self) -> None:
        """resolve() creates a ContextRuntime."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            runtime = RuntimeResolver.resolve(workspace)
            assert isinstance(runtime, ContextRuntime)
            assert runtime.resolved_at is not None

    def test_resolve_detects_system_python(self) -> None:
        """resolve() detects system Python if available."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            runtime = RuntimeResolver.resolve(workspace)

            # System Python should be found (in most test environments)
            if shutil.which("python3") or shutil.which("python"):
                assert runtime.python_executable is not None
                assert runtime.python_version is not None

    def test_resolve_for_context_python(self) -> None:
        """resolve_for_context detects Python runtime."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            resolver = RuntimeResolver(repo_root)

            result = resolver.resolve_for_context(
                context_id=1,
                language_family="python",
                root_path="",
            )

            assert isinstance(result, RuntimeResolutionResult)
            assert result.runtime.context_id == 1
            assert result.method in [
                "venv_detected",
                "poetry_detected",
                "path_detected",
                "not_found",
            ]

    def test_resolve_for_context_detects_venv(self) -> None:
        """resolve_for_context detects venv in repo."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)

            # Create a fake venv
            venv_path = repo_root / ".venv"
            bin_path = venv_path / "bin"
            bin_path.mkdir(parents=True)

            # Create pyvenv.cfg to mark as venv
            (venv_path / "pyvenv.cfg").write_text("home = /usr/bin\n")

            # Create fake python
            python_exe = bin_path / "python"
            python_exe.write_text("#!/bin/bash\necho 'Python 3.12.0'")
            python_exe.chmod(0o755)

            resolver = RuntimeResolver(repo_root)
            result = resolver.resolve_for_context(
                context_id=1,
                language_family="python",
                root_path="",
            )

            # Should detect venv
            assert result.method == "venv_detected"
            assert result.runtime.python_venv_path == str(venv_path)

    def test_resolve_for_context_javascript(self) -> None:
        """resolve_for_context handles JavaScript."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            resolver = RuntimeResolver(repo_root)

            result = resolver.resolve_for_context(
                context_id=2,
                language_family="javascript",
                root_path="",
            )

            assert result.runtime.context_id == 2
            # Will find node if installed, or not_found
            assert result.method in ["nvm_detected", "path_detected", "not_found"]

    def test_resolve_for_context_unknown_language(self) -> None:
        """resolve_for_context handles unknown language."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            resolver = RuntimeResolver(repo_root)

            result = resolver.resolve_for_context(
                context_id=3,
                language_family="brainfuck",  # Unknown language
                root_path="",
            )

            assert result.method == "not_found"

    def test_resolve_detects_package_manager(self) -> None:
        """Resolver detects npm/pnpm/yarn from lockfiles."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)

            # Create pnpm lockfile
            (repo_root / "pnpm-lock.yaml").write_text("lockfileVersion: 5.4")

            resolver = RuntimeResolver(repo_root)
            result = resolver.resolve_for_context(
                context_id=1,
                language_family="javascript",
                root_path="",
            )

            assert result.runtime.package_manager == "pnpm"


# =============================================================================
# Tests for ExecutionContextBuilder
# =============================================================================


class TestExecutionContextBuilder:
    """Tests for ExecutionContextBuilder class."""

    def test_build_creates_execution_context(self) -> None:
        """build() creates RuntimeExecutionContext."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            runtime = ContextRuntime(context_id=1)
            runtime.python_executable = "/usr/bin/python3"

            ctx = ExecutionContextBuilder.build(
                context_root=workspace,
                runtime=runtime,
                language_family="python",
            )

            assert isinstance(ctx, RuntimeExecutionContext)
            assert ctx.context_id == 1
            assert ctx.language_family == "python"
            assert ctx.root_path == workspace

    def test_build_infers_language_from_runtime(self) -> None:
        """build() infers language family from runtime."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            runtime = ContextRuntime(context_id=1)
            runtime.node_executable = "/usr/bin/node"

            ctx = ExecutionContextBuilder.build(
                context_root=workspace,
                runtime=runtime,
                language_family=None,  # Auto-detect
            )

            assert ctx.language_family == "javascript"

    def test_build_infers_python_from_runtime(self) -> None:
        """build() infers python language from python_executable."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            runtime = ContextRuntime(context_id=1)
            runtime.python_executable = "/venv/bin/python"

            ctx = ExecutionContextBuilder.build(
                context_root=workspace,
                runtime=runtime,
            )

            assert ctx.language_family == "python"

    def test_build_infers_go_from_runtime(self) -> None:
        """build() infers go language from go_executable."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            runtime = ContextRuntime(context_id=1)
            runtime.go_executable = "/usr/local/go/bin/go"

            ctx = ExecutionContextBuilder.build(
                context_root=workspace,
                runtime=runtime,
            )

            assert ctx.language_family == "go"

    def test_build_infers_rust_from_runtime(self) -> None:
        """build() infers rust language from cargo_executable."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            runtime = ContextRuntime(context_id=1)
            runtime.cargo_executable = "/home/user/.cargo/bin/cargo"

            ctx = ExecutionContextBuilder.build(
                context_root=workspace,
                runtime=runtime,
            )

            assert ctx.language_family == "rust"

    def test_build_infers_java_from_runtime(self) -> None:
        """build() infers java language from java_executable."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            runtime = ContextRuntime(context_id=1)
            runtime.java_executable = "/usr/bin/java"

            ctx = ExecutionContextBuilder.build(
                context_root=workspace,
                runtime=runtime,
            )

            assert ctx.language_family == "java"

    def test_build_infers_unknown_when_no_runtime(self) -> None:
        """build() returns 'unknown' when no runtime detected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            runtime = ContextRuntime(context_id=1)
            # No executables set

            ctx = ExecutionContextBuilder.build(
                context_root=workspace,
                runtime=runtime,
            )

            assert ctx.language_family == "unknown"

    def test_build_preserves_env_vars(self) -> None:
        """build() preserves runtime env vars."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            runtime = ContextRuntime(context_id=1)
            runtime.set_env_vars({"VIRTUAL_ENV": "/venv", "CUSTOM": "value"})

            ctx = ExecutionContextBuilder.build(
                context_root=workspace,
                runtime=runtime,
                language_family="python",
            )

            assert ctx.env_vars["VIRTUAL_ENV"] == "/venv"
            assert ctx.env_vars["CUSTOM"] == "value"

    def test_build_sets_working_directory(self) -> None:
        """build() sets working_directory to context_root."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            runtime = ContextRuntime(context_id=1)

            ctx = ExecutionContextBuilder.build(
                context_root=workspace,
                runtime=runtime,
            )

            assert ctx.working_directory == workspace


# =============================================================================
# Tests for RuntimeResolutionResult
# =============================================================================


class TestRuntimeResolutionResult:
    """Tests for RuntimeResolutionResult dataclass."""

    def test_default_warnings_empty(self) -> None:
        """warnings defaults to empty list."""
        runtime = ContextRuntime(context_id=1)
        result = RuntimeResolutionResult(
            runtime=runtime,
            method="path_detected",
        )
        assert result.warnings == []

    def test_with_warnings(self) -> None:
        """Result can include warnings."""
        runtime = ContextRuntime(context_id=1)
        result = RuntimeResolutionResult(
            runtime=runtime,
            method="path_detected",
            warnings=["Using system Python"],
        )
        assert "Using system Python" in result.warnings
