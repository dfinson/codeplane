"""Tests for permission policy evaluation (backend.services.permission_policy)."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from backend.services.permission_policy import (
    _HARD_GATED_SHELL_RE,
    _READONLY_SHELL_RE,
    PolicyDecision,
    _is_path_within_workspace,
    evaluate_approval_required,
    evaluate_auto,
    evaluate_read_only,
)

# ---------------------------------------------------------------------------
# PolicyDecision enum
# ---------------------------------------------------------------------------


class TestPolicyDecision:
    def test_values(self) -> None:
        assert PolicyDecision.approve == "approve"
        assert PolicyDecision.ask == "ask"
        assert PolicyDecision.deny == "deny"


# ---------------------------------------------------------------------------
# _READONLY_SHELL_RE
# ---------------------------------------------------------------------------


class TestReadonlyShellRegex:
    @pytest.mark.parametrize(
        "cmd",
        [
            "grep -r pattern .",
            "  grep foo",
            "rg --json pattern",
            "find . -name '*.py'",
            "ls -la",
            "cat file.txt",
            "head -n 10 file.txt",
            "tail -f log.txt",
            "wc -l file.txt",
            "sort file.txt",
            "diff a.txt b.txt",
            "file script.sh",
            "stat somefile",
            "du -sh .",
            "tree .",
            "echo hello",
            "pwd",
            "which python",
            "printenv PATH",
            "env",
            # Windows
            "dir /b",
            "findstr /s pattern *.txt",
            "where python",
            # PowerShell
            "Get-ChildItem .",
            "Select-String pattern file.txt",
            "Test-Path ./foo",
            "gci .",
            "sls pattern file.txt",
        ],
    )
    def test_matches_readonly_commands(self, cmd: str) -> None:
        assert _READONLY_SHELL_RE.match(cmd), f"Expected match for: {cmd}"

    @pytest.mark.parametrize(
        "cmd",
        [
            "rm -rf /",
            "mv a b",
            "cp a b",
            "chmod 777 file",
            "pip install foo",
            "python script.py",
            "node index.js",
            "curl http://example.com",
            "wget http://example.com",
        ],
    )
    def test_does_not_match_write_commands(self, cmd: str) -> None:
        assert not _READONLY_SHELL_RE.match(cmd), f"Expected no match for: {cmd}"

    def test_case_insensitive(self) -> None:
        assert _READONLY_SHELL_RE.match("GREP foo")
        assert _READONLY_SHELL_RE.match("Ls -la")


# ---------------------------------------------------------------------------
# _HARD_GATED_SHELL_RE
# ---------------------------------------------------------------------------


class TestHardGatedShellRegex:
    @pytest.mark.parametrize(
        "cmd",
        [
            "git merge main",
            "git merge --no-ff feature",
            "  git merge origin/main",
            "git pull",
            "git pull origin main",
            "git pull --rebase",
            "git rebase main",
            "git rebase -i HEAD~3",
            "git cherry-pick abc123",
            "git cherry-pick --no-commit abc123",
            "git reset --hard",
            "git reset --hard HEAD",
            "git reset --hard HEAD~1",
            "git reset --mixed --hard HEAD",
            "GIT MERGE main",
            "Git Pull origin main",
        ],
    )
    def test_matches_hard_gated_commands(self, cmd: str) -> None:
        assert _HARD_GATED_SHELL_RE.search(cmd), f"Expected match for: {cmd}"

    @pytest.mark.parametrize(
        "cmd",
        [
            "git status",
            "git log --oneline",
            "git diff",
            "git add .",
            "git commit -m 'msg'",
            "git push origin main",
            "git branch feature",
            "git checkout main",
            "git stash",
            "git reset --soft HEAD~1",
            "git reset HEAD file.py",
            "grep merge file.txt",
            "echo git merge",
        ],
    )
    def test_does_not_match_safe_commands(self, cmd: str) -> None:
        assert not _HARD_GATED_SHELL_RE.search(cmd), f"Expected no match for: {cmd}"


# ---------------------------------------------------------------------------
# Hard gate integration: always asks regardless of mode
# ---------------------------------------------------------------------------


class TestHardGateIntegration:
    """Hard-gated commands must return 'ask' in every permission mode."""

    @pytest.mark.parametrize(
        "cmd",
        [
            "git merge main",
            "git pull origin main",
            "git rebase main",
            "git cherry-pick abc123",
            "git reset --hard HEAD",
        ],
    )
    def test_auto_mode_asks_for_hard_gated(self, tmp_path: Path, cmd: str) -> None:
        result = evaluate_auto(kind="shell", workspace_path=str(tmp_path), full_command_text=cmd)
        assert result == PolicyDecision.ask

    @pytest.mark.parametrize(
        "cmd",
        [
            "git merge main",
            "git pull origin main",
            "git rebase main",
            "git cherry-pick abc123",
            "git reset --hard HEAD",
        ],
    )
    def test_read_only_mode_asks_for_hard_gated(self, tmp_path: Path, cmd: str) -> None:
        result = evaluate_read_only(kind="shell", workspace_path=str(tmp_path), full_command_text=cmd)
        assert result == PolicyDecision.ask

    @pytest.mark.parametrize(
        "cmd",
        [
            "git merge main",
            "git pull origin main",
            "git rebase main",
            "git cherry-pick abc123",
            "git reset --hard HEAD",
        ],
    )
    def test_approval_required_mode_asks_for_hard_gated(self, tmp_path: Path, cmd: str) -> None:
        result = evaluate_approval_required(kind="shell", workspace_path=str(tmp_path), full_command_text=cmd)
        assert result == PolicyDecision.ask

    def test_auto_mode_still_approves_normal_shell(self, tmp_path: Path) -> None:
        """Non-hard-gated commands remain auto-approved in auto mode."""
        result = evaluate_auto(kind="shell", workspace_path=str(tmp_path), full_command_text="python script.py")
        assert result == PolicyDecision.approve


# ---------------------------------------------------------------------------
# _is_path_within_workspace
# ---------------------------------------------------------------------------


class TestIsPathWithinWorkspace:
    def test_path_inside_workspace(self, tmp_path: Path) -> None:
        workspace = str(tmp_path)
        inner = os.path.join(workspace, "subdir", "file.py")
        os.makedirs(os.path.dirname(inner), exist_ok=True)
        assert _is_path_within_workspace(inner, workspace) is True

    def test_path_is_workspace(self, tmp_path: Path) -> None:
        workspace = str(tmp_path)
        assert _is_path_within_workspace(workspace, workspace) is True

    def test_path_outside_workspace(self, tmp_path: Path) -> None:
        workspace = str(tmp_path / "project")
        outside = str(tmp_path / "other")
        os.makedirs(workspace, exist_ok=True)
        os.makedirs(outside, exist_ok=True)
        assert _is_path_within_workspace(outside, workspace) is False

    def test_path_with_traversal(self, tmp_path: Path) -> None:
        workspace = str(tmp_path / "project")
        os.makedirs(workspace, exist_ok=True)
        sneaky = os.path.join(workspace, "..", "other")
        assert _is_path_within_workspace(sneaky, workspace) is False

    def test_similar_prefix_not_inside(self, tmp_path: Path) -> None:
        workspace = str(tmp_path / "project")
        sibling = str(tmp_path / "project-extra")
        os.makedirs(workspace, exist_ok=True)
        os.makedirs(sibling, exist_ok=True)
        assert _is_path_within_workspace(sibling, workspace) is False

    def test_invalid_path_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Cover the TypeError/ValueError except branch."""

        def _raise_type_error(p: str) -> str:
            raise TypeError("bad")

        monkeypatch.setattr(os.path, "realpath", _raise_type_error)
        assert _is_path_within_workspace("/a", "/b") is False


# ---------------------------------------------------------------------------
# evaluate_auto
# ---------------------------------------------------------------------------


class TestEvaluateAuto:
    """AUTO mode: everything should be approved."""

    def test_read_approved(self, tmp_path: Path) -> None:
        result = evaluate_auto(kind="read", workspace_path=str(tmp_path))
        assert result == PolicyDecision.approve

    def test_memory_approved(self, tmp_path: Path) -> None:
        result = evaluate_auto(kind="memory", workspace_path=str(tmp_path))
        assert result == PolicyDecision.approve

    def test_write_within_workspace(self, tmp_path: Path) -> None:
        ws = str(tmp_path)
        target = os.path.join(ws, "file.py")
        result = evaluate_auto(kind="write", workspace_path=ws, file_name=target)
        assert result == PolicyDecision.approve

    def test_write_with_path_param(self, tmp_path: Path) -> None:
        ws = str(tmp_path)
        target = os.path.join(ws, "file.py")
        result = evaluate_auto(kind="write", workspace_path=ws, path=target)
        assert result == PolicyDecision.approve

    def test_write_outside_workspace_still_approved(self, tmp_path: Path) -> None:
        ws = str(tmp_path / "project")
        os.makedirs(ws, exist_ok=True)
        outside = str(tmp_path / "other" / "file.py")
        # AUTO mode approves writes even outside workspace (falls through to kind=="write")
        result = evaluate_auto(kind="write", workspace_path=ws, file_name=outside)
        assert result == PolicyDecision.approve

    def test_shell_approved(self, tmp_path: Path) -> None:
        result = evaluate_auto(kind="shell", workspace_path=str(tmp_path))
        assert result == PolicyDecision.approve

    def test_mcp_approved(self, tmp_path: Path) -> None:
        result = evaluate_auto(kind="mcp", workspace_path=str(tmp_path))
        assert result == PolicyDecision.approve

    def test_url_approved(self, tmp_path: Path) -> None:
        result = evaluate_auto(kind="url", workspace_path=str(tmp_path))
        assert result == PolicyDecision.approve

    def test_unknown_kind_approved(self, tmp_path: Path) -> None:
        result = evaluate_auto(kind="unknown-thing", workspace_path=str(tmp_path))
        assert result == PolicyDecision.approve

    def test_possible_paths_all_inside(self, tmp_path: Path) -> None:
        ws = str(tmp_path)
        paths = [os.path.join(ws, "a.py"), os.path.join(ws, "b.py")]
        result = evaluate_auto(kind="write", workspace_path=ws, possible_paths=paths)
        assert result == PolicyDecision.approve

    def test_write_no_path_info(self, tmp_path: Path) -> None:
        result = evaluate_auto(kind="write", workspace_path=str(tmp_path))
        assert result == PolicyDecision.approve


# ---------------------------------------------------------------------------
# evaluate_read_only
# ---------------------------------------------------------------------------


class TestEvaluateReadOnly:
    """READ_ONLY mode: reads + grep/find allowed; everything else denied."""

    def test_memory_approved(self, tmp_path: Path) -> None:
        result = evaluate_read_only(kind="memory", workspace_path=str(tmp_path))
        assert result == PolicyDecision.approve

    def test_read_within_workspace(self, tmp_path: Path) -> None:
        ws = str(tmp_path)
        target = os.path.join(ws, "file.py")
        result = evaluate_read_only(kind="read", workspace_path=ws, file_name=target)
        assert result == PolicyDecision.approve

    def test_read_with_path_param(self, tmp_path: Path) -> None:
        ws = str(tmp_path)
        target = os.path.join(ws, "file.py")
        result = evaluate_read_only(kind="read", workspace_path=ws, path=target)
        assert result == PolicyDecision.approve

    def test_read_no_target_approved(self, tmp_path: Path) -> None:
        result = evaluate_read_only(kind="read", workspace_path=str(tmp_path))
        assert result == PolicyDecision.approve

    def test_read_outside_workspace_denied(self, tmp_path: Path) -> None:
        ws = str(tmp_path / "project")
        outside = str(tmp_path / "secret")
        os.makedirs(ws, exist_ok=True)
        os.makedirs(outside, exist_ok=True)
        result = evaluate_read_only(kind="read", workspace_path=ws, file_name=outside)
        assert result == PolicyDecision.deny

    def test_shell_grep_approved(self, tmp_path: Path) -> None:
        result = evaluate_read_only(kind="shell", workspace_path=str(tmp_path), full_command_text="grep -r pattern .")
        assert result == PolicyDecision.approve

    def test_shell_find_approved(self, tmp_path: Path) -> None:
        result = evaluate_read_only(kind="shell", workspace_path=str(tmp_path), full_command_text="find . -name '*.py'")
        assert result == PolicyDecision.approve

    def test_shell_rm_denied(self, tmp_path: Path) -> None:
        result = evaluate_read_only(kind="shell", workspace_path=str(tmp_path), full_command_text="rm -rf /")
        assert result == PolicyDecision.deny

    def test_shell_no_command_denied(self, tmp_path: Path) -> None:
        result = evaluate_read_only(kind="shell", workspace_path=str(tmp_path))
        assert result == PolicyDecision.deny

    def test_mcp_readonly_approved(self, tmp_path: Path) -> None:
        result = evaluate_read_only(kind="mcp", workspace_path=str(tmp_path), read_only=True)
        assert result == PolicyDecision.approve

    def test_mcp_mutation_denied(self, tmp_path: Path) -> None:
        result = evaluate_read_only(kind="mcp", workspace_path=str(tmp_path), read_only=False)
        assert result == PolicyDecision.deny

    def test_mcp_no_readonly_flag_denied(self, tmp_path: Path) -> None:
        result = evaluate_read_only(kind="mcp", workspace_path=str(tmp_path))
        assert result == PolicyDecision.deny

    def test_write_denied(self, tmp_path: Path) -> None:
        result = evaluate_read_only(kind="write", workspace_path=str(tmp_path))
        assert result == PolicyDecision.deny

    def test_url_denied(self, tmp_path: Path) -> None:
        result = evaluate_read_only(kind="url", workspace_path=str(tmp_path))
        assert result == PolicyDecision.deny

    def test_custom_tool_denied(self, tmp_path: Path) -> None:
        result = evaluate_read_only(kind="custom-tool", workspace_path=str(tmp_path))
        assert result == PolicyDecision.deny

    def test_unknown_kind_denied(self, tmp_path: Path) -> None:
        result = evaluate_read_only(kind="something-new", workspace_path=str(tmp_path))
        assert result == PolicyDecision.deny


# ---------------------------------------------------------------------------
# evaluate_approval_required
# ---------------------------------------------------------------------------


class TestEvaluateApprovalRequired:
    """APPROVAL_REQUIRED mode: reads auto-approve; rest needs approval."""

    def test_memory_approved(self, tmp_path: Path) -> None:
        result = evaluate_approval_required(kind="memory", workspace_path=str(tmp_path))
        assert result == PolicyDecision.approve

    def test_read_approved(self, tmp_path: Path) -> None:
        result = evaluate_approval_required(kind="read", workspace_path=str(tmp_path))
        assert result == PolicyDecision.approve

    def test_shell_grep_approved(self, tmp_path: Path) -> None:
        result = evaluate_approval_required(
            kind="shell", workspace_path=str(tmp_path), full_command_text="grep pattern file"
        )
        assert result == PolicyDecision.approve

    def test_shell_rg_approved(self, tmp_path: Path) -> None:
        result = evaluate_approval_required(
            kind="shell", workspace_path=str(tmp_path), full_command_text="rg pattern ."
        )
        assert result == PolicyDecision.approve

    def test_shell_dangerous_asks(self, tmp_path: Path) -> None:
        result = evaluate_approval_required(
            kind="shell", workspace_path=str(tmp_path), full_command_text="python script.py"
        )
        assert result == PolicyDecision.ask

    def test_shell_no_command_asks(self, tmp_path: Path) -> None:
        result = evaluate_approval_required(kind="shell", workspace_path=str(tmp_path))
        assert result == PolicyDecision.ask

    def test_write_asks(self, tmp_path: Path) -> None:
        result = evaluate_approval_required(kind="write", workspace_path=str(tmp_path))
        assert result == PolicyDecision.ask

    def test_url_asks(self, tmp_path: Path) -> None:
        result = evaluate_approval_required(kind="url", workspace_path=str(tmp_path))
        assert result == PolicyDecision.ask

    def test_mcp_readonly_approved(self, tmp_path: Path) -> None:
        result = evaluate_approval_required(kind="mcp", workspace_path=str(tmp_path), read_only=True)
        assert result == PolicyDecision.approve

    def test_mcp_mutation_asks(self, tmp_path: Path) -> None:
        result = evaluate_approval_required(kind="mcp", workspace_path=str(tmp_path), read_only=False)
        assert result == PolicyDecision.ask

    def test_mcp_no_readonly_flag_asks(self, tmp_path: Path) -> None:
        result = evaluate_approval_required(kind="mcp", workspace_path=str(tmp_path))
        assert result == PolicyDecision.ask

    def test_custom_tool_asks(self, tmp_path: Path) -> None:
        result = evaluate_approval_required(kind="custom-tool", workspace_path=str(tmp_path))
        assert result == PolicyDecision.ask

    def test_unknown_kind_asks(self, tmp_path: Path) -> None:
        result = evaluate_approval_required(kind="something-new", workspace_path=str(tmp_path))
        assert result == PolicyDecision.ask
