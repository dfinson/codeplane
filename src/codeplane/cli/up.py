"""cpl up command - start the server."""

import asyncio
from importlib.metadata import version
from pathlib import Path
from typing import cast

import click

from codeplane.cli.init import initialize_repo
from codeplane.config.loader import load_config
from codeplane.config.models import CodePlaneConfig
from codeplane.daemon.lifecycle import is_server_running, read_server_info, run_server
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
    if is_server_running(codeplane_dir):
        info = read_server_info(codeplane_dir)
        if info:
            pid, server_port = info
            click.echo(f"Already running (PID {pid}, port {server_port})")
            return

    # Load config
    config = cast(CodePlaneConfig, load_config(repo_root))
    if port is not None:
        config.server.port = port

    # Initialize if needed (this creates config with correct index_path)
    codeplane_dir = repo_root / ".codeplane"
    if not codeplane_dir.exists() and not initialize_repo(repo_root):
        raise click.ClickException("Failed to initialize repository")

    # Get index paths from config AFTER init (so we read the created config)
    from codeplane.config.loader import get_index_paths

    db_path, tantivy_path = get_index_paths(repo_root)

    # Now create coordinator and load
    coordinator = IndexCoordinator(
        repo_root=repo_root,
        db_path=db_path,
        tantivy_path=tantivy_path,
    )

    loop = asyncio.new_event_loop()
    try:
        if not loop.run_until_complete(coordinator.load_existing()):
            raise click.ClickException("Failed to load index")
    finally:
        loop.close()

    try:
        asyncio.run(run_server(repo_root, coordinator, config.server))
    except KeyboardInterrupt:
        click.echo("\nStopped")
    finally:
        coordinator.close()
