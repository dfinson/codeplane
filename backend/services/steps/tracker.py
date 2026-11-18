"""Turn-based step boundary detection.

Maps adapter-provided turnId changes to step lifecycle events. Pure state
machine — no LLM, no heuristics, no time-gap guessing.

SDK-agnostic: depends on the adapter contract (§2.2) not on SDK internals.
The adapters guarantee non-empty turn_id on every transcript event. If an
adapter violates this, the tracker logs a warning and assigns the event to
the current step (no phantom split).

Tracks: file paths touched per step, Git SHA at step boundaries.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from pydantic import TypeAdapter

if TYPE_CHECKING:
    from collections.abc import Sequence

    from backend.services.events.event_bus import EventBus
    from backend.services.git.git_service import GitService

from backend.models.events import (
    EventKind,
    SessionEvent,
    StepCompletedPayloadDict,
    StepStartedPayloadDict,
    is_admission_receipt,
    new_event,
)
from backend.models.session_event import EventMetadata
from backend.services.events.observation import has_source_grouping, is_observation_revision, observation_label
from backend.services.git.git_service import GitError
from backend.services.tools.tool_classifier import TOOL_CATEGORIES

log = structlog.get_logger()

_MAX_FILES_PER_STEP = 50

_READ_TOOLS = frozenset(name for name, cat in TOOL_CATEGORIES.items() if cat in ("file_read", "file_search"))
_WRITE_TOOLS = frozenset(name for name, cat in TOOL_CATEGORIES.items() if cat == "file_write")


def _extract_file_path(tool_name: str, tool_args: str) -> str | None:
    """Best-effort extract of workspace-relative file path from tool args."""
    if not tool_args:
        return None
    try:
        args = json.loads(tool_args) if tool_args.startswith("{") else {}
        return args.get("filePath") or args.get("file_path") or args.get("path") or args.get("query")
    except (json.JSONDecodeError, AttributeError):
        return None


def _tool_args_to_str(tool_args: Any) -> str:
    if tool_args is None:
        return ""
    if isinstance(tool_args, str):
        return tool_args
    return json.dumps(tool_args, ensure_ascii=False)


@dataclass
class _StepState:
    step_id: str
    step_number: int
    turn_id: str | None
    intent: str
    trigger: str
    started_at: datetime
    start_sha: str | None = None
    tool_count: int = 0
    last_agent_message: str | None = None
    files_read: list[str] = field(default_factory=list)
    files_written: list[str] = field(default_factory=list)
    tool_names: list[str] = field(default_factory=list)
    source_start_known: bool = True
    source_start_event_id: str | None = None


@dataclass(frozen=True)
class _SourceClock:
    timestamp: datetime
    event_id: str
    known: bool


class StepTracker:
    """Track step boundaries from transcript events using turnId.

    Rules (exhaustive):
    1. Operator message → always starts a new step
    2. First event of a new turnId → starts a new step (closes previous)
    3. First transcript event for a job → starts the first step
    4. Job terminal → closes the current step

    Idempotent: replayed events (e.g. after SSE reconnection) do not
    corrupt state — _open() is a no-op if the turnId is already active,
    and _close() tolerates closing an already-closed step.
    """

    def __init__(
        self,
        event_bus: EventBus,
        git_service: GitService | None = None,
        *,
        metadata: EventMetadata | None = None,
        source_timestamps: bool = False,
    ) -> None:
        self._event_bus = event_bus
        self._git_service = git_service
        self._metadata = metadata
        self._source_timestamps = source_timestamps
        self._source_clock: dict[str, _SourceClock] = {}
        self._attempts: dict[tuple[str, str, str], StepTracker] = {}
        self._current: dict[str, _StepState] = {}
        self._counters: dict[str, int] = {}
        self._worktree_paths: dict[str, str] = {}  # job_id → worktree cwd
        self._observed_jobs: set[str] = set()
        # Ring buffer of recent transcript entries per job — used to build
        # preceding_context snapshots at step close time.
        self._transcript_buffers: dict[str, list[dict[str, str]]] = {}

    _BUFFER_SIZE = 10

    def observe_source_time(self, job_id: str, event: SessionEvent) -> None:
        if not self._source_timestamps or is_admission_receipt(event):
            return
        source = event.payload.get("_codeplane_source")
        known = not (isinstance(source, dict) and source.get("timestamp_fallback") is True)
        timestamp = event.timestamp.replace(tzinfo=UTC) if event.timestamp.tzinfo is None else event.timestamp
        self._source_clock[job_id] = _SourceClock(timestamp, event.id, known)

    def source_end_timestamp(self, job_id: str) -> datetime | None:
        clock = self._source_clock.get(job_id)
        return clock.timestamp if clock is not None else None

    def snapshot(self, job_id: str) -> dict[str, Any]:
        current = self._current.get(job_id)
        state = None
        if current is not None:
            state = asdict(current)
            state["started_at"] = current.started_at.isoformat()
        return {
            "version": 1,
            "counter": self._counters.get(job_id, 0),
            "current": state,
            "buffer": [dict(entry) for entry in self._transcript_buffers.get(job_id, ())],
            "source_timestamps": self._source_timestamps,
            "source_clock": (
                {
                    "timestamp": self._source_clock[job_id].timestamp.isoformat(),
                    "event_id": self._source_clock[job_id].event_id,
                    "known": self._source_clock[job_id].known,
                }
                if job_id in self._source_clock
                else None
            ),
        }

    def restore_snapshot(self, job_id: str, value: dict[str, Any]) -> None:
        if value.get("version") != 1 or not isinstance(value.get("counter"), int) or value["counter"] < 0:
            raise ValueError("Invalid step projection checkpoint")
        if value.get("source_timestamps", False) != self._source_timestamps:
            raise ValueError("Step timing semantics changed; source reconciliation is required")
        raw_clock = value.get("source_clock")
        if raw_clock is not None:
            self._source_clock[job_id] = TypeAdapter(_SourceClock).validate_python(raw_clock)
        else:
            self._source_clock.pop(job_id, None)
        buffer = TypeAdapter(list[dict[str, str]]).validate_python(value.get("buffer"))
        if len(buffer) > self._BUFFER_SIZE:
            raise ValueError("Step projection checkpoint exceeds buffer bound")
        raw_current = value.get("current")
        current = TypeAdapter(_StepState).validate_python(raw_current) if raw_current is not None else None
        if current is not None and (current.step_number < 1 or current.step_number > value["counter"]):
            raise ValueError("Invalid step projection progress")
        self._counters[job_id] = value["counter"]
        self._transcript_buffers[job_id] = buffer
        if current is not None:
            self._current[job_id] = current
        else:
            self._current.pop(job_id, None)

    def _attempt_tracker(self, job_id: str, metadata: EventMetadata) -> StepTracker:
        if not metadata.attempt_id or not metadata.execution_epoch:
            raise ValueError("Attempt transcript requires an explicit attempt and execution epoch")
        key = (job_id, metadata.execution_epoch, metadata.attempt_id)
        tracker = self._attempts.get(key)
        if tracker is None:
            tracker = StepTracker(self._event_bus, self._git_service, metadata=metadata)
            self._attempts[key] = tracker
        return tracker

    def register_worktree(
        self,
        job_id: str,
        worktree_path: str,
        *,
        managed: bool = True,
        attempt_id: str | None = None,
        execution_epoch: str | None = None,
    ) -> None:
        """Set the worktree path for a job. Called from _execute_session_attempt."""
        if attempt_id is not None:
            tracker = self._attempt_tracker(
                job_id, EventMetadata(attempt_id=attempt_id, execution_epoch=execution_epoch)
            )
            existing = tracker._worktree_paths.get(job_id)
            if existing is not None and existing != worktree_path:
                raise ValueError("An attempt workspace cannot change within its execution epoch")
            tracker.register_worktree(job_id, worktree_path, managed=managed)
            return
        self._worktree_paths[job_id] = worktree_path
        if not managed:
            self._observed_jobs.add(job_id)

    def restore_event(self, job_id: str, event: SessionEvent) -> None:
        if self._metadata is None and event.metadata.attempt_id is not None:
            self._attempt_tracker(job_id, event.metadata).restore_event(job_id, event)
            return
        payload = event.payload
        if event.kind == EventKind.step_started:
            number = int(payload["step_number"])
            self._counters[job_id] = max(number, self._counters.get(job_id, 0))
            self._current[job_id] = _StepState(
                step_id=str(payload["step_id"]),
                step_number=number,
                turn_id=payload.get("turn_id"),
                intent=str(payload.get("intent") or ""),
                trigger=str(payload.get("trigger") or "recovered"),
                started_at=event.timestamp.replace(tzinfo=UTC) if event.timestamp.tzinfo is None else event.timestamp,
            )
        elif event.kind == EventKind.step_completed:
            current = self._current.get(job_id)
            if current is not None and current.step_id == payload.get("step_id"):
                self._current.pop(job_id, None)
        elif event.kind in (EventKind.tool_call_completed, EventKind.tool_call_failed, EventKind.message_assistant):
            current = self._current.get(job_id)
            if current is not None:
                self._accumulate_step(job_id, current, event)

    def current_step(
        self, job_id: str, *, attempt_id: str | None = None, execution_epoch: str | None = None
    ) -> _StepState | None:
        if attempt_id is not None:
            tracker = self._attempts.get((job_id, execution_epoch or "", attempt_id))
            return tracker.current_step(job_id) if tracker is not None else None
        return self._current.get(job_id)

    async def on_transcript_event(self, job_id: str, event: SessionEvent) -> None:
        """Process a TranscriptUpdated event."""
        if is_admission_receipt(event):
            return
        if is_observation_revision(event):
            return
        if self._metadata is None and event.metadata.attempt_id is not None:
            tracker = self._attempt_tracker(job_id, event.metadata)
            tracker._metadata = event.metadata
            await tracker.on_transcript_event(job_id, event)
            return
        payload = event.payload
        content = str(payload.get("content", "") or "")
        turn_id = payload.get("turn_id") or ""
        motivation = getattr(event.metadata, "motivation", None)
        tool_intent = (
            observation_label(event) if has_source_grouping(event) else str(getattr(motivation, "intent", "") or "")
        )

        if event.kind == EventKind.message_delta:
            return

        # Buffer non-delta transcript entries for preceding_context.
        # Completed tool calls (tool.call.completed with a result field)
        # store the raw result content so the downstream motivation model
        # can interpret it directly — no separate summarization step.
        if event.kind not in (EventKind.message_delta, EventKind.tool_result_chunk, EventKind.tool_call_started):
            entry: dict[str, str] = {"kind": str(event.kind)}
            t_name = payload.get("tool_name")
            if event.kind in (EventKind.tool_call_completed, EventKind.tool_call_failed):
                if t_name:
                    entry["tool_name"] = str(t_name)
                t_args = _tool_args_to_str(payload.get("arguments"))
                if t_args:
                    entry["arguments"] = t_args
                raw_result = str(payload.get("result", "") or "")
                if raw_result:
                    entry["result"] = raw_result
            else:
                entry["content"] = content
                if t_name:
                    entry["tool_name"] = str(t_name)
            buf = self._transcript_buffers.setdefault(job_id, [])
            buf.append(entry)
            if len(buf) > self._BUFFER_SIZE:
                del buf[: len(buf) - self._BUFFER_SIZE]

        # Skip SDK-internal tools (they carry a new turn_id but shouldn't
        # trigger a step boundary).
        tool_name = payload.get("tool_name", "")
        if tool_name == "report_intent" and not has_source_grouping(event):
            # Extract the intent label and attach it to the current step
            # so the title generator can use it later.
            current = self._current.get(job_id)
            if current:
                args_raw = payload.get("arguments") or ""
                if isinstance(args_raw, str):
                    try:
                        args_obj = json.loads(args_raw)
                    except (ValueError, TypeError):
                        args_obj = {}
                else:
                    args_obj = args_raw
                intent_text = args_obj.get("intent") or args_obj.get("description") or ""
                if intent_text:
                    current.intent = intent_text
            return

        if not turn_id and event.kind != EventKind.message_user:
            log.debug(
                "step_tracker_missing_turn_id",
                job_id=job_id,
                kind=str(event.kind),
                event_id=event.id,
            )

        current = self._current.get(job_id)

        new_step_trigger: str | None = None
        intent = ""

        if event.kind == EventKind.message_user:
            new_step_trigger = "operator_message"
            first_line = content.split("\n")[0].strip()
            intent = first_line if first_line else "Operator request"

        elif current is None:
            new_step_trigger = "job_start"
            intent = tool_intent or content or "Starting work"

        elif turn_id and turn_id != current.turn_id:
            new_step_trigger = "turn_change"
            intent = tool_intent or content or "Continuing work"

        # Idempotency: if turn_id matches current, no new step
        if new_step_trigger:
            if current:
                await self._close(job_id, current, "completed")
            await self._open(job_id, intent, turn_id, new_step_trigger, source_event=event)
            current = self._current[job_id]

        if current:
            self._accumulate_step(job_id, current, event)

    def _accumulate_step(self, job_id: str, current: _StepState, event: SessionEvent) -> None:
        if is_observation_revision(event):
            return
        payload = event.payload
        turn_id = payload.get("turn_id")
        content = str(payload.get("content") or "")
        if turn_id and not current.turn_id:
            current.turn_id = turn_id
        if event.kind in (EventKind.tool_call_completed, EventKind.tool_call_failed):
            current.tool_count += 1
            tool_name = payload.get("tool_name", "")
            if tool_name and tool_name not in current.tool_names:
                current.tool_names.append(tool_name)
            if (
                event.kind == EventKind.tool_call_failed
                or payload.get("success") is False
                or (has_source_grouping(event) and payload.get("success") is not True)
            ):
                return
            tool_args = _tool_args_to_str(payload.get("arguments"))
            path = _extract_file_path(tool_name, tool_args)
            if path:
                wt = self._worktree_paths.get(job_id, "")
                if wt and path.startswith(wt):
                    path = path[len(wt) :].lstrip("/\\")
                if (
                    tool_name in _READ_TOOLS
                    and path not in current.files_read
                    and len(current.files_read) < _MAX_FILES_PER_STEP
                ):
                    current.files_read.append(path)
                elif (
                    tool_name in _WRITE_TOOLS
                    and path not in current.files_written
                    and len(current.files_written) < _MAX_FILES_PER_STEP
                ):
                    current.files_written.append(path)
        if event.kind == EventKind.message_assistant and content.strip():
            current.last_agent_message = content

    async def on_job_terminal(self, job_id: str, outcome: str) -> None:
        """Close current step when job reaches terminal state."""
        for key, tracker in tuple(self._attempts.items()):
            if key[0] == job_id:
                await tracker.on_job_terminal(job_id, outcome)
        current = self._current.get(job_id)
        if not current:
            return  # Already closed or never opened — idempotent
        status = "completed" if outcome in ("review", "completed") else outcome
        await self._close(job_id, current, status)

    async def on_attempt_terminal(self, job_id: str, attempt_id: str, execution_epoch: str, outcome: str) -> None:
        tracker = self._attempts.get((job_id, execution_epoch, attempt_id))
        if tracker is not None:
            await tracker.on_job_terminal(job_id, outcome)

    async def _open(
        self,
        job_id: str,
        intent: str,
        turn_id: str | None,
        trigger: str,
        *,
        source_event: SessionEvent | None = None,
    ) -> None:
        n = self._counters.get(job_id, 0) + 1
        self._counters[job_id] = n
        step_id = f"step-{uuid.uuid4().hex[:12]}"
        if self._metadata is not None:
            step_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"cp-step-v1:{job_id}:{self._metadata.execution_epoch}:{self._metadata.attempt_id}:{n}:{turn_id}",
                )
            )

        start_sha: str | None = None
        if self._git_service:
            cwd = self._worktree_paths.get(job_id)
            if cwd:
                try:
                    start_sha = await self._git_service.rev_parse("HEAD", cwd=cwd)
                except GitError:
                    log.debug("step_open_rev_parse_failed", job_id=job_id, exc_info=True)

        source = source_event.payload.get("_codeplane_source") if source_event is not None else None
        source_known = not (isinstance(source, dict) and source.get("timestamp_fallback") is True)
        if self._source_timestamps:
            if source_event is None:
                raise ValueError("An observation step requires source timestamp provenance")
            started_at = source_event.timestamp
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=UTC)
        else:
            started_at = datetime.now(UTC)
        state = _StepState(
            step_id=step_id,
            step_number=n,
            turn_id=turn_id,
            intent=intent,
            trigger=trigger,
            started_at=started_at,
            start_sha=start_sha,
            source_start_known=source_known if self._source_timestamps else True,
            source_start_event_id=source_event.id if self._source_timestamps and source_event is not None else None,
        )
        self._current[job_id] = state
        payload: StepStartedPayloadDict = {
            "step_id": step_id,
            "step_number": n,
            "turn_id": turn_id,
            "intent": intent,
            "trigger": trigger,
            "timing_source": "source" if self._source_timestamps else "live",
        }
        if self._source_timestamps:
            payload["source_started_at"] = state.started_at.isoformat() if state.source_start_known else None
        await self._event_bus.publish(
            new_event(
                session_id=job_id,
                timestamp=state.started_at,
                kind=EventKind.step_started,
                metadata=self._metadata,
                payload=payload,
            )
        )

    async def _close(
        self,
        job_id: str,
        state: _StepState,
        status: str,
    ) -> None:
        if job_id not in self._current:
            return  # Already closed — idempotent

        timing: StepCompletedPayloadDict = {}
        duration_ms: int | None
        if self._source_timestamps:
            clock = self._source_clock.get(job_id)
            now = clock.timestamp if clock is not None else state.started_at
            end_known = (
                clock is not None
                and clock.known
                and clock.event_id != state.source_start_event_id
                and (not state.source_start_known or now >= state.started_at)
            )
            duration_ms = (
                int((now - state.started_at).total_seconds() * 1000) if state.source_start_known and end_known else None
            )
            timing = {
                "timing_source": "source",
                "source_started_at": state.started_at.isoformat() if state.source_start_known else None,
                "source_completed_at": now.isoformat() if end_known else None,
            }
        else:
            now = datetime.now(UTC)
            duration_ms = int((now - state.started_at).total_seconds() * 1000)
            timing = {"timing_source": "live"}

        end_sha: str | None = None
        if self._git_service:
            cwd = self._worktree_paths.get(job_id)
            if cwd:
                try:
                    # Auto-commit any uncommitted work so the SHA boundary
                    # captures exactly this step's changes.  ~50 ms, no-op
                    # when the worktree is clean.
                    if job_id not in self._observed_jobs:
                        await self._git_service.auto_commit(
                            cwd=cwd,
                            message=f"codeplane: step {state.step_number}",
                        )
                    end_sha = await self._git_service.rev_parse("HEAD", cwd=cwd)
                except GitError:
                    log.debug("step_close_git_failed", job_id=job_id, exc_info=True)

        # Snapshot preceding context from the ring buffer
        preceding_context: str | None = None
        buf = self._transcript_buffers.get(job_id)
        if buf:
            preceding_context = json.dumps(buf[-8:], ensure_ascii=False)

        # Compute diff line counts while the worktree is still alive
        diff_additions = 0
        diff_deletions = 0
        if state.start_sha and end_sha and state.start_sha != end_sha and self._git_service:
            cwd = self._worktree_paths.get(job_id)
            if cwd:
                try:
                    diff_additions, diff_deletions = await self._git_service.diff_numstat(
                        state.start_sha, end_sha, cwd=cwd
                    )
                except GitError:
                    log.debug("step_diff_numstat_failed", job_id=job_id, exc_info=True)

        payload: StepCompletedPayloadDict = {
            "step_id": state.step_id,
            "turn_id": state.turn_id,
            "status": status,
            "tool_count": state.tool_count,
            "duration_ms": duration_ms,
            "has_summary": state.last_agent_message is not None,
            "agent_message": state.last_agent_message,
            "files_read": state.files_read,
            "files_written": state.files_written,
            "tool_names": state.tool_names,
            "start_sha": state.start_sha,
            "end_sha": end_sha,
            "preceding_context": preceding_context,
            "diff_additions": diff_additions,
            "diff_deletions": diff_deletions,
            "worktree_path": self._worktree_paths.get(job_id),
        }
        payload.update(timing)
        await self._event_bus.publish(
            new_event(
                session_id=job_id,
                timestamp=now,
                kind=EventKind.step_completed,
                metadata=self._metadata,
                payload=payload,
            )
        )
        self._current.pop(job_id, None)

    def cleanup(self, job_id: str) -> None:
        """Remove all in-memory state for a job."""
        for key in tuple(self._attempts):
            if key[0] == job_id:
                self._attempts.pop(key).cleanup(job_id)
        self._current.pop(job_id, None)
        self._counters.pop(job_id, None)
        self._worktree_paths.pop(job_id, None)
        self._observed_jobs.discard(job_id)
        self._transcript_buffers.pop(job_id, None)
        self._source_clock.pop(job_id, None)

    def cleanup_attempt(self, job_id: str, attempt_id: str, execution_epoch: str) -> None:
        tracker = self._attempts.pop((job_id, execution_epoch, attempt_id), None)
        if tracker is not None:
            tracker.cleanup(job_id)


# ---------------------------------------------------------------------------
# Plan hydration from persisted events
# ---------------------------------------------------------------------------


def _detect_latest_generation(step_events: Sequence[SessionEvent]) -> set[str]:
    """Detect the latest plan generation from a sequence of plan_step_updated events.

    The step tracker replaces the in-memory plan when re-inferring, but old
    plan_step_updated events remain in the DB.  We detect generation
    boundaries: a batch of >=2 *new* step IDs appearing together (within
    5 seconds) marks a new plan generation.  Returns only the latest
    generation's IDs.
    """
    seen_ids: set[str] = set()
    current_gen_ids: list[str] = []
    current_gen_start: float = 0.0

    for ev in step_events:
        payload = ev.payload
        sid = payload.get("plan_step_id", "")
        if not sid:
            continue
        ts = ev.timestamp.timestamp() if hasattr(ev.timestamp, "timestamp") else 0.0
        is_new = sid not in seen_ids

        if is_new:
            if not current_gen_ids or (ts - current_gen_start > 5.0):
                current_gen_ids = [sid]
                current_gen_start = ts
            else:
                current_gen_ids.append(sid)
            seen_ids.add(sid)

    if len(current_gen_ids) >= 2:
        return set(current_gen_ids)
    return seen_ids


def hydrate_plan_steps(
    step_events: Sequence[SessionEvent],
    job_id: str,
    *,
    detect_generations: bool = True,
    exclude_pending: bool = False,
) -> list[dict[str, Any]]:
    """Build ordered plan step payloads from persisted plan_step_updated events.

    Args:
        step_events: Chronological plan_step_updated domain events.
        job_id: The job these events belong to.
        detect_generations: When True, detect plan re-inference and keep only
            the latest generation's steps.  Set False for simpler contexts
            (e.g. shared snapshots) that don't need generation filtering.
        exclude_pending: When True, omit steps still in "pending" status.

    Returns:
        List of dicts suitable for constructing PlanStepPayload objects.
    """
    allowed_ids = _detect_latest_generation(step_events) if detect_generations else None

    step_latest: dict[str, dict[str, Any]] = {}
    step_order: list[str] = []
    for ev in step_events:
        payload = ev.payload
        sid = payload.get("plan_step_id", "")
        if not sid:
            continue
        if allowed_ids is not None and sid not in allowed_ids:
            continue
        step_latest[sid] = payload
        if sid not in step_order:
            step_order.append(sid)

    result: list[dict[str, Any]] = []
    for sid in step_order:
        p = step_latest[sid]
        if exclude_pending and p.get("status") == "pending":
            continue
        result.append(
            {
                "job_id": job_id,
                "plan_step_id": p.get("plan_step_id", ""),
                "label": p.get("label", ""),
                "summary": p.get("summary"),
                "status": p.get("status", "pending"),
                "order": p.get("order", 0),
                "tool_count": p.get("tool_count", 0),
                "files_written": p.get("files_written"),
                "started_at": p.get("started_at"),
                "completed_at": p.get("completed_at"),
                "duration_ms": p.get("duration_ms"),
                "start_sha": p.get("start_sha"),
                "end_sha": p.get("end_sha"),
            }
        )
    return result
