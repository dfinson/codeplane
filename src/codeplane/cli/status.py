"""cpl status command - show daemon status."""

import json
from pathlib import Path

import click
import httpx

from codeplane.cli.utils import find_repo_root
from codeplane.daemon.lifecycle import is_server_running, read_server_info


@click.command()
@click.argument("path", default=None, required=False, type=click.Path(exists=True, path_type=Path))
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def status_command(path: Path | None, as_json: bool) -> None:
    """Show CodePlane daemon status.

    PATH is the repository root. If not specified, auto-detects by walking
    up from the current directory to find the git root.
    """
    repo_root = find_repo_root(path)

    codeplane_dir = repo_root / ".codeplane"
    if not codeplane_dir.exists():
        if as_json:
            click.echo(json.dumps({"initialized": False}))
        else:
            click.echo("Repository not initialized. Run 'cpl init' first.")
        return

    if not is_server_running(codeplane_dir):
        if as_json:
            click.echo(json.dumps({"initialized": True, "running": False}))
        else:
            click.echo("Daemon: not running")
            click.echo(f"Repository: {repo_root}")
        return

    info = read_server_info(codeplane_dir)
    if info is None:
        if as_json:
            click.echo(json.dumps({"initialized": True, "running": False}))
        else:
            click.echo("Daemon: not running (stale PID file)")
        return

    pid, port = info

    # Query daemon status
    try:
        response = httpx.get(
            f"http://127.0.0.1:{port}/status",
            timeout=5.0,
        )
        status_data = response.json()
    except (httpx.RequestError, json.JSONDecodeError) as e:
        if as_json:
            click.echo(
                json.dumps(
                    {
                        "initialized": True,
                        "running": True,
                        "pid": pid,
                        "port": port,
                        "error": str(e),
                    }
                )
            )
        else:
            click.echo(f"Daemon: running (PID {pid}, port {port})")
            click.echo(f"Status: unavailable ({e})")
        return

    if as_json:
        click.echo(
            json.dumps(
                {
                    "initialized": True,
                    "running": True,
                    "pid": pid,
                    "port": port,
                    **status_data,
                }
            )
        )
    else:
        click.echo(f"Daemon: running (PID {pid}, port {port})")
        click.echo(f"Repository: {repo_root}")

        indexer = status_data.get("indexer", {})
        click.echo(f"Indexer: {indexer.get('state', 'unknown')}")
        if indexer.get("queue_size", 0) > 0:
            click.echo(f"  Queue: {indexer['queue_size']} pending")
        if indexer.get("last_error"):
            click.echo(f"  Last error: {indexer['last_error']}")

        watcher = status_data.get("watcher", {})
        click.echo(f"Watcher: {'active' if watcher.get('running') else 'stopped'}")
