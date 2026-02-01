"""Performance budget loader and RSS monitoring for E2E tests."""

from __future__ import annotations

import json
import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Budget:
    """Performance budget for a repository."""

    full_index_seconds: float
    incremental_seconds: float
    max_rss_mb: float


BUDGETS_PATH = Path(__file__).parent / "budgets.json"


def load_budgets() -> dict[str, Budget]:
    """Load all performance budgets."""
    with BUDGETS_PATH.open() as f:
        data = json.load(f)

    return {
        key: Budget(
            full_index_seconds=val["full_index_seconds"],
            incremental_seconds=val["incremental_seconds"],
            max_rss_mb=val["max_rss_mb"],
        )
        for key, val in data.items()
    }


def get_budget(repo_key: str) -> Budget:
    """Get budget for a specific repo."""
    budgets = load_budgets()
    if repo_key not in budgets:
        # Default budget for repos without explicit budget
        return Budget(
            full_index_seconds=60,
            incremental_seconds=10,
            max_rss_mb=2000,
        )
    return budgets[repo_key]


@dataclass
class RSSStats:
    """RSS memory statistics."""

    peak_mb: float = 0.0
    final_mb: float = 0.0


def get_rss_mb() -> float:
    """Get current process RSS in MB."""
    try:
        import psutil

        return psutil.Process().memory_info().rss / (1024 * 1024)
    except ImportError:
        return 0.0


@contextmanager
def rss_monitor(sample_interval: float = 0.1) -> Generator[RSSStats, None, None]:
    """Context manager to monitor peak RSS during execution.

    Usage:
        with rss_monitor() as stats:
            # do work
        print(f"Peak RSS: {stats.peak_mb} MB")
    """
    stats = RSSStats()

    try:
        import threading

        import psutil

        stop_event = threading.Event()
        process = psutil.Process()

        def monitor() -> None:
            while not stop_event.is_set():
                rss = process.memory_info().rss / (1024 * 1024)
                stats.peak_mb = max(stats.peak_mb, rss)
                time.sleep(sample_interval)

        thread = threading.Thread(target=monitor, daemon=True)
        thread.start()

        try:
            yield stats
        finally:
            stop_event.set()
            thread.join(timeout=1.0)
            stats.final_mb = process.memory_info().rss / (1024 * 1024)

    except ImportError:
        # psutil not available, return zeros
        yield stats
