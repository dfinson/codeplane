"""Collect retrieval signals — Rich UI with per-worker progress."""

from __future__ import annotations

import os
import time
from pathlib import Path

import click
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TaskProgressColumn, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich.text import Text


console = Console()


def _iter_repos(data_dir: Path, repo_set: str) -> list[str]:
    """List repo IDs for signal collection (training sets only)."""
    from cpl_lab.clone import REPO_MANIFEST
    train_sets = {"ranker-gate", "cutoff"}
    allowed = {repo_set} if repo_set != "all" else train_sets
    train_ids = {rid for rid, info in REPO_MANIFEST.items() if info.get("set") in allowed}
    return sorted(
        d.name for d in data_dir.iterdir()
        if d.is_dir() and d.name in train_ids and (d / "ground_truth.jsonl").exists()
    )


def _find_clone_dir(clones_dir: Path, repo_id: str) -> Path | None:
    from cpl_lab.clone import clone_dir_for
    return clone_dir_for(repo_id, clones_dir)


def run_collect(
    data_dir: Path,
    clones_dir: Path,
    repo_set: str = "all",
    repo: str | None = None,
    workers: int = 0,
    verbose: bool = False,
) -> None:
    """Collect retrieval signals — direct in-process, multi-worker."""
    from cpl_lab.collect_signals import collect_all

    repo_ids = [repo] if repo else _iter_repos(data_dir, repo_set)
    if not repo_ids:
        console.print("[yellow]No repos with ground truth found.[/yellow]")
        return

    jobs: list[tuple[str, Path, Path]] = []
    skipped = 0
    for rid in repo_ids:
        # Skip repos that already have completed signals
        sig_dir = data_dir / rid / "signals"
        if (sig_dir / "summary.json").exists() and (sig_dir / "candidates_rank.parquet").exists():
            skipped += 1
            continue
        cd = _find_clone_dir(clones_dir, rid)
        if cd and (cd / ".codeplane" / "index.db").exists():
            jobs.append((rid, data_dir / rid, cd))

    if skipped:
        console.print(f"[dim]Skipping {skipped} repos with completed signals.[/dim]")

    if not jobs:
        console.print("[yellow]No indexed repos ready for collection.[/yellow]")
        return

    if workers <= 0:
        workers = min(os.cpu_count() or 6, 6)

    total = len(jobs)

    # ── Rich progress ────────────────────────────────────────────
    # Overall bar
    overall = Progress(
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(bar_width=30, complete_style="green"),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console, expand=False,
    )
    overall_bar = overall.add_task("Overall", total=total)

    # Per-worker bars (shows active repos)
    worker_progress = Progress(
        TextColumn("  {task.description}", style="dim"),
        BarColumn(bar_width=25, style="cyan", complete_style="green"),
        TaskProgressColumn(),
        console=console, expand=False,
    )
    # Track active worker tasks: repo_id -> task_id
    worker_tasks: dict[str, object] = {}

    # Completed table
    tbl = Table(box=None, pad_edge=False, show_header=True, header_style="dim")
    tbl.add_column("Repo", style="cyan", min_width=28)
    tbl.add_column("Queries", justify="right", min_width=7)
    tbl.add_column("Candidates", justify="right", min_width=10)
    tbl.add_column("Time", justify="right", min_width=7)
    tbl.add_column("", width=4)

    ok = failed = tot_q = tot_c = 0
    t_start = time.monotonic()

    def _render() -> Panel:
        header = Text.from_markup(
            f"  [bold]{total}[/bold] repos  ·  [bold]{workers}[/bold] workers"
            f"  ·  {ok}[green] ok[/green]  {failed}[red] fail[/red]"
            f"  ·  {tot_q:,} queries → {tot_c:,} candidates"
        )
        parts = [header, Text(""), overall]
        # Only show worker bars if there are active tasks
        if worker_progress.tasks:
            parts.append(worker_progress)
        if tbl.rows:
            parts.extend([Text(""), tbl])
        return Panel(Group(*parts),
                     title="Signal Collection", title_align="left",
                     border_style="dim", width=72, padding=(0, 1))

    _last_refresh = 0.0

    def on_progress(repo_id: str, done: int, total_q: int) -> None:
        nonlocal _last_refresh
        if repo_id not in worker_tasks:
            tid = worker_progress.add_task(repo_id, total=total_q)
            worker_tasks[repo_id] = tid
        worker_progress.update(worker_tasks[repo_id], completed=done, total=total_q)
        # Throttle UI refreshes to avoid flicker
        now = time.monotonic()
        if now - _last_refresh > 0.5:
            _last_refresh = now
            live.update(_render())

    def on_done(s: dict) -> None:
        nonlocal ok, failed, tot_q, tot_c
        rid = s["repo_id"]
        q, c = s["queries_processed"], s["total_candidates"]
        sec = s.get("elapsed_sec", 0)

        # Remove worker bar
        if rid in worker_tasks:
            worker_progress.remove_task(worker_tasks[rid])
            del worker_tasks[rid]

        if s.get("status") == "ok":
            ok += 1; tot_q += q; tot_c += c
            mark = "[green]✓[/green]"
        else:
            failed += 1
            mark = "[red]✗[/red]"

        tbl.add_row(rid, f"{q}", f"{c:,}", f"{sec}s", mark)
        overall.update(overall_bar, advance=1)
        live.update(_render())

    with Live(_render(), console=console, refresh_per_second=4) as live:
        collect_all(repo_jobs=jobs, workers=workers,
                    on_progress=on_progress, on_complete=on_done)

    # Final summary
    elapsed = round(time.monotonic() - t_start, 1)
    console.print()
    console.print(Panel(
        Text.from_markup(
            f"[green]✓[/green] {ok} collected  [red]{failed}[/red] failed"
            f"  ({elapsed}s)\n"
            f"  {tot_q:,} queries → {tot_c:,} candidates"
        ),
        title="Done", title_align="left",
        border_style="green" if failed == 0 else "yellow", width=60,
    ))
