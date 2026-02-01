"""cpl up command - start the daemon."""

import asyncio
import os
import sys
from importlib.metadata import version
from pathlib import Path
from typing import cast

import click

from codeplane.cli.init import initialize_repo
from codeplane.config.loader import load_config
from codeplane.config.models import CodePlaneConfig
from codeplane.daemon.lifecycle import is_daemon_running, read_daemon_info, run_daemon
from codeplane.index.ops import IndexCoordinator

LOGO = r"""
                        *+++++++++++++*
                     +++++++++++++++++++++
                  ++++++++++++***++++++++++++
                ++++++++*              ++++++++
               +++++++                   +++++++
              ++++++                       ++++++
             ++++++                         *+++++
            ++++++          *++              +++++*
            +++++           +++++             +++++
                             *+++++
        +++++++++++++++++++*   +++++*++++++++++++++++++
                             ++++++
            +++++           +++++             +++++
            ++++++          *++              ++++++
             ++++++                         ++++++
              ++++++                       ++++++
               +++++++                   +++++++
                +++++++++             +++++++++
                  +++++++++++++++++++++++++++
                     +++++++++++++++++++++*
                         +++++++++++++*
"""


def _print_banner(host: str, port: int) -> None:
    """Print startup banner with logo and info."""
    ver = version("codeplane")
    click.echo(LOGO)
    click.echo(click.style("  CodePlane", fg="cyan", bold=True) + f" v{ver}")
    click.echo("  Local repository control plane for AI coding agents")
    click.echo()
    click.echo(f"  Listening on {click.style(f'http://{host}:{port}', fg='green')}")
    click.echo(f"  Press {click.style('Ctrl+C', bold=True)} to stop")
    click.echo()


@click.command()
@click.argument("path", default=".", type=click.Path(exists=True, path_type=Path))
@click.option("--foreground", "-f", is_flag=True, help="Run in foreground (don't daemonize)")
@click.option("--port", "-p", type=int, help="Override daemon port")
def up_command(path: Path, foreground: bool, port: int | None) -> None:
    """Start the CodePlane daemon for this repository.

    If already running, reports the existing daemon. Idempotent.

    PATH is the repository root (default: current directory).
    """
    repo_root = path.resolve()
    if not (repo_root / ".git").exists():
        raise click.ClickException(
            f"Not a git repository: {repo_root}"
        )

    codeplane_dir = repo_root / ".codeplane"

    # Auto-init if not initialized
    if not codeplane_dir.exists():
        click.echo("Initializing repository...")
        if not initialize_repo(repo_root):
            raise click.ClickException("Failed to initialize repository")

    # Check if already running
    if is_daemon_running(codeplane_dir):
        info = read_daemon_info(codeplane_dir)
        if info:
            pid, daemon_port = info
            click.echo(f"Daemon already running (PID {pid}, port {daemon_port})")
            return

    # Load config
    config = cast(CodePlaneConfig, load_config(repo_root))
    if port is not None:
        config.daemon.port = port

    # Initialize coordinator
    db_path = codeplane_dir / "index.db"
    tantivy_path = codeplane_dir / "tantivy"

    coordinator = IndexCoordinator(
        repo_root=repo_root,
        db_path=db_path,
        tantivy_path=tantivy_path,
    )

    # Initialize if needed (loads existing index)
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(coordinator.initialize())
    finally:
        loop.close()

    if foreground:
        # Run in foreground
        _print_banner(config.daemon.host, config.daemon.port)
        try:
            asyncio.run(run_daemon(repo_root, coordinator, config.daemon))
        except KeyboardInterrupt:
            click.echo("\nDaemon stopped")
        finally:
            coordinator.close()
    else:
        # Fork to background
        pid = os.fork()
        if pid > 0:
            # Parent process
            click.echo(f"Daemon started (PID {pid}, port {config.daemon.port})")
            sys.exit(0)
        else:
            # Child process - become daemon
            os.setsid()

            # Close standard file descriptors
            sys.stdin.close()
            sys.stdout.close()
            sys.stderr.close()

            # Redirect to /dev/null (intentionally not using context manager for daemonization)
            sys.stdin = open(os.devnull)  # noqa: SIM115
            sys.stdout = open(os.devnull, "w")  # noqa: SIM115
            sys.stderr = open(os.devnull, "w")  # noqa: SIM115

            try:
                asyncio.run(run_daemon(repo_root, coordinator, config.daemon))
            finally:
                coordinator.close()
