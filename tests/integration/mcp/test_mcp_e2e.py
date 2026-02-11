"""Integration tests for MCP tools against real repositories.

These tests verify that MCP tools work end-to-end with real filesystem
operations, real git repositories, and real index databases.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from codeplane.files.ops import FileOps
from codeplane.git.ops import GitOps
from codeplane.mutation.ops import Edit, MutationOps

if TYPE_CHECKING:
    pass

pytestmark = pytest.mark.integration


class TestMCPFilesIntegration:
    """Integration tests for MCP file operations."""

    def test_read_files_real_filesystem(self, integration_repo: Path) -> None:
        """MCP read_files reads actual files from disk."""
        file_ops = FileOps(integration_repo)
        result = file_ops.read_files(["src/main.py"])

        assert len(result.files) == 1
        assert result.files[0].path == "src/main.py"
        assert "def greet" in result.files[0].content
        assert result.files[0].language == "python"

    def test_read_multiple_files(self, integration_repo: Path) -> None:
        """MCP read_files can read multiple files at once."""
        file_ops = FileOps(integration_repo)
        result = file_ops.read_files(["src/main.py", "src/utils.py"])

        assert len(result.files) == 2
        paths = {f.path for f in result.files}
        assert paths == {"src/main.py", "src/utils.py"}

    def test_read_files_with_line_range(self, integration_repo: Path) -> None:
        """MCP read_files can read specific line ranges."""
        file_ops = FileOps(integration_repo)
        result = file_ops.read_files(
            ["src/main.py"],
            targets={"src/main.py": (1, 5)},
        )

        assert len(result.files) == 1
        # Should only have first 5 lines
        lines = result.files[0].content.strip().split("\n")
        assert len(lines) <= 5

    def test_list_files_real_filesystem(self, integration_repo: Path) -> None:
        """MCP list_files lists actual directory contents."""
        file_ops = FileOps(integration_repo)
        result = file_ops.list_files("src")

        assert result.path == "src"
        names = {e.name for e in result.entries}
        assert "main.py" in names
        assert "utils.py" in names
        assert "__init__.py" in names

    def test_list_files_recursive(self, integration_repo: Path) -> None:
        """MCP list_files can list recursively."""
        file_ops = FileOps(integration_repo)
        result = file_ops.list_files("", recursive=True, pattern="*.py")

        paths = {e.path for e in result.entries}
        assert "src/main.py" in paths
        assert "tests/test_main.py" in paths

    def test_list_files_with_metadata(self, integration_repo: Path) -> None:
        """MCP list_files can include file metadata."""
        file_ops = FileOps(integration_repo)
        result = file_ops.list_files("src", include_metadata=True)

        # Files should have size info (may be 0 for __init__.py)
        for entry in result.entries:
            if entry.type == "file":
                assert entry.size is not None


class TestMCPGitIntegration:
    """Integration tests for MCP git operations."""

    def test_git_status_clean_repo(self, integration_repo: Path) -> None:
        """MCP git_status reports clean repo correctly."""
        git_ops = GitOps(integration_repo)

        status = git_ops.status()

        # Clean repo has empty status dict
        assert len(status) == 0
        # Can get current branch
        branch = git_ops.current_branch()
        assert branch in ("master", "main")

    def test_git_status_with_changes(self, integration_repo: Path) -> None:
        """MCP git_status detects file changes."""
        # Make a change
        (integration_repo / "src" / "new_file.py").write_text("# new file\n")

        git_ops = GitOps(integration_repo)
        status = git_ops.status()

        # Repo is not clean
        assert len(status) > 0
        # New file should be in status
        assert any("new_file.py" in path for path in status)

    def test_git_diff_working_tree(self, integration_repo: Path) -> None:
        """MCP git_diff shows working tree changes."""
        # Modify existing file
        main_py = integration_repo / "src" / "main.py"
        content = main_py.read_text()
        main_py.write_text(content + "\n# Added comment\n")

        git_ops = GitOps(integration_repo)
        diff = git_ops.diff()

        assert diff.files_changed > 0
        assert diff.total_additions > 0

    def test_git_commit_workflow(self, integration_repo: Path) -> None:
        """MCP git operations support full commit workflow."""
        git_ops = GitOps(integration_repo)

        # Create new file
        new_file = integration_repo / "src" / "feature.py"
        new_file.write_text('"""New feature."""\n\ndef feature():\n    pass\n')

        # Stage file
        git_ops.stage(["src/feature.py"])

        # Verify staged via diff
        staged_diff = git_ops.diff(staged=True)
        assert staged_diff.files_changed > 0

        # Commit
        sha = git_ops.commit("Add feature module")

        assert sha is not None
        assert len(sha) == 40

        # Verify clean after commit
        status = git_ops.status()
        assert len(status) == 0

    def test_git_log(self, integration_repo: Path) -> None:
        """MCP git_log returns commit history."""
        git_ops = GitOps(integration_repo)

        commits = git_ops.log(limit=10)

        assert len(commits) >= 1
        # First commit should be our initial
        assert "Initial" in commits[0].message

    def test_git_branch_operations(self, integration_repo: Path) -> None:
        """MCP git branch operations work."""
        git_ops = GitOps(integration_repo)

        # List branches
        branches = git_ops.branches(include_remote=False)
        assert len(branches) >= 1

        # Create branch
        new_branch = git_ops.create_branch("feature-test")
        # Branch name may include full ref path
        assert "feature-test" in new_branch.name

        # Switch to branch
        git_ops.checkout("feature-test")
        assert git_ops.current_branch() == "feature-test"

        # Switch back
        git_ops.checkout("master")
        assert git_ops.current_branch() == "master"


class TestMCPMutationIntegration:
    """Integration tests for MCP mutation operations."""

    def test_atomic_edit_creates_file(self, integration_repo: Path) -> None:
        """MCP write_files creates new files."""
        mutation_ops = MutationOps(integration_repo)

        result = mutation_ops.write_files(
            edits=[
                Edit(
                    path="src/new_module.py",
                    action="create",
                    content='"""New module."""\n\ndef new_func():\n    return 42\n',
                )
            ],
        )

        assert result.applied
        assert (integration_repo / "src" / "new_module.py").exists()
        assert "def new_func" in (integration_repo / "src" / "new_module.py").read_text()

    def test_atomic_edit_updates_file(self, integration_repo: Path) -> None:
        """MCP write_files updates existing files with content-addressed edits."""
        mutation_ops = MutationOps(integration_repo)

        result = mutation_ops.write_files(
            edits=[
                Edit(
                    path="src/main.py",
                    action="update",
                    old_content="def greet(name: str) -> str:",
                    new_content='def greet(name: str, greeting: str = "Hello") -> str:',
                )
            ],
        )

        assert result.applied
        updated = (integration_repo / "src" / "main.py").read_text()
        assert 'greeting: str = "Hello"' in updated

    def test_atomic_edit_dry_run(self, integration_repo: Path) -> None:
        """MCP write_files dry_run doesn't modify files."""
        mutation_ops = MutationOps(integration_repo)

        original = (integration_repo / "src" / "main.py").read_text()

        result = mutation_ops.write_files(
            edits=[
                Edit(
                    path="src/main.py",
                    action="update",
                    old_content="def greet",
                    new_content="def say_hello",
                )
            ],
            dry_run=True,
        )

        # File should be unchanged
        assert (integration_repo / "src" / "main.py").read_text() == original
        # Dry run returns applied=False
        assert not result.applied
        assert result.dry_run

    def test_atomic_edit_delete(self, integration_repo: Path) -> None:
        """MCP write_files can delete files."""
        # First create a file to delete
        to_delete = integration_repo / "src" / "to_delete.py"
        to_delete.write_text("# Delete me\n")
        assert to_delete.exists()

        mutation_ops = MutationOps(integration_repo)

        result = mutation_ops.write_files(
            edits=[
                Edit(
                    path="src/to_delete.py",
                    action="delete",
                )
            ],
        )

        assert result.applied
        assert not to_delete.exists()

    def test_atomic_edit_content_not_found_error(self, integration_repo: Path) -> None:
        """MCP write_files raises error when old_content not found."""
        from codeplane.mutation.ops import ContentNotFoundError

        mutation_ops = MutationOps(integration_repo)

        with pytest.raises(ContentNotFoundError):
            mutation_ops.write_files(
                edits=[
                    Edit(
                        path="src/main.py",
                        action="update",
                        old_content="THIS CONTENT DOES NOT EXIST",
                        new_content="replacement",
                    )
                ],
            )
