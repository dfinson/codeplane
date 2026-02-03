"""Safe execution context for running tests on untrusted repositories.

This module provides defensive environment isolation to protect CodePlane from
misconfigurations in target repositories that could cause:
- Data corruption (e.g., SQLite coverage DB race conditions)
- Hangs (e.g., interactive prompts, watchman file watching)
- False failures (e.g., coverage threshold enforcement)
- Resource leaks (e.g., orphaned processes)

Each language has specific defensive strategies documented inline.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass
class SafeExecutionConfig:
    """Configuration for safe execution context."""

    artifact_dir: Path
    workspace_root: Path
    timeout_sec: int = 300
    # Whether to strip coverage flags from commands (when we inject our own)
    strip_coverage_flags: bool = False
    # Unique run identifier for isolation
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])


LanguageFamily = Literal[
    "python",
    "javascript",
    "typescript",
    "go",
    "rust",
    "java",
    "csharp",
    "cpp",
    "ruby",
    "php",
    "elixir",
    "dart",
    "swift",
    "kotlin",
    "scala",
    "unknown",
]


def _get_language_family(pack_id: str) -> LanguageFamily:
    """Map pack_id to language family for strategy selection."""
    mappings: dict[str, LanguageFamily] = {
        "python.pytest": "python",
        "python.unittest": "python",
        "python.nose": "python",
        "js.jest": "javascript",
        "js.vitest": "javascript",
        "js.mocha": "javascript",
        "ts.jest": "typescript",
        "ts.vitest": "typescript",
        "go.gotest": "go",
        "rust.nextest": "rust",
        "rust.cargo_test": "rust",
        "java.maven": "java",
        "java.gradle": "java",
        "kotlin.gradle": "kotlin",
        "scala.sbt": "scala",
        "csharp.dotnet": "csharp",
        "cpp.ctest": "cpp",
        "cpp.gtest": "cpp",
        "cpp.catch2": "cpp",
        "ruby.rspec": "ruby",
        "ruby.minitest": "ruby",
        "php.phpunit": "php",
        "php.pest": "php",
        "elixir.exunit": "elixir",
        "dart.darttest": "dart",
        "dart.fluttertest": "dart",
        "swift.xctest": "swift",
    }
    return mappings.get(pack_id, "unknown")


class SafeExecutionContext:
    """Provides defensive environment isolation for test execution.

    Protects against misconfigurations in target repositories by:
    1. Setting environment variables that override project configs
    2. Sanitizing commands to remove dangerous flags
    3. Isolating coverage/artifact files to prevent corruption
    4. Enforcing non-interactive execution modes
    """

    def __init__(self, config: SafeExecutionConfig) -> None:
        self._config = config
        self._temp_dirs: list[Path] = []

    @property
    def config(self) -> SafeExecutionConfig:
        return self._config

    def prepare_environment(
        self,
        pack_id: str,
        *,
        base_env: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """Build safe environment variables for test execution.

        Args:
            pack_id: Runner pack identifier (e.g., 'python.pytest')
            base_env: Base environment to extend (defaults to os.environ)

        Returns:
            Environment dict with defensive overrides applied
        """
        env = dict(base_env) if base_env is not None else dict(os.environ)

        # Apply universal protections first
        env.update(self._universal_env_overrides())

        # Apply language-specific protections
        lang = _get_language_family(pack_id)
        strategy = self._get_env_strategy(lang)
        env.update(strategy())

        return env

    def sanitize_command(
        self,
        cmd: list[str],
        pack_id: str,
    ) -> list[str]:
        """Remove or override dangerous flags from test commands.

        Args:
            cmd: Original command list
            pack_id: Runner pack identifier

        Returns:
            Sanitized command list
        """
        if not cmd:
            return cmd

        lang = _get_language_family(pack_id)
        strategy = self._get_cmd_strategy(lang)
        return strategy(list(cmd))  # Copy to avoid mutation

    def cleanup(self) -> None:
        """Clean up any temporary resources created during execution."""
        # Temp dirs are managed by tempfile module, but we track them
        # for explicit cleanup if needed
        self._temp_dirs.clear()

    # =========================================================================
    # Universal Environment Overrides
    # =========================================================================

    def _universal_env_overrides(self) -> dict[str, str]:
        """Environment variables that apply to all languages.

        These enforce non-interactive mode and CI-like behavior.
        """
        return {
            # Signal CI environment - most tools respect this
            "CI": "true",
            "CONTINUOUS_INTEGRATION": "true",
            # Prevent interactive prompts
            "NONINTERACTIVE": "1",
            "DEBIAN_FRONTEND": "noninteractive",
            # Disable color output for cleaner parsing
            "NO_COLOR": "1",
            "FORCE_COLOR": "0",
            # Prevent git prompts
            "GIT_TERMINAL_PROMPT": "0",
            # Prevent SSH prompts
            "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o StrictHostKeyChecking=no",
            # Disable telemetry for various tools
            "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
            "GATSBY_TELEMETRY_DISABLED": "1",
            "NEXT_TELEMETRY_DISABLED": "1",
            "NUXT_TELEMETRY_DISABLED": "1",
            "HOMEBREW_NO_ANALYTICS": "1",
            # Disable auto-update prompts
            "HOMEBREW_NO_AUTO_UPDATE": "1",
            "DISABLE_OPENCOLLECTIVE": "1",
            # Prevent browser opening
            "BROWSER": "none",
            # Codeplane marker
            "CODEPLANE_EXECUTION": "1",
            "CODEPLANE_RUN_ID": self._config.run_id,
        }

    # =========================================================================
    # Language-Specific Environment Strategies
    # =========================================================================

    def _get_env_strategy(self, lang: LanguageFamily) -> Callable[[], dict[str, str]]:
        """Get environment strategy for language family."""
        strategies: dict[LanguageFamily, Callable[[], dict[str, str]]] = {
            "python": self._python_env,
            "javascript": self._javascript_env,
            "typescript": self._javascript_env,  # Same as JS
            "go": self._go_env,
            "rust": self._rust_env,
            "java": self._java_env,
            "kotlin": self._java_env,  # JVM-based
            "scala": self._java_env,  # JVM-based
            "csharp": self._csharp_env,
            "cpp": self._cpp_env,
            "ruby": self._ruby_env,
            "php": self._php_env,
            "elixir": self._elixir_env,
            "dart": self._dart_env,
            "swift": self._swift_env,
            "unknown": lambda: {},
        }
        return strategies.get(lang, lambda: {})

    def _python_env(self) -> dict[str, str]:
        """Python-specific environment overrides.

        Key protections:
        - COVERAGE_FILE: Prevents SQLite corruption from parallel pytest-cov runs
          by forcing each run to use a unique coverage file path. This overrides
          any pyproject.toml, setup.cfg, or .coveragerc settings.
        - PYTHONDONTWRITEBYTECODE: Prevents __pycache__ pollution
        - PYTEST_CURRENT_TEST: Cleared to avoid test pollution
        """
        # Create isolated coverage file path
        coverage_dir = self._config.artifact_dir / "coverage"
        coverage_dir.mkdir(parents=True, exist_ok=True)
        coverage_file = coverage_dir / f".coverage.{self._config.run_id}"

        return {
            # CRITICAL: Isolate coverage database to prevent SQLite corruption
            # This overrides any project-level coverage configuration
            "COVERAGE_FILE": str(coverage_file),
            # Force coverage to use parallel mode even if project doesn't set it
            "COVERAGE_PROCESS_START": "",  # Clear any subprocess coverage
            # Prevent bytecode pollution
            "PYTHONDONTWRITEBYTECODE": "1",
            # Disable hash randomization for reproducible test order
            "PYTHONHASHSEED": "0",
            # Clear potentially polluting variables
            "PYTEST_CURRENT_TEST": "",
            # Ensure UTF-8 encoding
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            # Disable warnings that might clutter output
            "PYTHONWARNINGS": "ignore::DeprecationWarning",
            # Prevent pip from prompting
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            # Prevent virtualenv prompts
            "VIRTUAL_ENV_DISABLE_PROMPT": "1",
            # Pytest specific
            "PYTEST_ADDOPTS": "--tb=short -q",  # Override verbose project settings
        }

    def _javascript_env(self) -> dict[str, str]:
        """JavaScript/TypeScript environment overrides.

        Key protections:
        - Disables watchman (file watching) which can hang in CI
        - Forces exit after tests (prevents Jest hanging)
        - Disables workers in some scenarios for stability
        """
        # Create isolated coverage directory
        coverage_dir = self._config.artifact_dir / "coverage"
        coverage_dir.mkdir(parents=True, exist_ok=True)

        return {
            # Jest specific
            "JEST_WORKER_ID": "1",  # Consistent worker ID
            # Disable watchman to prevent file watching hangs
            "WATCHMAN_SOCK": "/dev/null",
            # Node.js options
            "NODE_ENV": "test",
            "NODE_OPTIONS": "--max-old-space-size=4096",  # Prevent OOM
            # Prevent npm/yarn prompts
            "npm_config_yes": "true",
            "YARN_ENABLE_IMMUTABLE_INSTALLS": "false",
            # Disable update checks
            "NO_UPDATE_NOTIFIER": "1",
            "NPM_CONFIG_UPDATE_NOTIFIER": "false",
            # Vitest specific
            "VITEST": "true",
            # Coverage isolation
            "COVERAGE_DIR": str(coverage_dir),
            # Disable interactive mode
            "npm_config_progress": "false",
            # Prevent Nx/Turborepo prompts
            "NX_DAEMON": "false",
            "TURBO_TELEMETRY_DISABLED": "1",
        }

    def _go_env(self) -> dict[str, str]:
        """Go environment overrides.

        Key protections:
        - Isolates coverage profile to prevent overwrites
        - Disables cgo for faster, more portable builds (optional)
        - Ensures module mode
        """
        coverage_dir = self._config.artifact_dir / "coverage"
        coverage_dir.mkdir(parents=True, exist_ok=True)

        return {
            # Ensure module mode
            "GO111MODULE": "on",
            # Isolated coverage output
            "GOCOVERDIR": str(coverage_dir),
            # Disable cgo for more portable test runs (can be overridden)
            # "CGO_ENABLED": "0",  # Commented: may break tests that need cgo
            # Reduce test flakiness from timing
            "GOTESTFLAGS": "-count=1",  # Disable test caching
            # Ensure consistent GOPATH behavior
            "GOFLAGS": "-mod=readonly",
            # Prevent prompts
            "GOTELEMETRY": "off",
        }

    def _rust_env(self) -> dict[str, str]:
        """Rust environment overrides.

        Key protections:
        - Isolates coverage profile for llvm-cov
        - Controls cargo behavior
        """
        coverage_dir = self._config.artifact_dir / "coverage"
        coverage_dir.mkdir(parents=True, exist_ok=True)

        return {
            # Coverage isolation for llvm-cov
            "LLVM_PROFILE_FILE": str(coverage_dir / f"{self._config.run_id}-%p-%m.profraw"),
            # Cargo settings
            "CARGO_TERM_COLOR": "never",
            "CARGO_INCREMENTAL": "0",  # Reproducible builds
            # Prevent rustup prompts
            "RUSTUP_TOOLCHAIN": "stable",  # Use stable by default
            # Disable cargo update checks
            "CARGO_NET_OFFLINE": "false",  # Allow network but...
            "CARGO_HTTP_CHECK_REVOKE": "false",  # ...skip cert checks for speed
        }

    def _java_env(self) -> dict[str, str]:
        """Java/Kotlin/Scala environment overrides.

        Key protections:
        - Disables Gradle daemon (can hang)
        - Prevents Maven interactive mode
        - Isolates JaCoCo output
        """
        coverage_dir = self._config.artifact_dir / "coverage"
        coverage_dir.mkdir(parents=True, exist_ok=True)

        return {
            # Gradle settings - disable daemon to prevent orphan processes
            "GRADLE_OPTS": "-Dorg.gradle.daemon=false -Dorg.gradle.parallel=true",
            # Maven settings - batch mode, no prompts
            "MAVEN_OPTS": "-Djansi.force=false -Dstyle.color=never",
            "MAVEN_BATCH_MODE": "true",
            # JaCoCo coverage output directory
            "JACOCO_DESTFILE": str(coverage_dir / "jacoco.exec"),
            # Disable sbt prompts (Scala)
            "SBT_OPTS": "-Dsbt.ci=true -Dsbt.color=false",
            # Prevent Java auto-update prompts
            "_JAVA_OPTIONS": "-Djava.awt.headless=true",
            # Kotlin specific
            "KOTLIN_DAEMON_ENABLED": "false",
        }

    def _csharp_env(self) -> dict[str, str]:
        """C#/.NET environment overrides.

        Key protections:
        - Disables telemetry
        - Prevents nuget prompts
        - Isolates coverage output
        """
        coverage_dir = self._config.artifact_dir / "coverage"
        coverage_dir.mkdir(parents=True, exist_ok=True)

        return {
            # .NET CLI settings
            "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
            "DOTNET_NOLOGO": "1",
            "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1",
            # Nuget settings - no prompts
            "NUGET_XMLDOC_MODE": "skip",
            # Coverage output
            "COVERLET_OUTPUT": str(coverage_dir) + "/",
            "COVERLET_OUTPUT_FORMAT": "cobertura",
            # Prevent interactive restore
            "DOTNET_INTERACTIVE": "false",
            # MSBuild settings
            "MSBUILDDISABLENODEREUSE": "1",
        }

    def _cpp_env(self) -> dict[str, str]:
        """C/C++ environment overrides.

        Key protections:
        - Isolates coverage profiles (gcov/llvm)
        - Controls CMake behavior
        """
        coverage_dir = self._config.artifact_dir / "coverage"
        coverage_dir.mkdir(parents=True, exist_ok=True)

        return {
            # GCC coverage output
            "GCOV_PREFIX": str(coverage_dir),
            "GCOV_PREFIX_STRIP": "0",
            # LLVM coverage output
            "LLVM_PROFILE_FILE": str(coverage_dir / f"{self._config.run_id}-%p.profraw"),
            # CMake settings
            "CMAKE_COLOR_DIAGNOSTICS": "OFF",
            # CTest settings
            "CTEST_OUTPUT_ON_FAILURE": "1",
            "CTEST_PARALLEL_LEVEL": "4",
        }

    def _ruby_env(self) -> dict[str, str]:
        """Ruby environment overrides.

        Key protections:
        - Isolates SimpleCov output
        - Disables Spring (Rails preloader that can hang)
        """
        coverage_dir = self._config.artifact_dir / "coverage"
        coverage_dir.mkdir(parents=True, exist_ok=True)

        return {
            # SimpleCov coverage directory
            "COVERAGE": "true",
            "COVERAGE_DIR": str(coverage_dir),
            "SIMPLECOV_COVERAGE_DIR": str(coverage_dir),
            # Disable Spring preloader (can hang)
            "DISABLE_SPRING": "1",
            # Rails environment
            "RAILS_ENV": "test",
            "RACK_ENV": "test",
            # Bundler settings - no prompts
            "BUNDLE_SILENCE_ROOT_WARNING": "1",
            "BUNDLE_DISABLE_SHARED_GEMS": "1",
            # RSpec settings
            "SPEC_OPTS": "--no-color --format documentation",
        }

    def _php_env(self) -> dict[str, str]:
        """PHP environment overrides.

        Key protections:
        - Isolates coverage output (Xdebug/PCOV)
        - Disables interactive composer
        """
        coverage_dir = self._config.artifact_dir / "coverage"
        coverage_dir.mkdir(parents=True, exist_ok=True)

        return {
            # Xdebug coverage output
            "XDEBUG_MODE": "coverage",
            # PCOV settings
            "PCOV_ENABLED": "1",
            # Coverage output directory
            "COVERAGE_OUTPUT_DIR": str(coverage_dir),
            # Composer settings - no prompts
            "COMPOSER_NO_INTERACTION": "1",
            "COMPOSER_ALLOW_SUPERUSER": "1",
            # PHPUnit settings
            "PHPUNIT_RESULT_CACHE": str(self._config.artifact_dir / ".phpunit.result.cache"),
            # Disable memory limit for tests
            "PHP_MEMORY_LIMIT": "-1",
        }

    def _elixir_env(self) -> dict[str, str]:
        """Elixir environment overrides.

        Key protections:
        - Isolates ExCoveralls output
        - Controls Mix behavior
        """
        coverage_dir = self._config.artifact_dir / "coverage"
        coverage_dir.mkdir(parents=True, exist_ok=True)

        return {
            # Mix environment
            "MIX_ENV": "test",
            # Disable prompts
            "MIX_QUIET": "1",
            # ExCoveralls output
            "COVERALLS_REPO_TOKEN": "",  # Clear to prevent accidental uploads
            # Coverage output
            "EXCOVERALLS_OUTPUT_DIR": str(coverage_dir),
            # Hex settings - no prompts
            "HEX_HTTP_TIMEOUT": "60",
        }

    def _dart_env(self) -> dict[str, str]:
        """Dart/Flutter environment overrides.

        Key protections:
        - Isolates coverage output
        - Disables analytics
        """
        coverage_dir = self._config.artifact_dir / "coverage"
        coverage_dir.mkdir(parents=True, exist_ok=True)

        return {
            # Dart settings
            "PUB_CACHE": str(self._config.workspace_root / ".pub-cache"),
            # Flutter settings
            "FLUTTER_SUPPRESS_ANALYTICS": "true",
            "CI": "true",  # Flutter respects this
            # Coverage output
            "DART_COVERAGE_DIR": str(coverage_dir),
        }

    def _swift_env(self) -> dict[str, str]:
        """Swift environment overrides.

        Key protections:
        - Controls Xcode behavior
        - Isolates coverage output
        """
        coverage_dir = self._config.artifact_dir / "coverage"
        coverage_dir.mkdir(parents=True, exist_ok=True)

        return {
            # Xcode settings
            "DEVELOPER_DIR": os.environ.get(
                "DEVELOPER_DIR", "/Applications/Xcode.app/Contents/Developer"
            ),
            # Disable derived data caching issues
            "SWIFT_DETERMINISTIC_HASHING": "1",
            # Coverage output
            "LLVM_PROFILE_FILE": str(coverage_dir / f"{self._config.run_id}-%p.profraw"),
        }

    # =========================================================================
    # Language-Specific Command Sanitization Strategies
    # =========================================================================

    def _get_cmd_strategy(self, lang: LanguageFamily) -> Callable[[list[str]], list[str]]:
        """Get command sanitization strategy for language family."""
        strategies: dict[LanguageFamily, Callable[[list[str]], list[str]]] = {
            "python": self._sanitize_python_cmd,
            "javascript": self._sanitize_javascript_cmd,
            "typescript": self._sanitize_javascript_cmd,
            "go": self._sanitize_go_cmd,
            "rust": self._sanitize_rust_cmd,
            "java": self._sanitize_java_cmd,
            "kotlin": self._sanitize_java_cmd,
            "scala": self._sanitize_java_cmd,
            "csharp": self._sanitize_csharp_cmd,
            "cpp": self._sanitize_cpp_cmd,
            "ruby": self._sanitize_ruby_cmd,
            "php": self._sanitize_php_cmd,
            "elixir": self._sanitize_elixir_cmd,
            "dart": self._sanitize_dart_cmd,
            "swift": self._sanitize_swift_cmd,
            "unknown": lambda cmd: cmd,
        }
        return strategies.get(lang, lambda cmd: cmd)

    def _sanitize_python_cmd(self, cmd: list[str]) -> list[str]:
        """Sanitize Python test commands.

        Removes:
        - Coverage flags if we're injecting our own (strip_coverage_flags=True)
        - Verbose flags that clutter output
        - Watch mode flags
        """
        result = []
        skip_next = False

        for i, arg in enumerate(cmd):
            if skip_next:
                skip_next = False
                continue

            # Strip coverage flags if requested
            if self._config.strip_coverage_flags:
                if arg.startswith("--cov"):
                    # Handle --cov=path and --cov path formats
                    if "=" not in arg and i + 1 < len(cmd) and not cmd[i + 1].startswith("-"):
                        skip_next = True
                    continue
                if arg == "--cov-report":
                    skip_next = True
                    continue
                if arg.startswith("--cov-report="):
                    continue

            # Remove watch mode
            if arg in ("--watch", "-w", "--watch-all"):
                continue

            # Remove overly verbose flags (we set our own via PYTEST_ADDOPTS)
            if arg in ("-vvv", "-vvvv"):
                result.append("-v")  # Keep single verbose
                continue

            result.append(arg)

        return result

    def _sanitize_javascript_cmd(self, cmd: list[str]) -> list[str]:
        """Sanitize JavaScript/TypeScript test commands.

        Adds:
        - --forceExit for Jest (prevents hanging)
        - --run for Vitest (prevents watch mode)
        - --no-watchman (prevents file watching hangs)

        Removes:
        - --watch flags
        - Interactive flags
        """
        result = []
        is_jest = any("jest" in arg.lower() for arg in cmd)
        is_vitest = any("vitest" in arg.lower() for arg in cmd)

        for arg in cmd:
            # Remove watch mode flags
            if arg in ("--watch", "-w", "--watchAll", "--watch-all"):
                continue

            # Remove interactive flags
            if arg in ("--interactive", "-i"):
                continue

            result.append(arg)

        # Add safety flags
        if is_jest:
            if "--forceExit" not in result:
                result.append("--forceExit")
            if "--no-watchman" not in result:
                result.append("--no-watchman")
            if "--detectOpenHandles" not in result:
                result.append("--detectOpenHandles")

        if is_vitest and "run" not in result and result and result[0] not in ("vitest",):
            # Ensure run mode (not watch)
            # Find vitest in command and insert 'run' after it
            for i, arg in enumerate(result):
                if "vitest" in arg:
                    result.insert(i + 1, "run")
                    break

        return result

    def _sanitize_go_cmd(self, cmd: list[str]) -> list[str]:
        """Sanitize Go test commands.

        Adds:
        - -count=1 to disable test caching
        - Timeout if not specified
        """
        result = list(cmd)

        # Add cache-busting flag
        if "-count" not in " ".join(result):
            result.append("-count=1")

        # Add timeout if not present
        has_timeout = any("-timeout" in arg for arg in result)
        if not has_timeout:
            result.append(f"-timeout={self._config.timeout_sec}s")

        return result

    def _sanitize_rust_cmd(self, cmd: list[str]) -> list[str]:
        """Sanitize Rust test commands.

        Adds:
        - --no-fail-fast for comprehensive test runs
        - Color disabled
        """
        result = list(cmd)

        # Disable color
        if "--color" not in " ".join(result):
            result.append("--color=never")

        return result

    def _sanitize_java_cmd(self, cmd: list[str]) -> list[str]:
        """Sanitize Java/Maven/Gradle commands.

        Adds:
        - Batch mode for Maven
        - No-daemon for Gradle
        - Fail-safe thresholds disabled
        """
        result = list(cmd)

        # Check if Maven
        is_maven = any(arg in ("mvn", "./mvnw", "mvnw") for arg in cmd)
        is_gradle = any(arg in ("gradle", "./gradlew", "gradlew") for arg in cmd)

        if is_maven:
            if "-B" not in result and "--batch-mode" not in result:
                # Insert batch mode early in command
                insert_pos = 1 if len(result) > 1 else len(result)
                result.insert(insert_pos, "-B")
            # Disable fail-on-coverage-threshold
            if "-DfailIfNoTests=false" not in result:
                result.append("-DfailIfNoTests=false")

        if is_gradle:
            if "--no-daemon" not in result:
                insert_pos = 1 if len(result) > 1 else len(result)
                result.insert(insert_pos, "--no-daemon")
            if "--console" not in " ".join(result):
                result.append("--console=plain")

        return result

    def _sanitize_csharp_cmd(self, cmd: list[str]) -> list[str]:
        """Sanitize .NET test commands.

        Adds:
        - --no-restore to speed up (assume restore done)
        - Verbosity control
        """
        result = list(cmd)

        # Add verbosity control
        if "--verbosity" not in " ".join(result) and "-v" not in result:
            result.append("--verbosity=minimal")

        return result

    def _sanitize_cpp_cmd(self, cmd: list[str]) -> list[str]:
        """Sanitize C/C++ test commands (CTest, etc).

        Adds:
        - --output-on-failure
        - Parallel execution
        """
        result = list(cmd)

        is_ctest = "ctest" in " ".join(cmd).lower()

        if is_ctest:
            if "--output-on-failure" not in result:
                result.append("--output-on-failure")
            if "--parallel" not in " ".join(result) and "-j" not in result:
                result.extend(["--parallel", "4"])

        return result

    def _sanitize_ruby_cmd(self, cmd: list[str]) -> list[str]:
        """Sanitize Ruby test commands.

        Adds:
        - --format for consistent output
        - Disable color
        """
        result = list(cmd)

        is_rspec = "rspec" in " ".join(cmd).lower()

        if is_rspec:
            if "--no-color" not in result:
                result.append("--no-color")
            # Ensure formatter for parsing
            has_format = any("--format" in arg or arg == "-f" for arg in result)
            if not has_format:
                result.extend(["--format", "documentation"])

        return result

    def _sanitize_php_cmd(self, cmd: list[str]) -> list[str]:
        """Sanitize PHP test commands.

        Adds:
        - --no-interaction
        - Disable color
        """
        result = list(cmd)

        is_phpunit = "phpunit" in " ".join(cmd).lower()

        if is_phpunit:
            if "--no-interaction" not in result:
                result.append("--no-interaction")
            if "--colors" not in " ".join(result):
                result.append("--colors=never")

        return result

    def _sanitize_elixir_cmd(self, cmd: list[str]) -> list[str]:
        """Sanitize Elixir test commands.

        Adds:
        - --no-start for isolation
        """
        result = list(cmd)

        # Elixir mix test is generally safe
        # Just ensure we're not in watch mode
        if "--stale" in result:
            result.remove("--stale")  # Run all tests, not just stale

        return result

    def _sanitize_dart_cmd(self, cmd: list[str]) -> list[str]:
        """Sanitize Dart/Flutter test commands.

        Adds:
        - --no-pub for speed
        - Reporter format
        """
        result = list(cmd)

        is_flutter = "flutter" in " ".join(cmd).lower()

        if is_flutter and "--no-pub" not in result:
            result.append("--no-pub")

        return result

    def _sanitize_swift_cmd(self, cmd: list[str]) -> list[str]:
        """Sanitize Swift test commands.

        Generally safe, minimal modifications needed.
        """
        result = list(cmd)

        # Swift package manager tests
        if "swift" in cmd and "test" in cmd and "--parallel" not in result:
            result.append("--parallel")

        return result


# =============================================================================
# Factory Function
# =============================================================================


def create_safe_context(
    artifact_dir: Path,
    workspace_root: Path,
    *,
    timeout_sec: int = 300,
    strip_coverage_flags: bool = False,
) -> SafeExecutionContext:
    """Create a safe execution context for test runs.

    Args:
        artifact_dir: Directory for test artifacts
        workspace_root: Root of the workspace being tested
        timeout_sec: Timeout for test execution
        strip_coverage_flags: Whether to strip coverage flags from commands

    Returns:
        Configured SafeExecutionContext
    """
    config = SafeExecutionConfig(
        artifact_dir=artifact_dir,
        workspace_root=workspace_root,
        timeout_sec=timeout_sec,
        strip_coverage_flags=strip_coverage_flags,
    )
    return SafeExecutionContext(config)
