"""Comprehensive tests for SafeExecutionContext.

Tests all language-specific defensive strategies for environment isolation
and command sanitization to prevent test execution issues in target repos.
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from codeplane.testing.safe_execution import (
    LanguageFamily,
    SafeExecutionConfig,
    SafeExecutionContext,
    _get_language_family,
    create_safe_context,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def temp_artifact_dir(tmp_path: Path) -> Path:
    """Create a temporary artifact directory."""
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir(parents=True)
    return artifact_dir


@pytest.fixture
def temp_workspace(tmp_path: Path) -> Path:
    """Create a temporary workspace directory."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    return workspace


@pytest.fixture
def base_config(temp_artifact_dir: Path, temp_workspace: Path) -> SafeExecutionConfig:
    """Create a base configuration for testing."""
    return SafeExecutionConfig(
        artifact_dir=temp_artifact_dir,
        workspace_root=temp_workspace,
        timeout_sec=300,
        strip_coverage_flags=False,
        run_id="test-run-12345",
    )


@pytest.fixture
def safe_ctx(base_config: SafeExecutionConfig) -> SafeExecutionContext:
    """Create a SafeExecutionContext for testing."""
    return SafeExecutionContext(base_config)


# =============================================================================
# Language Family Mapping Tests
# =============================================================================


class TestLanguageFamilyMapping:
    """Tests for pack_id to language family mapping."""

    @pytest.mark.parametrize(
        "pack_id,expected_family",
        [
            # Python
            ("python.pytest", "python"),
            ("python.unittest", "python"),
            ("python.nose", "python"),
            # JavaScript/TypeScript
            ("js.jest", "javascript"),
            ("js.vitest", "javascript"),
            ("js.mocha", "javascript"),
            ("ts.jest", "typescript"),
            ("ts.vitest", "typescript"),
            # Go
            ("go.gotest", "go"),
            # Rust
            ("rust.nextest", "rust"),
            ("rust.cargo_test", "rust"),
            # JVM languages
            ("java.maven", "java"),
            ("java.gradle", "java"),
            ("kotlin.gradle", "kotlin"),
            ("scala.sbt", "scala"),
            # .NET
            ("csharp.dotnet", "csharp"),
            # C/C++
            ("cpp.ctest", "cpp"),
            ("cpp.gtest", "cpp"),
            ("cpp.catch2", "cpp"),
            # Ruby
            ("ruby.rspec", "ruby"),
            ("ruby.minitest", "ruby"),
            # PHP
            ("php.phpunit", "php"),
            ("php.pest", "php"),
            # Elixir
            ("elixir.exunit", "elixir"),
            # Dart/Flutter
            ("dart.darttest", "dart"),
            ("dart.fluttertest", "dart"),
            # Swift
            ("swift.xctest", "swift"),
            # Unknown
            ("unknown.runner", "unknown"),
            ("foo.bar", "unknown"),
            ("", "unknown"),
        ],
    )
    def test_language_family_mapping(self, pack_id: str, expected_family: LanguageFamily) -> None:
        """Verify correct mapping from pack_id to language family."""
        assert _get_language_family(pack_id) == expected_family


# =============================================================================
# Universal Environment Override Tests
# =============================================================================


class TestUniversalEnvironmentOverrides:
    """Tests for environment variables applied to all languages."""

    def test_ci_environment_set(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify CI environment variables are set."""
        env = safe_ctx.prepare_environment("python.pytest")
        assert env["CI"] == "true"
        assert env["CONTINUOUS_INTEGRATION"] == "true"

    def test_noninteractive_flags_set(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify non-interactive flags are set."""
        env = safe_ctx.prepare_environment("python.pytest")
        assert env["NONINTERACTIVE"] == "1"
        assert env["DEBIAN_FRONTEND"] == "noninteractive"

    def test_color_disabled(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify color output is disabled for clean parsing."""
        env = safe_ctx.prepare_environment("python.pytest")
        assert env["NO_COLOR"] == "1"
        assert env["FORCE_COLOR"] == "0"

    def test_git_terminal_prompt_disabled(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify git prompts are disabled."""
        env = safe_ctx.prepare_environment("python.pytest")
        assert env["GIT_TERMINAL_PROMPT"] == "0"

    def test_telemetry_disabled(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify various telemetry is disabled."""
        env = safe_ctx.prepare_environment("python.pytest")
        assert env["DOTNET_CLI_TELEMETRY_OPTOUT"] == "1"
        assert env["GATSBY_TELEMETRY_DISABLED"] == "1"
        assert env["NEXT_TELEMETRY_DISABLED"] == "1"
        assert env["NUXT_TELEMETRY_DISABLED"] == "1"
        assert env["HOMEBREW_NO_ANALYTICS"] == "1"

    def test_browser_disabled(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify browser opening is prevented."""
        env = safe_ctx.prepare_environment("python.pytest")
        assert env["BROWSER"] == "none"

    def test_codeplane_markers_set(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify CodePlane execution markers are set."""
        env = safe_ctx.prepare_environment("python.pytest")
        assert env["CODEPLANE_EXECUTION"] == "1"
        assert env["CODEPLANE_RUN_ID"] == "test-run-12345"

    def test_base_environment_preserved(self, base_config: SafeExecutionConfig) -> None:
        """Verify base environment variables are preserved."""
        ctx = SafeExecutionContext(base_config)
        base_env = {"MY_CUSTOM_VAR": "my_value", "PATH": "/usr/bin"}
        env = ctx.prepare_environment("python.pytest", base_env=base_env)
        assert env["MY_CUSTOM_VAR"] == "my_value"
        assert env["PATH"] == "/usr/bin"
        # Universal overrides still applied
        assert env["CI"] == "true"


# =============================================================================
# Python Environment Tests
# =============================================================================


class TestPythonEnvironment:
    """Tests for Python-specific environment overrides."""

    def test_coverage_file_isolation(
        self, safe_ctx: SafeExecutionContext, temp_artifact_dir: Path
    ) -> None:
        """Verify COVERAGE_FILE is set to isolated path to prevent SQLite corruption."""
        env = safe_ctx.prepare_environment("python.pytest")
        coverage_file = env["COVERAGE_FILE"]
        assert temp_artifact_dir.as_posix() in coverage_file
        assert "test-run-12345" in coverage_file
        # Verify directory was created
        assert (temp_artifact_dir / "coverage").exists()

    def test_coverage_process_start_cleared(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify COVERAGE_PROCESS_START is cleared."""
        env = safe_ctx.prepare_environment("python.pytest")
        assert env["COVERAGE_PROCESS_START"] == ""

    def test_bytecode_disabled(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify Python bytecode is disabled."""
        env = safe_ctx.prepare_environment("python.pytest")
        assert env["PYTHONDONTWRITEBYTECODE"] == "1"

    def test_hash_seed_reproducible(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify hash seed is set for reproducibility."""
        env = safe_ctx.prepare_environment("python.pytest")
        assert env["PYTHONHASHSEED"] == "0"

    def test_utf8_encoding(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify UTF-8 encoding is set."""
        env = safe_ctx.prepare_environment("python.pytest")
        assert env["PYTHONIOENCODING"] == "utf-8"
        assert env["PYTHONUTF8"] == "1"

    def test_pytest_addopts_override(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify PYTEST_ADDOPTS is set to override verbose project settings."""
        env = safe_ctx.prepare_environment("python.pytest")
        assert "--tb=short" in env["PYTEST_ADDOPTS"]
        assert "-q" in env["PYTEST_ADDOPTS"]

    def test_pip_version_check_disabled(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify pip version check is disabled."""
        env = safe_ctx.prepare_environment("python.pytest")
        assert env["PIP_DISABLE_PIP_VERSION_CHECK"] == "1"


# =============================================================================
# JavaScript/TypeScript Environment Tests
# =============================================================================


class TestJavaScriptEnvironment:
    """Tests for JavaScript/TypeScript-specific environment overrides."""

    @pytest.mark.parametrize("pack_id", ["js.jest", "js.vitest", "ts.jest", "ts.vitest"])
    def test_node_env_set(self, safe_ctx: SafeExecutionContext, pack_id: str) -> None:
        """Verify NODE_ENV is set to test."""
        env = safe_ctx.prepare_environment(pack_id)
        assert env["NODE_ENV"] == "test"

    def test_watchman_disabled(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify watchman socket is disabled to prevent hangs."""
        env = safe_ctx.prepare_environment("js.jest")
        assert env["WATCHMAN_SOCK"] == "/dev/null"

    def test_npm_prompts_disabled(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify npm/yarn prompts are disabled."""
        env = safe_ctx.prepare_environment("js.jest")
        assert env["npm_config_yes"] == "true"
        assert env["npm_config_progress"] == "false"

    def test_update_notifier_disabled(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify update notifiers are disabled."""
        env = safe_ctx.prepare_environment("js.jest")
        assert env["NO_UPDATE_NOTIFIER"] == "1"
        assert env["NPM_CONFIG_UPDATE_NOTIFIER"] == "false"

    def test_nx_daemon_disabled(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify Nx daemon is disabled to prevent orphan processes."""
        env = safe_ctx.prepare_environment("js.jest")
        assert env["NX_DAEMON"] == "false"

    def test_coverage_dir_set(
        self, safe_ctx: SafeExecutionContext, temp_artifact_dir: Path
    ) -> None:
        """Verify coverage directory is set."""
        env = safe_ctx.prepare_environment("js.jest")
        assert temp_artifact_dir.as_posix() in env["COVERAGE_DIR"]

    def test_vitest_marker_set(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify VITEST marker is set."""
        env = safe_ctx.prepare_environment("js.vitest")
        assert env["VITEST"] == "true"


# =============================================================================
# Go Environment Tests
# =============================================================================


class TestGoEnvironment:
    """Tests for Go-specific environment overrides."""

    def test_module_mode_enabled(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify Go module mode is enabled."""
        env = safe_ctx.prepare_environment("go.gotest")
        assert env["GO111MODULE"] == "on"

    def test_coverage_dir_set(
        self, safe_ctx: SafeExecutionContext, temp_artifact_dir: Path
    ) -> None:
        """Verify coverage directory is set."""
        env = safe_ctx.prepare_environment("go.gotest")
        assert temp_artifact_dir.as_posix() in env["GOCOVERDIR"]

    def test_test_caching_disabled(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify test caching is disabled via GOTESTFLAGS."""
        env = safe_ctx.prepare_environment("go.gotest")
        assert "-count=1" in env["GOTESTFLAGS"]

    def test_telemetry_disabled(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify Go telemetry is disabled."""
        env = safe_ctx.prepare_environment("go.gotest")
        assert env["GOTELEMETRY"] == "off"


# =============================================================================
# Rust Environment Tests
# =============================================================================


class TestRustEnvironment:
    """Tests for Rust-specific environment overrides."""

    @pytest.mark.parametrize("pack_id", ["rust.nextest", "rust.cargo_test"])
    def test_llvm_profile_file_isolation(
        self, safe_ctx: SafeExecutionContext, temp_artifact_dir: Path, pack_id: str
    ) -> None:
        """Verify LLVM_PROFILE_FILE is set for coverage isolation."""
        env = safe_ctx.prepare_environment(pack_id)
        profile_file = env["LLVM_PROFILE_FILE"]
        assert temp_artifact_dir.as_posix() in profile_file
        assert "test-run-12345" in profile_file

    def test_cargo_color_disabled(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify cargo color is disabled."""
        env = safe_ctx.prepare_environment("rust.nextest")
        assert env["CARGO_TERM_COLOR"] == "never"

    def test_incremental_builds_disabled(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify incremental builds are disabled for reproducibility."""
        env = safe_ctx.prepare_environment("rust.nextest")
        assert env["CARGO_INCREMENTAL"] == "0"


# =============================================================================
# Java/JVM Environment Tests
# =============================================================================


class TestJavaEnvironment:
    """Tests for Java/Kotlin/Scala-specific environment overrides."""

    @pytest.mark.parametrize("pack_id", ["java.maven", "java.gradle", "kotlin.gradle", "scala.sbt"])
    def test_headless_mode(self, safe_ctx: SafeExecutionContext, pack_id: str) -> None:
        """Verify headless mode is set to prevent GUI prompts."""
        env = safe_ctx.prepare_environment(pack_id)
        assert "headless=true" in env["_JAVA_OPTIONS"]

    def test_gradle_daemon_disabled(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify Gradle daemon is disabled to prevent orphan processes."""
        env = safe_ctx.prepare_environment("java.gradle")
        assert "daemon=false" in env["GRADLE_OPTS"]

    def test_maven_batch_mode(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify Maven batch mode is configured."""
        env = safe_ctx.prepare_environment("java.maven")
        assert env["MAVEN_BATCH_MODE"] == "true"

    def test_jacoco_output_isolated(
        self, safe_ctx: SafeExecutionContext, temp_artifact_dir: Path
    ) -> None:
        """Verify JaCoCo output is isolated."""
        env = safe_ctx.prepare_environment("java.maven")
        assert temp_artifact_dir.as_posix() in env["JACOCO_DESTFILE"]

    def test_sbt_ci_mode(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify SBT CI mode is set."""
        env = safe_ctx.prepare_environment("scala.sbt")
        assert "sbt.ci=true" in env["SBT_OPTS"]

    def test_kotlin_daemon_disabled(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify Kotlin daemon is disabled."""
        env = safe_ctx.prepare_environment("kotlin.gradle")
        assert env["KOTLIN_DAEMON_ENABLED"] == "false"


# =============================================================================
# C#/.NET Environment Tests
# =============================================================================


class TestCSharpEnvironment:
    """Tests for C#/.NET-specific environment overrides."""

    def test_dotnet_telemetry_disabled(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify .NET telemetry is disabled."""
        env = safe_ctx.prepare_environment("csharp.dotnet")
        assert env["DOTNET_CLI_TELEMETRY_OPTOUT"] == "1"

    def test_dotnet_logo_disabled(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify .NET logo is disabled."""
        env = safe_ctx.prepare_environment("csharp.dotnet")
        assert env["DOTNET_NOLOGO"] == "1"

    def test_nuget_no_interaction(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify NuGet non-interactive mode."""
        env = safe_ctx.prepare_environment("csharp.dotnet")
        assert env["NUGET_XMLDOC_MODE"] == "skip"

    def test_coverlet_output_isolated(
        self, safe_ctx: SafeExecutionContext, temp_artifact_dir: Path
    ) -> None:
        """Verify Coverlet output is isolated."""
        env = safe_ctx.prepare_environment("csharp.dotnet")
        assert temp_artifact_dir.as_posix() in env["COVERLET_OUTPUT"]

    def test_msbuild_node_reuse_disabled(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify MSBuild node reuse is disabled."""
        env = safe_ctx.prepare_environment("csharp.dotnet")
        assert env["MSBUILDDISABLENODEREUSE"] == "1"


# =============================================================================
# C/C++ Environment Tests
# =============================================================================


class TestCppEnvironment:
    """Tests for C/C++-specific environment overrides."""

    @pytest.mark.parametrize("pack_id", ["cpp.ctest", "cpp.gtest", "cpp.catch2"])
    def test_gcov_prefix_set(
        self, safe_ctx: SafeExecutionContext, temp_artifact_dir: Path, pack_id: str
    ) -> None:
        """Verify GCOV_PREFIX is set for coverage isolation."""
        env = safe_ctx.prepare_environment(pack_id)
        assert temp_artifact_dir.as_posix() in env["GCOV_PREFIX"]

    def test_llvm_profile_file_set(
        self, safe_ctx: SafeExecutionContext, temp_artifact_dir: Path
    ) -> None:
        """Verify LLVM_PROFILE_FILE is set."""
        env = safe_ctx.prepare_environment("cpp.ctest")
        assert temp_artifact_dir.as_posix() in env["LLVM_PROFILE_FILE"]

    def test_cmake_color_disabled(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify CMake color diagnostics are disabled."""
        env = safe_ctx.prepare_environment("cpp.ctest")
        assert env["CMAKE_COLOR_DIAGNOSTICS"] == "OFF"

    def test_ctest_output_on_failure(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify CTest output on failure is enabled."""
        env = safe_ctx.prepare_environment("cpp.ctest")
        assert env["CTEST_OUTPUT_ON_FAILURE"] == "1"


# =============================================================================
# Ruby Environment Tests
# =============================================================================


class TestRubyEnvironment:
    """Tests for Ruby-specific environment overrides."""

    @pytest.mark.parametrize("pack_id", ["ruby.rspec", "ruby.minitest"])
    def test_rails_env_set(self, safe_ctx: SafeExecutionContext, pack_id: str) -> None:
        """Verify Rails environment is set to test."""
        env = safe_ctx.prepare_environment(pack_id)
        assert env["RAILS_ENV"] == "test"
        assert env["RACK_ENV"] == "test"

    def test_spring_disabled(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify Spring preloader is disabled."""
        env = safe_ctx.prepare_environment("ruby.rspec")
        assert env["DISABLE_SPRING"] == "1"

    def test_simplecov_dir_isolated(
        self, safe_ctx: SafeExecutionContext, temp_artifact_dir: Path
    ) -> None:
        """Verify SimpleCov output is isolated."""
        env = safe_ctx.prepare_environment("ruby.rspec")
        assert temp_artifact_dir.as_posix() in env["SIMPLECOV_COVERAGE_DIR"]

    def test_bundler_warnings_silenced(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify Bundler warnings are silenced."""
        env = safe_ctx.prepare_environment("ruby.rspec")
        assert env["BUNDLE_SILENCE_ROOT_WARNING"] == "1"


# =============================================================================
# PHP Environment Tests
# =============================================================================


class TestPHPEnvironment:
    """Tests for PHP-specific environment overrides."""

    @pytest.mark.parametrize("pack_id", ["php.phpunit", "php.pest"])
    def test_xdebug_mode_coverage(self, safe_ctx: SafeExecutionContext, pack_id: str) -> None:
        """Verify Xdebug mode is set to coverage."""
        env = safe_ctx.prepare_environment(pack_id)
        assert env["XDEBUG_MODE"] == "coverage"

    def test_composer_no_interaction(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify Composer non-interactive mode."""
        env = safe_ctx.prepare_environment("php.phpunit")
        assert env["COMPOSER_NO_INTERACTION"] == "1"

    def test_memory_limit_disabled(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify PHP memory limit is disabled."""
        env = safe_ctx.prepare_environment("php.phpunit")
        assert env["PHP_MEMORY_LIMIT"] == "-1"

    def test_phpunit_cache_isolated(
        self, safe_ctx: SafeExecutionContext, temp_artifact_dir: Path
    ) -> None:
        """Verify PHPUnit cache is isolated."""
        env = safe_ctx.prepare_environment("php.phpunit")
        assert temp_artifact_dir.as_posix() in env["PHPUNIT_RESULT_CACHE"]


# =============================================================================
# Elixir Environment Tests
# =============================================================================


class TestElixirEnvironment:
    """Tests for Elixir-specific environment overrides."""

    def test_mix_env_set(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify MIX_ENV is set to test."""
        env = safe_ctx.prepare_environment("elixir.exunit")
        assert env["MIX_ENV"] == "test"

    def test_mix_quiet(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify Mix quiet mode is enabled."""
        env = safe_ctx.prepare_environment("elixir.exunit")
        assert env["MIX_QUIET"] == "1"

    def test_coveralls_token_cleared(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify Coveralls token is cleared to prevent uploads."""
        env = safe_ctx.prepare_environment("elixir.exunit")
        assert env["COVERALLS_REPO_TOKEN"] == ""


# =============================================================================
# Dart/Flutter Environment Tests
# =============================================================================


class TestDartEnvironment:
    """Tests for Dart/Flutter-specific environment overrides."""

    @pytest.mark.parametrize("pack_id", ["dart.darttest", "dart.fluttertest"])
    def test_flutter_analytics_disabled(self, safe_ctx: SafeExecutionContext, pack_id: str) -> None:
        """Verify Flutter analytics are disabled."""
        env = safe_ctx.prepare_environment(pack_id)
        assert env["FLUTTER_SUPPRESS_ANALYTICS"] == "true"


# =============================================================================
# Swift Environment Tests
# =============================================================================


class TestSwiftEnvironment:
    """Tests for Swift-specific environment overrides."""

    def test_deterministic_hashing(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify deterministic hashing is enabled."""
        env = safe_ctx.prepare_environment("swift.xctest")
        assert env["SWIFT_DETERMINISTIC_HASHING"] == "1"

    def test_llvm_profile_file_set(
        self, safe_ctx: SafeExecutionContext, temp_artifact_dir: Path
    ) -> None:
        """Verify LLVM_PROFILE_FILE is set."""
        env = safe_ctx.prepare_environment("swift.xctest")
        assert temp_artifact_dir.as_posix() in env["LLVM_PROFILE_FILE"]


# =============================================================================
# Python Command Sanitization Tests
# =============================================================================


class TestPythonCommandSanitization:
    """Tests for Python command sanitization."""

    def test_watch_mode_removed(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify watch mode flags are removed."""
        cmd = ["pytest", "tests/", "--watch", "-w"]
        result = safe_ctx.sanitize_command(cmd, "python.pytest")
        assert "--watch" not in result
        assert "-w" not in result

    def test_excessive_verbose_reduced(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify excessive verbose flags are reduced."""
        cmd = ["pytest", "tests/", "-vvv"]
        result = safe_ctx.sanitize_command(cmd, "python.pytest")
        assert "-vvv" not in result
        assert "-v" in result

    def test_coverage_flags_preserved_when_not_stripping(
        self, base_config: SafeExecutionConfig
    ) -> None:
        """Verify coverage flags are preserved when strip_coverage_flags is False."""
        base_config.strip_coverage_flags = False
        ctx = SafeExecutionContext(base_config)
        cmd = ["pytest", "tests/", "--cov=mypackage", "--cov-report=html"]
        result = ctx.sanitize_command(cmd, "python.pytest")
        assert "--cov=mypackage" in result
        assert "--cov-report=html" in result

    def test_coverage_flags_stripped_when_enabled(self, base_config: SafeExecutionConfig) -> None:
        """Verify coverage flags are stripped when strip_coverage_flags is True."""
        base_config.strip_coverage_flags = True
        ctx = SafeExecutionContext(base_config)
        cmd = ["pytest", "tests/", "--cov=mypackage", "--cov-report=html"]
        result = ctx.sanitize_command(cmd, "python.pytest")
        assert "--cov=mypackage" not in result
        assert "--cov-report=html" not in result

    def test_coverage_with_separate_args_stripped(self, base_config: SafeExecutionConfig) -> None:
        """Verify coverage flags with separate args are stripped."""
        base_config.strip_coverage_flags = True
        ctx = SafeExecutionContext(base_config)
        cmd = ["pytest", "tests/", "--cov", "mypackage", "--cov-report", "html"]
        result = ctx.sanitize_command(cmd, "python.pytest")
        assert "--cov" not in result
        assert "mypackage" not in result
        assert "--cov-report" not in result

    def test_normal_args_preserved(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify normal arguments are preserved."""
        cmd = ["pytest", "tests/", "-v", "--tb=short", "-x"]
        result = safe_ctx.sanitize_command(cmd, "python.pytest")
        assert result == ["pytest", "tests/", "-v", "--tb=short", "-x"]


# =============================================================================
# JavaScript Command Sanitization Tests
# =============================================================================


class TestJavaScriptCommandSanitization:
    """Tests for JavaScript/TypeScript command sanitization."""

    def test_jest_force_exit_added(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify --forceExit is added for Jest."""
        cmd = ["npx", "jest", "tests/"]
        result = safe_ctx.sanitize_command(cmd, "js.jest")
        assert "--forceExit" in result

    def test_jest_no_watchman_added(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify --no-watchman is added for Jest."""
        cmd = ["npx", "jest", "tests/"]
        result = safe_ctx.sanitize_command(cmd, "js.jest")
        assert "--no-watchman" in result

    def test_jest_detect_open_handles_added(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify --detectOpenHandles is added for Jest."""
        cmd = ["npx", "jest", "tests/"]
        result = safe_ctx.sanitize_command(cmd, "js.jest")
        assert "--detectOpenHandles" in result

    def test_watch_mode_removed(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify watch mode flags are removed."""
        cmd = ["npx", "jest", "tests/", "--watch", "--watchAll"]
        result = safe_ctx.sanitize_command(cmd, "js.jest")
        assert "--watch" not in result
        assert "--watchAll" not in result

    def test_interactive_flag_removed(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify interactive flag is removed."""
        cmd = ["npx", "jest", "tests/", "--interactive"]
        result = safe_ctx.sanitize_command(cmd, "js.jest")
        assert "--interactive" not in result

    def test_vitest_run_mode_added(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify run mode is added for Vitest."""
        cmd = ["npx", "vitest", "tests/"]
        result = safe_ctx.sanitize_command(cmd, "js.vitest")
        assert "run" in result

    def test_safety_flags_not_duplicated(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify safety flags are not duplicated if already present."""
        cmd = ["npx", "jest", "tests/", "--forceExit", "--no-watchman"]
        result = safe_ctx.sanitize_command(cmd, "js.jest")
        assert result.count("--forceExit") == 1
        assert result.count("--no-watchman") == 1


# =============================================================================
# Go Command Sanitization Tests
# =============================================================================


class TestGoCommandSanitization:
    """Tests for Go command sanitization."""

    def test_cache_busting_added(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify -count=1 is added to disable test caching."""
        cmd = ["go", "test", "./..."]
        result = safe_ctx.sanitize_command(cmd, "go.gotest")
        assert "-count=1" in result

    def test_timeout_added(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify timeout is added if not present."""
        cmd = ["go", "test", "./..."]
        result = safe_ctx.sanitize_command(cmd, "go.gotest")
        assert any("-timeout=" in arg for arg in result)

    def test_existing_timeout_preserved(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify existing timeout is not overridden."""
        cmd = ["go", "test", "./...", "-timeout=600s"]
        result = safe_ctx.sanitize_command(cmd, "go.gotest")
        assert "-timeout=600s" in result
        assert sum(1 for arg in result if "-timeout=" in arg) == 1


# =============================================================================
# Rust Command Sanitization Tests
# =============================================================================


class TestRustCommandSanitization:
    """Tests for Rust command sanitization."""

    @pytest.mark.parametrize("pack_id", ["rust.nextest", "rust.cargo_test"])
    def test_color_disabled(self, safe_ctx: SafeExecutionContext, pack_id: str) -> None:
        """Verify color is disabled."""
        cmd = ["cargo", "test"]
        result = safe_ctx.sanitize_command(cmd, pack_id)
        assert "--color=never" in result


# =============================================================================
# Java Command Sanitization Tests
# =============================================================================


class TestJavaCommandSanitization:
    """Tests for Java/Maven/Gradle command sanitization."""

    def test_maven_batch_mode_added(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify -B (batch mode) is added for Maven."""
        cmd = ["mvn", "test"]
        result = safe_ctx.sanitize_command(cmd, "java.maven")
        assert "-B" in result

    def test_maven_fail_if_no_tests_disabled(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify failIfNoTests is disabled."""
        cmd = ["mvn", "test"]
        result = safe_ctx.sanitize_command(cmd, "java.maven")
        assert "-DfailIfNoTests=false" in result

    def test_gradle_no_daemon_added(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify --no-daemon is added for Gradle."""
        cmd = ["./gradlew", "test"]
        result = safe_ctx.sanitize_command(cmd, "java.gradle")
        assert "--no-daemon" in result

    def test_gradle_console_plain_added(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify --console=plain is added for Gradle."""
        cmd = ["./gradlew", "test"]
        result = safe_ctx.sanitize_command(cmd, "java.gradle")
        assert "--console=plain" in result

    def test_maven_wrapper_detected(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify Maven wrapper is detected."""
        cmd = ["./mvnw", "test"]
        result = safe_ctx.sanitize_command(cmd, "java.maven")
        assert "-B" in result


# =============================================================================
# C#/.NET Command Sanitization Tests
# =============================================================================


class TestCSharpCommandSanitization:
    """Tests for C#/.NET command sanitization."""

    def test_verbosity_added(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify --verbosity=minimal is added."""
        cmd = ["dotnet", "test", "MyProject.csproj"]
        result = safe_ctx.sanitize_command(cmd, "csharp.dotnet")
        assert "--verbosity=minimal" in result


# =============================================================================
# C/C++ Command Sanitization Tests
# =============================================================================


class TestCppCommandSanitization:
    """Tests for C/C++ command sanitization."""

    def test_ctest_output_on_failure_added(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify --output-on-failure is added for CTest."""
        cmd = ["ctest", "--test-dir", "build"]
        result = safe_ctx.sanitize_command(cmd, "cpp.ctest")
        assert "--output-on-failure" in result

    def test_ctest_parallel_added(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify --parallel is added for CTest."""
        cmd = ["ctest", "--test-dir", "build"]
        result = safe_ctx.sanitize_command(cmd, "cpp.ctest")
        assert "--parallel" in result


# =============================================================================
# Ruby Command Sanitization Tests
# =============================================================================


class TestRubyCommandSanitization:
    """Tests for Ruby command sanitization."""

    def test_rspec_no_color_added(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify --no-color is added for RSpec."""
        cmd = ["bundle", "exec", "rspec", "spec/"]
        result = safe_ctx.sanitize_command(cmd, "ruby.rspec")
        assert "--no-color" in result

    def test_rspec_format_added(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify --format is added for RSpec."""
        cmd = ["bundle", "exec", "rspec", "spec/"]
        result = safe_ctx.sanitize_command(cmd, "ruby.rspec")
        assert "--format" in result
        assert "documentation" in result


# =============================================================================
# PHP Command Sanitization Tests
# =============================================================================


class TestPHPCommandSanitization:
    """Tests for PHP command sanitization."""

    def test_phpunit_no_interaction_added(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify --no-interaction is added for PHPUnit."""
        cmd = ["./vendor/bin/phpunit", "tests/"]
        result = safe_ctx.sanitize_command(cmd, "php.phpunit")
        assert "--no-interaction" in result

    def test_phpunit_colors_disabled(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify colors are disabled for PHPUnit."""
        cmd = ["./vendor/bin/phpunit", "tests/"]
        result = safe_ctx.sanitize_command(cmd, "php.phpunit")
        assert "--colors=never" in result


# =============================================================================
# Elixir Command Sanitization Tests
# =============================================================================


class TestElixirCommandSanitization:
    """Tests for Elixir command sanitization."""

    def test_stale_flag_removed(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify --stale flag is removed to run all tests."""
        cmd = ["mix", "test", "--stale"]
        result = safe_ctx.sanitize_command(cmd, "elixir.exunit")
        assert "--stale" not in result


# =============================================================================
# Dart Command Sanitization Tests
# =============================================================================


class TestDartCommandSanitization:
    """Tests for Dart/Flutter command sanitization."""

    def test_flutter_no_pub_added(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify --no-pub is added for Flutter."""
        cmd = ["flutter", "test"]
        result = safe_ctx.sanitize_command(cmd, "dart.fluttertest")
        assert "--no-pub" in result


# =============================================================================
# Swift Command Sanitization Tests
# =============================================================================


class TestSwiftCommandSanitization:
    """Tests for Swift command sanitization."""

    def test_parallel_added(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify --parallel is added for Swift tests."""
        cmd = ["swift", "test"]
        result = safe_ctx.sanitize_command(cmd, "swift.xctest")
        assert "--parallel" in result


# =============================================================================
# Unknown Language Tests
# =============================================================================


class TestUnknownLanguage:
    """Tests for unknown language handling."""

    def test_unknown_language_returns_universal_env_only(
        self, safe_ctx: SafeExecutionContext
    ) -> None:
        """Verify unknown languages only get universal environment."""
        env = safe_ctx.prepare_environment("unknown.runner")
        # Should have universal overrides
        assert env["CI"] == "true"
        # But not language-specific ones
        assert "COVERAGE_FILE" not in env or env.get("COVERAGE_FILE") is None

    def test_unknown_language_command_unchanged(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify unknown language commands are unchanged."""
        cmd = ["custom-runner", "--some-flag", "tests/"]
        result = safe_ctx.sanitize_command(cmd, "unknown.runner")
        assert result == cmd


# =============================================================================
# Factory Function Tests
# =============================================================================


class TestFactoryFunction:
    """Tests for create_safe_context factory function."""

    def test_creates_context_with_defaults(
        self, temp_artifact_dir: Path, temp_workspace: Path
    ) -> None:
        """Verify factory creates context with defaults."""
        ctx = create_safe_context(temp_artifact_dir, temp_workspace)
        assert ctx.config.artifact_dir == temp_artifact_dir
        assert ctx.config.workspace_root == temp_workspace
        assert ctx.config.timeout_sec == 300
        assert ctx.config.strip_coverage_flags is False

    def test_creates_context_with_custom_values(
        self, temp_artifact_dir: Path, temp_workspace: Path
    ) -> None:
        """Verify factory creates context with custom values."""
        ctx = create_safe_context(
            temp_artifact_dir,
            temp_workspace,
            timeout_sec=600,
            strip_coverage_flags=True,
        )
        assert ctx.config.timeout_sec == 600
        assert ctx.config.strip_coverage_flags is True

    def test_run_id_is_unique(self, temp_artifact_dir: Path, temp_workspace: Path) -> None:
        """Verify each context gets a unique run_id."""
        ctx1 = create_safe_context(temp_artifact_dir, temp_workspace)
        ctx2 = create_safe_context(temp_artifact_dir, temp_workspace)
        assert ctx1.config.run_id != ctx2.config.run_id


# =============================================================================
# Cleanup Tests
# =============================================================================


class TestCleanup:
    """Tests for cleanup functionality."""

    def test_cleanup_clears_temp_dirs(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify cleanup clears tracked temp directories."""
        safe_ctx._temp_dirs.append(Path("/tmp/fake"))
        safe_ctx.cleanup()
        assert len(safe_ctx._temp_dirs) == 0

    def test_cleanup_is_idempotent(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify cleanup can be called multiple times safely."""
        safe_ctx.cleanup()
        safe_ctx.cleanup()
        assert len(safe_ctx._temp_dirs) == 0


# =============================================================================
# Edge Case Tests
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_empty_command_handled(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify empty commands are handled gracefully."""
        result = safe_ctx.sanitize_command([], "python.pytest")
        assert result == []

    def test_single_arg_command(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify single argument commands work."""
        result = safe_ctx.sanitize_command(["pytest"], "python.pytest")
        assert result == ["pytest"]

    def test_command_with_special_characters(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify commands with special characters work."""
        cmd = ["pytest", "tests/test_foo.py::TestClass::test_method[param]"]
        result = safe_ctx.sanitize_command(cmd, "python.pytest")
        assert "tests/test_foo.py::TestClass::test_method[param]" in result

    def test_environment_with_none_base(self, safe_ctx: SafeExecutionContext) -> None:
        """Verify environment preparation with None base uses os.environ."""
        with patch.dict(os.environ, {"EXISTING_VAR": "value"}, clear=False):
            env = safe_ctx.prepare_environment("python.pytest", base_env=None)
            assert env["CI"] == "true"
            # os.environ should be included
            assert "PATH" in env or "EXISTING_VAR" in env

    def test_coverage_directory_creation(
        self, temp_artifact_dir: Path, temp_workspace: Path
    ) -> None:
        """Verify coverage directories are created for each language."""
        pack_ids = [
            "python.pytest",
            "js.jest",
            "go.gotest",
            "rust.nextest",
            "java.maven",
            "csharp.dotnet",
            "cpp.ctest",
            "ruby.rspec",
            "php.phpunit",
        ]
        for pack_id in pack_ids:
            ctx = create_safe_context(temp_artifact_dir, temp_workspace)
            ctx.prepare_environment(pack_id)
            assert (temp_artifact_dir / "coverage").exists()


# =============================================================================
# Integration Tests
# =============================================================================


class TestIntegration:
    """Integration tests for SafeExecutionContext."""

    def test_full_python_workflow(self, temp_artifact_dir: Path, temp_workspace: Path) -> None:
        """Test complete Python test execution workflow."""
        ctx = create_safe_context(
            temp_artifact_dir,
            temp_workspace,
            timeout_sec=60,
            strip_coverage_flags=True,
        )

        # Prepare environment
        env = ctx.prepare_environment("python.pytest")

        # Verify critical Python protections
        assert "COVERAGE_FILE" in env
        assert temp_artifact_dir.as_posix() in env["COVERAGE_FILE"]
        assert env["CI"] == "true"
        assert env["PYTHONDONTWRITEBYTECODE"] == "1"

        # Sanitize command with coverage flags
        cmd = ["pytest", "tests/", "--cov=myapp", "--cov-report=html", "-vvv", "--watch"]
        sanitized = ctx.sanitize_command(cmd, "python.pytest")

        # Verify sanitization
        assert "--cov=myapp" not in sanitized
        assert "--cov-report=html" not in sanitized
        assert "--watch" not in sanitized
        assert "-vvv" not in sanitized
        assert "-v" in sanitized

        # Cleanup
        ctx.cleanup()

    def test_full_javascript_workflow(self, temp_artifact_dir: Path, temp_workspace: Path) -> None:
        """Test complete JavaScript test execution workflow."""
        ctx = create_safe_context(temp_artifact_dir, temp_workspace)

        # Prepare environment
        env = ctx.prepare_environment("js.jest")

        # Verify critical JS protections
        assert env["NODE_ENV"] == "test"
        assert env["WATCHMAN_SOCK"] == "/dev/null"
        assert env["NX_DAEMON"] == "false"

        # Sanitize command
        cmd = ["npx", "jest", "src/", "--watch", "--interactive"]
        sanitized = ctx.sanitize_command(cmd, "js.jest")

        # Verify sanitization
        assert "--watch" not in sanitized
        assert "--interactive" not in sanitized
        assert "--forceExit" in sanitized
        assert "--no-watchman" in sanitized
        assert "--detectOpenHandles" in sanitized

        ctx.cleanup()

    def test_full_java_maven_workflow(self, temp_artifact_dir: Path, temp_workspace: Path) -> None:
        """Test complete Java/Maven test execution workflow."""
        ctx = create_safe_context(temp_artifact_dir, temp_workspace)

        # Prepare environment
        env = ctx.prepare_environment("java.maven")

        # Verify critical Java protections
        assert "headless=true" in env["_JAVA_OPTIONS"]
        assert env["MAVEN_BATCH_MODE"] == "true"
        assert "JACOCO_DESTFILE" in env

        # Sanitize command
        cmd = ["mvn", "test"]
        sanitized = ctx.sanitize_command(cmd, "java.maven")

        # Verify sanitization
        assert "-B" in sanitized
        assert "-DfailIfNoTests=false" in sanitized

        ctx.cleanup()

    def test_all_languages_produce_valid_env(
        self, temp_artifact_dir: Path, temp_workspace: Path
    ) -> None:
        """Verify all supported languages produce valid environment dicts."""
        pack_ids = [
            "python.pytest",
            "js.jest",
            "js.vitest",
            "ts.jest",
            "go.gotest",
            "rust.nextest",
            "rust.cargo_test",
            "java.maven",
            "java.gradle",
            "kotlin.gradle",
            "scala.sbt",
            "csharp.dotnet",
            "cpp.ctest",
            "ruby.rspec",
            "php.phpunit",
            "elixir.exunit",
            "dart.darttest",
            "swift.xctest",
            "unknown.runner",
        ]

        for pack_id in pack_ids:
            ctx = create_safe_context(temp_artifact_dir, temp_workspace)
            env = ctx.prepare_environment(pack_id)

            # All should have universal overrides
            assert env["CI"] == "true", f"{pack_id} missing CI"
            assert env["CODEPLANE_EXECUTION"] == "1", f"{pack_id} missing marker"

            # All values should be strings
            for key, value in env.items():
                assert isinstance(key, str), f"{pack_id} has non-string key: {key}"
                assert isinstance(value, str), f"{pack_id} has non-string value for {key}: {value}"

            ctx.cleanup()

    def test_all_languages_sanitize_commands(
        self, temp_artifact_dir: Path, temp_workspace: Path
    ) -> None:
        """Verify all supported languages can sanitize commands."""
        test_cases = [
            ("python.pytest", ["pytest", "tests/"]),
            ("js.jest", ["npx", "jest", "src/"]),
            ("js.vitest", ["npx", "vitest", "src/"]),
            ("go.gotest", ["go", "test", "./..."]),
            ("rust.nextest", ["cargo", "test"]),
            ("java.maven", ["mvn", "test"]),
            ("java.gradle", ["./gradlew", "test"]),
            ("csharp.dotnet", ["dotnet", "test"]),
            ("cpp.ctest", ["ctest", "--test-dir", "build"]),
            ("ruby.rspec", ["bundle", "exec", "rspec"]),
            ("php.phpunit", ["./vendor/bin/phpunit"]),
            ("elixir.exunit", ["mix", "test"]),
            ("dart.fluttertest", ["flutter", "test"]),
            ("swift.xctest", ["swift", "test"]),
            ("unknown.runner", ["custom", "command"]),
        ]

        ctx = create_safe_context(temp_artifact_dir, temp_workspace)

        for pack_id, cmd in test_cases:
            result = ctx.sanitize_command(cmd, pack_id)

            # Should always return a list
            assert isinstance(result, list), f"{pack_id} returned non-list"

            # Should preserve at least the command name
            assert len(result) >= len(cmd) or pack_id.startswith(
                "elixir"
            ), f"{pack_id} removed too many args"

        ctx.cleanup()
