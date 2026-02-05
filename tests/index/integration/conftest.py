"""Shared fixtures for integration tests."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pygit2
import pytest


@pytest.fixture
def integration_repo(tmp_path: Path) -> Generator[Path, None, None]:
    """Create a fully initialized git repository for integration tests."""
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

def main():
    """Entry point."""
    print("Hello, World!")


if __name__ == "__main__":
    main()
''')
    (repo_path / "src" / "utils.py").write_text('''"""Utility functions."""

def helper(x: int) -> int:
    """Helper function."""
    return x + 1


def another_helper(s: str) -> str:
    """Another helper."""
    return s.upper()
''')

    # Create pyproject.toml
    (repo_path / "pyproject.toml").write_text("""[project]
name = "test-project"
version = "0.1.0"

[tool.pytest.ini_options]
testpaths = ["tests"]
""")

    # Create .codeplane/.cplignore (simulating cpl init)
    codeplane_dir = repo_path / ".codeplane"
    codeplane_dir.mkdir()
    from codeplane.templates import get_cplignore_template

    (codeplane_dir / ".cplignore").write_text(get_cplignore_template())

    # Create tests directory
    (repo_path / "tests").mkdir()
    (repo_path / "tests" / "__init__.py").write_text("")
    (repo_path / "tests" / "test_main.py").write_text('''"""Tests for main module."""

from src.main import main


def test_main():
    """Test main function."""
    main()
''')

    # Commit everything
    repo.index.add_all()
    repo.index.write()
    tree = repo.index.write_tree()
    sig = pygit2.Signature("Integration Test", "integration@test.com")
    repo.create_commit("HEAD", sig, sig, "Initial structure", tree, [])

    yield repo_path


@pytest.fixture
def integration_monorepo(tmp_path: Path) -> Generator[Path, None, None]:
    """Create a monorepo structure for integration tests."""
    repo_path = tmp_path / "monorepo"
    repo_path.mkdir()
    pygit2.init_repository(str(repo_path))

    repo = pygit2.Repository(str(repo_path))
    repo.config["user.name"] = "Monorepo Test"
    repo.config["user.email"] = "monorepo@test.com"

    # Create pnpm-workspace.yaml
    (repo_path / "pnpm-workspace.yaml").write_text("""packages:
  - 'pkgs/*'
""")

    # Create .codeplane/.cplignore (simulating cpl init)
    codeplane_dir = repo_path / ".codeplane"
    codeplane_dir.mkdir()
    from codeplane.templates import get_cplignore_template

    (codeplane_dir / ".cplignore").write_text(get_cplignore_template())

    # Create root package.json
    (repo_path / "package.json").write_text("""{
  "name": "monorepo",
  "private": true,
  "workspaces": ["pkgs/*"]
}
""")

    # Create package A
    pkg_a = repo_path / "pkgs" / "pkg-a"
    pkg_a.mkdir(parents=True)
    (pkg_a / "package.json").write_text("""{
  "name": "@monorepo/pkg-a",
  "version": "1.0.0"
}
""")
    (pkg_a / "index.js").write_text("""// Package A
function hello() {
    return "Hello from A";
}

module.exports = { hello };
""")

    # Create package B
    pkg_b = repo_path / "pkgs" / "pkg-b"
    pkg_b.mkdir(parents=True)
    (pkg_b / "package.json").write_text("""{
  "name": "@monorepo/pkg-b",
  "version": "1.0.0",
  "dependencies": {
    "@monorepo/pkg-a": "workspace:*"
  }
}
""")
    (pkg_b / "index.js").write_text("""// Package B
const { hello } = require("@monorepo/pkg-a");

function greet(name) {
    return `${hello()}, ${name}!`;
}

module.exports = { greet };
""")

    # Commit everything
    repo.index.add_all()
    repo.index.write()
    tree = repo.index.write_tree()
    sig = pygit2.Signature("Monorepo Test", "monorepo@test.com")
    repo.create_commit("HEAD", sig, sig, "Initial monorepo", tree, [])

    yield repo_path
