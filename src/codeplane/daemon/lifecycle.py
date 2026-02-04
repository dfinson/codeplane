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

from codeplane.config.models import CodePlaneConfig, IndexerConfig, ServerConfig, TimeoutsConfig
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
    server_config: ServerConfig
    timeouts_config: TimeoutsConfig = field(default_factory=TimeoutsConfig)
    indexer_config: IndexerConfig = field(default_factory=IndexerConfig)

    indexer: BackgroundIndexer = field(init=False)
    watcher: FileWatcher = field(init=False)
    _shutdown_event: asyncio.Event = field(default_factory=asyncio.Event, init=False)

    def __post_init__(self) -> None:
        """Initialize components."""
        # Create indexer with config
        self.indexer = BackgroundIndexer(
            coordinator=self.coordinator,
            config=self.indexer_config,
        )

        # Create watcher with configurable poll interval
        self.watcher = FileWatcher(
            repo_root=self.repo_root,
            on_change=self.indexer.queue_paths,
            poll_interval=self.server_config.poll_interval_sec,
        )

    async def start(self) -> None:
        """Start all daemon components."""
        logger.info("server starting", repo_root=str(self.repo_root))

        # Start indexer thread pool
        self.indexer.start()

        # Start file watcher
        await self.watcher.start()

        base_url = f"http://{self.server_config.host}:{self.server_config.port}"
        logger.info("server started")
        logger.info("endpoint", name="mcp", url=f"{base_url}/mcp")
        logger.info("endpoint", name="health", url=f"{base_url}/health")
        logger.info("endpoint", name="status", url=f"{base_url}/status")

    async def stop(self) -> None:
        """Stop all daemon components gracefully."""
        logger.info("server stopping")

        # Stop with timeout to prevent hanging
        try:
            async with asyncio.timeout(self.timeouts_config.server_stop_sec):
                # Stop watcher first (no new events)
                await self.watcher.stop()

                # Stop indexer (complete pending work)
                await self.indexer.stop()
        except TimeoutError:
            logger.warning(
                "server_stop_timeout",
                message=f"Shutdown timed out after {self.timeouts_config.server_stop_sec}s",
            )

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
    config: CodePlaneConfig,
) -> None:
    """Run the daemon until shutdown signal."""
    from codeplane.core.progress import summary_stream
    from codeplane.daemon.app import create_app

    # Ensure index is up-to-date with summary stream output
    with summary_stream() as stream:
        # Reindex (shows progress bar if there are files to process)
        stats = await coordinator.reindex_full()

        if stats.files_processed > 0:
            # Build summary message
            parts = []
            if stats.files_added > 0:
                parts.append(f"{stats.files_added} new")
            if stats.files_updated > 0:
                parts.append(f"{stats.files_updated} updated")
            if stats.files_removed > 0:
                parts.append(f"{stats.files_removed} removed")
            summary = ", ".join(parts) + f" ({stats.duration_seconds:.1f}s)"
            stream.done(summary)

    # Print banner with logo
    from codeplane.cli.up import _print_banner

    _print_banner(config.server.host, config.server.port)

    controller = ServerController(
        repo_root=repo_root,
        coordinator=coordinator,
        server_config=config.server,
        timeouts_config=config.timeouts,
        indexer_config=config.indexer,
    )

    app = create_app(controller, repo_root, coordinator)

    # Configure uvicorn
    uvicorn_config = uvicorn.Config(
        app,
        host=config.server.host,
        port=config.server.port,
        log_level="warning",  # Use structlog instead
        ws="none",  # Disable websockets - we use SSE for MCP
    )
    server = uvicorn.Server(uvicorn_config)

    # Write PID file
    codeplane_dir = repo_root / ".codeplane"
    write_pid_file(codeplane_dir, config.server.port)

    # Setup signal handlers with force exit on second signal
    loop = asyncio.get_event_loop()
    shutdown_count = 0
    force_exit_task: asyncio.Task[None] | None = None

    async def force_exit_after_timeout() -> None:
        """Force exit if graceful shutdown takes too long."""
        await asyncio.sleep(config.timeouts.force_exit_sec)
        logger.info("forcing_exit_after_timeout")
        server.force_exit = True

    def signal_handler() -> None:
        nonlocal shutdown_count, force_exit_task
        shutdown_count += 1
        logger.info("shutdown_signal_received", count=shutdown_count)
        server.should_exit = True
        if shutdown_count == 1:
            # Schedule force exit after timeout
            force_exit_task = loop.create_task(force_exit_after_timeout())
        else:
            # Second signal - force immediate exit
            server.force_exit = True
            if force_exit_task:
                force_exit_task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

    try:
        await controller.start()
        await server.serve()
    finally:
        await controller.stop()
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
