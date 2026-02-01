"""CLI runner for true E2E tests.

Creates isolated Python environments with codeplane installed from source,
then runs cpl commands via subprocess. This ensures tests exercise the
actual user experience through the CLI.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from threading import Thread


@dataclass
class CLIResult:
    """Result from running a CLI command."""

    returncode: int
    stdout: str
    stderr: str
    command: list[str]
    duration_seconds: float

    @property
    def success(self) -> bool:
        return self.returncode == 0

    def check(self) -> None:
        """Raise if command failed."""
        if not self.success:
            msg = (
                f"Command failed: {' '.join(self.command)}\n"
                f"Exit code: {self.returncode}\n"
                f"stderr: {self.stderr}\n"
                f"stdout: {self.stdout}"
            )
            raise RuntimeError(msg)


@dataclass
class RSSTracker:
    """Tracks peak RSS of a subprocess."""

    peak_mb: float = 0.0
    _thread: Thread | None = None
    _stop: bool = False


@dataclass
class IsolatedEnv:
    """An isolated test environment with its own venv and cpl installation."""

    root: Path
    venv_path: Path
    python_path: Path
    cpl_path: Path
    _env: dict[str, str] = field(default_factory=dict)

    def run_cpl(
        self,
        args: list[str],
        *,
        cwd: Path | None = None,
        timeout: float = 600,
        check: bool = False,
        track_rss: bool = False,
    ) -> tuple[CLIResult, float]:
        """Run cpl command in this environment.

        Returns:
            Tuple of (CLIResult, peak_rss_mb)
        """
        cmd = [str(self.cpl_path), *args]
        env = {
            **os.environ,
            **self._env,
            "PATH": f"{self.venv_path / 'bin'}:{os.environ.get('PATH', '')}",
        }

        peak_rss_mb = 0.0
        t0 = time.perf_counter()

        if track_rss:
            # Run with RSS tracking
            peak_rss_mb = self._run_with_rss_tracking(cmd, cwd, env, timeout)
            # Re-run to get output (RSS tracking consumes it)
            # Actually, let's do it properly in one pass
            pass

        result = subprocess.run(
            cmd,
            cwd=cwd or self.root,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )

        duration = time.perf_counter() - t0

        cli_result = CLIResult(
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            command=cmd,
            duration_seconds=duration,
        )

        if check:
            cli_result.check()

        return cli_result, peak_rss_mb

    def _run_with_rss_tracking(
        self,
        cmd: list[str],
        cwd: Path | None,
        env: dict[str, str],
        timeout: float,
    ) -> float:
        """Run command and track peak RSS."""
        import threading

        peak_rss = 0.0
        stop_event = threading.Event()

        proc = subprocess.Popen(
            cmd,
            cwd=cwd or self.root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )

        def monitor() -> None:
            nonlocal peak_rss
            try:
                import psutil

                ps_proc = psutil.Process(proc.pid)
                while not stop_event.is_set() and proc.poll() is None:
                    try:
                        mem = ps_proc.memory_info()
                        rss_mb = mem.rss / (1024 * 1024)
                        peak_rss = max(peak_rss, rss_mb)
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        break
                    time.sleep(0.1)
            except ImportError:
                pass

        thread = threading.Thread(target=monitor, daemon=True)
        thread.start()

        try:
            proc.wait(timeout=timeout)
        finally:
            stop_event.set()
            thread.join(timeout=1.0)

        return peak_rss


def get_codeplane_source_root() -> Path:
    """Get the root of the codeplane source tree."""
    # This file is at tests/e2e/cli_runner.py
    # Source root is 2 levels up
    return Path(__file__).parent.parent.parent


def create_isolated_env(base_path: Path, name: str = "test_env") -> IsolatedEnv:
    """Create an isolated environment with codeplane installed from source.

    Args:
        base_path: Base directory for the environment
        name: Name of the environment directory

    Returns:
        IsolatedEnv ready for testing
    """
    env_root = base_path / name
    env_root.mkdir(parents=True, exist_ok=True)

    venv_path = env_root / ".venv"
    python_path = venv_path / "bin" / "python"
    cpl_path = venv_path / "bin" / "cpl"

    # Create venv
    subprocess.run(
        [sys.executable, "-m", "venv", str(venv_path)],
        check=True,
        capture_output=True,
    )

    # Upgrade pip
    subprocess.run(
        [str(python_path), "-m", "pip", "install", "--upgrade", "pip"],
        check=True,
        capture_output=True,
    )

    # Install codeplane from source (editable mode)
    source_root = get_codeplane_source_root()
    subprocess.run(
        [str(python_path), "-m", "pip", "install", "-e", str(source_root)],
        check=True,
        capture_output=True,
        timeout=600,  # Tree-sitter deps can be slow
    )

    return IsolatedEnv(
        root=env_root,
        venv_path=venv_path,
        python_path=python_path,
        cpl_path=cpl_path,
    )
