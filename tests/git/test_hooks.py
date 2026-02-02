"""Tests for git hook execution."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from codeplane.git._internal.hooks import HookResult, run_hook


class TestRunHook:
    """Tests for run_hook function."""

    def test_no_hook_returns_success(self, tmp_path: Path) -> None:
        """When hook doesn't exist, return success."""
        (tmp_path / ".git" / "hooks").mkdir(parents=True)

        result = run_hook(tmp_path, "pre-commit")

        assert result.success is True
        assert result.exit_code == 0

    def test_non_executable_hook_returns_success(self, tmp_path: Path) -> None:
        """Non-executable hook is skipped with warning."""
        hooks_dir = tmp_path / ".git" / "hooks"
        hooks_dir.mkdir(parents=True)
        hook = hooks_dir / "pre-commit"
        hook.write_text("#!/bin/sh\nexit 1\n")
        # Don't make it executable

        result = run_hook(tmp_path, "pre-commit")

        assert result.success is True
        assert "not executable" in result.stderr

    def test_passing_hook(self, tmp_path: Path) -> None:
        """Hook that exits 0 returns success."""
        hooks_dir = tmp_path / ".git" / "hooks"
        hooks_dir.mkdir(parents=True)
        hook = hooks_dir / "pre-commit"
        hook.write_text("#!/bin/sh\necho 'All good'\nexit 0\n")
        hook.chmod(hook.stat().st_mode | stat.S_IXUSR)

        result = run_hook(tmp_path, "pre-commit")

        assert result.success is True
        assert result.exit_code == 0
        assert "All good" in result.stdout

    def test_failing_hook(self, tmp_path: Path) -> None:
        """Hook that exits non-zero returns failure."""
        hooks_dir = tmp_path / ".git" / "hooks"
        hooks_dir.mkdir(parents=True)
        hook = hooks_dir / "pre-commit"
        hook.write_text("#!/bin/sh\necho 'Error: lint failed' >&2\nexit 1\n")
        hook.chmod(hook.stat().st_mode | stat.S_IXUSR)

        result = run_hook(tmp_path, "pre-commit")

        assert result.success is False
        assert result.exit_code == 1
        assert "lint failed" in result.stderr

    def test_hook_output_capture(self, tmp_path: Path) -> None:
        """Both stdout and stderr are captured."""
        hooks_dir = tmp_path / ".git" / "hooks"
        hooks_dir.mkdir(parents=True)
        hook = hooks_dir / "pre-commit"
        hook.write_text(
            "#!/bin/sh\necho 'stdout message'\necho 'stderr message' >&2\nexit 0\n"
        )
        hook.chmod(hook.stat().st_mode | stat.S_IXUSR)

        result = run_hook(tmp_path, "pre-commit")

        assert "stdout message" in result.stdout
        assert "stderr message" in result.stderr

    def test_hook_timeout(self, tmp_path: Path) -> None:
        """Hook that takes too long times out."""
        hooks_dir = tmp_path / ".git" / "hooks"
        hooks_dir.mkdir(parents=True)
        hook = hooks_dir / "pre-commit"
        hook.write_text("#!/bin/sh\nsleep 10\n")
        hook.chmod(hook.stat().st_mode | stat.S_IXUSR)

        result = run_hook(tmp_path, "pre-commit", timeout=1)

        assert result.success is False
        assert result.exit_code == -1
        assert "timed out" in result.stderr


class TestHookResult:
    """Tests for HookResult dataclass."""

    def test_dataclass_fields(self) -> None:
        """HookResult has expected fields."""
        result = HookResult(
            success=True,
            exit_code=0,
            stdout="output",
            stderr="errors",
            modified_files=["file.py"],
        )

        assert result.success is True
        assert result.exit_code == 0
        assert result.stdout == "output"
        assert result.stderr == "errors"
        assert result.modified_files == ["file.py"]
