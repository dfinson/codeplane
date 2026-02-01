"""CodePlane daemon - HTTP server with file watching and background indexing."""

from codeplane.daemon.app import create_app
from codeplane.daemon.indexer import BackgroundIndexer
from codeplane.daemon.lifecycle import DaemonController
from codeplane.daemon.watcher import FileWatcher

__all__ = [
    "BackgroundIndexer",
    "DaemonController",
    "FileWatcher",
    "create_app",
]
