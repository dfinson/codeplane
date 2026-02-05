"""cpl init command - initialize a repository for CodePlane."""

import asyncio
import hashlib
import math
import sys
from pathlib import Path

import click
from rich.table import Table

from codeplane.config.user_config import (
    RuntimeState,
    UserConfig,
    write_runtime_state,
    write_user_config,
)
from codeplane.core.progress import (
    get_console,
    phase_box,
    status,
)
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


def initialize_repo(repo_root: Path, *, force: bool = False, show_cpl_up_hint: bool = True) -> bool:
    """Initialize a repository for CodePlane, returning True on success.

    Args:
        repo_root: Path to the repository root
        force: Overwrite existing .codeplane directory
        show_cpl_up_hint: Show "Run 'cpl up'" hint at end (False when auto-init from cpl up)
    """
    codeplane_dir = repo_root / ".codeplane"
    console = get_console()

    if codeplane_dir.exists() and not force:
        status(f"Already initialized: {codeplane_dir}", style="info")
        status("Use --force to reinitialize", style="info")
        return False

    console.print()
    status(f"Initializing CodePlane in {repo_root}", style="none")
    console.print()

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
        status(
            f"Cross-filesystem detected, storing index at: {index_dir}",
            style="info",
        )
    else:
        index_dir = codeplane_dir

    # Write minimal user config
    config_path = codeplane_dir / "config.yaml"
    write_user_config(config_path, UserConfig())

    # Write runtime state (index_path) - auto-generated, not user-editable
    state_path = codeplane_dir / "state.yaml"
    write_runtime_state(state_path, RuntimeState(index_path=str(index_dir)))

    cplignore_path = codeplane_dir / ".cplignore"
    if not cplignore_path.exists() or force:
        cplignore_path.write_text(get_cplignore_template())

    # Create .gitignore to exclude artifacts from version control per SPEC.md §7.7
    gitignore_path = codeplane_dir / ".gitignore"
    if not gitignore_path.exists() or force:
        gitignore_path.write_text(
            "# Ignore everything except user config files\n"
            "*\n"
            "!.gitignore\n"
            "!config.yaml\n"
            "# state.yaml is auto-generated, do not commit\n"
        )

    # === Discovery Phase ===
    from codeplane.index._internal.grammars import (
        get_needed_grammars,
        install_grammars,
        scan_repo_languages,
    )

    with phase_box("Discovery", width=60) as phase:
        # Step 1: Scan languages
        task_id = phase.add_progress("Scanning", total=100)
        languages = scan_repo_languages(repo_root)
        phase.advance(task_id, 100)
        # Use .value to get string name, not enum repr
        lang_names = ", ".join(sorted(lang.value for lang in languages)) if languages else "none"
        phase.complete(f"{len(languages)} languages: {lang_names}")

        # Step 2: Install grammars if needed
        needed = get_needed_grammars(languages)
        if needed:
            task_id = phase.add_progress("Installing grammars", total=len(needed))
            grammar_result = install_grammars(needed, quiet=True, status_fn=None)
            phase.advance(task_id, len(needed))
            if grammar_result.success:
                phase.complete(f"{len(needed)} grammars installed")
            else:
                failed_list = ", ".join(grammar_result.failed_packages)
                phase.complete(
                    f"{len(grammar_result.installed_packages)} installed, {len(grammar_result.failed_packages)} failed",
                    style="yellow",
                )
                phase.add_text(f"Failed grammars: {failed_list}", style="yellow")
                phase.add_text(
                    "Note: Files without grammars are still searchable via text search",
                    style="dim",
                )
        else:
            phase.complete("Grammars ready")

    # === Indexing Phase ===
    from codeplane.index.ops import IndexCoordinator

    db_path = index_dir / "index.db"
    tantivy_path = index_dir / "tantivy"
    tantivy_path.mkdir(exist_ok=True)

    coord = IndexCoordinator(
        repo_root=repo_root,
        db_path=db_path,
        tantivy_path=tantivy_path,
    )

    try:
        import time

        start_time = time.time()

        # Run indexing with phase box and live table updates
        with phase_box("Indexing", width=60) as phase:
            # Progress callback to update the live table
            def on_index_progress(indexed: int, total: int, files_by_ext: dict[str, int]) -> None:
                # Update progress bar (scale to 100)
                if total > 0:
                    pct = int(indexed * 100 / total)
                    phase._progress.update(task_id, completed=pct)  # type: ignore[union-attr]
                # Update live table with current file type counts
                if files_by_ext:
                    table = _make_init_extension_table(files_by_ext)
                    phase.set_live_table(table)

            task_id = phase.add_progress("Indexing files", total=100)

            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(
                    coord.initialize(on_index_progress=on_index_progress)
                )
            finally:
                loop.close()

            elapsed = time.time() - start_time

            if result.errors:
                for err in result.errors:
                    phase.add_text(f"Error: {err}", style="red")
                return False

            # Final table state and completion message
            phase.set_live_table(None)  # Remove live table
            phase.complete(f"{result.files_indexed} files indexed ({elapsed:.1f}s)")

            # Add final extension breakdown as static content
            if result.files_by_ext:
                phase.add_text("")  # Spacer
                ext_table = _make_init_extension_table(result.files_by_ext)
                phase.add_table(ext_table)
    finally:
        coord.close()

    # Final config confirmation
    console.print()
    rel_config_path = config_path.relative_to(repo_root)
    status(f"Config created at {rel_config_path}", style="success")

    if show_cpl_up_hint:
        console.print()
        status("Ready. Run 'cpl up' to start the server.", style="none")

    return True


def _make_init_extension_table(files_by_ext: dict[str, int]) -> Table:
    """Create extension breakdown table for init output."""
    sorted_exts = sorted(files_by_ext.items(), key=lambda x: -x[1])
    if not sorted_exts:
        return Table(show_header=False, box=None)

    max_count = sorted_exts[0][1]
    max_sqrt = math.sqrt(max_count) if max_count > 0 else 1

    table = Table(show_header=False, box=None, padding=(0, 1), pad_edge=False)
    table.add_column("ext", style="cyan", width=8)
    table.add_column("count", style="white", justify="right", width=4)
    table.add_column("bar", width=20)

    for ext, count in sorted_exts[:8]:
        bar_width = max(1, int(math.sqrt(count) / max_sqrt * 20)) if max_sqrt > 0 else 1
        bar = f"[green]{'━' * bar_width}[/green][dim]{'━' * (20 - bar_width)}[/dim]"
        table.add_row(ext, str(count), bar)

    rest = sorted_exts[8:]
    if rest:
        rest_count = sum(c for _, c in rest)
        bar_width = max(1, int(math.sqrt(rest_count) / max_sqrt * 20)) if max_sqrt > 0 else 1
        bar = f"[dim green]{'━' * bar_width}[/dim green][dim]{'━' * (20 - bar_width)}[/dim]"
        table.add_row("other", str(rest_count), bar, style="dim")

    return table


@click.command()
@click.argument("path", default=None, required=False, type=click.Path(exists=True, path_type=Path))
@click.option("--force", "-f", is_flag=True, help="Overwrite existing .codeplane directory")
def init_command(path: Path | None, force: bool) -> None:
    """Initialize a repository for CodePlane management.

    Creates .codeplane/ directory with default configuration and builds
    the initial index.

    PATH is the repository root. If not specified, auto-detects by walking
    up from the current directory to find the git root.
    """
    from codeplane.cli.utils import find_repo_root

    repo_root = find_repo_root(path)

    if not initialize_repo(repo_root, force=force):
        if not force:
            return  # Already initialized, message printed
        sys.exit(1)  # Errors occurred
