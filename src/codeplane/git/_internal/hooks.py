"""Git hook execution utilities."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class HookResult:
    """Result of running a git hook."""

    success: bool
    exit_code: int
    stdout: str
    stderr: str
    modified_files: list[str]


def run_hook(repo_path: Path, hook_name: str, *, timeout: int = 120) -> HookResult:
    """Run a git hook if it exists.

    Args:
        repo_path: Path to the repository root
        hook_name: Name of the hook (e.g., "pre-commit", "commit-msg")
        timeout: Maximum seconds to wait for hook completion

    Returns:
        HookResult with success=True if hook doesn't exist or passed
    """
    hook_path = repo_path / ".git" / "hooks" / hook_name

    if not hook_path.exists():
        return HookResult(
            success=True,
            exit_code=0,
            stdout="",
            stderr="",
            modified_files=[],
        )

    if not os.access(hook_path, os.X_OK):
        return HookResult(
            success=True,
            exit_code=0,
            stdout="",
            stderr=f"Hook {hook_name} exists but is not executable",
            modified_files=[],
        )

    # Capture working tree state before hook runs
    modified_before = _get_modified_files(repo_path)

    try:
        result = subprocess.run(
            [str(hook_path)],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "GIT_DIR": str(repo_path / ".git")},
        )
    except subprocess.TimeoutExpired:
        return HookResult(
            success=False,
            exit_code=-1,
            stdout="",
            stderr=f"Hook {hook_name} timed out after {timeout}s",
            modified_files=[],
        )

    # Check what files were modified by the hook (e.g., auto-formatting)
    modified_after = _get_modified_files(repo_path)
    newly_modified = sorted(set(modified_after) - set(modified_before))

    return HookResult(
        success=result.returncode == 0,
        exit_code=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        modified_files=newly_modified,
    )


def _get_modified_files(repo_path: Path) -> list[str]:
    """Get list of files with uncommitted changes."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        staged = subprocess.run(
            ["git", "diff", "--name-only", "--cached"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        files = result.stdout.strip().split("\n") + staged.stdout.strip().split("\n")
        return [f for f in files if f]
    except (subprocess.SubprocessError, OSError):
        return []
