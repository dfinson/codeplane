"""cpl init command - initialize a repository for CodePlane."""

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

    Creates .codeplane/ directory with default configuration.
    Must be run inside a git repository.

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

    ignore_path = repo_root / ".cplignore"
    if not ignore_path.exists() or force:
        ignore_path.write_text(get_cplignore_template())

    click.echo(f"Initialized CodePlane in {repo_root}")
    click.echo(f"  Config: {config_path}")
    click.echo(f"  Ignore: {ignore_path}")
