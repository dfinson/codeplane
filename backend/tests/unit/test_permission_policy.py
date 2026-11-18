"""Tests for is_git_reset_hard (backend.services.auth.permission_policy)."""

from __future__ import annotations

import pytest

from backend.services.auth.permission_policy import is_git_reset_hard, is_hard_gated_argv, is_hard_gated_syntax


@pytest.mark.parametrize(
    "command",
    [
        "git -C . merge topic",
        "'/usr/bin/git' -C . rebase topic",
        "env LANG=C git -C . merge topic",
        "bash -c 'git -C . merge topic'",
    ],
)
def test_ast_hard_gate_uses_executable_nodes(command):
    from backend.services.action_policy.syntax import analyze_syntax

    assert is_hard_gated_syntax(analyze_syntax(command, "bash"))


@pytest.mark.parametrize(
    "argv",
    [
        ["git", "status"],
        ["env", "LANG=C", "git", "status"],
        ["bash", "-c", "echo 'git -C . merge topic'"],
        ["bash", "-c", "git commit -m 'git merge topic'"],
    ],
)
def test_wrapper_inspection_does_not_gate_quoted_prose_or_readonly_git(argv):
    assert not is_hard_gated_argv(argv)


class TestIsGitResetHard:
    """is_git_reset_hard must detect all common git reset --hard patterns."""

    @pytest.mark.parametrize(
        "cmd",
        [
            "git reset --hard",
            "git reset --hard HEAD",
            "git reset --hard HEAD~1",
            "git reset --hard origin/main",
            "git reset HEAD --hard",
            "  git reset --hard  ",
            "cd /repo && git reset --hard HEAD",
            "git fetch origin && git reset --hard origin/main",
            "GIT reset --hard HEAD",  # case-insensitive
        ],
    )
    def test_detects_git_reset_hard(self, cmd: str) -> None:
        assert is_git_reset_hard(cmd), f"Expected detection for: {cmd!r}"

    @pytest.mark.parametrize(
        "cmd",
        [
            "git reset HEAD",
            "git reset --soft HEAD",
            "git reset --mixed HEAD",
            "git status",
            "git checkout -- .",
            # quoted strings — the text is not a live command invocation
            "echo 'git reset --hard' is dangerous",
            'git commit -m "Document hard-gated commands (git reset --hard, merge, rebase)"',
            "git commit -m 'fix: mention git reset --hard in docs'",
            "grep 'hard' file.txt",
            "git reset",
        ],
    )
    def test_ignores_non_hard_reset(self, cmd: str) -> None:
        assert not is_git_reset_hard(cmd), f"Expected no detection for: {cmd!r}"
