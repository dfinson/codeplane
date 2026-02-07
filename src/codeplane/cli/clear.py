"""cpl clear command - remove CodePlane data from a repository."""

import shutil
from pathlib import Path

import click
import questionary
from rich.console import Console

from codeplane.cli.init import _get_xdg_index_dir
from codeplane.cli.utils import find_repo_root


def clear_repo(repo_root: Path, *, yes: bool = False) -> bool:
    """Remove all CodePlane data from a repository.

    This removes:
    - .codeplane/ directory (config, local index if stored there)
    - XDG index directory (for cross-filesystem setups like WSL)

    Returns True if cleared successfully, False if cancelled or nothing to clear.
    """
    console = Console(stderr=True)
    codeplane_dir = repo_root / ".codeplane"
    xdg_index_dir = _get_xdg_index_dir(repo_root)

    # Check what exists
    has_codeplane_dir = codeplane_dir.exists()
    has_xdg_index = xdg_index_dir.exists()

    if not has_codeplane_dir and not has_xdg_index:
        console.print("[yellow]Nothing to clear[/yellow] - no CodePlane data found")
        return False

    # Show what will be deleted
    console.print("\n[bold]The following will be permanently deleted:[/bold]\n")

    if has_codeplane_dir:
        console.print(f"  [cyan]•[/cyan] {codeplane_dir}")

    if has_xdg_index:
        console.print(f"  [cyan]•[/cyan] {xdg_index_dir}")

    console.print()

    # Confirm unless --yes
    if not yes:
        answer = questionary.select(
            "This action cannot be undone. Are you sure?",
            choices=[
                questionary.Choice("No, keep my data", value=False),
                questionary.Choice("Yes, delete everything", value=True),
            ],
            style=questionary.Style(
                [
                    ("question", "bold"),
                    ("highlighted", "fg:red bold"),
                    ("selected", "fg:red"),
                ]
            ),
        ).ask()

        if not answer:
            console.print("[dim]Cancelled[/dim]")
            return False

    # Delete
    errors: list[str] = []

    if has_codeplane_dir:
        try:
            shutil.rmtree(codeplane_dir)
            console.print(f"  [green]✓[/green] Removed {codeplane_dir}")
        except OSError as e:
            errors.append(f"Failed to remove {codeplane_dir}: {e}")
            console.print(f"  [red]✗[/red] Failed to remove {codeplane_dir}: {e}")

    if has_xdg_index:
        try:
            shutil.rmtree(xdg_index_dir)
            console.print(f"  [green]✓[/green] Removed {xdg_index_dir}")
        except OSError as e:
            errors.append(f"Failed to remove {xdg_index_dir}: {e}")
            console.print(f"  [red]✗[/red] Failed to remove {xdg_index_dir}: {e}")

    if errors:
        return False

    console.print("\n[green]CodePlane data cleared successfully[/green]")
    return True


@click.command()
@click.argument("path", default=None, required=False, type=click.Path(exists=True, path_type=Path))
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
def clear_command(path: Path | None, yes: bool) -> None:
    """Remove all CodePlane data from a repository.

    This removes the .codeplane/ directory and any associated index files
    (including cross-filesystem index storage for WSL setups).

    PATH is the repository root. If not specified, auto-detects by walking
    up from the current directory to find the git root.
    """
    repo_root = find_repo_root(path)

    if not clear_repo(repo_root, yes=yes):
        if not yes:
            return  # Cancelled or nothing to clear
        raise click.ClickException("Failed to clear CodePlane data")
