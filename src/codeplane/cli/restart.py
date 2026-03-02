"""cpl restart command - stop and re-launch the CodePlane daemon."""

from __future__ import annotations

import time
from pathlib import Path

import click

from codeplane.cli.utils import find_repo_root
from codeplane.daemon.lifecycle import is_server_running, read_server_info, stop_daemon


@click.command()
@click.argument("path", default=None, required=False, type=click.Path(exists=True, path_type=Path))
@click.option(
    "--port",
    "-p",
    type=int,
    help="Server port (overrides config; persisted on init/reindex)",
)
@click.option(
    "-r",
    "--reindex",
    is_flag=True,
    help="Wipe and rebuild the entire index from scratch",
)
@click.pass_context
def restart_command(ctx: click.Context, path: Path | None, port: int | None, reindex: bool) -> None:
    """Restart the CodePlane daemon (stop then start).

    Stops the running daemon (if any), then starts a fresh instance.
    Equivalent to ``cpl down && cpl up``.

    PATH is the repository root. If not specified, auto-detects by walking
    up from the current directory to find the git root.
    """
    from codeplane.cli.up import up_command

    repo_root = find_repo_root(path)
    codeplane_dir = repo_root / ".codeplane"

    # ── Stop phase ──
    if codeplane_dir.exists() and is_server_running(codeplane_dir):
        info = read_server_info(codeplane_dir)
        if info:
            pid, old_port = info
            click.echo(f"Stopping daemon (PID {pid}, port {old_port})...")

            if not stop_daemon(codeplane_dir):
                click.echo("Failed to send stop signal.", err=True)
                raise SystemExit(1)

            # Wait for process to exit (up to 5 seconds)
            for _ in range(50):
                if not is_server_running(codeplane_dir):
                    click.echo("Daemon stopped.")
                    break
                time.sleep(0.1)
            else:
                click.echo("Daemon did not stop within 5 seconds.", err=True)
                raise SystemExit(1)
    else:
        click.echo("No running daemon found — starting fresh.")

    # ── Start phase — delegate to `cpl up` ──
    up_args: list[str] = []
    if port is not None:
        up_args.extend(["--port", str(port)])
    if reindex:
        up_args.append("--reindex")
    if path is not None:
        up_args.append(str(path))

    ctx.invoke(up_command, path=path, port=port, reindex=reindex)
