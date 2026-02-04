"""User-facing progress feedback for CLI operations.

Design principles:
- Show something if operation takes >0.5s
- Progress bar if iterating >100 items
- Single line updates, no spam
- Graceful degradation in non-TTY (CI, pipes)
- Suppress structlog during spinners to avoid line collision (Issue #5)

Usage::

    from codeplane.core.progress import progress, status, spinner

    # Simple status message
    status("Discovering files...")

    # Progress bar for iteration
    for file in progress(files, desc="Indexing"):
        process(file)

    # Success/error markers
    status("Ready", style="success")  # ✓ Ready
    status("Failed to connect", style="error")  # ✗ Failed to connect

    # Spinner with log suppression
    with spinner("Reindexing 3 files"):
        do_work()  # structlog suppressed during this block
"""

from __future__ import annotations

import logging
import sys
import threading
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
    "warning": "[yellow]![/yellow] ",
    "info": "  ",
    "none": "",
}

# Global flag to suppress console logging during spinners (Issue #5)
_suppress_console_logs = threading.local()


def is_console_suppressed() -> bool:
    """Check if console logging is currently suppressed."""
    return getattr(_suppress_console_logs, "active", False)


@contextmanager
def suppress_console_logs() -> Iterator[None]:
    """Context manager to suppress structlog console output.

    Used during spinners to prevent log lines from colliding with
    Rich's live display. Logs are still written to file handlers.
    """
    _suppress_console_logs.active = True
    try:
        yield
    finally:
        _suppress_console_logs.active = False


class ConsoleSuppressingFilter(logging.Filter):
    """Filter that blocks console output when suppression is active.

    Allows file handlers to continue receiving logs while console
    output is paused during Rich live displays.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: ARG002
        # Only filter if suppression is active AND this is a console handler
        # File handlers should still receive logs
        return not is_console_suppressed()


def _get_logger() -> BoundLogger:
    """Get logger lazily to respect runtime config."""
    from codeplane.core.logging import get_logger

    return get_logger("progress")


def _is_tty() -> bool:
    """Check if stderr is a TTY."""
    return hasattr(sys.stderr, "isatty") and sys.stderr.isatty()


def get_console() -> Console:
    """Get the shared Rich console instance."""
    return _console


def status(message: str, *, style: str = "info", indent: int = 0) -> None:
    """Print a styled status message to stderr."""
    prefix = _STYLES.get(style, "")
    padding = " " * indent
    _console.print(f"{padding}{prefix}{message}", highlight=False)

    # Log at DEBUG for observability (lazy to respect runtime config)
    _get_logger().debug("status", message=message, style=style)


def pluralize(count: int, singular: str, plural: str | None = None) -> str:
    """Return grammatically correct singular/plural form.

    Args:
        count: The number of items
        singular: Singular form (e.g., "file")
        plural: Plural form (default: singular + "s")

    Returns:
        Formatted string like "1 file" or "3 files"
    """
    if plural is None:
        plural = singular + "s"
    word = singular if count == 1 else plural
    return f"{count} {word}"


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
        with (
            suppress_console_logs(),
            Progress(
                TextColumn("    {task.description}:"),
                BarColumn(bar_width=25, style="cyan", complete_style="cyan"),
                TaskProgressColumn(),
                TextColumn("{task.completed}/{task.total} {task.fields[unit]}"),
                console=_console,
                transient=True,
            ) as pbar,
        ):
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
def spinner(message: str, *, indent: int = 0) -> Iterator[None]:
    """Context manager for a spinner with log suppression.

    Suppresses structlog console output during the spinner to prevent
    log lines from colliding with Rich's live display (Issue #5).

    Usage::

        with spinner("Reindexing 3 files"):
            do_work()
    """
    padding = " " * indent
    if _is_tty():
        with (
            suppress_console_logs(),
            _console.status(f"{padding}[cyan]{message}[/cyan]", spinner="dots"),
        ):
            yield
    else:
        # Non-TTY: just print the message
        _console.print(f"{padding}{message}...")
        yield


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


def animate_text(text: str, delay: float = 0.02) -> None:
    """Print text line-by-line with a small delay for dramatic effect.

    Args:
        text: Multi-line text to animate
        delay: Seconds between each line (default 0.02)
    """
    import time

    for line in text.splitlines():
        _console.print(line, highlight=False)
        if delay > 0 and _is_tty():
            time.sleep(delay)
