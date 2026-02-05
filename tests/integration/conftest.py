"""Shared fixtures for integration tests."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pygit2
import pytest

from codeplane.templates import get_cplignore_template


@pytest.fixture
def integration_repo(tmp_path: Path) -> Generator[Path, None, None]:
    """Create a fully initialized git repository for integration tests.

    Includes:
    - Git repository with initial commit
    - Python source files with proper structure
    - .codeplane directory (simulates cpl init)
    - Tests directory with basic tests
    """
    repo_path = tmp_path / "integration_repo"
    repo_path.mkdir()
    pygit2.init_repository(str(repo_path))

    repo = pygit2.Repository(str(repo_path))
    repo.config["user.name"] = "Integration Test"
    repo.config["user.email"] = "integration@test.com"

    # Create Python package structure
    (repo_path / "src").mkdir()
    (repo_path / "src" / "__init__.py").write_text("")
    (repo_path / "src" / "main.py").write_text('''"""Main module."""


def greet(name: str) -> str:
    """Greet someone by name."""
    return f"Hello, {name}!"


def main() -> None:
    """Entry point."""
    print(greet("World"))


if __name__ == "__main__":
    main()
''')
    (repo_path / "src" / "utils.py").write_text('''"""Utility functions."""


def helper(x: int) -> int:
    """Helper function that adds one."""
    return x + 1


def another_helper(s: str) -> str:
    """Another helper that uppercases."""
    return s.upper()


class Calculator:
    """Simple calculator class."""

    def __init__(self, initial: int = 0) -> None:
        self.value = initial

    def add(self, x: int) -> int:
        """Add to current value."""
        self.value += x
        return self.value

    def subtract(self, x: int) -> int:
        """Subtract from current value."""
        self.value -= x
        return self.value
''')

    # Create pyproject.toml with ruff config
    (repo_path / "pyproject.toml").write_text("""[project]
name = "test-project"
version = "0.1.0"

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]

[tool.ruff]
line-length = 100
""")

    # Create .codeplane directory (simulating cpl init)
    codeplane_dir = repo_path / ".codeplane"
    codeplane_dir.mkdir()
    (codeplane_dir / ".cplignore").write_text(get_cplignore_template())

    # Create tests directory
    (repo_path / "tests").mkdir()
    (repo_path / "tests" / "__init__.py").write_text("")
    (repo_path / "tests" / "test_main.py").write_text('''"""Tests for main module."""

from src.main import greet


def test_greet() -> None:
    """Test greet function."""
    assert greet("World") == "Hello, World!"


def test_greet_with_name() -> None:
    """Test greet with custom name."""
    assert greet("Alice") == "Hello, Alice!"
''')
    (repo_path / "tests" / "test_utils.py").write_text('''"""Tests for utility functions."""

from src.utils import Calculator, another_helper, helper


def test_helper() -> None:
    """Test helper function."""
    assert helper(1) == 2
    assert helper(0) == 1


def test_another_helper() -> None:
    """Test another_helper function."""
    assert another_helper("hello") == "HELLO"


class TestCalculator:
    """Tests for Calculator class."""

    def test_initial_value(self) -> None:
        """Test initial value."""
        calc = Calculator(10)
        assert calc.value == 10

    def test_add(self) -> None:
        """Test add method."""
        calc = Calculator(0)
        assert calc.add(5) == 5
        assert calc.add(3) == 8

    def test_subtract(self) -> None:
        """Test subtract method."""
        calc = Calculator(10)
        assert calc.subtract(3) == 7
''')

    # Commit everything
    repo.index.add_all()
    repo.index.write()
    tree = repo.index.write_tree()
    sig = pygit2.Signature("Integration Test", "integration@test.com")
    repo.create_commit("HEAD", sig, sig, "Initial structure", tree, [])

    yield repo_path


@pytest.fixture
def integration_repo_with_errors(tmp_path: Path) -> Generator[Path, None, None]:
    """Create a repo with intentional errors for testing error handling."""
    repo_path = tmp_path / "error_repo"
    repo_path.mkdir()
    pygit2.init_repository(str(repo_path))

    repo = pygit2.Repository(str(repo_path))
    repo.config["user.name"] = "Error Test"
    repo.config["user.email"] = "error@test.com"

    # Create file with syntax error
    (repo_path / "broken.py").write_text('''"""Broken module."""

def broken_function(
    # Missing closing paren and body
''')

    # Create file with lint issues
    (repo_path / "lint_issues.py").write_text("""import os
import sys  # unused import

def bad_function(x,y):
    unused_var = 1
    return x+y
""")

    # Create tests that will fail
    (repo_path / "tests").mkdir()
    (repo_path / "tests" / "test_failing.py").write_text('''"""Tests that will fail."""


def test_will_fail() -> None:
    """This test always fails."""
    assert False, "Expected failure"


def test_will_pass() -> None:
    """This test passes."""
    assert True
''')

    # Create pyproject.toml
    (repo_path / "pyproject.toml").write_text("""[project]
name = "error-project"
version = "0.1.0"

[tool.pytest.ini_options]
testpaths = ["tests"]
""")

    # Create .codeplane directory
    codeplane_dir = repo_path / ".codeplane"
    codeplane_dir.mkdir()
    (codeplane_dir / ".cplignore").write_text(get_cplignore_template())

    # Commit
    repo.index.add_all()
    repo.index.write()
    tree = repo.index.write_tree()
    sig = pygit2.Signature("Error Test", "error@test.com")
    repo.create_commit("HEAD", sig, sig, "Initial with errors", tree, [])

    yield repo_path
