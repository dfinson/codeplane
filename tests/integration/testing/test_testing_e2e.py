"""Integration tests for testing framework operations.

These tests verify that the testing module can discover and run
real tests in real project structures.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from codeplane.testing.ops import detect_workspaces, get_python_executable
from codeplane.testing.runner_pack import runner_registry

pytestmark = pytest.mark.integration


class TestWorkspaceDetectionIntegration:
    """Integration tests for workspace detection."""

    def test_detect_python_workspace(self, integration_repo: Path) -> None:
        """Detects Python workspace with pytest."""
        workspaces = detect_workspaces(integration_repo)

        # Should find at least one workspace
        assert len(workspaces) > 0

        # Should detect pytest runner
        pack_ids = {ws.pack.pack_id for ws in workspaces}
        assert "python.pytest" in pack_ids

    def test_detect_workspace_root(self, integration_repo: Path) -> None:
        """Workspace detection finds correct root."""
        workspaces = detect_workspaces(integration_repo)

        # At least one workspace should be at repo root
        roots = {ws.root for ws in workspaces}
        assert integration_repo in roots


class TestRunnerPackIntegration:
    """Integration tests for runner pack discovery."""

    def test_pytest_pack_discovers_tests(self, integration_repo: Path) -> None:
        """Pytest runner pack discovers test files."""
        # Get pytest pack
        pack_class = runner_registry.get("python.pytest")
        assert pack_class is not None

        pack = pack_class()

        # Discover should find our test file
        import asyncio

        targets = asyncio.run(pack.discover(integration_repo))

        # Should find test_main.py
        selectors = {t.selector for t in targets}
        assert any("test_main.py" in s for s in selectors)

    def test_pytest_pack_builds_command(self, integration_repo: Path) -> None:
        """Pytest runner pack builds valid command."""
        pack_class = runner_registry.get("python.pytest")
        assert pack_class is not None

        pack = pack_class()

        # Create a target
        from codeplane.testing.models import TestTarget

        target = TestTarget(
            target_id="test::tests/test_main.py",
            selector="tests/test_main.py",
            kind="file",
            language="python",
            runner_pack_id="python.pytest",
            workspace_root=str(integration_repo),
        )

        # Build command
        cmd = pack.build_command(target, output_path=integration_repo / "output.xml")

        assert cmd is not None
        assert any("pytest" in c for c in cmd)
        assert "tests/test_main.py" in cmd


class TestPythonRuntimeIntegration:
    """Integration tests for Python runtime detection."""

    def test_get_python_executable(self, integration_repo: Path) -> None:
        """Gets Python executable path."""
        python_exe = get_python_executable(integration_repo)

        assert python_exe is not None
        assert "python" in python_exe.lower()


class TestPytestExecutionIntegration:
    """Integration tests for actual pytest execution.

    Note: These tests actually run pytest in subprocess.
    """

    def test_run_pytest_passing(self, integration_repo: Path) -> None:
        """Runs pytest on passing tests."""
        result = subprocess.run(
            ["python", "-m", "pytest", "tests/test_main.py", "-v"],
            cwd=integration_repo,
            capture_output=True,
            text=True,
        )

        # Tests should pass
        assert result.returncode == 0
        assert "passed" in result.stdout or "passed" in result.stderr

    def test_run_pytest_with_markers(self, integration_repo: Path) -> None:
        """Pytest respects markers."""
        # Create a test with a marker
        marked_test = integration_repo / "tests" / "test_marked.py"
        marked_test.write_text(
            """import pytest

@pytest.mark.slow
def test_slow_operation():
    assert True

def test_fast_operation():
    assert True
"""
        )

        # Run only unmarked tests
        result = subprocess.run(
            ["python", "-m", "pytest", "tests/test_marked.py", "-m", "not slow", "-v"],
            cwd=integration_repo,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        # The fast test should run (passed)
        assert "1 passed" in result.stdout
        # The slow test should be deselected
        assert "deselected" in result.stdout

    def test_run_pytest_captures_failures(self, integration_repo: Path) -> None:
        """Pytest output captures failure details."""
        # Create a failing test
        failing_test = integration_repo / "tests" / "test_fail.py"
        failing_test.write_text(
            """def test_will_fail():
    assert 1 == 2, "Numbers should be equal"
"""
        )

        result = subprocess.run(
            ["python", "-m", "pytest", "tests/test_fail.py", "-v"],
            cwd=integration_repo,
            capture_output=True,
            text=True,
        )

        assert result.returncode != 0
        # Should have failure info
        combined = result.stdout + result.stderr
        assert "FAILED" in combined or "AssertionError" in combined
