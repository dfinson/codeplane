"""CLI utilities."""

from pathlib import Path

import click


def find_repo_root(start_path: Path | None = None) -> Path:
    """Find the git repository root from the given path.

    Walks up the directory tree looking for a .git directory.
    If start_path is None, uses the current working directory.

    Args:
        start_path: Starting directory to search from

    Returns:
        Path to repository root

    Raises:
        click.ClickException: If not inside a git repository
    """
    if start_path is None:
        start_path = Path.cwd()

    current = start_path.resolve()

    # Walk up to find .git
    while current != current.parent:
        if (current / ".git").exists():
            return current
        current = current.parent

    # Check root as well
    if (current / ".git").exists():
        return current

    raise click.ClickException(
        f"Not inside a git repository: {start_path}\n"
        "CodePlane commands must be run from within a git repository."
    )
