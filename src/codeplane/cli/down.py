"""cpl down command - stop the daemon."""

from pathlib import Path

import click

from codeplane.daemon.lifecycle import is_daemon_running, read_daemon_info, stop_daemon


@click.command()
@click.argument("path", default=".", type=click.Path(exists=True, path_type=Path))
def down_command(path: Path) -> None:
    """Stop the CodePlane daemon for this repository.

    If not running, reports that. Idempotent.

    PATH is the repository root (default: current directory).
    """
    repo_root = path.resolve()
    if not (repo_root / ".git").exists():
        raise click.ClickException(
            f"Not a git repository: {repo_root}"
        )

    codeplane_dir = repo_root / ".codeplane"
    if not codeplane_dir.exists():
        click.echo("Repository not initialized. Nothing to stop.")
        return

    if not is_daemon_running(codeplane_dir):
        click.echo("Daemon is not running")
        return

    info = read_daemon_info(codeplane_dir)
    if info:
        pid, port = info
        click.echo(f"Stopping daemon (PID {pid}, port {port})...")

    if stop_daemon(codeplane_dir):
        click.echo("Daemon stopped")
    else:
        click.echo("Failed to stop daemon (may have already exited)")
