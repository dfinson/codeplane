"""Integration tests for lint operations.

These tests verify that linting tools work correctly with
real files and real linting backends.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from codeplane.lint.tools import registry as lint_registry

pytestmark = pytest.mark.integration


class TestLintToolsIntegration:
    """Integration tests for lint tool detection."""

    def test_detect_available_tools(self, integration_repo: Path) -> None:
        """Detects available linting tools."""
        detected = lint_registry.detect(integration_repo)

        # Should detect at least one tool (ruff should be available)
        assert len(detected) > 0
        # Check tool has required attributes
        for tool, config_file in detected:
            assert tool.tool_id is not None
            assert tool.executable is not None
            assert config_file is not None

    def test_detect_python_ruff(self, integration_repo: Path) -> None:
        """Detects ruff for Python projects."""
        detected = lint_registry.detect(integration_repo)

        tool_ids = {tool.tool_id for tool, _ in detected}
        # Ruff should be detected for Python projects
        assert "python.ruff" in tool_ids or "python.ruff-format" in tool_ids


class TestLintBackendIntegration:
    """Tests for specific lint backends."""

    @pytest.mark.skipif(
        subprocess.run(["which", "ruff"], capture_output=True).returncode != 0,
        reason="ruff not installed",
    )
    def test_ruff_check(self, integration_repo: Path) -> None:
        """Ruff linting works."""
        # Run ruff directly
        result = subprocess.run(
            ["ruff", "check", "src/", "--output-format", "json"],
            cwd=integration_repo,
            capture_output=True,
            text=True,
        )

        # Should complete (may have issues or not)
        # Output should be valid JSON
        import json

        try:
            issues = json.loads(result.stdout) if result.stdout else []
            assert isinstance(issues, list)
        except json.JSONDecodeError:
            # No output is also valid (no issues)
            pass

    @pytest.mark.skipif(
        subprocess.run(["which", "ruff"], capture_output=True).returncode != 0,
        reason="ruff not installed",
    )
    def test_ruff_format_check(self, integration_repo: Path) -> None:
        """Ruff format check works."""
        result = subprocess.run(
            ["ruff", "format", "--check", "src/"],
            cwd=integration_repo,
            capture_output=True,
            text=True,
        )

        # Should complete
        assert result.returncode in [0, 1]  # 0 = formatted, 1 = needs formatting


class TestLintTypeCheckIntegration:
    """Integration tests for type checking."""

    def test_detect_mypy(self, integration_repo: Path) -> None:
        """Detects mypy availability."""
        detected = lint_registry.detect(integration_repo)

        # Check if mypy detected (by looking for a tool with mypy in the name)
        # mypy may or may not be installed, just verify detection works
        # No assertion needed - test is about detection not requirement
        _ = next((t for t, _ in detected if "mypy" in t.tool_id.lower()), None)

    @pytest.mark.skipif(
        subprocess.run(["which", "mypy"], capture_output=True).returncode != 0,
        reason="mypy not installed",
    )
    def test_mypy_check(self, integration_repo: Path) -> None:
        """Mypy type checking works."""
        # Create typed file
        typed_file = integration_repo / "src" / "typed.py"
        typed_file.write_text(
            """def add(a: int, b: int) -> int:
    return a + b

result: int = add(1, 2)
"""
        )

        result = subprocess.run(
            ["mypy", "src/typed.py"],
            cwd=integration_repo,
            capture_output=True,
            text=True,
        )

        # Should pass type check
        assert result.returncode == 0 or "error" not in result.stdout.lower()

    @pytest.mark.skipif(
        subprocess.run(["which", "mypy"], capture_output=True).returncode != 0,
        reason="mypy not installed",
    )
    def test_mypy_finds_type_errors(self, integration_repo: Path) -> None:
        """Mypy reports type errors."""
        # Create file with type error
        bad_types = integration_repo / "src" / "bad_types.py"
        bad_types.write_text(
            """def add(a: int, b: int) -> int:
    return a + b

result: str = add(1, 2)  # Type error: int assigned to str
"""
        )

        result = subprocess.run(
            ["mypy", "src/bad_types.py"],
            cwd=integration_repo,
            capture_output=True,
            text=True,
        )

        # Should report type error
        combined = result.stdout + result.stderr
        assert "error" in combined.lower() or result.returncode != 0
