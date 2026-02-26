"""cpl down command - stop the CodePlane daemon."""

from __future__ import annotations

import time
from pathlib import Path

import click

from codeplane.cli.utils import find_repo_root
from codeplane.daemon.lifecycle import is_server_running, read_server_info, stop_daemon


@click.command()
@click.argument("path", default=None, required=False, type=click.Path(exists=True, path_type=Path))
def down_command(path: Path | None) -> None:
    """Stop the CodePlane daemon.

    PATH is the repository root. If not specified, auto-detects by walking
    up from the current directory to find the git root.
    """
    repo_root = find_repo_root(path)
    codeplane_dir = repo_root / ".codeplane"

    if not codeplane_dir.exists():
        click.echo("Repository not initialized. Nothing to stop.")
        raise SystemExit(1)

    info = read_server_info(codeplane_dir)
    if info is None or not is_server_running(codeplane_dir):
        click.echo("Daemon is not running.")
        return

    pid, port = info
    click.echo(f"Stopping daemon (PID {pid}, port {port})...")

    if not stop_daemon(codeplane_dir):
        click.echo("Failed to send stop signal.", err=True)
        raise SystemExit(1)

    # Wait for process to exit (up to 5 seconds)
    for _ in range(50):
        if not is_server_running(codeplane_dir):
            click.echo("Daemon stopped.")
            return
        time.sleep(0.1)

    click.echo("Daemon did not stop within 5 seconds.", err=True)
    raise SystemExit(1)
