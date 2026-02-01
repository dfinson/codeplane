"""cpl init command - initialize a repository for CodePlane."""

import asyncio
import sys
from pathlib import Path

import click
import yaml

from codeplane.config.models import CodePlaneConfig
from codeplane.templates import get_cplignore_template


def _find_git_root(start: Path) -> Path | None:
    """Find .git directory walking up from start."""
    current = start.resolve()
    while current != current.parent:
        if (current / ".git").exists():
            return current
        current = current.parent
    return None


@click.command()
@click.argument("path", default=".", type=click.Path(exists=True, path_type=Path))
@click.option("--force", "-f", is_flag=True, help="Overwrite existing .codeplane directory")
def init_command(path: Path, force: bool) -> None:
    """Initialize a repository for CodePlane management.

    Creates .codeplane/ directory with default configuration and builds
    the initial index. Must be run inside a git repository.

    PATH is the repository path (default: current directory).
    """
    repo_root = _find_git_root(path.resolve())
    if repo_root is None:
        raise click.ClickException("Not inside a git repository")

    codeplane_dir = repo_root / ".codeplane"

    if codeplane_dir.exists() and not force:
        click.echo(f"Already initialized: {codeplane_dir}")
        click.echo("Use --force to reinitialize")
        return

    codeplane_dir.mkdir(exist_ok=True)

    config = CodePlaneConfig()
    config_path = codeplane_dir / "config.yaml"
    with config_path.open("w") as f:
        yaml.dump(config.model_dump(), f, default_flow_style=False, sort_keys=False)

    cplignore_path = codeplane_dir / ".cplignore"
    if not cplignore_path.exists() or force:
        cplignore_path.write_text(get_cplignore_template())

    # Create .gitignore to exclude artifacts from version control per SPEC.md §7.7
    gitignore_path = codeplane_dir / ".gitignore"
    if not gitignore_path.exists() or force:
        gitignore_path.write_text(
            "# Ignore everything except config files\n*\n!.gitignore\n!config.yaml\n"
        )

    click.echo(f"Initialized CodePlane in {repo_root}")
    click.echo(f"  Config: {config_path.relative_to(repo_root)}")
    click.echo(f"  Ignore: {cplignore_path.relative_to(repo_root)}")

    # Build initial index per SPEC.md §4.2
    click.echo("Building initial index...")

    from codeplane.index.ops import IndexCoordinator

    db_path = codeplane_dir / "index.db"
    tantivy_path = codeplane_dir / "tantivy"
    tantivy_path.mkdir(exist_ok=True)

    coord = IndexCoordinator(
        repo_root=repo_root,
        db_path=db_path,
        tantivy_path=tantivy_path,
    )

    try:
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(coord.initialize())
        finally:
            loop.close()

        if result.errors:
            for err in result.errors:
                click.echo(f"Error: {err}", err=True)
            sys.exit(1)

        click.echo(f"  Indexed {result.files_indexed} files")
        click.echo(f"  Contexts: {result.contexts_valid} valid")
    finally:
        coord.close()
