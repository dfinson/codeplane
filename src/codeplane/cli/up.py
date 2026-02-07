"""cpl up command - start the server.

Improved UX:
- Logo renders line-by-line with animation delay (Issue #3)
- Rich-based banner with centered text and rules
- Summary stream for startup progress
"""

import asyncio
from importlib.metadata import version
from pathlib import Path

import click

from codeplane.cli.init import initialize_repo
from codeplane.cli.utils import find_repo_root
from codeplane.config.loader import load_config
from codeplane.core.progress import (
    animate_text,
    get_console,
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


def _print_banner(
    host: str,
    port: int,
    repo_root: Path | None = None,
    *,
    animate: bool = True,
) -> None:
    """Print startup banner with logo and info using Rich.

    Args:
        host: Server host
        port: Server port
        repo_root: Repository root path (optional)
        animate: If True, render logo line-by-line with delay (Issue #3)
    """
    ver = version("codeplane")
    console = get_console()

    if animate:
        # Render logo line-by-line with small delay for dramatic effect
        animate_text(LOGO, delay=0.015)
    else:
        console.print(LOGO, highlight=False)

    # Ready banner with rule separators (fixed width to match logo ~64 chars)
    banner_width = 64
    rule_line = "─" * banner_width
    base_url = f"http://{host}:{port}"

    console.print()
    console.print(rule_line, style="dim cyan", highlight=False)
    console.print(
        f"CodePlane v{ver} · Ready".center(banner_width), style="bold cyan", highlight=False
    )
    console.print(rule_line, style="dim cyan", highlight=False)
    console.print()

    # Endpoint info
    console.print(f"  MCP Endpoint:    {base_url}/mcp", style="green", highlight=False)
    console.print(f"  Dashboard:       {base_url}/dashboard", highlight=False)
    console.print(f"  Health Check:    {base_url}/health", highlight=False)
    console.print(f"  Status:          {base_url}/status", highlight=False)

    if repo_root:
        console.print(f"  Repository:      {repo_root}", style="dim", highlight=False)

    console.print()


@click.command()
@click.argument("path", default=None, required=False, type=click.Path(exists=True, path_type=Path))
@click.option("--port", "-p", type=int, help="Override server port")
@click.option(
    "--reindex",
    is_flag=True,
    help="Wipe and rebuild the entire index from scratch",
)
def up_command(path: Path | None, port: int | None, reindex: bool) -> None:
    """Start the CodePlane server for this repository.

    If already running, reports the existing instance. Runs in foreground.

    PATH is the repository root. If not specified, auto-detects by walking
    up from the current directory to find the git root.
    """
    from datetime import datetime
    from uuid import uuid4

    from codeplane.config.models import LoggingConfig, LogOutputConfig
    from codeplane.core.logging import configure_logging
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
    config = load_config(repo_root)
    if port is not None:
        config.server.port = port

    # Initialize if needed, or reindex if requested
    codeplane_dir = repo_root / ".codeplane"
    if reindex and not initialize_repo(repo_root, reindex=True, show_cpl_up_hint=False):
        raise click.ClickException("Failed to reinitialize repository")
    elif (
        not reindex
        and not codeplane_dir.exists()
        and not initialize_repo(repo_root, show_cpl_up_hint=False)
    ):
        raise click.ClickException("Failed to initialize repository")

    # Get index paths from config AFTER init (so we read the created config)
    from codeplane.config.loader import get_index_paths

    db_path, tantivy_path = get_index_paths(repo_root)

    # Create coordinator and load existing index
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

    # Generate log file path
    # Format: .codeplane/logs/YYYY-MM-DD/HHMMSS-<6-digit-hash>.log
    now = datetime.now()
    server_run_id = uuid4().hex[:6]
    log_dir = codeplane_dir / "logs" / now.strftime("%Y-%m-%d")
    log_file = log_dir / f"{now.strftime('%H%M%S')}-{server_run_id}.log"

    # Configure logging: Console INFO, File DEBUG
    configure_logging(
        config=LoggingConfig(
            level="DEBUG",
            outputs=[
                LogOutputConfig(destination="stderr", format="console", level="INFO"),
                LogOutputConfig(destination=str(log_file), format="json", level="DEBUG"),
            ],
        ),
    )

    try:
        asyncio.run(run_server(repo_root, coordinator, config))
    except KeyboardInterrupt:
        click.echo("\nStopped")
    finally:
        coordinator.close()
