"""Permission policy — hard-gated command detection.

Only ``is_git_reset_hard`` and the ``PermissionRequest`` dataclass survive
from the legacy rule-based permission system.  All runtime policy decisions
are now made by the action_policy router/classifier.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PureWindowsPath

import structlog

log = structlog.get_logger()


@dataclass(frozen=True, slots=True)
class PermissionRequest:
    """Context for a single permission evaluation."""

    kind: str
    workspace_path: str
    possible_paths: list[str] | None = field(default=None)
    full_command_text: str | None = None
    file_name: str | None = None
    path: str | None = None
    read_only: bool | None = None


# ---------------------------------------------------------------------------
# Hard-blocked commands — always require explicit operator approval,
# regardless of trust level.
# ---------------------------------------------------------------------------

# Matches `git reset --hard` in any reasonable shell command string, including
# compound commands joined with &&, || or ;.  Both orderings are covered:
#   git reset --hard HEAD
#   git reset HEAD --hard
#   cd /repo && git reset --hard origin/main
_GIT_RESET_HARD_RE = re.compile(
    r"\bgit\s+reset\b[^|;&\n]*?\s--hard\b",
    re.IGNORECASE,
)

# Strips the *contents* of shell string literals so that `git reset --hard`
# appearing only inside a quoted argument (e.g. a ``git commit -m "..."``
# message) is not mistakenly matched as a real command invocation.
_QUOTED_STRING_RE = re.compile(
    r'"[^"\\]*(?:\\.[^"\\]*)*"|\'[^\'\\]*(?:\\.[^\'\\]*)*\'',
    re.DOTALL,
)


def _strip_quoted_strings(cmd: str) -> str:
    """Replace the contents of every quoted string with an empty placeholder."""
    return _QUOTED_STRING_RE.sub('""', cmd)


def is_git_reset_hard(command: str) -> bool:
    """Return True if *command* contains a ``git reset --hard`` invocation.

    Quoted string contents (e.g. a ``git commit -m "..."`` message) are
    stripped before matching so that literal text inside arguments does not
    cause false positives.
    """
    return bool(_GIT_RESET_HARD_RE.search(_strip_quoted_strings(command)))


# Matches the other SPEC §18.2 hard-gated git operations (merge / pull / rebase /
# cherry-pick).  These bypass CodePlane's managed merge workflow or implicitly
# merge remote changes, so they are always routed to the operator.
_GIT_HARD_GATE_RE = re.compile(
    r"\bgit\s+(?:merge|pull|rebase|cherry-pick)\b",
    re.IGNORECASE,
)

# Force-pushes rewrite published history and can destroy a protected branch.
# SPEC §18.2's table does not enumerate them, but binding condition §3 requires
# force-push to reach a human unconditionally — and TraceForge would otherwise
# classify it as an ordinary (waivable) network mutation.  Detecting it here, in
# the CP structural pre-check that runs BEFORE governance scoring, keeps the hard
# gate independent of any trust grant or budget waiver (defence in depth).
_GIT_FORCE_PUSH_RE = re.compile(
    r"\bgit\s+push\b[^|;&\n]*?(?:--force(?:-with-lease)?\b|\s-f\b)",
    re.IGNORECASE,
)


def is_hard_gated_command(command: str) -> bool:
    """Return True if *command* is a SPEC §18.2 hard-gated git operation.

    Covers ``git merge``, ``git pull``, ``git rebase``, ``git cherry-pick``,
    ``git reset --hard`` and force-pushes (``git push --force`` /
    ``--force-with-lease`` / ``-f``). These are **always** routed to the operator
    for approval regardless of the active preset, trust grants, or governance
    budget — a defence-in-depth backstop that runs *before* governance scoring so
    a wholesale governance verdict or a trust grant can never silently waive them
    (binding condition §3). Quoted-string contents are stripped first to avoid
    false positives from commit messages and other quoted arguments.
    """
    stripped = _strip_quoted_strings(command)
    return bool(
        _GIT_HARD_GATE_RE.search(stripped) or _GIT_RESET_HARD_RE.search(stripped) or _GIT_FORCE_PUSH_RE.search(stripped)
    )


def is_hard_gated_argv(argv: list[str]) -> bool:
    """Evaluate native Git argv before model semantics, without shell reconstruction."""
    if not argv or PureWindowsPath(argv[0]).name.lower() not in {"git", "git.exe"}:
        return False
    index = 1
    while index < len(argv) and argv[index].startswith("-"):
        option = argv[index]
        if option in {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}:
            index += 2
        elif "=" in option or option in {"--no-pager", "--paginate", "--bare", "--no-optional-locks"}:
            index += 1
        else:
            return True
    if index >= len(argv):
        return False
    operation = argv[index]
    arguments = argv[index + 1 :]
    return (
        operation in {"merge", "pull", "rebase", "cherry-pick"}
        or operation == "reset"
        and "--hard" in arguments
        or operation == "push"
        and any(value == "-f" or value == "--force" or value.startswith("--force-with-lease") for value in arguments)
    )
