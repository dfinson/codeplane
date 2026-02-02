"""Daemon lifecycle management."""

from __future__ import annotations

import asyncio
import contextlib
import signal
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
import uvicorn

from codeplane.config.models import ServerConfig
from codeplane.daemon.indexer import BackgroundIndexer
from codeplane.daemon.watcher import FileWatcher

if TYPE_CHECKING:
    from codeplane.index.ops import IndexCoordinator

logger = structlog.get_logger()

# PID file location relative to .codeplane/
PID_FILE = "daemon.pid"
PORT_FILE = "daemon.port"


@dataclass
class ServerController:
    """
    Orchestrates daemon components.

    Components:
    - IndexCoordinator: Database and search operations
    - BackgroundIndexer: Thread pool for CPU-bound indexing
    - FileWatcher: Async filesystem monitoring
    """

    repo_root: Path
    coordinator: IndexCoordinator
    config: ServerConfig

    indexer: BackgroundIndexer = field(init=False)
    watcher: FileWatcher = field(init=False)
    _shutdown_event: asyncio.Event = field(default_factory=asyncio.Event, init=False)

    def __post_init__(self) -> None:
        """Initialize components."""
        # Create indexer with configurable debounce
        self.indexer = BackgroundIndexer(
            coordinator=self.coordinator,
            debounce_seconds=self.config.debounce_sec,
        )

        # Create watcher with configurable poll interval
        self.watcher = FileWatcher(
            repo_root=self.repo_root,
            on_change=self.indexer.queue_paths,
            poll_interval=self.config.poll_interval_sec,
        )

    async def start(self) -> None:
        """Start all daemon components."""
        logger.info("server starting", repo_root=str(self.repo_root))

        # Start indexer thread pool
        self.indexer.start()

        # Start file watcher
        await self.watcher.start()

        logger.info("server started")

    async def stop(self) -> None:
        """Stop all daemon components gracefully."""
        logger.info("server stopping")

        # Stop watcher first (no new events)
        await self.watcher.stop()

        # Stop indexer (complete pending work)
        await self.indexer.stop()

        # Signal shutdown complete
        self._shutdown_event.set()

        logger.info("server stopped")

    def wait_for_shutdown(self) -> asyncio.Event:
        """Get the shutdown event for external coordination."""
        return self._shutdown_event


def write_pid_file(codeplane_dir: Path, port: int) -> None:
    """Write PID and port files for daemon discovery."""
    import os

    pid_path = codeplane_dir / PID_FILE
    port_path = codeplane_dir / PORT_FILE

    pid_path.write_text(str(os.getpid()))
    port_path.write_text(str(port))

    logger.debug("pid_file_written", pid_path=str(pid_path), port=port)


def remove_pid_file(codeplane_dir: Path) -> None:
    """Remove PID and port files on shutdown."""
    pid_path = codeplane_dir / PID_FILE
    port_path = codeplane_dir / PORT_FILE

    for path in (pid_path, port_path):
        with contextlib.suppress(FileNotFoundError):
            path.unlink()


def read_server_info(codeplane_dir: Path) -> tuple[int, int] | None:
    """Read daemon PID and port from files. Returns (pid, port) or None."""
    pid_path = codeplane_dir / PID_FILE
    port_path = codeplane_dir / PORT_FILE

    try:
        pid = int(pid_path.read_text().strip())
        port = int(port_path.read_text().strip())
        return (pid, port)
    except (FileNotFoundError, ValueError):
        return None


def is_server_running(codeplane_dir: Path) -> bool:
    """Check if daemon is running by verifying PID file and process."""
    import os

    info = read_server_info(codeplane_dir)
    if info is None:
        return False

    pid, _ = info

    # Check if process exists
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        # Process doesn't exist - clean up stale files
        remove_pid_file(codeplane_dir)
        return False


async def run_server(
    repo_root: Path,
    coordinator: IndexCoordinator,
    config: ServerConfig,
) -> None:
    """Run the daemon until shutdown signal."""
    from importlib.metadata import version

    from rich.console import Console

    from codeplane.daemon.app import create_app

    console = Console(stderr=True)

    # Ensure index is up-to-date (idempotent - does minimal work if already current)
    stats = await coordinator.reindex_full()

    if stats.files_processed > 0:
        console.print(
            f"  [green]✓[/green] Indexed {stats.files_added} new, "
            f"{stats.files_updated} updated, {stats.files_removed} removed "
            f"in {stats.duration_seconds:.2f}s"
        )

    # Print banner
    ver = version("codeplane")
    console.print()
    console.print(f"  [cyan bold]CodePlane[/cyan bold] v{ver}")
    console.print("  Local repository control plane for AI coding agents")
    console.print()
    console.print(f"  Listening on [green]http://{config.host}:{config.port}[/green]")
    console.print("  Press [bold]Ctrl+C[/bold] to stop")
    console.print()

    controller = ServerController(
        repo_root=repo_root,
        coordinator=coordinator,
        config=config,
    )

    app = create_app(controller, repo_root, coordinator)

    # Configure uvicorn
    uvicorn_config = uvicorn.Config(
        app,
        host=config.host,
        port=config.port,
        log_level="warning",  # Use structlog instead
    )
    server = uvicorn.Server(uvicorn_config)

    # Write PID file
    codeplane_dir = repo_root / ".codeplane"
    write_pid_file(codeplane_dir, config.port)

    # Setup signal handlers
    loop = asyncio.get_event_loop()

    def signal_handler() -> None:
        logger.info("shutdown_signal_received")
        asyncio.create_task(server.shutdown())

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

    try:
        logger.info(
            "server listening",
            host=config.host,
            port=config.port,
        )
        await server.serve()
    finally:
        remove_pid_file(codeplane_dir)


def stop_daemon(codeplane_dir: Path) -> bool:
    """Stop a running daemon by sending SIGTERM. Returns True if stopped."""
    import os

    info = read_server_info(codeplane_dir)
    if info is None:
        return False

    pid, _ = info

    try:
        os.kill(pid, signal.SIGTERM)
        logger.info("daemon_stop_signal_sent", pid=pid)
        return True
    except (OSError, ProcessLookupError):
        remove_pid_file(codeplane_dir)
        return False
