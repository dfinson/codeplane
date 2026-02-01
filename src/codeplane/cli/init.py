"""cpl init command - initialize a repository for CodePlane."""

import asyncio
import hashlib
import math
import sys
from pathlib import Path

import click
import yaml
from rich.console import Console
from rich.padding import Padding
from rich.table import Table

from codeplane.config.models import CodePlaneConfig
from codeplane.core.progress import status
from codeplane.templates import get_cplignore_template


def _is_cross_filesystem(path: Path) -> bool:
    """Detect if path is on a cross-filesystem mount (WSL /mnt/*, network drives, etc.)."""
    resolved = path.resolve()
    path_str = str(resolved)
    # WSL accessing Windows filesystem
    if path_str.startswith("/mnt/") and len(path_str) > 5 and path_str[5].isalpha():
        return True
    # Common network/remote mounts
    return path_str.startswith(("/run/user/", "/media/", "/net/"))


def _get_xdg_index_dir(repo_root: Path) -> Path:
    """Get XDG-compliant index directory for a repo."""
    xdg_data = Path.home() / ".local" / "share" / "codeplane" / "indices"
    repo_hash = hashlib.sha256(str(repo_root.resolve()).encode()).hexdigest()[:12]
    return xdg_data / repo_hash


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

    # Determine index storage location before writing config
    # Cross-filesystem paths (WSL /mnt/*) need index on native filesystem
    index_dir: Path
    if _is_cross_filesystem(repo_root):
        index_dir = _get_xdg_index_dir(repo_root)
        index_dir.mkdir(parents=True, exist_ok=True)
        if not quiet:
            status(
                f"Cross-filesystem detected, storing index at: {index_dir}",
                style="info",
                indent=2,
            )
    else:
        index_dir = codeplane_dir

    # Create config with index_path
    config = CodePlaneConfig()
    config.index.index_path = str(index_dir)
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

    db_path = index_dir / "index.db"
    tantivy_path = index_dir / "tantivy"
    tantivy_path.mkdir(exist_ok=True)

    coord = IndexCoordinator(
        repo_root=repo_root,
        db_path=db_path,
        tantivy_path=tantivy_path,
        quiet=quiet,
    )

    try:
        import time

        start_time = time.time()
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(coord.initialize())
        finally:
            loop.close()
        elapsed = time.time() - start_time

        if result.errors:
            for err in result.errors:
                status(f"Error: {err}", style="error")
            return False

        if not quiet:
            status(
                f"Indexed {result.files_indexed} files in {elapsed:.1f}s", style="success", indent=2
            )
            # Show breakdown by extension
            if result.files_by_ext:
                sorted_exts = sorted(
                    result.files_by_ext.items(),
                    key=lambda x: -x[1],  # Sort by count descending
                )
                max_count = sorted_exts[0][1] if sorted_exts else 1
                max_sqrt = math.sqrt(max_count)
                table = Table(show_header=False, box=None, padding=(0, 1), pad_edge=False)
                table.add_column("ext", style="cyan", width=12)
                table.add_column("count", style="white", justify="right", width=4)
                table.add_column("bar")
                for ext, count in sorted_exts[:8]:
                    bar_width = max(1, int(math.sqrt(count) / max_sqrt * 20))
                    bar = f"[green]{'━' * bar_width}[/green][dim]{'━' * (20 - bar_width)}[/dim]"
                    table.add_row(ext, str(count), bar)
                rest = sorted_exts[8:]
                if rest:
                    rest_count = sum(c for _, c in rest)
                    bar_width = max(1, int(math.sqrt(rest_count) / max_sqrt * 20))
                    bar = f"[dim green]{'━' * bar_width}[/dim green][dim]{'━' * (20 - bar_width)}[/dim]"
                    table.add_row("other", str(rest_count), bar, style="dim")
                console = Console(stderr=True)
                console.print(Padding(table, (0, 0, 0, 4)))
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
