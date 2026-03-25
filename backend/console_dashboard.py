"""Rich live dashboard for CodePlane console output.

Replaces noisy per-event console logging with an in-place Rich panel that
shows server uptime, active jobs, recent events, and accumulated errors.
Falls back transparently to a no-op when stderr is not a TTY (CI, piped
output, systemd) so the existing file-only log behaviour is unchanged.

Architecture
------------
* ``ConsoleDashboard`` owns the Rich ``Live`` context and all in-memory
  state (jobs, events, errors).  It is created early in the CLI startup
  path (before ``uvicorn.run``), wired to the log system as a handler, then
  *started* (Live display activated) after the startup banner is printed.
* ``DashboardLogHandler`` is a stdlib ``logging.Handler``.  While the Live
  display is not yet running it falls back to a plain stderr
  ``StreamHandler`` so nothing is lost during startup.  Once the Live
  display starts, WARNING/ERROR records are routed into the dashboard's
  error panel; INFO and below are silently dropped on the console (they are
  always present in the rotating log file).
* The dashboard subscribes to the EventBus as an async coroutine
  (``handle_event``) so job state and progress headlines update in
  real-time.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from rich.box import ROUNDED, SIMPLE_HEAVY
from rich.console import Console, ConsoleRenderable, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
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
    "succeeded": "✓",
    "failed": "✗",
    "canceled": "⊘",
}

_STATE_STYLE: dict[str, str] = {
    "queued": "yellow",
    "running": "bold green",
    "waiting_for_approval": "bold magenta",
    "succeeded": "dim green",
    "failed": "red",
    "canceled": "dim",
}

_ACTIVE_STATES = frozenset({"queued", "running", "waiting_for_approval"})
_TERMINAL_STATES = frozenset({"succeeded", "failed", "canceled"})

# How long completed jobs remain visible in the Jobs panel
_COMPLETED_JOB_TTL_S = 60.0


# ---------------------------------------------------------------------------
# Internal state dataclasses
# ---------------------------------------------------------------------------


@dataclass
class _JobRow:
    job_id: str
    state: str
    title: str | None = None
    started_at: float = field(default_factory=time.monotonic)
    completed_at: float | None = None

    def elapsed(self) -> str:
        end = self.completed_at or time.monotonic()
        secs = int(end - self.started_at)
        if secs < 60:
            return f"{secs}s"
        return f"{secs // 60}m {secs % 60}s"


@dataclass
class _ErrorEntry:
    timestamp: str
    logger_name: str
    message: str


# ---------------------------------------------------------------------------
# Renderable: Rich calls __rich__ on every automatic refresh cycle
# ---------------------------------------------------------------------------


class _DashboardView:
    """Thin wrapper so Rich Live can call back into the dashboard to render."""

    __slots__ = ("_dashboard",)

    def __init__(self, dashboard: ConsoleDashboard) -> None:
        self._dashboard = dashboard

    def __rich__(self) -> ConsoleRenderable:  # type: ignore[override]
        return self._dashboard._build_renderable()


# ---------------------------------------------------------------------------
# Main dashboard
# ---------------------------------------------------------------------------


class ConsoleDashboard:
    """In-place Rich live dashboard.

    Create via ``ConsoleDashboard.create_if_tty()`` so non-TTY environments
    receive ``None`` and continue using the plain logging path.
    """

    _MAX_EVENTS = 14
    _MAX_ERRORS = 6

    def __init__(self, log_file_path: str | None = None) -> None:
        self._console = Console(stderr=True, highlight=False)
        self._live: Live | None = None
        self._lock = threading.Lock()

        self._jobs: dict[str, _JobRow] = {}
        self._recent_events: deque[tuple[str, str]] = deque(maxlen=self._MAX_EVENTS)
        self._errors: deque[_ErrorEntry] = deque(maxlen=self._MAX_ERRORS)
        self._error_count = 0
        self._warning_count = 0
        self._start_time = time.monotonic()
        self._log_file_path = log_file_path
        self._server_url: str | None = None
        self._tunnel_url: str | None = None

    def set_server_info(self, *, server_url: str | None = None, tunnel_url: str | None = None) -> None:
        """Set server URLs for the dashboard header."""
        self._server_url = server_url
        self._tunnel_url = tunnel_url

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def create_if_tty(cls, log_file_path: str | None = None) -> ConsoleDashboard | None:
        """Return a dashboard only when stderr is a real interactive TTY."""
        if sys.stderr.isatty():
            return cls(log_file_path=log_file_path)
        return None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @property
    def is_live_running(self) -> bool:
        return self._live is not None and self._live.is_started

    def start(self) -> None:
        """Activate the Rich Live display. Call after the startup banner."""
        if self._live is not None:
            return
        self._live = Live(
            _DashboardView(self),
            console=self._console,
            refresh_per_second=4,
            screen=True,
            auto_refresh=True,
            vertical_overflow="crop",
        )
        self._live.start(refresh=True)

    def stop(self) -> None:
        """Deactivate the display and restore normal terminal state."""
        if self._live is not None:
            self._live.stop()
            self._live = None

    # ------------------------------------------------------------------
    # EventBus subscriber
    # ------------------------------------------------------------------

    async def handle_event(self, event: DomainEvent) -> None:
        """Async EventBus subscriber — updates in-memory state."""
        with self._lock:
            self._apply_event(event)

    # ------------------------------------------------------------------
    # Logging handler integration
    # ------------------------------------------------------------------

    def add_log_record(self, record: logging.LogRecord) -> None:
        """Route a WARNING/ERROR log record into the dashboard panels."""
        msg = record.getMessage()
        # First line only — never surface stack traces on the console
        first_line = (msg.splitlines()[0] if msg else "(no message)")[:90]
        # Shorten the logger name for compact display
        short_name = record.name
        for prefix in ("backend.", "backend.services.", "backend.api."):
            if short_name.startswith(prefix):
                short_name = short_name[len(prefix) :]
                break
        ts = datetime.now(UTC).strftime("%H:%M:%S")

        with self._lock:
            if record.levelno >= logging.ERROR:
                self._error_count += 1
                self._errors.append(_ErrorEntry(ts, short_name, first_line))
            else:
                self._warning_count += 1
                self._recent_events.append((ts, f"⚠ {short_name}  {first_line[:52]}"))

    # ------------------------------------------------------------------
    # Internal: apply a domain event to state (lock must be held)
    # ------------------------------------------------------------------

    def _apply_event(self, event: DomainEvent) -> None:
        ts = datetime.now(UTC).strftime("%H:%M:%S")
        kind = event.kind

        if kind == DomainEventKind.job_created:
            self._jobs[event.job_id] = _JobRow(job_id=event.job_id, state="queued")
            self._recent_events.append((ts, f"○ {event.job_id}  created"))

        elif kind == DomainEventKind.job_state_changed:
            new_state = str(event.payload.get("new_state", ""))
            if event.job_id not in self._jobs:
                self._jobs[event.job_id] = _JobRow(job_id=event.job_id, state=new_state)
            else:
                self._jobs[event.job_id].state = new_state
            if new_state in _TERMINAL_STATES:
                self._jobs[event.job_id].completed_at = time.monotonic()
            icon = _STATE_ICON.get(new_state, "?")
            self._recent_events.append((ts, f"{icon} {event.job_id}  → {new_state}"))

        elif kind in (
            DomainEventKind.job_succeeded,
            DomainEventKind.job_failed,
            DomainEventKind.job_canceled,
        ):
            # These dedicated terminal events are the authoritative signal that a
            # job has finished — job_state_changed is NOT published for terminal
            # transitions, so we must handle them here.
            new_state = {
                DomainEventKind.job_succeeded: "succeeded",
                DomainEventKind.job_failed: "failed",
                DomainEventKind.job_canceled: "canceled",
            }[kind]
            if event.job_id not in self._jobs:
                self._jobs[event.job_id] = _JobRow(job_id=event.job_id, state=new_state)
            else:
                self._jobs[event.job_id].state = new_state
            self._jobs[event.job_id].completed_at = time.monotonic()
            icon = _STATE_ICON.get(new_state, "?")
            self._recent_events.append((ts, f"{icon} {event.job_id}  → {new_state}"))

        elif kind == DomainEventKind.job_title_updated:
            title = event.payload.get("title")
            if title and event.job_id in self._jobs:
                self._jobs[event.job_id].title = str(title)

        elif kind == DomainEventKind.progress_headline:
            headline = str(event.payload.get("headline") or "")
            if headline:
                # Use only the last segment of the job id for compact display
                short_id = event.job_id.rsplit("-", 1)[-1] if "-" in event.job_id else event.job_id[:8]
                self._recent_events.append((ts, f"  [{short_id}] {headline[:52]}"))

        elif kind == DomainEventKind.approval_requested:
            desc = str(event.payload.get("description") or "approval needed")
            self._recent_events.append((ts, f"⏸ {event.job_id}  {desc[:40]}"))

        # Prune expired completed jobs every time state changes
        now = time.monotonic()
        self._jobs = {
            jid: row
            for jid, row in self._jobs.items()
            if row.state in _ACTIVE_STATES
            or (row.completed_at is not None and now - row.completed_at < _COMPLETED_JOB_TTL_S)
        }

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _build_renderable(self) -> ConsoleRenderable:
        with self._lock:
            return self._render()

    def _render(self) -> ConsoleRenderable:
        """Build the full dashboard layout. Caller must hold ``_lock``."""
        # --- Header ---
        uptime_s = int(time.monotonic() - self._start_time)
        h, rem = divmod(uptime_s, 3600)
        m, s = divmod(rem, 60)
        uptime_str = f"{h}h {m}m" if h else f"{m}m {s}s"
        log_hint = f"  ·  logs → {self._log_file_path}" if self._log_file_path else ""
        url_hint = ""
        if self._tunnel_url:
            url_hint = f"  ·  {self._tunnel_url}"
        elif self._server_url:
            url_hint = f"  ·  {self._server_url}"
        header_text = Text.assemble(
            ("CodePlane", "bold cyan"),
            "  up ",
            (uptime_str, "green"),
            (url_hint, "bold"),
            (log_hint, "dim"),
        )
        header_panel = Panel(header_text, box=ROUNDED, padding=(0, 1))

        # --- Jobs table ---
        active = [r for r in self._jobs.values() if r.state in _ACTIVE_STATES]
        terminal = sorted(
            (r for r in self._jobs.values() if r.state in _TERMINAL_STATES),
            key=lambda r: r.completed_at or 0,
            reverse=True,
        )
        jobs_table = Table(
            box=SIMPLE_HEAVY,
            expand=True,
            show_header=bool(active or terminal),
            header_style="bold dim",
            padding=(0, 1),
        )
        jobs_table.add_column("ID", style="cyan", no_wrap=True)
        jobs_table.add_column("State", no_wrap=True)
        jobs_table.add_column("Time", no_wrap=True, justify="right")
        jobs_table.add_column("Title")

        for row in sorted(active, key=lambda r: r.started_at):
            icon = _STATE_ICON.get(row.state, "?")
            style = _STATE_STYLE.get(row.state, "white")
            jobs_table.add_row(
                row.job_id,
                Text(f"{icon} {row.state}", style=style),
                row.elapsed(),
                Text((row.title or "")[:44], style="dim"),
            )
        for row in terminal[:3]:
            icon = _STATE_ICON.get(row.state, "?")
            style = _STATE_STYLE.get(row.state, "dim")
            jobs_table.add_row(
                row.job_id,
                Text(f"{icon} {row.state}", style=style),
                row.elapsed(),
                Text((row.title or "")[:44], style="dim"),
            )

        if not active and not terminal:
            jobs_table.add_row("", Text("no active jobs", style="dim"), "", "")

        jobs_panel = Panel(jobs_table, title="[bold]Jobs[/bold]", box=ROUNDED)

        # --- Recent events ---
        events_text = Text()
        for ts_str, msg in self._recent_events:
            events_text.append(f"{ts_str}  ", style="dim")
            if msg.startswith(("✗", "⚠")):
                events_text.append(msg + "\n", style="red" if msg.startswith("✗") else "yellow")
            elif msg.startswith("✓"):
                events_text.append(msg + "\n", style="green")
            elif msg.startswith("⏸"):
                events_text.append(msg + "\n", style="magenta")
            else:
                events_text.append(msg + "\n")
        if not self._recent_events:
            events_text.append("waiting for events…", style="dim")

        events_panel = Panel(events_text, title="[bold]Events[/bold]", box=ROUNDED)

        # --- Errors footer ---
        if self._error_count == 0 and self._warning_count == 0:
            err_title = "[bold green]Errors — none[/bold green]"
        else:
            parts = []
            if self._error_count:
                parts.append(f"{self._error_count} error{'s' if self._error_count != 1 else ''}")
            if self._warning_count:
                parts.append(f"{self._warning_count} warning{'s' if self._warning_count != 1 else ''}")
            err_title = f"[bold red]Errors — {', '.join(parts)}[/bold red]"

        if self._errors:
            err_content = Text()
            for entry in self._errors:
                err_content.append(f"{entry.timestamp}  ", style="dim")
                err_content.append(f"{entry.logger_name}  ", style="dim cyan")
                err_content.append(entry.message + "\n", style="red")
        else:
            err_content = Text("(none)", style="dim")

        errors_panel = Panel(err_content, title=err_title, box=ROUNDED)

        # --- Assemble: header + side-by-side body + errors footer ---
        # Use a Layout only for the body row (horizontal split); wrap the
        # whole thing in a Group so the header and footer stack vertically.
        console_height = self._console.size.height
        body_height = max(6, console_height - 10)

        body_layout = Layout(size=body_height)
        body_layout.split_row(
            Layout(jobs_panel, name="jobs", ratio=5),
            Layout(events_panel, name="events", ratio=4),
        )

        return Group(header_panel, body_layout, errors_panel)


# ---------------------------------------------------------------------------
# Logging handler
# ---------------------------------------------------------------------------


class DashboardLogHandler(logging.Handler):
    """Stdlib logging handler that routes records to ``ConsoleDashboard``.

    * **Before the Live display starts** (pre-startup): falls back to a
      plain ``StreamHandler`` so startup messages are not silently dropped.
    * **While the Live display runs**: WARNING records appear as events in
      the dashboard; ERROR/CRITICAL records appear in the errors panel.
      INFO and below are dropped on the console (they are in the log file).
    """

    def __init__(
        self,
        dashboard: ConsoleDashboard,
        fallback_formatter: logging.Formatter,
        fallback_filter: logging.Filter,
    ) -> None:
        super().__init__(level=logging.WARNING)
        self._dashboard = dashboard
        self._fallback = logging.StreamHandler()
        self._fallback.setLevel(logging.WARNING)
        self._fallback.setFormatter(fallback_formatter)
        self._fallback.addFilter(fallback_filter)

    def emit(self, record: logging.LogRecord) -> None:
        if not self._dashboard.is_live_running:
            self._fallback.emit(record)
            return
        try:
            self._dashboard.add_log_record(record)
        except Exception:  # noqa: BLE001
            self.handleError(record)
