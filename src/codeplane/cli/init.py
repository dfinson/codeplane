"""cpl init command - initialize a repository for CodePlane."""

import asyncio
import sys
from pathlib import Path

import click
import yaml

from codeplane.config.models import CodePlaneConfig
from codeplane.core.progress import status
from codeplane.templates import get_cplignore_template


def initialize_repo(repo_root: Path, *, force: bool = False, quiet: bool = False) -> bool:
    """Initialize a repository for CodePlane, returning True on success."""
    codeplane_dir = repo_root / ".codeplane"

    if codeplane_dir.exists() and not force:
        if not quiet:
            status(f"Already initialized: {codeplane_dir}", style="info")
            status("Use --force to reinitialize", style="info")
        return False

    if not quiet:
        status(f"Initializing CodePlane in {repo_root}", style="none")

    # If force is set and directory exists, remove it completely to start fresh
    if force and codeplane_dir.exists():
        import shutil

        shutil.rmtree(codeplane_dir)

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
        status("Config created", style="success", indent=2)

    # Scan repo and install any needed tree-sitter grammars
    from codeplane.index._internal.grammars import ensure_grammars_for_repo

    status_fn = status if not quiet else None
    if not ensure_grammars_for_repo(repo_root, quiet=quiet, status_fn=status_fn) and not quiet:
        status("Warning: some grammars failed to install", style="warning", indent=2)

    # Build initial index per SPEC.md §4.2
    if not quiet:
        status("Building index...", style="none", indent=2)

    from codeplane.index.ops import IndexCoordinator

    db_path = codeplane_dir / "index.db"
    tantivy_path = codeplane_dir / "tantivy"
    tantivy_path.mkdir(exist_ok=True)

    coord = IndexCoordinator(
        repo_root=repo_root,
        db_path=db_path,
        tantivy_path=tantivy_path,
        quiet=quiet,
    )

    try:
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(coord.initialize())
        finally:
            loop.close()

        if result.errors:
            for err in result.errors:
                status(f"Error: {err}", style="error")
            return False

        if not quiet:
            status(
                f"Indexed {result.files_indexed} files, {result.contexts_valid} contexts",
                style="success",
                indent=2,
            )
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
