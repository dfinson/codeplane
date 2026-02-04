"""cpl up command - start the server.

Improved UX:
- Logo renders line-by-line with animation delay (Issue #3)
- Rich-based banner with centered text and rules
- Summary stream for startup progress
"""

import asyncio
from importlib.metadata import version
from pathlib import Path
from typing import cast

import click

from codeplane.cli.init import initialize_repo
from codeplane.cli.utils import find_repo_root
from codeplane.config.loader import load_config
from codeplane.config.models import CodePlaneConfig
from codeplane.core.progress import (
    animate_text,
    get_console,
    print_centered,
    print_rule,
)
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


def _print_banner(host: str, port: int, *, animate: bool = True) -> None:
    """Print startup banner with logo and info using Rich.

    Args:
        host: Server host
        port: Server port
        animate: If True, render logo line-by-line with delay (Issue #3)
    """
    ver = version("codeplane")
    console = get_console()

    if animate:
        # Render logo line-by-line with small delay for dramatic effect
        animate_text(LOGO, delay=0.015)
    else:
        console.print(LOGO, highlight=False)

    # Ready banner with rule separators
    console.print()
    print_rule(style="dim cyan")
    print_centered(f"CodePlane v{ver} · Ready", style="bold cyan")
    print_centered(f"Listening at http://{host}:{port}", style="green")
    print_rule(style="dim cyan")
    console.print()


@click.command()
@click.argument("path", default=None, required=False, type=click.Path(exists=True, path_type=Path))
@click.option("--port", "-p", type=int, help="Override server port")
def up_command(path: Path | None, port: int | None) -> None:
    """Start the CodePlane server for this repository.

    If already running, reports the existing instance. Runs in foreground.

    PATH is the repository root. If not specified, auto-detects by walking
    up from the current directory to find the git root.
    """
    from codeplane.daemon.lifecycle import is_server_running, read_server_info, run_server

    repo_root = find_repo_root(path)

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
    if not codeplane_dir.exists() and not initialize_repo(repo_root, show_cpl_up_hint=False):
        raise click.ClickException("Failed to initialize repository")

    # Get index paths from config AFTER init (so we read the created config)
    from codeplane.config.loader import get_index_paths

    db_path, tantivy_path = get_index_paths(repo_root)

    # Now create coordinator and load (quiet=True since lifecycle.py handles progress)
    coordinator = IndexCoordinator(
        repo_root=repo_root,
        db_path=db_path,
        tantivy_path=tantivy_path,
        quiet=True,
    )

    loop = asyncio.new_event_loop()
    try:
        if not loop.run_until_complete(coordinator.load_existing()):
            raise click.ClickException("Failed to load index")
    finally:
        loop.close()

    try:
        asyncio.run(run_server(repo_root, coordinator, config))
    except KeyboardInterrupt:
        click.echo("\nStopped")
    finally:
        coordinator.close()
