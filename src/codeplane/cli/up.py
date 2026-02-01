"""cpl up command - start the server."""

import asyncio
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
                          ++++++++++++++++++++++
                       *++++++++++++++++++++++++++*
                     *+++++++*              *+++++++*
                   *+++++++                    *++++++*
                  ++++++*                        *++++++
                *+++++                              +++++*
               ++++++                                *+++++
               +++++             ++++                 +++++
               *****              +++++                ****
                                    +++++
         *++++++++++++++++++++++     *++++ ++++++++++++++++++++++
                                    ++++*
               ++++*              ++++*               *++++
               +++++             ++++                 +++++
               *+++++                                +++++*
                 *+++++                            ++++++
                  *++++++*                       +++++++
                    ++++++++                  ++++++++
                     *++++++++++          ++++++++++*
                        *+++++++++++++++++++++++++
                           *++++++++++++++++++*
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
@click.option("--port", "-p", type=int, help="Override server port")
def up_command(path: Path, port: int | None) -> None:
    """Start the CodePlane server for this repository.

    If already running, reports the existing instance. Runs in foreground.

    PATH is the repository root (default: current directory).
    """
    repo_root = path.resolve()
    if not (repo_root / ".git").exists():
        raise click.ClickException(f"Not a git repository: {repo_root}")

    codeplane_dir = repo_root / ".codeplane"

    # Check if already running
    if is_daemon_running(codeplane_dir):
        info = read_daemon_info(codeplane_dir)
        if info:
            pid, daemon_port = info
            click.echo(f"Already running (PID {pid}, port {daemon_port})")
            return

    # Load config
    config = cast(CodePlaneConfig, load_config(repo_root))
    if port is not None:
        config.daemon.port = port

    # Get index paths from config (respects index.index_path for cross-filesystem)
    from codeplane.config.loader import get_index_paths

    db_path, tantivy_path = get_index_paths(repo_root)

    coordinator = IndexCoordinator(
        repo_root=repo_root,
        db_path=db_path,
        tantivy_path=tantivy_path,
    )

    # Load existing index or initialize if needed
    loop = asyncio.new_event_loop()
    try:
        loaded = loop.run_until_complete(coordinator.load_existing())
        if not loaded:
            # No valid index - run full initialization
            # initialize_repo handles config, grammars, AND index building
            if not initialize_repo(repo_root):
                raise click.ClickException("Failed to initialize repository")
            # Re-load now that index exists
            loop.run_until_complete(coordinator.load_existing())
    finally:
        loop.close()

    _print_banner(config.daemon.host, config.daemon.port)
    try:
        asyncio.run(run_daemon(repo_root, coordinator, config.daemon))
    except KeyboardInterrupt:
        click.echo("\nStopped")
    finally:
        coordinator.close()
