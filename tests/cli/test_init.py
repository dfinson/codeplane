"""Tests for cpl init command."""

from collections.abc import Generator
from pathlib import Path

import pygit2
import pytest
import yaml
from click.testing import CliRunner

from codeplane.cli.main import cli

runner = CliRunner()


@pytest.fixture
def temp_git_repo(tmp_path: Path) -> Generator[Path, None, None]:
    """Create a temporary git repository with initial commit."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    pygit2.init_repository(str(repo_path))

    # Configure and create initial commit (required for HEAD to exist)
    repo = pygit2.Repository(str(repo_path))
    repo.config["user.name"] = "Test"
    repo.config["user.email"] = "test@test.com"

    # Create a file and commit
    (repo_path / "README.md").write_text("# Test repo")
    repo.index.add("README.md")
    repo.index.write()
    tree = repo.index.write_tree()
    sig = pygit2.Signature("Test", "test@test.com")
    repo.create_commit("HEAD", sig, sig, "Initial commit", tree, [])

    yield repo_path


@pytest.fixture
def temp_non_git(tmp_path: Path) -> Generator[Path, None, None]:
    """Create a temporary non-git directory."""
    non_git = tmp_path / "not-a-repo"
    non_git.mkdir()
    yield non_git


class TestInitCommand:
    """cpl init command tests."""

    def test_given_git_repo_when_init_then_creates_codeplane_dir(self, temp_git_repo: Path) -> None:
        """Init creates .codeplane/ directory with config."""
        # Given
        repo = temp_git_repo

        # When
        result = runner.invoke(cli, ["init", str(repo)])

        # Then
        assert result.exit_code == 0
        assert (repo / ".codeplane").is_dir()
        assert (repo / ".codeplane" / "config.yaml").exists()

    def test_given_git_repo_when_init_then_creates_ignore_file(self, temp_git_repo: Path) -> None:
        """Init creates .codeplane/.cplignore with default patterns."""
        # Given
        repo = temp_git_repo

        # When
        runner.invoke(cli, ["init", str(repo)])

        # Then
        cplignore_path = repo / ".codeplane" / ".cplignore"
        assert cplignore_path.exists()
        content = cplignore_path.read_text()
        assert "node_modules/" in content
        assert ".env" in content

    def test_given_git_repo_when_init_then_config_is_valid_yaml(self, temp_git_repo: Path) -> None:
        """Init creates valid YAML config with simplified user fields."""
        # Given
        repo = temp_git_repo

        # When
        runner.invoke(cli, ["init", str(repo)])

        # Then - new simplified config format
        config_path = repo / ".codeplane" / "config.yaml"
        with config_path.open() as f:
            config = yaml.safe_load(f)
        # New format has root-level fields, not nested
        assert "port" in config

    def test_given_git_repo_when_init_then_creates_state_file(self, temp_git_repo: Path) -> None:
        """Init creates state.yaml with index_path."""
        # Given
        repo = temp_git_repo

        # When
        runner.invoke(cli, ["init", str(repo)])

        # Then - state.yaml should exist with index_path
        state_path = repo / ".codeplane" / "state.yaml"
        assert state_path.exists()
        with state_path.open() as f:
            state = yaml.safe_load(f)
        assert "index_path" in state

    def test_given_non_git_dir_when_init_then_fails(self, temp_non_git: Path) -> None:
        """Init fails with error when run outside git repository."""
        # Given
        non_git_dir = temp_non_git

        # When
        result = runner.invoke(cli, ["init", str(non_git_dir)])

        # Then
        assert result.exit_code == 1
        assert "Not inside a git repository" in result.output
        assert "CodePlane commands must be run from within a git repository" in result.output

    def test_given_initialized_repo_when_init_again_then_idempotent(
        self, temp_git_repo: Path
    ) -> None:
        """Init without --force is idempotent on already initialized repo."""
        # Given
        repo = temp_git_repo
        runner.invoke(cli, ["init", str(repo)])

        # When
        result = runner.invoke(cli, ["init", str(repo)])

        # Then
        assert result.exit_code == 0
        assert "Already initialized" in result.output

    def test_given_initialized_repo_when_init_force_then_reinitializes(
        self, temp_git_repo: Path
    ) -> None:
        """Init with --force overwrites existing config."""
        # Given
        repo = temp_git_repo
        runner.invoke(cli, ["init", str(repo)])
        config_path = repo / ".codeplane" / "config.yaml"
        config_path.write_text("custom: true")

        # When
        result = runner.invoke(cli, ["init", "--force", str(repo)])

        # Then
        assert result.exit_code == 0
        assert "Initializing CodePlane" in result.output
        with config_path.open() as f:
            config = yaml.safe_load(f)
        assert "custom" not in config
