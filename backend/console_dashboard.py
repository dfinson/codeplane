"""Structured console log for CodePlane.

Writes one colorized line per significant domain event to stderr.
No TUI, no cursor manipulation — output is a clean, scrollable stream
that preserves full terminal scrollback and works with grep, less, pipes.

Falls back to ``None`` on non-TTY (CI, piped output, systemd) so the
existing file-only log behaviour is unchanged.

Architecture
------------
* ``ConsoleLog`` is created early in the CLI startup path (before
  ``uvicorn.run``) and wired to the log system via ``ConsoleLogHandler``.
  It subscribes to the ``EventBus`` so job lifecycle and progress print
  in real-time as single lines.
* ``ConsoleLogHandler`` is a stdlib ``logging.Handler``.  Before
  ``start()`` it falls back to a plain stderr ``StreamHandler`` so
  startup messages are not lost.  After ``start()`` only ERROR records
  print as structured lines; everything else is file-only.

What prints (and nothing else):
  - job_created, job_state_changed, terminal states (completed/failed/canceled)
  - progress_headline (indented under the active job)
  - approval_requested
  - ERROR-level log records
"""

from __future__ import annotations

import logging
import sys
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from rich.console import Console
from rich.text import Text

from backend.models.events import DomainEventKind

if TYPE_CHECKING:
    from backend.models.events import DomainEvent

# ---------------------------------------------------------------------------
# Display constants
# ---------------------------------------------------------------------------

_STATE_ICON: dict[str, str] = {
    "queued": "○",
    "running": "●",
    "waiting_for_approval": "⏸",
    "review": "⏳",
    "completed": "✓",
    "failed": "✗",
    "canceled": "⊘",
}

_STATE_STYLE: dict[str, str] = {
    "queued": "yellow",
    "running": "bold green",
    "waiting_for_approval": "bold magenta",
    "review": "bold cyan",
    "completed": "green",
    "failed": "red",
    "canceled": "dim",
}

_TERMINAL_STATES = frozenset({"completed", "failed", "canceled"})


# ---------------------------------------------------------------------------
# Per-job tracking (elapsed time + title)
# ---------------------------------------------------------------------------


class _JobInfo:
    __slots__ = ("job_id", "started_at", "title")

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        self.started_at = time.monotonic()
        self.title: str | None = None

    def elapsed(self) -> str:
        secs = int(time.monotonic() - self.started_at)
        if secs < 60:
            return f"{secs}s"
        return f"{secs // 60}m {secs % 60}s"


# ---------------------------------------------------------------------------
# Main console log
# ---------------------------------------------------------------------------


class ConsoleLog:
    """Structured event log for the terminal.

    One colorized line per significant domain event.  No Rich Live, no
    Layout — just ``console.print()`` calls that scroll naturally.

    Create via ``ConsoleLog.create_if_tty()``; non-TTY environments receive
    ``None`` so the plain logging path continues unchanged.
    """

    def __init__(self, log_file_path: str | None = None) -> None:
        self._console = Console(stderr=True, highlight=False)
        self._jobs: dict[str, _JobInfo] = {}
        self._log_file_path = log_file_path
        self._started = False
        self._server_url: str | None = None
        self._tunnel_url: str | None = None
        self._password: str | None = None

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def create_if_tty(cls, log_file_path: str | None = None) -> ConsoleLog | None:
        """Return a console log only when stderr is a real interactive TTY."""
        if sys.stderr.isatty():
            return cls(log_file_path=log_file_path)
        return None

    # ------------------------------------------------------------------
    # Server info (stored, printed on start)
    # ------------------------------------------------------------------

    def set_server_info(
        self,
        *,
        server_url: str | None = None,
        tunnel_url: str | None = None,
        password: str | None = None,
    ) -> None:
        self._server_url = server_url
        self._tunnel_url = tunnel_url
        self._password = password

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @property
    def is_started(self) -> bool:
        return self._started

    def start(self) -> None:
        """Print the startup banner and begin accepting events."""
        if self._started:
            return
        banner = Text()
        banner.append("CodePlane", style="bold cyan")
        if self._server_url:
            banner.append(f"  {self._server_url}", style="dim")
        if self._tunnel_url:
            banner.append(f"  🔗 {self._tunnel_url}")
        if self._password:
            banner.append(f"  🔑 {self._password}")
        if self._log_file_path:
            banner.append(f"  logs → {self._log_file_path}", style="dim")
        self._console.print(banner)
        self._console.rule(style="dim")
        self._started = True

    def stop(self) -> None:
        """Print a shutdown marker."""
        if not self._started:
            return
        self._console.rule(style="dim")
        self._console.print("[dim]CodePlane stopped[/]")
        self._started = False

    # ------------------------------------------------------------------
    # EventBus subscriber
    # ------------------------------------------------------------------

    async def handle_event(self, event: DomainEvent) -> None:
        """Async EventBus subscriber — prints one line per significant event."""
        self._apply_event(event)

    # ------------------------------------------------------------------
    # Logging handler integration
    # ------------------------------------------------------------------

    def add_log_record(self, record: logging.LogRecord) -> None:
        """Print an ERROR-level log record as a structured line."""
        if record.levelno < logging.ERROR:
            return
        msg = record.getMessage()
        first_line = (msg.splitlines()[0] if msg else "(no message)")[:120]
        short_name = record.name
        for prefix in ("backend.services.", "backend.api.", "backend."):
            if short_name.startswith(prefix):
                short_name = short_name[len(prefix) :]
                break
        ts = datetime.now(UTC).strftime("%H:%M:%S")
        line = Text()
        line.append(ts, style="dim")
        line.append("  ✗  ", style="red")
        line.append(short_name, style="dim cyan")
        line.append(f"  {first_line}", style="red")
        self._console.print(line)

    # ------------------------------------------------------------------
    # Internal: map domain event → printed line
    # ------------------------------------------------------------------

    def _apply_event(self, event: DomainEvent) -> None:
        ts = datetime.now(UTC).strftime("%H:%M:%S")
        kind = event.kind

        if kind == DomainEventKind.job_created:
            self._jobs[event.job_id] = _JobInfo(event.job_id)
            self._print_event(ts, "○", "queued", event.job_id, "created")

        elif kind == DomainEventKind.job_state_changed:
            new_state = str(event.payload.get("new_state", ""))
            if event.job_id not in self._jobs:
                self._jobs[event.job_id] = _JobInfo(event.job_id)
            label = new_state.replace("_", " ")
            icon = _STATE_ICON.get(new_state, "?")
            self._print_event(ts, icon, new_state, event.job_id, label)

        elif kind in (
            DomainEventKind.job_review,
            DomainEventKind.job_completed,
            DomainEventKind.job_failed,
            DomainEventKind.job_canceled,
        ):
            new_state = {
                DomainEventKind.job_review: "review",
                DomainEventKind.job_completed: "completed",
                DomainEventKind.job_failed: "failed",
                DomainEventKind.job_canceled: "canceled",
            }[kind]
            job = self._jobs.get(event.job_id)
            elapsed = f"  ({job.elapsed()})" if job else ""
            title = f'  "{job.title}"' if job and job.title else ""
            icon = _STATE_ICON.get(new_state, "?")
            self._print_event(ts, icon, new_state, event.job_id, new_state + elapsed + title)
            self._jobs.pop(event.job_id, None)

        elif kind == DomainEventKind.job_title_updated:
            updated_title: str | None = event.payload.get("title")
            if updated_title and event.job_id in self._jobs:
                self._jobs[event.job_id].title = updated_title

        elif kind == DomainEventKind.progress_headline:
            headline = str(event.payload.get("headline") or "")
            if headline:
                line = Text()
                line.append(ts, style="dim")
                line.append("       ↳ ", style="dim")
                line.append(headline[:80])
                self._console.print(line)

        elif kind == DomainEventKind.approval_requested:
            desc = str(event.payload.get("description") or "approval needed")
            self._print_event(ts, "⏸", "waiting_for_approval", event.job_id, f"approval needed · {desc[:60]}")

    def _print_event(self, ts: str, icon: str, state: str, job_id: str, message: str) -> None:
        style = _STATE_STYLE.get(state, "")
        line = Text()
        line.append(ts, style="dim")
        line.append(f"  {icon}  ", style=style)
        line.append(job_id, style="cyan")
        line.append(f"  {message}")
        self._console.print(line)


# ---------------------------------------------------------------------------
# Logging handler
# ---------------------------------------------------------------------------


class ConsoleLogHandler(logging.Handler):
    """Stdlib logging handler that routes records through ``ConsoleLog``.

    * **Before start()**: falls back to a plain ``StreamHandler`` so
      startup messages are not silently dropped.
    * **After start()**: ERROR/CRITICAL records print as structured lines;
      everything else is suppressed on the console (still in the log file).
    """

    def __init__(
        self,
        console_log: ConsoleLog,
        fallback_formatter: logging.Formatter,
        fallback_filter: logging.Filter,
    ) -> None:
        super().__init__(level=logging.WARNING)
        self._console_log = console_log
        self._fallback = logging.StreamHandler()
        self._fallback.setLevel(logging.WARNING)
        self._fallback.setFormatter(fallback_formatter)
        self._fallback.addFilter(fallback_filter)

    def emit(self, record: logging.LogRecord) -> None:
        if not self._console_log.is_started:
            self._fallback.emit(record)
            return
        try:
            self._console_log.add_log_record(record)
        except Exception:  # noqa: BLE001
            self.handleError(record)
