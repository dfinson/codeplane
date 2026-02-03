"""Tests for MCP git tools."""

import pytest
from pydantic import ValidationError

from codeplane.mcp.tools.git import (
    GitBranchParams,
    GitCommitParams,
    GitDiffParams,
    GitInspectParams,
    GitLogParams,
    GitResetParams,
    GitStageParams,
    GitStashParams,
    GitStatusParams,
)


class TestGitStatusParams:
    """Tests for GitStatusParams model."""

    def test_no_required_fields(self):
        """No required fields."""
        params = GitStatusParams()
        assert params.session_id is None


class TestGitDiffParams:
    """Tests for GitDiffParams model."""

    def test_defaults(self):
        """All fields have defaults."""
        params = GitDiffParams()
        assert params.base is None
        assert params.target is None
        assert params.staged is False

    def test_staged_only(self):
        """Can request staged changes only."""
        params = GitDiffParams(staged=True)
        assert params.staged is True

    def test_base_and_target(self):
        """Can compare between refs."""
        params = GitDiffParams(base="main", target="feature")
        assert params.base == "main"
        assert params.target == "feature"


class TestGitCommitParams:
    """Tests for GitCommitParams model."""

    def test_message_required(self):
        """message is required."""
        with pytest.raises(ValidationError):
            GitCommitParams()

    def test_message_provided(self):
        """Accepts commit message."""
        params = GitCommitParams(message="feat: add feature")
        assert params.message == "feat: add feature"

    def test_allow_empty_default(self):
        """allow_empty defaults to False."""
        params = GitCommitParams(message="msg")
        assert params.allow_empty is False


class TestGitLogParams:
    """Tests for GitLogParams model."""

    def test_defaults(self):
        """All fields have defaults."""
        params = GitLogParams()
        assert params.ref == "HEAD"
        assert params.limit == 50
        assert params.paths is None
        assert params.since is None
        assert params.until is None

    def test_limit_bounds(self):
        """limit is bounded."""
        params = GitLogParams(limit=100)
        assert params.limit == 100

    def test_paths_filter(self):
        """Can filter by paths."""
        params = GitLogParams(paths=["src/main.py"])
        assert params.paths == ["src/main.py"]


class TestGitStageParams:
    """Tests for GitStageParams model."""

    def test_action_required(self):
        """action is required."""
        with pytest.raises(ValidationError):
            GitStageParams()

    def test_add_action(self):
        """add action stages files."""
        params = GitStageParams(action="add", paths=["file.py"])
        assert params.action == "add"

    def test_remove_action(self):
        """remove action unstages files."""
        params = GitStageParams(action="remove", paths=["file.py"])
        assert params.action == "remove"

    def test_all_action(self):
        """all action stages everything."""
        params = GitStageParams(action="all")
        assert params.action == "all"
        assert params.paths is None

    def test_discard_action(self):
        """discard action discards changes."""
        params = GitStageParams(action="discard", paths=["file.py"])
        assert params.action == "discard"

    def test_invalid_action(self):
        """Invalid action rejected."""
        with pytest.raises(ValidationError):
            GitStageParams(action="commit")


class TestGitBranchParams:
    """Tests for GitBranchParams model."""

    def test_action_required(self):
        """action is required."""
        with pytest.raises(ValidationError):
            GitBranchParams()

    def test_list_action(self):
        """list action lists branches."""
        params = GitBranchParams(action="list")
        assert params.action == "list"

    def test_create_action(self):
        """create action creates branch."""
        params = GitBranchParams(action="create", name="new-feature")
        assert params.action == "create"
        assert params.name == "new-feature"

    def test_delete_action(self):
        """delete action deletes branch."""
        params = GitBranchParams(action="delete", name="old-branch")
        assert params.action == "delete"

    def test_create_with_ref(self):
        """create can specify base ref."""
        params = GitBranchParams(action="create", name="feature", ref="develop")
        assert params.ref == "develop"

    def test_force_delete(self):
        """force flag for delete."""
        params = GitBranchParams(action="delete", name="branch", force=True)
        assert params.force is True


class TestGitResetParams:
    """Tests for GitResetParams model."""

    def test_ref_required(self):
        """ref is required."""
        with pytest.raises(ValidationError):
            GitResetParams()

    def test_ref_provided(self):
        """Accepts ref."""
        params = GitResetParams(ref="HEAD~1")
        assert params.ref == "HEAD~1"

    def test_mode_default(self):
        """mode defaults to mixed."""
        params = GitResetParams(ref="HEAD")
        assert params.mode == "mixed"

    def test_mode_options(self):
        """mode accepts valid options."""
        for mode in ["soft", "mixed", "hard"]:
            params = GitResetParams(ref="HEAD", mode=mode)
            assert params.mode == mode

    def test_mode_invalid(self):
        """mode rejects invalid value."""
        with pytest.raises(ValidationError):
            GitResetParams(ref="HEAD", mode="keep")


class TestGitStashParams:
    """Tests for GitStashParams model."""

    def test_action_required(self):
        """action is required."""
        with pytest.raises(ValidationError):
            GitStashParams()

    def test_push_action(self):
        """push action creates stash."""
        params = GitStashParams(action="push")
        assert params.action == "push"

    def test_pop_action(self):
        """pop action applies and removes stash."""
        params = GitStashParams(action="pop")
        assert params.action == "pop"

    def test_list_action(self):
        """list action lists stashes."""
        params = GitStashParams(action="list")
        assert params.action == "list"

    def test_push_with_message(self):
        """push can have message."""
        params = GitStashParams(action="push", message="WIP: feature")
        assert params.message == "WIP: feature"

    def test_push_include_untracked(self):
        """push can include untracked."""
        params = GitStashParams(action="push", include_untracked=True)
        assert params.include_untracked is True

    def test_pop_with_index(self):
        """pop can specify stash index."""
        params = GitStashParams(action="pop", index=2)
        assert params.index == 2


class TestGitInspectParams:
    """Tests for GitInspectParams model."""

    def test_action_required(self):
        """action is required."""
        with pytest.raises(ValidationError):
            GitInspectParams()

    def test_show_action(self):
        """show action shows commit."""
        params = GitInspectParams(action="show")
        assert params.action == "show"

    def test_blame_action(self):
        """blame action annotates file."""
        params = GitInspectParams(action="blame", path="file.py")
        assert params.action == "blame"
        assert params.path == "file.py"

    def test_show_with_ref(self):
        """show can specify commit ref."""
        params = GitInspectParams(action="show", ref="abc1234")
        assert params.ref == "abc1234"

    def test_blame_with_lines(self):
        """blame can specify line range."""
        params = GitInspectParams(action="blame", path="f.py", start_line=10, end_line=20)
        assert params.start_line == 10
        assert params.end_line == 20


class TestGitStatusHandler:
    """Tests for git_status handler - parameter validation."""

    def test_status_params_accept_paths(self):
        """Status params can include paths filter."""
        params = GitStatusParams(paths=["src/"])
        assert params.paths == ["src/"]


class TestGitDiffHandler:
    """Tests for git_diff handler - parameter validation."""

    def test_diff_params_combinations(self):
        """Various param combinations are valid."""
        # Just staged
        p1 = GitDiffParams(staged=True)
        assert p1.staged is True

        # Base and target
        p2 = GitDiffParams(base="main", target="HEAD")
        assert p2.base == "main"


class TestGitCommitHandler:
    """Tests for git_commit handler - parameter validation."""

    def test_commit_params_with_paths(self):
        """Commit can specify paths."""
        params = GitCommitParams(message="fix", paths=["file.py"])
        assert params.paths == ["file.py"]


class TestGitLogHandler:
    """Tests for git_log handler - parameter validation."""

    def test_log_params_date_filters(self):
        """Log supports date filtering."""
        params = GitLogParams(since="2024-01-01", until="2024-12-31")
        assert params.since == "2024-01-01"
        assert params.until == "2024-12-31"


class TestGitStageHandler:
    """Tests for git_stage handler - parameter validation."""

    def test_stage_params_paths_optional_for_all(self):
        """Paths not required for 'all' action."""
        params = GitStageParams(action="all")
        assert params.paths is None


class TestGitBranchHandler:
    """Tests for git_branch handler - parameter validation."""

    def test_branch_params_ref_default(self):
        """Ref defaults to HEAD."""
        params = GitBranchParams(action="create", name="test")
        assert params.ref == "HEAD"


class TestGitResetHandler:
    """Tests for git_reset handler - parameter validation."""

    def test_reset_params_mode_variations(self):
        """All mode values work."""
        for mode in ["soft", "mixed", "hard"]:
            params = GitResetParams(ref="HEAD", mode=mode)
            assert params.mode == mode


class TestGitStashHandler:
    """Tests for git_stash handler - parameter validation."""

    def test_stash_params_index_default(self):
        """Index defaults to 0."""
        params = GitStashParams(action="pop")
        assert params.index == 0


class TestGitInspectHandler:
    """Tests for git_inspect handler - parameter validation."""

    def test_inspect_params_ref_default(self):
        """Ref defaults to HEAD."""
        params = GitInspectParams(action="show")
        assert params.ref == "HEAD"
