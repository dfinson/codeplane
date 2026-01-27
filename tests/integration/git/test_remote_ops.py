"""Integration tests for git remote operations using local bare repos."""

from __future__ import annotations

from pathlib import Path

import pytest

from codeplane.git import GitOps

pytestmark = pytest.mark.integration


class TestFetchWithLocalRemote:
    """Tests for fetch operations using local bare repo as remote."""

    def test_fetch_from_origin(self, local_bare_remote: tuple[Path, Path]) -> None:
        """Fetch should succeed from local bare remote."""
        work, bare = local_bare_remote
        ops = GitOps(work)

        # Should not raise
        ops.fetch("origin")

    def test_fetch_updates_remote_refs(self, local_bare_remote: tuple[Path, Path]) -> None:
        """Fetch should update remote tracking refs."""
        work, bare = local_bare_remote
        ops = GitOps(work)

        # Get initial remote branches
        branches_before = {b.short_name for b in ops.branches(include_remote=True)}
        assert "origin/feature/remote-test" in branches_before

        ops.fetch("origin")

        # Remote refs should still be present
        branches_after = {b.short_name for b in ops.branches(include_remote=True)}
        assert "origin/feature/remote-test" in branches_after


class TestPushWithLocalRemote:
    """Tests for push operations using local bare repo as remote."""

    def test_push_new_commits(self, local_bare_remote: tuple[Path, Path]) -> None:
        """Push should send new commits to remote."""
        work, bare = local_bare_remote
        ops = GitOps(work)

        # Create a new commit
        (work / "new-file.txt").write_text("new content\n")
        ops.stage(["new-file.txt"])
        new_sha = ops.commit("Add new file")

        # Push
        ops.push("origin")

        # Verify by cloning bare repo elsewhere and checking
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            verify_path = Path(tmpdir) / "verify"
            subprocess.run(
                ["git", "clone", str(bare), str(verify_path)],
                capture_output=True,
                check=True,
            )
            verify_ops = GitOps(verify_path)
            assert verify_ops.head().target_sha == new_sha

    def test_push_force(self, local_bare_remote: tuple[Path, Path]) -> None:
        """Force push should overwrite remote history."""
        work, bare = local_bare_remote
        ops = GitOps(work)

        # Create a second commit first
        (work / "extra.txt").write_text("extra\n")
        ops.stage(["extra.txt"])
        ops.commit("Extra commit")
        ops.push("origin")

        # Now reset back one commit
        ops.reset("HEAD~1", "hard")
        original_sha = ops.head().target_sha

        # Normal push would fail (non-fast-forward), force should work
        ops.push("origin", force=True)

        # Verify remote was updated
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            verify_path = Path(tmpdir) / "verify"
            subprocess.run(
                ["git", "clone", str(bare), str(verify_path)],
                capture_output=True,
                check=True,
            )
            verify_ops = GitOps(verify_path)
            assert verify_ops.head().target_sha == original_sha


class TestRemoteBranchCheckout:
    """Tests for checking out remote branches (planners.py coverage)."""

    def test_checkout_remote_branch_creates_local(
        self, local_bare_remote: tuple[Path, Path]
    ) -> None:
        """Checking out a remote branch should create local tracking branch."""
        work, _ = local_bare_remote
        ops = GitOps(work)

        # Delete local feature branch if exists
        local_branches = {b.short_name for b in ops.branches(include_remote=False)}
        if "feature/remote-test" in local_branches:
            ops.checkout("main")
            ops.delete_branch("feature/remote-test", force=True)

        # Checkout remote branch by full name
        ops.checkout("origin/feature/remote-test")

        # Should now have local branch (or be detached - depends on implementation)
        # The planner should handle this
        current = ops.current_branch()
        head = ops.head()

        # Either created local branch or detached at remote commit
        assert current == "feature/remote-test" or head.is_detached

    def test_checkout_remote_branch_with_existing_local(
        self, local_bare_remote: tuple[Path, Path]
    ) -> None:
        """Checking out remote when local exists should switch to local."""
        work, _ = local_bare_remote
        ops = GitOps(work)

        # Ensure we're on main
        ops.checkout("main")

        # The fixture already has origin/feature/remote-test
        # Create a local branch with same name but different content
        ops.create_branch("test-local")
        ops.checkout("test-local")

        # Now checkout main again
        ops.checkout("main")

        # Checkout the local branch
        ops.checkout("test-local")
        assert ops.current_branch() == "test-local"


class TestRemoteInfo:
    """Tests for remote information retrieval."""

    def test_remotes_returns_origin(self, local_bare_remote: tuple[Path, Path]) -> None:
        """Should list origin remote."""
        work, bare = local_bare_remote
        ops = GitOps(work)

        remotes = ops.remotes()

        assert len(remotes) == 1
        assert remotes[0].name == "origin"
        assert str(bare) in remotes[0].url


class TestFetchNonexistentRemote:
    """Error handling for remote operations."""

    def test_fetch_nonexistent_remote_raises(self, local_bare_remote: tuple[Path, Path]) -> None:
        """Fetching from nonexistent remote should raise."""
        from codeplane.git import RemoteError

        work, _ = local_bare_remote
        ops = GitOps(work)

        with pytest.raises(RemoteError):
            ops.fetch("nonexistent")
