"""Tests for core/excludes.py module.

Covers:
- PRUNABLE_DIRS frozenset
- UNIVERSAL_EXCLUDE_GLOBS tuple
- generate_cplignore_template() function
"""

from __future__ import annotations

from codeplane.core.excludes import (
    PRUNABLE_DIRS,
    UNIVERSAL_EXCLUDE_GLOBS,
    generate_cplignore_template,
)


class TestPrunableDirs:
    """Tests for PRUNABLE_DIRS constant."""

    def test_is_frozenset(self) -> None:
        """PRUNABLE_DIRS is a frozenset."""
        assert isinstance(PRUNABLE_DIRS, frozenset)

    def test_contains_vcs_directories(self) -> None:
        """Contains version control directories."""
        assert ".git" in PRUNABLE_DIRS
        assert ".svn" in PRUNABLE_DIRS
        assert ".hg" in PRUNABLE_DIRS

    def test_contains_node_modules(self) -> None:
        """Contains node_modules."""
        assert "node_modules" in PRUNABLE_DIRS

    def test_contains_build_directories(self) -> None:
        """Contains common build directories."""
        assert "dist" in PRUNABLE_DIRS
        assert "build" in PRUNABLE_DIRS

    def test_contains_python_cache(self) -> None:
        """Contains Python cache directories."""
        assert "__pycache__" in PRUNABLE_DIRS
        assert ".pytest_cache" in PRUNABLE_DIRS
        assert ".mypy_cache" in PRUNABLE_DIRS

    def test_contains_virtual_envs(self) -> None:
        """Contains virtual environment directories."""
        assert ".venv" in PRUNABLE_DIRS
        assert "venv" in PRUNABLE_DIRS
        assert ".env" in PRUNABLE_DIRS

    def test_contains_coverage(self) -> None:
        """Contains coverage directories."""
        assert "htmlcov" in PRUNABLE_DIRS

    def test_all_lowercase(self) -> None:
        """All entries are lowercase for consistent matching."""
        for entry in PRUNABLE_DIRS:
            assert entry == entry.lower()

    def test_no_empty_strings(self) -> None:
        """No empty strings in the set."""
        assert "" not in PRUNABLE_DIRS


class TestUniversalExcludeGlobs:
    """Tests for UNIVERSAL_EXCLUDE_GLOBS constant."""

    def test_is_tuple(self) -> None:
        """UNIVERSAL_EXCLUDE_GLOBS is a tuple."""
        assert isinstance(UNIVERSAL_EXCLUDE_GLOBS, tuple)

    def test_not_empty(self) -> None:
        """Contains at least one glob."""
        assert len(UNIVERSAL_EXCLUDE_GLOBS) > 0

    def test_contains_vcs_globs(self) -> None:
        """Contains VCS exclusion globs."""
        globs_str = " ".join(UNIVERSAL_EXCLUDE_GLOBS)
        assert ".git" in globs_str or "**/.git/**" in UNIVERSAL_EXCLUDE_GLOBS

    def test_all_strings(self) -> None:
        """All entries are strings."""
        for glob in UNIVERSAL_EXCLUDE_GLOBS:
            assert isinstance(glob, str)

    def test_no_empty_strings(self) -> None:
        """No empty strings in the tuple."""
        assert "" not in UNIVERSAL_EXCLUDE_GLOBS


class TestGenerateCplignoreTemplate:
    """Tests for generate_cplignore_template function."""

    def test_returns_string(self) -> None:
        """Returns a string."""
        result = generate_cplignore_template()
        assert isinstance(result, str)

    def test_not_empty(self) -> None:
        """Returns non-empty content."""
        result = generate_cplignore_template()
        assert len(result) > 0

    def test_starts_with_comment(self) -> None:
        """Template starts with a comment header."""
        result = generate_cplignore_template()
        assert result.startswith("#")

    def test_contains_prunable_dirs(self) -> None:
        """Template includes prunable directories."""
        result = generate_cplignore_template()
        # At least some prunable dirs should appear
        assert "node_modules" in result
        assert "__pycache__" in result
        assert ".git" in result

    def test_contains_section_headers(self) -> None:
        """Template contains section headers."""
        result = generate_cplignore_template()
        # Should have organized sections
        lines = result.split("\n")
        comment_lines = [line for line in lines if line.startswith("#")]
        assert len(comment_lines) > 1  # More than just the opening comment

    def test_has_newline_ending(self) -> None:
        """Template ends with newline."""
        result = generate_cplignore_template()
        assert result.endswith("\n")

    def test_no_trailing_whitespace(self) -> None:
        """Lines don't have trailing whitespace."""
        result = generate_cplignore_template()
        for i, line in enumerate(result.split("\n"), 1):
            if line:  # Skip empty lines
                assert line == line.rstrip(), f"Line {i} has trailing whitespace"

    def test_patterns_are_valid_gitignore_syntax(self) -> None:
        """All non-comment lines are valid gitignore patterns."""
        result = generate_cplignore_template()
        for line in result.split("\n"):
            if line and not line.startswith("#"):
                # Valid gitignore patterns don't start with spaces
                assert not line.startswith(" "), f"Invalid pattern: {line}"
                # Should be a reasonable length
                assert len(line) < 200, f"Pattern too long: {line}"

    def test_consistent_calls(self) -> None:
        """Multiple calls return the same result."""
        result1 = generate_cplignore_template()
        result2 = generate_cplignore_template()
        assert result1 == result2


class TestPrunableDirsAndGlobsRelationship:
    """Tests for relationship between PRUNABLE_DIRS and UNIVERSAL_EXCLUDE_GLOBS."""

    def test_prunable_dirs_appear_in_globs(self) -> None:
        """Prunable directories should be represented in exclude globs."""
        globs_str = " ".join(UNIVERSAL_EXCLUDE_GLOBS)
        # Key directories should appear
        assert "node_modules" in globs_str
        assert "__pycache__" in globs_str

    def test_globs_cover_nested_directories(self) -> None:
        """Globs should use ** pattern for nested exclusion."""
        has_recursive = any("**" in glob for glob in UNIVERSAL_EXCLUDE_GLOBS)
        assert has_recursive, "Should have recursive patterns"
