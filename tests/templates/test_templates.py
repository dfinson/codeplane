"""Tests for templates module.

Tests the template generation utilities.
"""

from __future__ import annotations

from codeplane.templates import get_cplignore_template


class TestGetCplignoreTemplate:
    """Tests for get_cplignore_template function."""

    def test_returns_string(self) -> None:
        """get_cplignore_template returns a string."""
        template = get_cplignore_template()
        assert isinstance(template, str)

    def test_non_empty(self) -> None:
        """Template is non-empty."""
        template = get_cplignore_template()
        assert len(template) > 0

    def test_contains_common_excludes(self) -> None:
        """Template contains common exclude patterns."""
        template = get_cplignore_template()

        # Should include common IDE/editor directories
        common_patterns = [".git", "node_modules", "__pycache__"]
        for pattern in common_patterns:
            assert pattern in template, f"Expected {pattern} in template"

    def test_has_venv_patterns(self) -> None:
        """Template includes Python virtual environment patterns."""
        template = get_cplignore_template()
        # At least one venv-related pattern
        venv_patterns = [".venv", "venv", "env"]
        has_venv = any(p in template for p in venv_patterns)
        assert has_venv, "Expected virtual environment patterns in template"

    def test_has_build_output_patterns(self) -> None:
        """Template includes build output patterns."""
        template = get_cplignore_template()
        # Common build directories
        build_patterns = ["build", "dist", "target"]
        has_build = any(p in template for p in build_patterns)
        assert has_build, "Expected build output patterns in template"

    def test_idempotent(self) -> None:
        """Multiple calls return same template."""
        template1 = get_cplignore_template()
        template2 = get_cplignore_template()
        assert template1 == template2

    def test_has_valid_gitignore_format(self) -> None:
        """Template follows gitignore format conventions."""
        template = get_cplignore_template()

        # Should have newlines (multiple patterns)
        assert "\n" in template

        # Lines should be either comments (#), empty, or patterns
        for line in template.split("\n"):
            line = line.strip()
            if line:
                # Either a comment or a pattern
                is_comment = line.startswith("#")
                is_pattern = not line.startswith("#")
                assert is_comment or is_pattern
