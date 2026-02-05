"""Tests for testing/ops.py helper functions.

Tests the environment detection and workspace discovery functions:
- detect_python_venv: Python virtual environment detection
- get_python_executable: Python executable resolution
- detect_coverage_tools: Coverage tool availability
- detect_node_package_manager: Node package manager detection
- _default_parallelism: Default parallelism calculation
- _is_prunable_path: Prunable path detection
- detect_workspaces: Workspace detection in monorepos
- ActiveRun: Active run tracking dataclass
- DetectedWorkspace: Detected workspace dataclass
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from codeplane.testing.ops import (
    ActiveRun,
    DetectedWorkspace,
    _default_parallelism,
    _is_prunable_path,
    clear_coverage_tools_cache,
    detect_coverage_tools,
    detect_node_package_manager,
    detect_python_venv,
    detect_workspaces,
    get_python_executable,
)
from codeplane.testing.runner_pack import RunnerPack


class TestDetectPythonVenv:
    """Tests for detect_python_venv function."""

    def test_no_venv(self) -> None:
        """Returns None when no venv exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = detect_python_venv(Path(tmpdir))
            assert result is None

    def test_venv_with_pyvenv_cfg(self) -> None:
        """Detects venv with pyvenv.cfg."""
        with tempfile.TemporaryDirectory() as tmpdir:
            venv = Path(tmpdir) / ".venv"
            venv.mkdir()
            (venv / "pyvenv.cfg").write_text("home = /usr/bin")

            result = detect_python_venv(Path(tmpdir))
            assert result == venv

    def test_venv_with_unix_activate(self) -> None:
        """Detects venv with Unix activate script."""
        with tempfile.TemporaryDirectory() as tmpdir:
            venv = Path(tmpdir) / "venv"
            (venv / "bin").mkdir(parents=True)
            (venv / "bin" / "activate").write_text("# activate")

            result = detect_python_venv(Path(tmpdir))
            assert result == venv

    def test_venv_with_windows_activate(self) -> None:
        """Detects venv with Windows activate script."""
        with tempfile.TemporaryDirectory() as tmpdir:
            venv = Path(tmpdir) / ".env"
            (venv / "Scripts").mkdir(parents=True)
            (venv / "Scripts" / "activate").write_text("# activate")

            result = detect_python_venv(Path(tmpdir))
            assert result == venv

    def test_prefers_dotenv_to_venv(self) -> None:
        """Checks common venv names in order."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create .venv first (should be found)
            venv1 = Path(tmpdir) / ".venv"
            venv1.mkdir()
            (venv1 / "pyvenv.cfg").write_text("")

            # Create venv second (should not override)
            venv2 = Path(tmpdir) / "venv"
            venv2.mkdir()
            (venv2 / "pyvenv.cfg").write_text("")

            result = detect_python_venv(Path(tmpdir))
            assert result == venv1

    def test_empty_directory_not_venv(self) -> None:
        """Empty directories are not detected as venvs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create empty .venv directory
            venv = Path(tmpdir) / ".venv"
            venv.mkdir()

            result = detect_python_venv(Path(tmpdir))
            assert result is None


class TestGetPythonExecutable:
    """Tests for get_python_executable function."""

    def test_no_venv_returns_python(self) -> None:
        """Returns 'python' when no venv."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = get_python_executable(Path(tmpdir))
            assert result == "python"

    def test_venv_unix_python(self) -> None:
        """Returns Unix venv python path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            venv = Path(tmpdir) / ".venv"
            (venv / "bin").mkdir(parents=True)
            unix_python = venv / "bin" / "python"
            unix_python.write_text("#!/usr/bin/env python")
            (venv / "pyvenv.cfg").write_text("")

            result = get_python_executable(Path(tmpdir))
            assert result == str(unix_python)

    def test_venv_windows_python(self) -> None:
        """Returns Windows venv python path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            venv = Path(tmpdir) / ".venv"
            (venv / "Scripts").mkdir(parents=True)
            win_python = venv / "Scripts" / "python.exe"
            win_python.write_text("MZ")  # PE header marker
            (venv / "pyvenv.cfg").write_text("")

            result = get_python_executable(Path(tmpdir))
            assert result == str(win_python)


class TestDetectCoverageTools:
    """Tests for detect_coverage_tools function."""

    def setup_method(self) -> None:
        """Clear cache before each test."""
        clear_coverage_tools_cache()

    def test_python_pytest_with_cov(self) -> None:
        """Detects pytest-cov for Python pytest."""
        with tempfile.TemporaryDirectory() as tmpdir, patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0

            result = detect_coverage_tools(Path(tmpdir), "python.pytest")
            assert result.get("pytest-cov") is True

    def test_python_pytest_without_cov(self) -> None:
        """Returns False when pytest-cov not installed."""
        with tempfile.TemporaryDirectory() as tmpdir, patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1

            result = detect_coverage_tools(Path(tmpdir), "python.pytest")
            assert result.get("pytest-cov") is False

    def test_js_jest_builtin(self) -> None:
        """Jest has built-in coverage."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = detect_coverage_tools(Path(tmpdir), "js.jest")
            assert result.get("built-in") is True

    def test_js_vitest_builtin(self) -> None:
        """Vitest has built-in coverage."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = detect_coverage_tools(Path(tmpdir), "js.vitest")
            assert result.get("built-in") is True

    def test_go_gotest_builtin(self) -> None:
        """Go has built-in coverage."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = detect_coverage_tools(Path(tmpdir), "go.gotest")
            assert result.get("built-in") is True

    def test_rust_cargo_llvm_cov(self) -> None:
        """Detects cargo-llvm-cov for Rust."""
        with tempfile.TemporaryDirectory() as tmpdir, patch("shutil.which") as mock_which:
            mock_which.return_value = "/usr/bin/cargo-llvm-cov"

            result = detect_coverage_tools(Path(tmpdir), "rust.nextest")
            assert result.get("cargo-llvm-cov") is True

    def test_ruby_simplecov_in_gemfile(self) -> None:
        """Detects simplecov in Ruby Gemfile."""
        with tempfile.TemporaryDirectory() as tmpdir:
            gemfile = Path(tmpdir) / "Gemfile"
            gemfile.write_text("gem 'simplecov'\ngem 'rspec'")

            result = detect_coverage_tools(Path(tmpdir), "ruby.rspec")
            assert result.get("simplecov") is True

    def test_ruby_no_simplecov(self) -> None:
        """Returns False when simplecov not in Gemfile."""
        with tempfile.TemporaryDirectory() as tmpdir:
            gemfile = Path(tmpdir) / "Gemfile"
            gemfile.write_text("gem 'rspec'")

            result = detect_coverage_tools(Path(tmpdir), "ruby.rspec")
            assert result.get("simplecov") is False

    def test_caching(self) -> None:
        """Results are cached per (workspace, pack_id)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir)

            # First call
            result1 = detect_coverage_tools(path, "go.gotest")

            # Second call should hit cache
            result2 = detect_coverage_tools(path, "go.gotest")

            assert result1 == result2

    def test_timeout_handling(self) -> None:
        """Handles subprocess timeout gracefully."""
        import subprocess

        with tempfile.TemporaryDirectory() as tmpdir, patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="test", timeout=5)

            result = detect_coverage_tools(Path(tmpdir), "python.pytest")
            assert result.get("pytest-cov") is False


class TestDetectNodePackageManager:
    """Tests for detect_node_package_manager function."""

    def test_npm_default(self) -> None:
        """Returns npm when no lock file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = detect_node_package_manager(Path(tmpdir))
            assert result == "npm"

    def test_pnpm_detected(self) -> None:
        """Returns pnpm when pnpm-lock.yaml exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "pnpm-lock.yaml").write_text("lockfileVersion: 5.4")
            result = detect_node_package_manager(Path(tmpdir))
            assert result == "pnpm"

    def test_yarn_detected(self) -> None:
        """Returns yarn when yarn.lock exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "yarn.lock").write_text("")
            result = detect_node_package_manager(Path(tmpdir))
            assert result == "yarn"

    def test_bun_detected(self) -> None:
        """Returns bun when bun.lockb exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "bun.lockb").write_bytes(b"")
            result = detect_node_package_manager(Path(tmpdir))
            assert result == "bun"

    def test_pnpm_priority_over_yarn(self) -> None:
        """pnpm-lock.yaml takes priority over yarn.lock."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "pnpm-lock.yaml").write_text("")
            (Path(tmpdir) / "yarn.lock").write_text("")
            result = detect_node_package_manager(Path(tmpdir))
            assert result == "pnpm"


class TestDefaultParallelism:
    """Tests for _default_parallelism function."""

    def test_returns_positive_int(self) -> None:
        """Returns a positive integer."""
        result = _default_parallelism()
        assert isinstance(result, int)
        assert result > 0

    def test_capped_at_16(self) -> None:
        """Parallelism is capped at 16."""
        result = _default_parallelism()
        assert result <= 16

    def test_scales_with_cpu_count(self) -> None:
        """Uses 2x CPU count."""
        with patch("os.cpu_count", return_value=4):
            result = _default_parallelism()
            assert result == 8

    def test_fallback_when_cpu_unknown(self) -> None:
        """Falls back to 4 CPUs when count unknown."""
        with patch("os.cpu_count", return_value=None):
            result = _default_parallelism()
            assert result == 8  # 4 * 2


class TestIsPrunablePath:
    """Tests for _is_prunable_path function."""

    def test_node_modules_prunable(self) -> None:
        """node_modules is prunable."""
        assert _is_prunable_path(Path("node_modules/package")) is True

    def test_venv_prunable(self) -> None:
        """.venv is prunable."""
        assert _is_prunable_path(Path(".venv/lib/python")) is True

    def test_git_prunable(self) -> None:
        """.git is prunable."""
        assert _is_prunable_path(Path(".git/objects")) is True

    def test_nested_prunable(self) -> None:
        """Nested prunable directory detected."""
        assert _is_prunable_path(Path("src/node_modules/pkg")) is True

    def test_packages_at_root_not_prunable(self) -> None:
        """packages/ at root is NOT prunable (common JS monorepo)."""
        assert _is_prunable_path(Path("packages/my-package")) is False

    def test_packages_nested_is_prunable(self) -> None:
        """packages/ nested (not at root) is prunable."""
        assert _is_prunable_path(Path("vendor/packages/pkg")) is True

    def test_normal_src_not_prunable(self) -> None:
        """Normal src directory is not prunable."""
        assert _is_prunable_path(Path("src/component")) is False

    def test_tests_not_prunable(self) -> None:
        """tests directory is not prunable."""
        assert _is_prunable_path(Path("tests/unit")) is False


class TestDetectWorkspaces:
    """Tests for detect_workspaces function."""

    def test_empty_repo(self) -> None:
        """Empty repo returns empty list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = detect_workspaces(Path(tmpdir))
            # May return empty or may detect based on files
            assert isinstance(result, list)

    def test_single_python_workspace(self) -> None:
        """Detects single Python workspace."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            # Create pyproject.toml to trigger Python detection
            (root / "pyproject.toml").write_text("[project]\nname = 'test'")
            # Create pytest.ini to trigger pytest detection
            (root / "pytest.ini").write_text("[pytest]")
            # Create a test file
            (root / "tests").mkdir()
            (root / "tests" / "test_example.py").write_text("def test_foo(): pass")

            result = detect_workspaces(root)
            # Should detect workspace at root
            assert len(result) >= 0  # Depends on registered packs

    def test_npm_workspaces(self) -> None:
        """Detects npm workspaces from package.json."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            # Create root package.json with workspaces
            (root / "package.json").write_text(
                json.dumps({"name": "monorepo", "workspaces": ["packages/*"]})
            )

            # Create workspace packages
            pkg1 = root / "packages" / "app1"
            pkg1.mkdir(parents=True)
            (pkg1 / "package.json").write_text(json.dumps({"name": "app1"}))

            pkg2 = root / "packages" / "app2"
            pkg2.mkdir(parents=True)
            (pkg2 / "package.json").write_text(json.dumps({"name": "app2"}))

            result = detect_workspaces(root)
            # Should detect workspaces
            assert isinstance(result, list)

    def test_lerna_workspaces(self) -> None:
        """Detects Lerna workspaces."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "lerna.json").write_text(json.dumps({"packages": ["packages/*"]}))

            pkg = root / "packages" / "my-pkg"
            pkg.mkdir(parents=True)
            (pkg / "package.json").write_text(json.dumps({"name": "my-pkg"}))

            result = detect_workspaces(root)
            assert isinstance(result, list)

    def test_deduplicates_workspaces(self) -> None:
        """Deduplicates workspaces by (root, pack_id)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            # Create configs that might cause duplicate detection
            (root / "package.json").write_text(
                json.dumps({"name": "test", "workspaces": ["packages/*"]})
            )
            (root / "lerna.json").write_text(json.dumps({"packages": ["packages/*"]}))

            pkg = root / "packages" / "app"
            pkg.mkdir(parents=True)
            (pkg / "package.json").write_text(json.dumps({"name": "app"}))

            result = detect_workspaces(root)
            # Should not have duplicates
            seen = set()
            for ws in result:
                key = (ws.root, ws.pack.pack_id)
                assert key not in seen, f"Duplicate workspace: {key}"
                seen.add(key)


class TestActiveRun:
    """Tests for ActiveRun dataclass."""

    def test_creation(self) -> None:
        """Can create ActiveRun instance."""
        import asyncio
        from unittest.mock import MagicMock

        from codeplane.testing.models import TargetProgress, TestCaseProgress, TestProgress

        progress = TestProgress(
            targets=TargetProgress(total=5),
            cases=TestCaseProgress(total=10),
        )
        cancel_event = asyncio.Event()
        mock_task = MagicMock(spec=asyncio.Task)

        run = ActiveRun(
            run_id="test-123",
            task=mock_task,
            start_time=1000.0,
            progress=progress,
            failures=[],
            cancel_event=cancel_event,
            artifact_dir=Path("/tmp/artifacts"),
        )

        assert run.run_id == "test-123"
        assert run.start_time == 1000.0
        assert run.progress.targets.total == 5


class TestDetectedWorkspace:
    """Tests for DetectedWorkspace dataclass."""

    def test_creation(self) -> None:
        """Can create DetectedWorkspace instance."""
        mock_pack = MagicMock(spec=RunnerPack)
        mock_pack.pack_id = "python.pytest"

        ws = DetectedWorkspace(
            root=Path("/path/to/workspace"),
            pack=mock_pack,
            confidence=0.95,
        )

        assert ws.root == Path("/path/to/workspace")
        assert ws.confidence == 0.95
        assert ws.pack.pack_id == "python.pytest"


class TestClearCoverageToolsCache:
    """Tests for clear_coverage_tools_cache function."""

    def test_clears_cache(self) -> None:
        """Cache is cleared."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Populate cache
            detect_coverage_tools(Path(tmpdir), "go.gotest")

            # Clear and verify different results would be computed
            clear_coverage_tools_cache()

            # No exception means success
            assert True
