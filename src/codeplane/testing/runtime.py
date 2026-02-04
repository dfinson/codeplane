"""Context runtime and execution context models.

This module implements Design A (Capture Runtime at Discovery Time) with elements
of Design D (Unified Execution Context) from the CodePlane architecture.

The key insight is that contexts (per SPEC.md §8.4) are the authority for file
membership, and should also own execution environment information. This ensures:

1. Runtime is captured once at discovery time, not detected per-execution
2. Test/lint targets inherit their execution context from their owning context
3. Deterministic execution: same context → same runtime → same behavior

Architecture:
- ContextRuntime: Captured execution environment (Python executable, Node, etc.)
- ToolConfig: Configuration for a specific tool (pytest, ruff, jest, etc.)
- ExecutionContext: Unified context + runtime + tool config for execution
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from sqlmodel import Field, SQLModel

if TYPE_CHECKING:
    from codeplane.index.models import Context


# =============================================================================
# Database Model: ContextRuntime
# =============================================================================


class ContextRuntime(SQLModel, table=True):
    """Execution environment captured at context discovery time.

    Persisted to SQLite so runtime info survives server restarts.
    Re-resolved when context markers change or on explicit refresh.
    """

    __tablename__ = "context_runtimes"

    id: int | None = Field(default=None, primary_key=True)
    context_id: int = Field(foreign_key="contexts.id", unique=True, index=True)

    # Python runtime
    python_executable: str | None = None  # Full path: /repo/.venv/bin/python
    python_version: str | None = None  # "3.12.1"
    python_venv_path: str | None = None  # /repo/.venv (if detected)

    # JavaScript/TypeScript runtime
    node_executable: str | None = None  # /usr/local/bin/node
    node_version: str | None = None  # "20.10.0"
    package_manager: str | None = None  # "npm" | "pnpm" | "yarn" | "bun"
    package_manager_executable: str | None = None  # Full path to pm

    # Go runtime
    go_executable: str | None = None  # /usr/local/go/bin/go
    go_version: str | None = None  # "1.21.5"
    go_mod_path: str | None = None  # Path to go.mod

    # Rust runtime
    cargo_executable: str | None = None  # /home/user/.cargo/bin/cargo
    rust_version: str | None = None  # "1.75.0"

    # Java/JVM runtime
    java_executable: str | None = None
    java_version: str | None = None
    gradle_executable: str | None = None
    maven_executable: str | None = None

    # .NET runtime
    dotnet_executable: str | None = None
    dotnet_version: str | None = None

    # Ruby runtime
    ruby_executable: str | None = None
    ruby_version: str | None = None
    bundle_executable: str | None = None

    # Generic environment overrides (JSON)
    env_vars_json: str | None = None  # {"VIRTUAL_ENV": "/path", ...}

    # Metadata
    resolved_at: float | None = None  # Unix timestamp
    resolution_method: str | None = None  # "venv_detected", "path_fallback", etc.

    def get_env_vars(self) -> dict[str, str]:
        """Parse env_vars_json to dict."""
        if self.env_vars_json is None:
            return {}
        result: dict[str, str] = json.loads(self.env_vars_json)
        return result

    def set_env_vars(self, env_vars: dict[str, str]) -> None:
        """Set env_vars_json from dict."""
        self.env_vars_json = json.dumps(env_vars) if env_vars else None


# =============================================================================
# Non-Table Models: Tool Configuration
# =============================================================================


@dataclass
class ToolConfig:
    """Configuration for a specific tool within an execution context.

    Examples:
    - pytest: executable="/repo/.venv/bin/python", base_args=["-m", "pytest"]
    - ruff: executable="/repo/.venv/bin/ruff", base_args=["check"]
    - jest: executable="npx", base_args=["jest"]
    """

    tool_id: str  # "python.pytest", "python.ruff", "js.jest"
    executable: str  # Full path or command name
    base_args: list[str] = field(default_factory=list)  # Default arguments
    env_overrides: dict[str, str] = field(default_factory=dict)  # Tool-specific env
    available: bool = True  # False if tool not installed
    version: str | None = None  # Tool version if detected


@dataclass
class RuntimeExecutionContext:
    """Unified context for executing operations (tests, lints, etc.).

    This is the primary interface for execution - it combines:
    - Context identity and boundaries
    - Runtime environment (executables, versions)
    - Tool configurations
    - Execution constraints

    Passed to runner packs and lint tools at execution time.

    Note: Named RuntimeExecutionContext to distinguish from testing.models.ExecutionContext
    which captures command execution results (command, exit_code, stdout, etc.).
    """

    # Identity (from Context)
    context_id: int
    language_family: str
    root_path: Path  # Absolute path

    # Runtime (from ContextRuntime)
    runtime: ContextRuntime

    # Tool configurations (resolved at context load time)
    test_runners: dict[str, ToolConfig] = field(default_factory=dict)  # pack_id -> config
    linters: dict[str, ToolConfig] = field(default_factory=dict)  # tool_id -> config
    formatters: dict[str, ToolConfig] = field(default_factory=dict)  # tool_id -> config

    # Environment (merged from runtime + context-specific)
    env_vars: dict[str, str] = field(default_factory=dict)
    working_directory: Path | None = None

    # Execution constraints
    timeout_sec: int = 300
    memory_limit_mb: int | None = None

    def get_test_runner(self, pack_id: str) -> ToolConfig | None:
        """Get tool config for a test runner pack."""
        return self.test_runners.get(pack_id)

    def get_linter(self, tool_id: str) -> ToolConfig | None:
        """Get tool config for a linter."""
        return self.linters.get(tool_id)

    def build_env(self, tool_config: ToolConfig | None = None) -> dict[str, str]:
        """Build complete environment for execution.

        Merges in order (later wins):
        1. Current process environment
        2. Runtime env_vars
        3. Context env_vars
        4. Tool-specific env_overrides
        """
        import os

        env = dict(os.environ)
        env.update(self.runtime.get_env_vars())
        env.update(self.env_vars)
        if tool_config:
            env.update(tool_config.env_overrides)
        return env


# =============================================================================
# Runtime Resolution
# =============================================================================

RuntimeResolutionMethod = Literal[
    "venv_detected",  # Found .venv or similar
    "poetry_detected",  # Found poetry.lock, using poetry run
    "conda_detected",  # Found conda env
    "nvm_detected",  # Found .nvmrc
    "path_detected",  # Found executable in PATH
    "not_found",  # Could not find runtime
]


@dataclass
class RuntimeResolutionResult:
    """Result of resolving runtime for a context."""

    runtime: ContextRuntime
    method: RuntimeResolutionMethod
    warnings: list[str] = field(default_factory=list)


class RuntimeResolver:
    """Resolves execution runtime for a context.

    Called during context discovery/validation to capture the execution
    environment. Results are persisted to ContextRuntime table.
    """

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root

    @staticmethod
    def resolve(workspace_root: Path) -> ContextRuntime:
        """Convenience method to resolve runtime for a workspace root.

        This is used at execution time when we need a ContextRuntime but
        don't have a persisted context. For the full context-aware resolution,
        use the instance method resolve_for_context().

        Args:
            workspace_root: Absolute path to workspace root

        Returns:
            ContextRuntime with detected executables
        """
        import time

        runtime = ContextRuntime(context_id=0)  # Dummy ID for non-persisted runtime
        runtime.resolved_at = time.time()

        # Resolve all language runtimes (best-effort)
        resolver = RuntimeResolver(workspace_root)
        warnings: list[str] = []

        # Try Python
        method = resolver._resolve_python(workspace_root, runtime, warnings)
        if method != "not_found":
            runtime.resolution_method = method

        # Try JavaScript
        resolver._resolve_javascript(workspace_root, runtime, warnings)

        # Try Go
        resolver._resolve_go(workspace_root, runtime, warnings)

        # Try Rust
        resolver._resolve_rust(workspace_root, runtime, warnings)

        # Try JVM
        resolver._resolve_jvm(workspace_root, runtime, warnings)

        # Try .NET
        resolver._resolve_dotnet(workspace_root, runtime, warnings)

        # Try Ruby
        resolver._resolve_ruby(workspace_root, runtime, warnings)

        return runtime

    def resolve_for_context(
        self, context_id: int, language_family: str, root_path: str
    ) -> RuntimeResolutionResult:
        """Resolve runtime for a context.

        Args:
            context_id: Database ID of the context
            language_family: Language family string (e.g., "python", "javascript")
            root_path: Relative path to context root ("" for repo root)

        Returns:
            RuntimeResolutionResult with populated ContextRuntime
        """
        import time

        runtime = ContextRuntime(context_id=context_id)
        runtime.resolved_at = time.time()
        warnings: list[str] = []
        method: RuntimeResolutionMethod = "not_found"

        context_root = self.repo_root / root_path if root_path else self.repo_root

        # Dispatch to language-specific resolver
        if language_family == "python":
            method = self._resolve_python(context_root, runtime, warnings)
        elif language_family == "javascript":
            method = self._resolve_javascript(context_root, runtime, warnings)
        elif language_family == "go":
            method = self._resolve_go(context_root, runtime, warnings)
        elif language_family == "rust":
            method = self._resolve_rust(context_root, runtime, warnings)
        elif language_family in ("java", "kotlin", "scala", "groovy"):
            method = self._resolve_jvm(context_root, runtime, warnings)
        elif language_family in ("csharp", "fsharp", "vbnet"):
            method = self._resolve_dotnet(context_root, runtime, warnings)
        elif language_family == "ruby":
            method = self._resolve_ruby(context_root, runtime, warnings)
        else:
            # No specific runtime for this language
            method = "not_found"

        runtime.resolution_method = method
        return RuntimeResolutionResult(runtime=runtime, method=method, warnings=warnings)

    def _resolve_python(
        self, context_root: Path, runtime: ContextRuntime, warnings: list[str]
    ) -> RuntimeResolutionMethod:
        """Resolve Python runtime.

        Resolution order:
        1. Context-local venv (.venv, venv, .env, env)
        2. Workspace-level venv (check parent directories up to repo root)
        3. Poetry environment (if poetry.lock exists)
        4. System Python via PATH
        """
        # Check for local venv
        venv_names = [".venv", "venv", ".env", "env"]
        for venv_name in venv_names:
            venv_path = context_root / venv_name
            python_exe = self._find_python_in_venv(venv_path)
            if python_exe:
                runtime.python_executable = str(python_exe)
                runtime.python_venv_path = str(venv_path)
                runtime.python_version = self._get_python_version(python_exe)
                self._set_python_env_vars(runtime, venv_path)
                return "venv_detected"

        # Check parent directories up to repo root
        current = context_root
        while current != self.repo_root and current != current.parent:
            current = current.parent
            for venv_name in venv_names:
                venv_path = current / venv_name
                python_exe = self._find_python_in_venv(venv_path)
                if python_exe:
                    runtime.python_executable = str(python_exe)
                    runtime.python_venv_path = str(venv_path)
                    runtime.python_version = self._get_python_version(python_exe)
                    self._set_python_env_vars(runtime, venv_path)
                    return "venv_detected"

        # Check repo root explicitly
        for venv_name in venv_names:
            venv_path = self.repo_root / venv_name
            python_exe = self._find_python_in_venv(venv_path)
            if python_exe:
                runtime.python_executable = str(python_exe)
                runtime.python_venv_path = str(venv_path)
                runtime.python_version = self._get_python_version(python_exe)
                self._set_python_env_vars(runtime, venv_path)
                return "venv_detected"

        # Check for Poetry
        if (context_root / "poetry.lock").exists() or (self.repo_root / "poetry.lock").exists():
            poetry_exe = shutil.which("poetry")
            if poetry_exe:
                # Get Python from poetry env
                poetry_python = self._get_poetry_python(context_root)
                if poetry_python:
                    runtime.python_executable = poetry_python
                    runtime.python_version = self._get_python_version(Path(poetry_python))
                    return "poetry_detected"

        # Fallback to system Python
        system_python = shutil.which("python3") or shutil.which("python")
        if system_python:
            runtime.python_executable = system_python
            runtime.python_version = self._get_python_version(Path(system_python))
            warnings.append(
                f"Using system Python: {system_python}. Consider creating a virtual environment."
            )
            return "path_detected"

        warnings.append("No Python executable found")
        return "not_found"

    def _find_python_in_venv(self, venv_path: Path) -> Path | None:
        """Find Python executable in a venv directory."""
        if not venv_path.is_dir():
            return None

        # Verify it's a venv by checking for pyvenv.cfg or activate scripts
        has_pyvenv_cfg = (venv_path / "pyvenv.cfg").exists()
        has_unix_activate = (venv_path / "bin" / "activate").exists()
        has_win_activate = (venv_path / "Scripts" / "activate").exists()
        if not (has_pyvenv_cfg or has_unix_activate or has_win_activate):
            return None

        # Find Python executable
        # Unix
        unix_python = venv_path / "bin" / "python"
        if unix_python.exists():
            return unix_python

        # Windows
        win_python = venv_path / "Scripts" / "python.exe"
        if win_python.exists():
            return win_python

        return None

    def _get_python_version(self, python_exe: Path) -> str | None:
        """Get Python version from executable."""
        try:
            result = subprocess.run(
                [str(python_exe), "--version"],
                capture_output=True,
                timeout=5,
                text=True,
            )
            if result.returncode == 0:
                # "Python 3.12.1" -> "3.12.1"
                return result.stdout.strip().split()[-1]
        except Exception:
            pass
        return None

    def _get_poetry_python(self, context_root: Path) -> str | None:
        """Get Python executable from Poetry environment."""
        try:
            result = subprocess.run(
                ["poetry", "env", "info", "-e"],
                capture_output=True,
                timeout=10,
                text=True,
                cwd=context_root,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return None

    def _set_python_env_vars(self, runtime: ContextRuntime, venv_path: Path) -> None:
        """Set Python-specific environment variables."""
        env_vars = runtime.get_env_vars()
        env_vars["VIRTUAL_ENV"] = str(venv_path)
        # Prepend venv bin to PATH hint (actual PATH manipulation happens at execution)
        bin_dir = venv_path / "bin" if (venv_path / "bin").exists() else venv_path / "Scripts"
        env_vars["_VENV_BIN"] = str(bin_dir)
        runtime.set_env_vars(env_vars)

    def _resolve_javascript(
        self, context_root: Path, runtime: ContextRuntime, warnings: list[str]
    ) -> RuntimeResolutionMethod:
        """Resolve JavaScript/Node runtime."""
        # Detect package manager
        pm, pm_exe = self._detect_package_manager(context_root)
        runtime.package_manager = pm
        runtime.package_manager_executable = pm_exe

        # Find Node
        # Check for .nvmrc
        nvmrc_path = context_root / ".nvmrc"
        has_nvmrc = nvmrc_path.exists()
        if not has_nvmrc:
            nvmrc_path = self.repo_root / ".nvmrc"
            has_nvmrc = nvmrc_path.exists()

        node_exe = shutil.which("node")
        if node_exe:
            runtime.node_executable = node_exe
            runtime.node_version = self._get_node_version(node_exe)
            return "nvm_detected" if has_nvmrc else "path_detected"

        warnings.append("No Node.js executable found")
        return "not_found"

    def _detect_package_manager(self, context_root: Path) -> tuple[str, str | None]:
        """Detect JavaScript package manager."""
        # Check lockfiles in order of preference
        checks = [
            ("pnpm-lock.yaml", "pnpm"),
            ("yarn.lock", "yarn"),
            ("bun.lockb", "bun"),
            ("package-lock.json", "npm"),
        ]

        for lockfile, pm in checks:
            if (context_root / lockfile).exists() or (self.repo_root / lockfile).exists():
                exe = shutil.which(pm)
                return (pm, exe)

        # Default to npm
        npm_exe = shutil.which("npm")
        return ("npm", npm_exe)

    def _get_node_version(self, node_exe: str) -> str | None:
        """Get Node.js version."""
        try:
            result = subprocess.run(
                [node_exe, "--version"],
                capture_output=True,
                timeout=5,
                text=True,
            )
            if result.returncode == 0:
                # "v20.10.0" -> "20.10.0"
                return result.stdout.strip().lstrip("v")
        except Exception:
            pass
        return None

    def _resolve_go(
        self, context_root: Path, runtime: ContextRuntime, warnings: list[str]
    ) -> RuntimeResolutionMethod:
        """Resolve Go runtime."""
        go_exe = shutil.which("go")
        if go_exe:
            runtime.go_executable = go_exe
            runtime.go_version = self._get_go_version(go_exe)

            # Find go.mod
            go_mod = context_root / "go.mod"
            if not go_mod.exists():
                go_mod = self.repo_root / "go.mod"
            if go_mod.exists():
                runtime.go_mod_path = str(go_mod)

            return "path_detected"

        warnings.append("No Go executable found")
        return "not_found"

    def _get_go_version(self, go_exe: str) -> str | None:
        """Get Go version."""
        try:
            result = subprocess.run(
                [go_exe, "version"],
                capture_output=True,
                timeout=5,
                text=True,
            )
            if result.returncode == 0:
                # "go version go1.21.5 linux/amd64" -> "1.21.5"
                parts = result.stdout.split()
                if len(parts) >= 3:
                    return parts[2].lstrip("go")
        except Exception:
            pass
        return None

    def _resolve_rust(
        self, _context_root: Path, runtime: ContextRuntime, warnings: list[str]
    ) -> RuntimeResolutionMethod:
        """Resolve Rust runtime."""
        cargo_exe = shutil.which("cargo")
        if cargo_exe:
            runtime.cargo_executable = cargo_exe
            runtime.rust_version = self._get_rust_version(cargo_exe)
            return "path_detected"

        warnings.append("No Cargo executable found")
        return "not_found"

    def _get_rust_version(self, cargo_exe: str) -> str | None:
        """Get Rust version via cargo."""
        try:
            result = subprocess.run(
                [cargo_exe, "--version"],
                capture_output=True,
                timeout=5,
                text=True,
            )
            if result.returncode == 0:
                # "cargo 1.75.0 (...)" -> "1.75.0"
                parts = result.stdout.split()
                if len(parts) >= 2:
                    return parts[1]
        except Exception:
            pass
        return None

    def _resolve_jvm(
        self, context_root: Path, runtime: ContextRuntime, warnings: list[str]
    ) -> RuntimeResolutionMethod:
        """Resolve JVM (Java/Kotlin/Scala) runtime."""
        java_exe = shutil.which("java")
        if java_exe:
            runtime.java_executable = java_exe
            runtime.java_version = self._get_java_version(java_exe)

            # Check for Gradle wrapper
            gradlew = context_root / "gradlew"
            if not gradlew.exists():
                gradlew = self.repo_root / "gradlew"
            if gradlew.exists():
                runtime.gradle_executable = str(gradlew)
            else:
                gradle = shutil.which("gradle")
                if gradle:
                    runtime.gradle_executable = gradle

            # Check for Maven wrapper
            mvnw = context_root / "mvnw"
            if not mvnw.exists():
                mvnw = self.repo_root / "mvnw"
            if mvnw.exists():
                runtime.maven_executable = str(mvnw)
            else:
                mvn = shutil.which("mvn")
                if mvn:
                    runtime.maven_executable = mvn

            return "path_detected"

        warnings.append("No Java executable found")
        return "not_found"

    def _get_java_version(self, java_exe: str) -> str | None:
        """Get Java version."""
        try:
            result = subprocess.run(
                [java_exe, "-version"],
                capture_output=True,
                timeout=5,
                text=True,
            )
            # Java version is on stderr
            output = result.stderr or result.stdout
            if output:
                # Parse: 'openjdk version "17.0.1"' or 'java version "1.8.0_..."'
                for line in output.split("\n"):
                    if "version" in line:
                        # Extract version between quotes
                        import re

                        match = re.search(r'"([^"]+)"', line)
                        if match:
                            return match.group(1)
        except Exception:
            pass
        return None

    def _resolve_dotnet(
        self, _context_root: Path, runtime: ContextRuntime, warnings: list[str]
    ) -> RuntimeResolutionMethod:
        """Resolve .NET runtime."""
        dotnet_exe = shutil.which("dotnet")
        if dotnet_exe:
            runtime.dotnet_executable = dotnet_exe
            runtime.dotnet_version = self._get_dotnet_version(dotnet_exe)
            return "path_detected"

        warnings.append("No .NET executable found")
        return "not_found"

    def _get_dotnet_version(self, dotnet_exe: str) -> str | None:
        """Get .NET version."""
        try:
            result = subprocess.run(
                [dotnet_exe, "--version"],
                capture_output=True,
                timeout=5,
                text=True,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return None

    def _resolve_ruby(
        self, _context_root: Path, runtime: ContextRuntime, warnings: list[str]
    ) -> RuntimeResolutionMethod:
        """Resolve Ruby runtime."""
        ruby_exe = shutil.which("ruby")
        if ruby_exe:
            runtime.ruby_executable = ruby_exe
            runtime.ruby_version = self._get_ruby_version(ruby_exe)

            bundle_exe = shutil.which("bundle")
            if bundle_exe:
                runtime.bundle_executable = bundle_exe

            return "path_detected"

        warnings.append("No Ruby executable found")
        return "not_found"

    def _get_ruby_version(self, ruby_exe: str) -> str | None:
        """Get Ruby version."""
        try:
            result = subprocess.run(
                [ruby_exe, "--version"],
                capture_output=True,
                timeout=5,
                text=True,
            )
            if result.returncode == 0:
                # "ruby 3.2.0 (2022-12-25...) [...]" -> "3.2.0"
                parts = result.stdout.split()
                if len(parts) >= 2:
                    return parts[1]
        except Exception:
            pass
        return None


# =============================================================================
# Execution Context Builder
# =============================================================================


class ExecutionContextBuilder:
    """Builds RuntimeExecutionContext from Context and ContextRuntime.

    This is the bridge between the index layer (Context, ContextRuntime)
    and the execution layer (RuntimeExecutionContext, ToolConfig).
    """

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root

    @staticmethod
    def build(
        context_root: Path,
        runtime: ContextRuntime,
        language_family: str | None = None,
    ) -> RuntimeExecutionContext:
        """Build RuntimeExecutionContext from workspace root and runtime.

        This is a convenience method for execution-time context building
        when we don't have a persisted Context object.

        Args:
            context_root: Absolute path to workspace/context root
            runtime: ContextRuntime with detected executables
            language_family: Optional language family (auto-detected if not provided)

        Returns:
            RuntimeExecutionContext ready for execution
        """
        # Infer language family from runtime if not provided
        if language_family is None:
            if runtime.python_executable:
                language_family = "python"
            elif runtime.node_executable:
                language_family = "javascript"
            elif runtime.go_executable:
                language_family = "go"
            elif runtime.cargo_executable:
                language_family = "rust"
            elif runtime.java_executable:
                language_family = "java"
            elif runtime.dotnet_executable:
                language_family = "csharp"
            elif runtime.ruby_executable:
                language_family = "ruby"
            else:
                language_family = "unknown"

        exec_ctx = RuntimeExecutionContext(
            context_id=runtime.context_id,
            language_family=language_family,
            root_path=context_root,
            runtime=runtime,
            env_vars=runtime.get_env_vars(),
            working_directory=context_root,
        )

        # Build tool configs
        builder = ExecutionContextBuilder(context_root)
        builder._build_tool_configs_for_runtime(exec_ctx, language_family, runtime)

        return exec_ctx

    def build_from_context(
        self, context: Context, runtime: ContextRuntime
    ) -> RuntimeExecutionContext:
        """Build RuntimeExecutionContext from Context and ContextRuntime."""
        root_path = self.repo_root / context.root_path if context.root_path else self.repo_root

        exec_ctx = RuntimeExecutionContext(
            context_id=context.id or 0,
            language_family=context.language_family,
            root_path=root_path,
            runtime=runtime,
            env_vars=runtime.get_env_vars(),
            working_directory=root_path,
        )

        # Build tool configs based on language
        self._build_tool_configs_for_runtime(exec_ctx, context.language_family, runtime)

        return exec_ctx

    def _build_tool_configs_for_runtime(
        self, exec_ctx: RuntimeExecutionContext, language_family: str, runtime: ContextRuntime
    ) -> None:
        """Populate tool configurations for the execution context."""
        if language_family == "python":
            self._build_python_tools(exec_ctx, runtime)
        elif language_family == "javascript":
            self._build_javascript_tools(exec_ctx, runtime)
        elif language_family == "go":
            self._build_go_tools(exec_ctx, runtime)
        elif language_family == "rust":
            self._build_rust_tools(exec_ctx, runtime)
        elif language_family in ("java", "kotlin", "scala", "groovy"):
            self._build_jvm_tools(exec_ctx, runtime)
        elif language_family in ("csharp", "fsharp", "vbnet"):
            self._build_dotnet_tools(exec_ctx, runtime)
        elif language_family == "ruby":
            self._build_ruby_tools(exec_ctx, runtime)

    def _build_tool_configs(
        self, exec_ctx: RuntimeExecutionContext, context: Context, runtime: ContextRuntime
    ) -> None:
        """Populate tool configurations for the execution context (deprecated - use _build_tool_configs_for_runtime)."""
        self._build_tool_configs_for_runtime(exec_ctx, context.language_family, runtime)

    def _build_python_tools(
        self, exec_ctx: RuntimeExecutionContext, runtime: ContextRuntime
    ) -> None:
        """Build Python tool configs."""
        python_exe = runtime.python_executable or "python"

        # pytest - invoke as module to ensure correct interpreter
        exec_ctx.test_runners["python.pytest"] = ToolConfig(
            tool_id="python.pytest",
            executable=python_exe,
            base_args=["-m", "pytest"],
            available=self._check_python_package(python_exe, "pytest"),
        )

        # unittest
        exec_ctx.test_runners["python.unittest"] = ToolConfig(
            tool_id="python.unittest",
            executable=python_exe,
            base_args=["-m", "unittest"],
            available=True,  # Built-in
        )

        # ruff linter
        ruff_available = self._check_python_package(python_exe, "ruff")
        exec_ctx.linters["python.ruff"] = ToolConfig(
            tool_id="python.ruff",
            executable=python_exe,
            base_args=["-m", "ruff", "check"],
            available=ruff_available,
        )

        # mypy type checker
        mypy_available = self._check_python_package(python_exe, "mypy")
        exec_ctx.linters["python.mypy"] = ToolConfig(
            tool_id="python.mypy",
            executable=python_exe,
            base_args=["-m", "mypy"],
            available=mypy_available,
        )

        # black formatter
        black_available = self._check_python_package(python_exe, "black")
        exec_ctx.formatters["python.black"] = ToolConfig(
            tool_id="python.black",
            executable=python_exe,
            base_args=["-m", "black"],
            available=black_available,
        )

    def _check_python_package(self, python_exe: str, package: str) -> bool:
        """Check if a Python package is installed."""
        try:
            result = subprocess.run(
                [python_exe, "-c", f"import {package}"],
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _build_javascript_tools(
        self, exec_ctx: RuntimeExecutionContext, runtime: ContextRuntime
    ) -> None:
        """Build JavaScript tool configs."""
        pm = runtime.package_manager or "npm"
        pm_exe = runtime.package_manager_executable or pm

        # Determine how to run packages
        if pm == "pnpm":
            run_prefix = [pm_exe, "exec"]
        elif pm == "yarn":
            run_prefix = [pm_exe]
        elif pm == "bun":
            run_prefix = [pm_exe, "run"]
        else:  # npm
            run_prefix = ["npx"]

        # jest
        exec_ctx.test_runners["js.jest"] = ToolConfig(
            tool_id="js.jest",
            executable=run_prefix[0],
            base_args=run_prefix[1:] + ["jest"] if len(run_prefix) > 1 else ["jest"],
            available=True,  # Assume available if package.json exists
        )

        # vitest
        exec_ctx.test_runners["js.vitest"] = ToolConfig(
            tool_id="js.vitest",
            executable=run_prefix[0],
            base_args=run_prefix[1:] + ["vitest", "run"]
            if len(run_prefix) > 1
            else ["vitest", "run"],
            available=True,
        )

        # eslint
        exec_ctx.linters["js.eslint"] = ToolConfig(
            tool_id="js.eslint",
            executable=run_prefix[0],
            base_args=run_prefix[1:] + ["eslint"] if len(run_prefix) > 1 else ["eslint"],
            available=True,
        )

        # prettier
        exec_ctx.formatters["js.prettier"] = ToolConfig(
            tool_id="js.prettier",
            executable=run_prefix[0],
            base_args=run_prefix[1:] + ["prettier"] if len(run_prefix) > 1 else ["prettier"],
            available=True,
        )

    def _build_go_tools(self, exec_ctx: RuntimeExecutionContext, runtime: ContextRuntime) -> None:
        """Build Go tool configs."""
        go_exe = runtime.go_executable or "go"

        # go test
        exec_ctx.test_runners["go.gotest"] = ToolConfig(
            tool_id="go.gotest",
            executable=go_exe,
            base_args=["test", "-json"],
            available=bool(runtime.go_executable),
        )

        # golangci-lint
        golint = shutil.which("golangci-lint")
        exec_ctx.linters["go.golangci-lint"] = ToolConfig(
            tool_id="go.golangci-lint",
            executable=golint or "golangci-lint",
            base_args=["run"],
            available=bool(golint),
        )

        # gofmt
        exec_ctx.formatters["go.gofmt"] = ToolConfig(
            tool_id="go.gofmt",
            executable=go_exe,
            base_args=["fmt"],
            available=bool(runtime.go_executable),
        )

    def _build_rust_tools(self, exec_ctx: RuntimeExecutionContext, runtime: ContextRuntime) -> None:
        """Build Rust tool configs."""
        cargo_exe = runtime.cargo_executable or "cargo"

        # cargo test
        exec_ctx.test_runners["rust.cargo_test"] = ToolConfig(
            tool_id="rust.cargo_test",
            executable=cargo_exe,
            base_args=["test"],
            available=bool(runtime.cargo_executable),
        )

        # cargo-nextest (if available)
        nextest = shutil.which("cargo-nextest")
        exec_ctx.test_runners["rust.nextest"] = ToolConfig(
            tool_id="rust.nextest",
            executable=cargo_exe,
            base_args=["nextest", "run"],
            available=bool(nextest),
        )

        # clippy
        exec_ctx.linters["rust.clippy"] = ToolConfig(
            tool_id="rust.clippy",
            executable=cargo_exe,
            base_args=["clippy"],
            available=bool(runtime.cargo_executable),
        )

        # rustfmt
        exec_ctx.formatters["rust.rustfmt"] = ToolConfig(
            tool_id="rust.rustfmt",
            executable=cargo_exe,
            base_args=["fmt"],
            available=bool(runtime.cargo_executable),
        )

    def _build_jvm_tools(self, exec_ctx: RuntimeExecutionContext, runtime: ContextRuntime) -> None:
        """Build JVM tool configs."""
        # Maven
        if runtime.maven_executable:
            exec_ctx.test_runners["java.maven"] = ToolConfig(
                tool_id="java.maven",
                executable=runtime.maven_executable,
                base_args=["test"],
                available=True,
            )

        # Gradle
        if runtime.gradle_executable:
            exec_ctx.test_runners["java.gradle"] = ToolConfig(
                tool_id="java.gradle",
                executable=runtime.gradle_executable,
                base_args=["test"],
                available=True,
            )

    def _build_dotnet_tools(
        self, exec_ctx: RuntimeExecutionContext, runtime: ContextRuntime
    ) -> None:
        """Build .NET tool configs."""
        dotnet_exe = runtime.dotnet_executable or "dotnet"

        exec_ctx.test_runners["csharp.dotnet"] = ToolConfig(
            tool_id="csharp.dotnet",
            executable=dotnet_exe,
            base_args=["test"],
            available=bool(runtime.dotnet_executable),
        )

    def _build_ruby_tools(self, exec_ctx: RuntimeExecutionContext, runtime: ContextRuntime) -> None:
        """Build Ruby tool configs."""
        bundle_exe = runtime.bundle_executable

        if bundle_exe:
            exec_ctx.test_runners["ruby.rspec"] = ToolConfig(
                tool_id="ruby.rspec",
                executable=bundle_exe,
                base_args=["exec", "rspec"],
                available=True,
            )
        else:
            rspec = shutil.which("rspec")
            exec_ctx.test_runners["ruby.rspec"] = ToolConfig(
                tool_id="ruby.rspec",
                executable=rspec or "rspec",
                base_args=[],
                available=bool(rspec),
            )

        # rubocop
        rubocop = shutil.which("rubocop")
        exec_ctx.linters["ruby.rubocop"] = ToolConfig(
            tool_id="ruby.rubocop",
            executable=rubocop or "rubocop",
            base_args=[],
            available=bool(rubocop),
        )
