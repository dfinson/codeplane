"""User-facing progress feedback for CLI operations.

Design principles:
- Show something if operation takes >0.5s
- Progress bar if iterating >100 items
- Single line updates, no spam
- Graceful degradation in non-TTY (CI, pipes)

Usage::

    from codeplane.core.progress import progress, status

    # Simple status message
    status("Discovering files...")

    # Progress bar for iteration
    for file in progress(files, desc="Indexing"):
        process(file)

    # Success/error markers
    status("Ready", style="success")  # ✓ Ready
    status("Failed to connect", style="error")  # ✗ Failed to connect
"""

from __future__ import annotations

import sys
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from rich.console import Console
from rich.progress import BarColumn, Progress, TaskProgressColumn, TextColumn

if TYPE_CHECKING:
    from structlog.stdlib import BoundLogger

# Threshold for showing progress bar
_PROGRESS_THRESHOLD = 100

# Console for output
_console = Console(stderr=True)

# Style prefixes
_STYLES = {
    "success": "[green]✓[/green] ",
    "error": "[red]✗[/red] ",
    "info": "  ",
    "none": "",
}


def _get_logger() -> BoundLogger:
    """Get logger lazily to respect runtime config."""
    from codeplane.core.logging import get_logger

    return get_logger("progress")


def _is_tty() -> bool:
    """Check if stderr is a TTY."""
    return hasattr(sys.stderr, "isatty") and sys.stderr.isatty()


def status(message: str, *, style: str = "info", indent: int = 0) -> None:
    """Print a styled status message to stderr."""
    prefix = _STYLES.get(style, "")
    padding = " " * indent
    _console.print(f"{padding}{prefix}{message}", highlight=False)

    # Log at DEBUG for observability (lazy to respect runtime config)
    _get_logger().debug("status", message=message, style=style)


def progress[T](
    iterable: Iterable[T],
    *,
    desc: str | None = None,
    total: int | None = None,
    unit: str = "files",
    force: bool = False,
) -> Iterator[T]:
    """Wrap an iterable with a progress bar if TTY and >100 items (or force=True)."""
    # Try to get total
    if total is None:
        try:
            total = len(iterable)  # type: ignore[arg-type]
        except TypeError:
            total = None

    # Decide whether to show progress
    show_bar = _is_tty() and total is not None and (force or total > _PROGRESS_THRESHOLD)

    if show_bar:
        with Progress(
            TextColumn("    {task.description}:"),
            BarColumn(bar_width=25, style="cyan", complete_style="cyan"),
            TaskProgressColumn(),
            TextColumn("{task.completed}/{task.total} {task.fields[unit]}"),
            console=_console,
            transient=True,
        ) as pbar:
            task_id = pbar.add_task(desc or "Processing", total=total, unit=unit)
            for item in iterable:
                yield item
                pbar.advance(task_id)
    else:
        # No progress bar, just yield
        log = _get_logger()
        if desc and total:
            log.debug("progress_start", desc=desc, total=total)
        for item in iterable:
            yield item
        if desc and total:
            log.debug("progress_done", desc=desc, total=total)


@contextmanager
def task(name: str) -> Iterator[None]:
    """Context manager for a named task with timing.

    Usage::

        with task("Building index"):
            # ... do work ...
        # Prints: ✓ Building index (3.2s)
    """
    import time

    log = _get_logger()
    log.debug("task_start", task=name)
    status(f"{name}...", style="none", indent=0)
    start = time.perf_counter()

    try:
        yield
        elapsed = time.perf_counter() - start
        status(f"{name} ({elapsed:.1f}s)", style="success")
        log.debug("task_done", task=name, elapsed_s=elapsed)
    except Exception as e:
        elapsed = time.perf_counter() - start
        status(f"{name} failed: {e}", style="error")
        log.error("task_failed", task=name, elapsed_s=elapsed, error=str(e))
        raise
