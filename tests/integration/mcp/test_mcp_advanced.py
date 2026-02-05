"""Advanced integration tests for MCP tools.

Tests complex workflows and interactions between MCP tools.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codeplane.files.ops import FileOps
from codeplane.git.ops import GitOps
from codeplane.index.ops import IndexCoordinator
from codeplane.mutation.ops import Edit, MutationOps

pytestmark = pytest.mark.integration


def _make_coordinator(repo_path: Path) -> IndexCoordinator:
    """Create an IndexCoordinator with proper paths."""
    codeplane_dir = repo_path / ".codeplane"
    codeplane_dir.mkdir(exist_ok=True)
    db_path = codeplane_dir / "index.db"
    tantivy_path = codeplane_dir / "tantivy"
    return IndexCoordinator(repo_path, db_path, tantivy_path)


def _noop_progress(indexed: int, total: int, by_ext: dict[str, int]) -> None:
    """No-op progress callback."""
    pass


class TestSearchAfterMutation:
    """Tests that search reflects file mutations."""

    @pytest.mark.asyncio
    async def test_search_finds_existing_content(self, integration_repo: Path) -> None:
        """Search finds content in repository after initialization."""
        # Initialize index
        index = _make_coordinator(integration_repo)
        await index.initialize(_noop_progress)

        # Search should work on existing content
        result = await index.search("def", mode="lexical")
        # SearchResponse has .results attribute which is a list
        assert result is not None
        assert isinstance(result.results, list)


class TestGitAfterMutation:
    """Tests git operations after file mutations."""

    def test_git_status_after_create(self, integration_repo: Path) -> None:
        """Git status shows newly created file."""
        mutation_ops = MutationOps(integration_repo)
        mutation_ops.atomic_edit_files(
            edits=[
                Edit(
                    path="src/new_file.py",
                    action="create",
                    content="# New file\n",
                )
            ]
        )

        git_ops = GitOps(integration_repo)
        status = git_ops.status()

        # New file should be in status
        assert len(status) > 0
        assert any("new_file.py" in path for path in status)

    def test_git_diff_after_update(self, integration_repo: Path) -> None:
        """Git diff shows file modifications."""
        mutation_ops = MutationOps(integration_repo)
        mutation_ops.atomic_edit_files(
            edits=[
                Edit(
                    path="src/main.py",
                    action="update",
                    old_content="Entry point.",
                    new_content="Entry point. (modified)",
                )
            ]
        )

        git_ops = GitOps(integration_repo)
        diff = git_ops.diff()

        assert diff.files_changed > 0

    def test_full_commit_workflow(self, integration_repo: Path) -> None:
        """Complete workflow: create -> stage -> commit."""
        mutation_ops = MutationOps(integration_repo)
        git_ops = GitOps(integration_repo)

        # Create file
        mutation_ops.atomic_edit_files(
            edits=[
                Edit(
                    path="src/feature.py",
                    action="create",
                    content='"""Feature module."""\n\ndef feature():\n    pass\n',
                )
            ]
        )

        # Stage
        git_ops.stage(["src/feature.py"])

        # Verify staged
        staged_diff = git_ops.diff(staged=True)
        assert staged_diff.files_changed > 0

        # Commit
        sha = git_ops.commit("Add feature module")
        assert sha is not None

        # Verify clean
        status = git_ops.status()
        assert "feature.py" not in str(status)


class TestMapRepoAfterInit:
    """Tests map_repo after initialization."""

    @pytest.mark.asyncio
    async def test_map_repo_returns_structure(self, integration_repo: Path) -> None:
        """map_repo returns structure after init."""
        index = _make_coordinator(integration_repo)
        await index.initialize(_noop_progress)

        result = await index.map_repo()

        # MapRepoResult has structure which contains tree
        assert result is not None
        assert result.structure is not None


class TestFileOpsWithGit:
    """Tests file operations with git tracking."""

    def test_list_basic_functionality(self, integration_repo: Path) -> None:
        """list_files works for basic directory listing."""
        file_ops = FileOps(integration_repo)
        result = file_ops.list_files("src")

        names = [e.name for e in result.entries]
        assert "main.py" in names
        assert "utils.py" in names

    def test_read_files_handles_binary(self, integration_repo: Path) -> None:
        """read_files handles binary file gracefully."""
        # Create a binary file
        binary_path = integration_repo / "data.bin"
        binary_path.write_bytes(b"\x00\x01\x02\xff\xfe")

        file_ops = FileOps(integration_repo)

        # Should not crash, may skip or mark as binary
        result = file_ops.read_files(["data.bin"])

        # Should return something (possibly error or marker)
        assert len(result.files) == 1


class TestMultiFileOperations:
    """Tests involving multiple files."""

    def test_atomic_edit_multiple_files(self, integration_repo: Path) -> None:
        """atomic_edit_files can modify multiple files atomically."""
        mutation_ops = MutationOps(integration_repo)

        result = mutation_ops.atomic_edit_files(
            edits=[
                Edit(
                    path="src/a.py",
                    action="create",
                    content="# File A\n",
                ),
                Edit(
                    path="src/b.py",
                    action="create",
                    content="# File B\n",
                ),
                Edit(
                    path="src/c.py",
                    action="create",
                    content="# File C\n",
                ),
            ]
        )

        assert result.applied
        assert (integration_repo / "src" / "a.py").exists()
        assert (integration_repo / "src" / "b.py").exists()
        assert (integration_repo / "src" / "c.py").exists()

    def test_read_multiple_files(self, integration_repo: Path) -> None:
        """read_files reads multiple files."""
        file_ops = FileOps(integration_repo)

        result = file_ops.read_files(["src/main.py", "src/utils.py"])

        # Should return existing files
        paths = [f.path for f in result.files]
        assert "src/main.py" in paths
        assert "src/utils.py" in paths
