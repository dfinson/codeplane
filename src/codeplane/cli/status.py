"""cpl status command - show daemon status."""

import json
from pathlib import Path

import click
import httpx

from codeplane.daemon.lifecycle import is_daemon_running, read_daemon_info


@click.command()
@click.argument("path", default=".", type=click.Path(exists=True, path_type=Path))
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def status_command(path: Path, as_json: bool) -> None:
    """Show CodePlane daemon status.

    PATH is the repository root (default: current directory).
    """
    repo_root = path.resolve()
    if not (repo_root / ".git").exists():
        raise click.ClickException(
            f"'{repo_root}' is not a git repository. "
            "CodePlane must be run from a git repository root, or pass a path: cpl status PATH"
        )

    codeplane_dir = repo_root / ".codeplane"
    if not codeplane_dir.exists():
        if as_json:
            click.echo(json.dumps({"initialized": False}))
        else:
            click.echo("Repository not initialized. Run 'cpl init' first.")
        return

    if not is_daemon_running(codeplane_dir):
        if as_json:
            click.echo(json.dumps({"initialized": True, "running": False}))
        else:
            click.echo("Daemon: not running")
            click.echo(f"Repository: {repo_root}")
        return

    info = read_daemon_info(codeplane_dir)
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
            headers={"X-CodePlane-Repo": str(repo_root)},
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
