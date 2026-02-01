"""cpl init command - initialize a repository for CodePlane."""

import asyncio
import sys
from pathlib import Path

import click
import yaml

from codeplane.config.models import CodePlaneConfig
from codeplane.templates import get_cplignore_template


def initialize_repo(repo_root: Path, *, force: bool = False, quiet: bool = False) -> bool:
    """Initialize a repository for CodePlane. Returns True on success.

    Args:
        repo_root: Path to the git repository root
        force: Overwrite existing .codeplane directory
        quiet: Suppress output (for auto-init from `cpl up`)

    Returns:
        True if initialization succeeded, False if already initialized (and not force)
    """
    codeplane_dir = repo_root / ".codeplane"

    if codeplane_dir.exists() and not force:
        if not quiet:
            click.echo(f"Already initialized: {codeplane_dir}")
            click.echo("Use --force to reinitialize")
        return False

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

    if not quiet:
        click.echo(f"Initialized CodePlane in {repo_root}")
        click.echo(f"  Config: {config_path.relative_to(repo_root)}")
        click.echo(f"  Ignore: {cplignore_path.relative_to(repo_root)}")

    # Build initial index per SPEC.md §4.2
    if not quiet:
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
            return False

        if not quiet:
            click.echo(f"  Indexed {result.files_indexed} files")
            click.echo(f"  Contexts: {result.contexts_valid} valid")
    finally:
        coord.close()

    return True


@click.command()
@click.argument("path", default=".", type=click.Path(exists=True, path_type=Path))
@click.option("--force", "-f", is_flag=True, help="Overwrite existing .codeplane directory")
def init_command(path: Path, force: bool) -> None:
    """Initialize a repository for CodePlane management.

    Creates .codeplane/ directory with default configuration and builds
    the initial index. Must be run from the git repository root.

    PATH is the repository root (default: current directory).
    """
    repo_root = path.resolve()
    if not (repo_root / ".git").exists():
        raise click.ClickException(
            f"'{repo_root}' is not a git repository. "
            "CodePlane must be run from a git repository root, or pass a path: cpl init PATH"
        )

    if not initialize_repo(repo_root, force=force):
        if not force:
            return  # Already initialized, message printed
        sys.exit(1)  # Errors occurred
