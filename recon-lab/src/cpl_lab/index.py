"""Index cloned repos with `cpl init` — ported from index_all.sh."""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import click


def _find_cpl() -> str:
    """Find the cpl binary, preferring the codeplane venv."""
    from cpl_lab.collect import _find_cpl_bin
    return _find_cpl_bin()


def _iter_clones(clones_dir: Path, repo_set: str) -> list[Path]:
    """Yield clone directories for the given repo set."""
    if repo_set == "all":
        set_dirs = sorted(d for d in clones_dir.iterdir() if d.is_dir())
    else:
        target = clones_dir / repo_set
        if not target.is_dir():
            raise click.ClickException(f"Set directory not found: {target}")
        set_dirs = [target]

    repos = []
    for set_dir in set_dirs:
        for repo_dir in sorted(set_dir.iterdir()):
            if (repo_dir / ".git").is_dir():
                repos.append(repo_dir)
    return repos


def run_index(
    clones_dir: Path,
    repo_set: str = "all",
    timeout: int = 1800,
    reindex: bool = False,
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    """Run `cpl init` on each clone."""
    cpl = _find_cpl()
    repos = _iter_clones(clones_dir, repo_set)

    ok = skipped = failed = 0

    for repo_dir in repos:
        rel = f"{repo_dir.parent.name}/{repo_dir.name}"

        if not reindex and (repo_dir / ".codeplane").is_dir():
            if verbose:
                click.echo(f"SKIP {rel} (already indexed)")
            skipped += 1
            continue

        if dry_run:
            click.echo(f"[dry-run] would index {rel}")
            continue

        click.echo(f"INDEX {rel} ... ", nl=False)
        start = time.monotonic()

        cmd = [cpl, "init"]
        if reindex:
            cmd.append("-r")
        cmd.append(str(repo_dir))

        try:
            subprocess.run(
                cmd, timeout=timeout, check=True,
                capture_output=True, text=True,
            )
            elapsed = time.monotonic() - start
            click.echo(f"OK ({elapsed:.0f}s)")
            ok += 1
        except subprocess.TimeoutExpired:
            elapsed = time.monotonic() - start
            click.echo(f"TIMEOUT ({elapsed:.0f}s)")
            failed += 1
        except subprocess.CalledProcessError as e:
            elapsed = time.monotonic() - start
            click.echo(f"FAILED exit={e.returncode} ({elapsed:.0f}s)")
            if verbose and e.stderr:
                click.echo(f"  {e.stderr[:200]}")
            failed += 1

    click.echo(f"\nTotal: {len(repos)} | OK: {ok} | Skipped: {skipped} | Failed: {failed}")
