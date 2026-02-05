"""File watcher infrastructure for continuous background indexing."""

from codeplane.index._internal.ignore import IgnoreChecker
from codeplane.index._internal.watcher.watcher import (
    BackgroundIndexer,
    FileChangeEvent,
    FileChangeKind,
    FileWatcher,
    WatcherConfig,
    WatcherQueue,
)

__all__ = [
    "BackgroundIndexer",
    "FileChangeEvent",
    "FileChangeKind",
    "FileWatcher",
    "IgnoreChecker",
    "WatcherConfig",
    "WatcherQueue",
]
