# Unified Agent Transcript System

**Status:** Proposal
**Scope:** Step-based execution model, search & navigation, mobile adaptation, context provenance, step-level checkpoints, artifact linking, resumability, view state persistence

---

## Table of Contents

1. [Motivation](#1-motivation)
2. [Core Design: Turns Are Steps](#2-core-design-turns-are-steps)
3. [Architecture Overview](#3-architecture-overview)
4. [Step Detection](#4-step-detection)
5. [Backend Schema](#5-backend-schema)
6. [Step Titles](#6-step-titles)
7. [Streaming Protocol](#7-streaming-protocol)
8. [Frontend Structure](#8-frontend-structure)
9. [End-to-End Flow](#9-end-to-end-flow)
10. [Search, Filters, and Navigation](#10-search-filters-and-navigation)
11. [Mobile Adaptation](#11-mobile-adaptation)
12. [Context Provenance](#12-context-provenance)
13. [Step-Level Checkpoints and Diff](#13-step-level-checkpoints-and-diff)
14. [Artifact Linking](#14-artifact-linking)
15. [Resumability](#15-resumability)
16. [View State Persistence](#16-view-state-persistence)
17. [Migration Strategy](#17-migration-strategy)
18. [Cost Analysis](#18-cost-analysis)
19. [Design Decisions](#19-design-decisions)

**Appendices:**
- [A: File Change Manifest](#appendix-a-file-change-manifest)
- [B: StepTracker Implementation](#appendix-b-steptracker-implementation)
- [C: SSE Event Type Registry](#appendix-c-sse-event-type-registry)

---

## 1. Motivation

Agent sessions in CodePlane produce a flat stream of transcript events — tool calls, agent messages, operator messages, and streaming deltas. The current frontend groups these by `turnId` for display (`AgentTurnData` in `TranscriptPanel`), but this grouping is:

- **Ephemeral** — exists only in the frontend render tree, lost on page refresh.
- **Unqueryable** — no way to ask "what happened in step 3?" or "which step touched auth.py?"
- **Unnavigable** — long sessions become walls of text with no structure, search, or deep linking.

Meanwhile, the `ProgressTrackingService` runs two timer-driven loops — `_headline_loop` (15s) and `_plan_loop` (20s) — that call a cheap LLM ~70 times per 10-minute session to generate milestone headlines and plan state. These headlines approximate what explicit step tracking would provide for free.

**This design formalizes the implicit turn grouping into persistent, queryable, status-tracked steps.** It replaces timer-driven summarization with event-driven structure, and builds search, navigation, checkpoints, and resumability on top.

### Design Principles

Three principles guide the design away from common pitfalls:

**Use deterministic signals, not heuristics.** The `turnId` changeover from the adapter layer is a reliable step boundary. Heuristic approaches — time gaps between events, pattern-matching on message content, counting tool calls — are fragile. A test suite can run for minutes (breaking time gaps), an agent can send intermediate messages between tool sequences (breaking message-based detection), and tool counts vary wildly across tasks.

**Don't regenerate what the agent already said.** The agent's final `role: "agent"` message in each turn IS the natural-language summary of what was done and why. It has full context — file contents, error messages, reasoning. A cheap model working from tool names and truncated progress strings produces a strictly worse version. The agent's own message is the summary; the cheap model's job is limited to generating a short title for scannability.

**Don't create parallel data paths.** Existing `tool_running` and `tool_call` events already carry tool name, intent, and display text — they ARE the progress stream. Rather than emitting separate "step progress" events that duplicate this data in a different format, tag existing events with step metadata and let the frontend derive progress at render time.

---

## 2. Core Design: Turns Are Steps

Both the Copilot and Claude adapters assign a `turnId` to each transcript event, grouping events that belong to the same assistant turn. The step system formalizes this into a persistent, queryable structure.

### 2.1 Where turnId Comes From

**Copilot adapter** — passes through the SDK's native turn identifier:
```python
# copilot_adapter.py:575
"turn_id": str(data.turn_id) if hasattr(data, "turn_id") and data.turn_id else self._current_turn_id
```
The SDK provides `data.turn_id` on most events. When absent, the adapter generates a fallback UUID per assistant message cycle. This fallback must be applied consistently at **all 5 emission sites** in the adapter: `_handle_tool_start`, `assistant.message`, `assistant.streaming_delta`, `tool.execution_start`, and `tool.execution_complete`. A partial fix (only one site) produces inconsistent turnIds within a single turn.

**Claude adapter** — synthesizes its own turn identifier:
```python
# claude_adapter.py:590
self._current_turn_id = str(uuid.uuid4())
# Each AssistantMessage starts a new turn for grouping
```
The Claude Code SDK has no native turn concept. The adapter generates a UUID per `AssistantMessage` block.

### 2.2 The Adapter Contract

`turnId` is an **adapter-level** signal, not an SDK-level signal. The two adapters produce it through completely different mechanisms. The step system depends on the adapter contract, not on SDK behavior.

**Required contract for `AgentAdapterInterface`:**

Every `SessionEvent` with `kind=transcript` MUST include a non-empty `turn_id` string that:
- Is stable within a single assistant turn (all tool calls + messages in the same turn share a `turn_id`)
- Changes when the SDK begins a new assistant turn
- Is a UUID or similar opaque identifier (no semantic meaning)

If the underlying SDK does not provide a native turn identifier, the adapter MUST synthesize one. **Empty `turn_id` is a protocol violation.**

The Claude adapter already satisfies this contract. The Copilot adapter requires a small fix: generate a fallback UUID when `data.turn_id` is absent instead of emitting `""`.

### 2.3 What a Turn Contains

A turn contains:
1. Optional `agent_delta` streaming chunks (ephemeral, not persisted)
2. Zero or more `tool_running` → `tool_call` pairs
3. A final `agent` message (the agent's own response/summary)

The frontend *already groups by turnId* — `AgentTurnData` in `TranscriptPanel` does exactly this:
```typescript
interface AgentTurnData {
  key: string;                        // turnId
  reasoning: TranscriptEntry | null;
  toolCalls: TranscriptEntry[];
  message: TranscriptEntry | null;
  firstTimestamp: string;
}
```

The turn IS the step. The system already tracks turns. This design formalizes them into a persistent, queryable, status-tracked structure.

### 2.4 Current State vs Target State

| Aspect | Current | Target |
|--------|---------|--------|
| **Turn tracking** | Frontend-only grouping via `turnId` | Backend persists turns as steps with IDs, numbers, status |
| **Current action** | Shown inline as streaming tool events | Derived at render time from latest `tool_running` within the step's turnId |
| **Summary** | Agent's final message shown as part of the chat stream | Agent's final message elevated as the step's primary output |
| **Title** | Raw intent from `report_intent` or none | Short label generated by cheap model from first few tool calls |
| **Status** | Implicit (is the turn still receiving events?) | Explicit: `running` → `completed` / `failed` / `canceled` |
| **Timeline** | Timer-driven LLM headlines (every 15s) | Step titles derived from step completion events |
| **Plan** | Timer-driven LLM extraction (every 20s) OR native todo | Native todo only; step list IS the plan for the current session |

---

## 3. Architecture Overview

```
                    ┌─────────────────────────────┐
                    │     Agent SDK Session        │
                    │  (Copilot / Claude adapter)  │
                    └──────────┬──────────────────┘
                               │ SessionEvent stream (with turnId)
                               ▼
                    ┌──────────────────────────────┐
                    │    RuntimeService             │
                    │                              │
                    │  StepTracker (inline)         │
                    │  • turnId changeover → step   │
                    │  • operator msg → step         │
                    │  • job terminal → close step   │
                    │  • Tags transcript events      │
                    │    with step_id + step_number  │
                    └──────────┬──────────────────┘
                               │ DomainEvents (existing + 3 new step events)
                               ▼
              ┌────────────────────────────────────┐
              │           EventBus                 │
              └──┬────────┬────────┬──────────┬───┘
                 │        │        │          │
                 ▼        ▼        ▼          ▼
            Persist   SSEManager  StepTitle  StepPersistence
              │           │       Generator   Subscriber
              │       SSE frames    │            │
              │           │     title event   StepRow
              │           ▼         │         (SQLite)
              │     ┌──────────┐    │
              │     │ Frontend │◄───┘ (via SSE)
              │     └──────────┘
              ▼
         EventRow (SQLite)
```

Key architectural choices:
- **StepTracker is thin** — a turnId state machine, not a heuristic engine.
- **No summarizer** — the agent's own message is the summary.
- **StepTitleGenerator** generates ≤6 word titles for scannability, not multi-sentence summaries.
- **No StepUpdated event** — progress is derived from existing tool events tagged with step metadata.
- **StepPersistenceSubscriber** listens for `step_started` / `step_completed` events and writes `StepRow` to the database via `StepRepository`.

---

## 4. Step Detection

### 4.1 Boundary Rules (Exhaustive)

A new step is created when:

| # | Condition | Signal | Intent Source |
|---|-----------|--------|---------------|
| 1 | **Operator message arrives** (role=`operator`) | Always | Operator message text (first sentence) |
| 2 | **First event of a new turnId** (role=`tool_running`, `tool_call`, `agent`, `reasoning`) where `turnId ≠ current_step.turn_id` | Adapter contract (§2.2) | `tool_intent` from first tool, or `report_intent` if present, or "Working" |
| 3 | **Job starts** (first transcript event for this job) | Always | Job prompt (first sentence) |

Three rules. Rules 1 and 3 are unconditional. Rule 2 depends on the adapter contract — specifically, that every transcript event carries a non-empty `turn_id`. This is enforced at the adapter layer, making the StepTracker itself SDK-agnostic.

**Defensive fallback:** If `turn_id` is empty/null on a transcript event despite the contract, the StepTracker treats it as belonging to the current step (no boundary). This prevents phantom step splits from adapter bugs but logs a warning.

**Idempotency:** If a `step_started` event replays (e.g., after SSE reconnection), the StepTracker ignores it if the `turnId` matches the already-open step. Similarly, `_close()` tolerates closing an already-closed step.

### 4.1.1 Integration Points in RuntimeService

StepTracker must process transcript events at **all** code paths that publish `TranscriptUpdated`, not just `_execute_session_attempt`:

| Code Path | What It Does | StepTracker Call |
|-----------|-------------|------------------|
| `_execute_session_attempt` | Main event loop consuming adapter stream | `on_transcript_event` on every non-delta event |
| `_run_followup_turn` | Verify/self-review turns (separate event loop) | Same — `on_transcript_event` on every non-delta event |
| `send_message` | Operator messages published directly to EventBus | `on_transcript_event` before `event_bus.publish` |

**`_run_followup_turn` fix:** This method has its own `async for` loop that currently bypasses StepTracker. It must call `step_tracker.on_transcript_event(job_id, domain_event)` and tag transcript events with `step_id`/`step_number` identically to the main loop. Without this, verify and self-review turns produce untagged transcript events and no step records.

**`send_message` fix:** `RuntimeService.send_message()` publishes a `transcript_updated` DomainEvent directly to the EventBus without going through the event loop. StepTracker must process this event *before* publication so that (a) an operator-triggered step is opened, and (b) the transcript event is tagged:

```python
# In RuntimeService.send_message(), before event_bus.publish:
await self._step_tracker.on_transcript_event(job_id, domain_event)
current = self._step_tracker.current_step(job_id)
if current:
    domain_event.payload["step_id"] = current.step_id
    domain_event.payload["step_number"] = current.step_number
```

**Echo suppression interaction:** Operator messages are echo-suppressed in `_process_agent_event` — the SDK echoes the operator message back and the adapter drops it. `send_message` publishes the *local* copy, which is the one StepTracker sees. The suppressed echo never reaches StepTracker. No double-step.

### 4.2 Step Completion

A step is completed when:

| # | Condition | Status |
|---|-----------|--------|
| 1 | A new step starts (rule above triggers) | `completed` — previous step closes |
| 2 | Final `role: "agent"` message arrives for this turnId | `completed` — this step is done and has a summary |
| 3 | Job reaches terminal state | Inherits from job: `completed` / `failed` / `canceled` |

### 4.3 Agent Message = Summary

When a step completes with a final `agent` message, that message becomes the step's `agent_message`. No LLM call needed:

```python
if role == "agent" and current_step and turn_id == current_step.turn_id:
    content = payload.get("content", "")
    if len(content) > 20:
        current_step.last_agent_message = content
```

For display, the frontend truncates to first 2-3 sentences in the collapsed view. The full message is available on expand.

### 4.4 Edge Cases

**Agent message too short or absent:** If the step completes without an agent message, the summary falls back to: "Completed {tool_count} operations." No LLM call — just a template.

**Agent message very long (>500 chars):** Frontend truncates at the first paragraph break or 300 chars for the collapsed view. Full message available on expand. No server-side truncation.

**Multiple agent messages in one turn:** Use the last one (the SDK sometimes emits partial `agent` messages before tool calls, then a final comprehensive one).

**Streaming deltas:** `agent_delta` events are ephemeral and NOT tracked by StepTracker. Only the final `agent` message (which replaces the deltas) is captured as the summary.

---

## 5. Backend Schema

### 5.1 Domain Events

Add to `DomainEventKind`:

```python
class DomainEventKind(StrEnum):
    # ... existing ...
    step_started = "StepStarted"
    step_completed = "StepCompleted"
    step_title_generated = "StepTitleGenerated"
```

Three new event kinds. No `StepUpdated` — progress is derived from existing tool events.

### 5.2 Event Payload Types

```python
class StepStartedPayloadDict(TypedDict, total=False):
    step_id: str           # "step-{uuid[:12]}"
    step_number: int       # 1-indexed sequential within job
    turn_id: str | None    # SDK turn identifier (the canonical link)
    intent: str            # detected or declared intent (≤120 chars)
    trigger: str           # "operator_message" | "turn_change" | "job_start"


class StepCompletedPayloadDict(TypedDict, total=False):
    step_id: str
    status: str            # "completed" | "failed" | "canceled"
    tool_count: int        # number of tool_call events in this step
    duration_ms: int       # wall-clock duration
    has_summary: bool      # whether an agent message was captured
    files_read: list[str]  # workspace-relative paths touched
    files_written: list[str]
    start_sha: str | None  # Git HEAD at step start
    end_sha: str | None    # Git HEAD at step end


class StepTitlePayloadDict(TypedDict, total=False):
    step_id: str
    title: str             # ≤6 words, generated by cheap model
```

### 5.3 Transcript Event Tagging

Every `TranscriptUpdated` event is tagged with the current step's ID and number. In `RuntimeService._process_agent_event`, after `StepTracker` processes the event:

```python
if domain_event.kind == DomainEventKind.transcript_updated:
    current = step_tracker.current_step(job_id)
    if current:
        domain_event.payload["step_id"] = current.step_id
        domain_event.payload["step_number"] = current.step_number
```

Every existing transcript event carries its step association. The frontend gets the mapping for free — no join logic, no timestamp-range matching.

Extension to `TranscriptPayloadDict`:

```python
class TranscriptPayloadDict(TypedDict, total=False):
    # ... existing fields ...
    step_id: str | None
    step_number: int | None
```

### 5.4 Database — StepRow

```python
class StepRow(Base):
    __tablename__ = "steps"

    id = Column(String(36), primary_key=True)
    job_id = Column(String(36), ForeignKey("jobs.id", ondelete="CASCADE"), index=True, nullable=False)
    step_number = Column(Integer, nullable=False)
    turn_id = Column(String(36), nullable=True)
    intent = Column(Text, nullable=False, default="")
    title = Column(String(60), nullable=True)
    status = Column(String(20), nullable=False, default="running")
    trigger = Column(String(30), nullable=False)
    tool_count = Column(Integer, nullable=False, default=0)
    agent_message = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    duration_ms = Column(Integer, nullable=True)
    start_sha = Column(String(40), nullable=True)
    end_sha = Column(String(40), nullable=True)
    files_read = Column(Text, nullable=True)    # JSON array
    files_written = Column(Text, nullable=True)  # JSON array

    __table_args__ = (
        Index("ix_steps_job_number", "job_id", "step_number"),
    )
```

Uses `Column()` syntax to match all existing ORM models in `db.py`. The `ondelete="CASCADE"` on `job_id` ensures steps are automatically deleted when `RetentionService` deletes the parent `JobRow` — no separate cleanup path needed.

### 5.5 Step Repository

```python
class StepRepository:
    """Persistence for execution steps."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(self, step: StepRow) -> None:
        async with self._session_factory() as session:
            session.add(step)
            await session.commit()

    async def complete(
        self,
        step_id: str,
        status: str,
        agent_message: str | None = None,
        tool_count: int = 0,
        completed_at: datetime | None = None,
        duration_ms: int | None = None,
        start_sha: str | None = None,
        end_sha: str | None = None,
        files_read: str | None = None,
        files_written: str | None = None,
    ) -> None:
        async with self._session_factory() as session:
            stmt = (
                update(StepRow)
                .where(StepRow.id == step_id)
                .values(
                    status=status,
                    agent_message=agent_message,
                    tool_count=tool_count,
                    completed_at=completed_at,
                    duration_ms=duration_ms,
                    start_sha=start_sha,
                    end_sha=end_sha,
                    files_read=files_read,
                    files_written=files_written,
                )
            )
            await session.execute(stmt)
            await session.commit()

    async def set_title(self, step_id: str, title: str) -> None:
        async with self._session_factory() as session:
            stmt = update(StepRow).where(StepRow.id == step_id).values(title=title)
            await session.execute(stmt)
            await session.commit()

    async def get(self, step_id: str) -> StepRow | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(StepRow).where(StepRow.id == step_id)
            )
            return result.scalar_one_or_none()

    async def get_by_job(self, job_id: str, limit: int = 200) -> list[StepRow]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(StepRow)
                .where(StepRow.job_id == job_id)
                .order_by(StepRow.step_number)
                .limit(limit)
            )
            return list(result.scalars().all())
```

**Scoping:** `StepRepository` uses `session_factory` (app-scoped) rather than per-request `AsyncSession`, matching the pattern used by `_persist_and_broadcast` in `lifespan.py`. This allows `StepPersistenceSubscriber` to call it from EventBus callbacks outside the HTTP request lifecycle.

### 5.6 Step Persistence Subscriber

An EventBus subscriber writes step lifecycle events to the database:

```python
class StepPersistenceSubscriber:
    """Listens for step events and persists them via StepRepository.

    Registered as an EventBus subscriber — receives ALL events.
    Filters to step_started / step_completed internally and early-returns
    on all other event kinds to minimize async overhead.
    """

    def __init__(self, step_repo: StepRepository) -> None:
        self._step_repo = step_repo

    async def __call__(self, event: DomainEvent) -> None:
        """EventBus entry point — dispatches to kind-specific handlers."""
        if event.kind == DomainEventKind.step_started:
            await self._on_step_started(event)
        elif event.kind == DomainEventKind.step_completed:
            await self._on_step_completed(event)
        # All other event kinds: early return (no-op)

    async def _on_step_started(self, event: DomainEvent) -> None:
        p = event.payload
        row = StepRow(
            id=p["step_id"],
            job_id=event.job_id,
            step_number=p["step_number"],
            turn_id=p.get("turn_id"),
            intent=p.get("intent", ""),
            trigger=p["trigger"],
            started_at=event.timestamp,
        )
        await self._step_repo.create(row)

    async def _on_step_completed(self, event: DomainEvent) -> None:
        p = event.payload
        await self._step_repo.complete(
            step_id=p["step_id"],
            status=p["status"],
            tool_count=p.get("tool_count", 0),
            duration_ms=p.get("duration_ms"),
            completed_at=event.timestamp,
            start_sha=p.get("start_sha"),
            end_sha=p.get("end_sha"),
            files_read=json.dumps(p.get("files_read", [])),
            files_written=json.dumps(p.get("files_written", [])),
        )
```

### 5.7 API Schema

```python
class StepPayload(CamelModel):
    """Step data for REST API and SSE."""
    step_id: str
    step_number: int
    job_id: str
    turn_id: str | None = None
    intent: str
    title: str | None = None
    status: str
    trigger: str
    tool_count: int = 0
    agent_message: str | None = None
    duration_ms: int | None = None
    started_at: str
    completed_at: str | None = None
    files_read: list[str] | None = None
    files_written: list[str] | None = None
    start_sha: str | None = None
    end_sha: str | None = None
    artifact_count: int = 0


class StepTitlePayload(CamelModel):
    """SSE payload for step title generation."""
    step_id: str
    title: str


class StepDiffPayload(CamelModel):
    """Response for step-scoped Git diff."""
    step_id: str
    diff: str
    files_changed: int


class TranscriptSearchResult(CamelModel):
    """A transcript event matching a search query."""
    seq: int
    role: str
    content: str
    tool_name: str | None = None
    step_id: str | None = None
    step_number: int | None = None
    timestamp: str
```

### 5.8 REST Endpoints

```python
@router.get("/api/jobs/{job_id}/steps", response_model=list[StepPayload])
async def get_job_steps(
    job_id: str,
    step_repo: StepRepository = Depends(get_step_repo),
) -> list[StepPayload]:
    """Return all steps for a job, ordered by step_number."""
    rows = await step_repo.get_by_job(job_id)
    return [StepPayload.from_row(row) for row in rows]


@router.get("/api/jobs/{job_id}/steps/{step_id}/diff")
async def get_step_diff(
    job_id: str,
    step_id: str,
    step_repo: StepRepository = Depends(get_step_repo),
    git_service: GitService = Depends(get_git_service),
) -> StepDiffPayload:
    """Return the Git diff for a specific step."""
    step = await step_repo.get(step_id)
    if not step or not step.start_sha or not step.end_sha:
        return StepDiffPayload(step_id=step_id, diff="", files_changed=0)
    if step.start_sha == step.end_sha:
        return StepDiffPayload(step_id=step_id, diff="", files_changed=0)

    # Look up worktree path — diff_range takes cwd, not job_id
    job = await job_service.get(job_id)
    if not job.worktree_path:
        return StepDiffPayload(step_id=step_id, diff="", files_changed=0)

    diff_text = await git_service.diff_range(step.start_sha, step.end_sha, cwd=job.worktree_path)
    files_changed = diff_text.count("\ndiff --git ") + (1 if diff_text.startswith("diff --git ") else 0)
    return StepDiffPayload(step_id=step_id, diff=diff_text, files_changed=files_changed)


@router.get("/api/jobs/{job_id}/transcript/search")
async def search_transcript(
    job_id: str,
    q: str = Query(..., min_length=2, max_length=200),
    roles: list[str] | None = Query(None),
    step_id: str | None = None,
    limit: int = Query(50, le=200),
    event_repo: EventRepository = Depends(get_event_repo),
) -> list[TranscriptSearchResult]:
    """Full-text search within a job's transcript events."""
    # Uses SQLite LIKE on per-job event set (~200 rows). Sub-millisecond.
    # FTS5 virtual tables are a future optimization if per-job event counts
    # grow past ~1000.
    ...


@router.post("/api/jobs/{job_id}/restore")
async def restore_to_sha(
    job_id: str,
    body: RestoreRequest,
    job_service: JobService = Depends(get_job_service),
    git_service: GitService = Depends(get_git_service),
) -> dict:
    """Reset the job's worktree to a specific commit SHA.

    Destructive — requires frontend confirmation dialog.
    Blocked while the agent is actively running to prevent
    corruption of in-progress work.
    """
    job = await job_service.get(job_id)
    if job.state in ("running", "agent_running"):
        raise HTTPException(
            status_code=409,
            detail="Cannot restore while the agent is running. Cancel the job first.",
        )
    if not job.worktree_path:
        raise HTTPException(status_code=404, detail="Job has no worktree.")

    # Use git reset --hard instead of git checkout to avoid detached HEAD,
    # which would break subsequent agent session pushes.
    await git_service.reset_hard(body.sha, cwd=job.worktree_path)
    return {"restored": True, "sha": body.sha}
```

---

## 6. Step Titles

The cheap model has one job: generate a short (3–6 word) title for each step. This replaces the timer-driven `_headline_loop` and its ~70 LLM calls per session.

**What the title is for:** The collapsed step header. "Fixing JWT validation" is scannable. "Can you fix the authentication bug in /api/login?" (raw operator message) is not.

**What the title is NOT:** A summary. The agent's own message is the summary.

### 6.1 StepTitleGenerator Service

```python
class StepTitleGenerator:
    """Generates short titles for steps using a cheap model.

    Triggered by StepCompleted events. Falls back to intent truncation
    if the model call fails.
    """

    def __init__(
        self,
        utility_session: UtilitySessionService,
        event_bus: EventBus,
        step_repo: StepRepository,
    ) -> None:
        self._utility_session = utility_session
        self._event_bus = event_bus
        self._step_repo = step_repo
        # Context accumulated during step execution, keyed by "job_id:step_id"
        self._step_context: dict[str, _TitleContext] = {}

    def _key(self, job_id: str, step_id: str) -> str:
        return f"{job_id}:{step_id}"

    def on_step_started(self, job_id: str, payload: dict) -> None:
        """Buffer context for title generation."""
        step_id = payload["step_id"]
        self._step_context[self._key(job_id, step_id)] = _TitleContext(
            intent=payload.get("intent", ""),
            tool_names=[],
            tool_intents=[],
        )

    def on_transcript_event(self, job_id: str, payload: dict) -> None:
        """Accumulate tool info for the active step."""
        step_id = payload.get("step_id")
        if not step_id:
            return
        ctx = self._step_context.get(self._key(job_id, step_id))
        if not ctx:
            return
        role = payload.get("role", "")
        if role in ("tool_call", "tool_running"):
            tool_name = payload.get("tool_name", "")
            if tool_name and tool_name not in ctx.tool_names:
                ctx.tool_names.append(tool_name)
            intent = payload.get("tool_intent", "")
            if intent:
                ctx.tool_intents.append(intent[:60])
                if len(ctx.tool_intents) > 5:
                    ctx.tool_intents = ctx.tool_intents[-5:]

    async def on_step_completed(self, event: DomainEvent) -> None:
        """Generate title for a completed step."""
        step_id = event.payload["step_id"]
        ctx = self._step_context.pop(self._key(event.job_id, step_id), None)
        if not ctx:
            return

        if event.payload.get("status") == "canceled":
            return

        # Skip LLM for trivial steps or already-short intents
        if ctx.intent and len(ctx.intent) <= 50 and not ctx.intent.startswith(("Can you", "Please", "I need")):
            title = _strip_to_title(ctx.intent)
        else:
            title = await self._generate_title(ctx)

        await self._step_repo.set_title(step_id, title)

        await self._event_bus.publish(DomainEvent(
            event_id=DomainEvent.make_event_id(),
            job_id=event.job_id,
            timestamp=datetime.now(UTC),
            kind=DomainEventKind.step_title_generated,
            payload={"step_id": step_id, "title": title},
        ))

    async def _generate_title(self, ctx: _TitleContext) -> str:
        """Call cheap model to generate a concise title."""
        tools_text = ", ".join(ctx.tool_names[:6]) if ctx.tool_names else ""
        intents_text = "; ".join(ctx.tool_intents[:3]) if ctx.tool_intents else ""

        prompt = (
            "Generate a 3-6 word title for this coding step. "
            "Use present participle (e.g. 'Fixing auth validation'). "
            "No period. No articles.\n\n"
            f"Original request: {ctx.intent[:100]}\n"
        )
        if tools_text:
            prompt += f"Tools used: {tools_text}\n"
        if intents_text:
            prompt += f"Tool intents: {intents_text}\n"
        prompt += "\nTitle:"

        try:
            raw = await self._utility_session.complete(prompt, timeout=10)
            title = raw.strip().strip('"').strip(".")[:50]
            return title if title else _strip_to_title(ctx.intent)
        except Exception:
            return _strip_to_title(ctx.intent)

    def cleanup(self, job_id: str) -> None:
        """Remove all buffered context for a job."""
        prefix = f"{job_id}:"
        to_remove = [k for k in self._step_context if k.startswith(prefix)]
        for k in to_remove:
            self._step_context.pop(k, None)


@dataclass
class _TitleContext:
    intent: str
    tool_names: list[str]
    tool_intents: list[str]


def _strip_to_title(text: str) -> str:
    """Fallback: extract a short title from raw intent text."""
    for prefix in ("Can you ", "Please ", "I need you to ", "Could you "):
        if text.startswith(prefix):
            text = text[len(prefix):]
    for sep in (".", ",", " — ", " - ", "\n"):
        if sep in text:
            text = text[:text.index(sep)]
            break
    return text[:50].strip()
```

### 6.2 Token Budget

| Component | Tokens |
|-----------|--------|
| Input prompt | ~80–120 |
| Output | ~5–15 |
| **Total per call** | **~100–135** |

For a 10-step session: **~1,000–1,350 tokens total.**

### 6.3 When NOT to Generate

- Steps with ≤1 tool call (trivial — the tool name is sufficient)
- Steps triggered by operator messages where the message is already short
- Canceled steps

This reduces LLM calls to ~5–8 per typical session.

---

## 7. Streaming Protocol

### 7.1 New SSE Event Types

Three new types:

```python
_SSE_EVENT_TYPE.update({
    DomainEventKind.step_started: "step_started",
    DomainEventKind.step_completed: "step_completed",
    DomainEventKind.step_title_generated: "step_title",
})
```

No `step_updated` event. Progress is derived from existing `transcript_update` events, which carry `stepId` and `stepNumber` in their payload.

**Payload registry:** Three new entries must be added to `_SSE_PAYLOAD_REGISTRY` alongside the event type mappings. Additionally, the existing `transcript_update` entry in the payload registry uses an explicit field map — `step_id` and `step_number` must be added to this field map, and `TranscriptPayload` in `api_schemas.py` must include these as optional fields. Without both changes, step metadata is tagged in the backend but silently dropped from SSE frames.

**Suppression policy:** Step events (`step_started`, `step_completed`, `step_title`) must NOT be added to `_SELECTIVE_SUPPRESSED` (high-frequency event suppression when >20 jobs active) or `_JOB_SCOPED_ONLY`. Step events are low-frequency (~5–30 per session) and must flow to both global and job-scoped SSE connections — global connections need them for dashboard progress indicators (the headline replacement). Add a code comment near both sets documenting this exclusion to prevent accidental future inclusion.

**Frontend SSE registration:** `useSSE.ts` maintains a hardcoded `eventTypes` allowlist for `EventSource` registration. `"step_started"`, `"step_completed"`, and `"step_title"` must be added to this array. Omitting them causes silent data loss — the `EventSource` ignores unregistered event types without error.

### 7.2 SSE Frame Formats

**step_started:**
```json
{
  "stepId": "step-a1b2c3d4e5f6",
  "stepNumber": 3,
  "jobId": "job-xyz",
  "turnId": "turn-uuid",
  "intent": "Fix input validation on /api/users endpoint",
  "trigger": "operator_message",
  "startedAt": "2026-04-03T10:15:00Z"
}
```

**step_completed:**
```json
{
  "stepId": "step-a1b2c3d4e5f6",
  "status": "completed",
  "toolCount": 7,
  "durationMs": 23400,
  "hasSummary": true,
  "filesRead": ["backend/api/auth.py", "backend/models/user.py"],
  "filesWritten": ["backend/api/auth.py"],
  "startSha": "abc1234",
  "endSha": "def5678"
}
```

**step_title:**
```json
{
  "stepId": "step-a1b2c3d4e5f6",
  "title": "Fixing JWT validation"
}
```

**transcript_update (existing, extended):**
```json
{
  "jobId": "job-xyz",
  "seq": 42,
  "role": "tool_call",
  "content": "...",
  "toolName": "read_file",
  "stepId": "step-a1b2c3d4e5f6",
  "stepNumber": 3
}
```

### 7.3 Progress Derivation

The frontend derives "current action" from existing `transcript_update` events:

```typescript
// In the store, when a transcript_update arrives with stepId:
if (payload.stepId && (payload.role === "tool_running" || payload.role === "tool_call")) {
  const stepId = payload.stepId;
  const toolName = payload.toolName;
  const toolIntent = payload.toolIntent;
  // Update the step's "last seen tool" for progress display
}
```

No separate progress events needed. The frontend already processes `transcript_update` — it just extracts one more field.

### 7.4 Summary Display

The agent's final message is already delivered as `transcript_update` with `role: "agent"`. The frontend already renders it via `AgentMessageBlock`. For the step view, the frontend finds the agent message by matching `stepId` from a pre-indexed map (see §8.1).

### 7.5 Backward Compatibility

All existing events continue unchanged. The only modification is adding optional `stepId` and `stepNumber` fields to `transcript_update` payloads. Clients that don't know about these fields ignore them.

---

## 8. Frontend Structure

### 8.1 Store Changes

```typescript
// Add to store state
steps: Record<string, Step[]>;
transcriptByStep: Record<string, Record<string, TranscriptEntry[]>>;  // jobId → stepId → entries

// Step type
interface Step {
  stepId: string;
  stepNumber: number;
  jobId: string;
  turnId: string | null;
  intent: string;
  title: string | null;
  status: "running" | "completed" | "failed" | "canceled";
  trigger: string;
  toolCount: number;
  durationMs: number | null;
  startedAt: string;
  completedAt: string | null;
  filesRead: string[] | null;
  filesWritten: string[] | null;
  startSha: string | null;
  endSha: string | null;
  artifactCount: number;
}

// Selectors
selectJobSteps(jobId: string): Step[]
selectActiveStep(jobId: string): Step | undefined
selectStepEntries(jobId: string, stepId: string): TranscriptEntry[]
```

The `transcriptByStep` index is maintained incrementally — each incoming `transcript_update` with a `stepId` is appended to the appropriate bucket. This avoids O(n) filtering per `StepContainer` render.

**Entry cap:** The main `transcript` slice caps at 10,000 entries. `transcriptByStep` must apply a parallel cap — when a step's entry array exceeds 500 entries, older entries are shifted out. This prevents unbounded memory growth for steps with high tool-call volume (e.g., a long test-debug cycle). When the main `transcript` slice evicts entries, the corresponding `transcriptByStep` entries must also be pruned.

**What's NOT in the store:** `currentAction`, `progressLines`, `summary`, `confidence`. These are derived at render time from the step's transcript entries, not duplicated.

### 8.2 Event Dispatcher

Three new SSE handlers:

```typescript
case "step_started": {
  const steps = get().steps[payload.jobId] ?? [];
  set({
    steps: {
      ...get().steps,
      [payload.jobId]: [...steps, {
        stepId: payload.stepId,
        stepNumber: payload.stepNumber,
        jobId: payload.jobId,
        turnId: payload.turnId,
        intent: payload.intent,
        title: null,
        status: "running",
        trigger: payload.trigger,
        toolCount: 0,
        durationMs: null,
        startedAt: payload.startedAt,
        completedAt: null,
        filesRead: null,
        filesWritten: null,
        startSha: null,
        endSha: null,
        artifactCount: 0,
      }],
    },
  });
  break;
}

case "step_completed": {
  const steps = get().steps[payload.jobId] ?? [];
  set({
    steps: {
      ...get().steps,
      [payload.jobId]: steps.map(s =>
        s.stepId === payload.stepId
          ? {
              ...s,
              status: payload.status,
              toolCount: payload.toolCount,
              durationMs: payload.durationMs,
              completedAt: new Date().toISOString(),
              filesRead: payload.filesRead ?? null,
              filesWritten: payload.filesWritten ?? null,
              startSha: payload.startSha ?? null,
              endSha: payload.endSha ?? null,
            }
          : s
      ),
    },
  });
  break;
}

case "step_title": {
  const steps = get().steps[payload.jobId] ?? [];
  set({
    steps: {
      ...get().steps,
      [payload.jobId]: steps.map(s =>
        s.stepId === payload.stepId
          ? { ...s, title: payload.title }
          : s
      ),
    },
  });
  break;
}
```

**`session_resumed` reset:** The existing `session_resumed` SSE handler resets `timelines` and `plans` for the job. It must also reset `steps` and `transcriptByStep` to prevent stale steps from a previous session appearing above the resume divider:

```typescript
case "session_resumed": {
  // ... existing reset logic for timelines and plans ...
  // Also reset step state:
  const { [payload.jobId]: _s, ...restSteps } = get().steps;
  const { [payload.jobId]: _t, ...restByStep } = get().transcriptByStep;
  set({ steps: restSteps, transcriptByStep: restByStep });
  break;
}
```

**Step hydration on mount and reconnect:** The frontend must fetch step data via REST when opening a job or reconnecting after SSE drops. Add to `hydrateJob()` alongside existing `fetchJobTranscript`, `fetchJobTimeline`, etc.:

```typescript
// In hydrateJob(jobId):
const [transcript, steps, ...rest] = await Promise.all([
  fetchJobTranscript(jobId),
  fetchJobSteps(jobId),   // GET /api/jobs/{jobId}/steps
  // ... existing fetches ...
]);

// Rebuild transcriptByStep index from hydrated transcript
const byStep: Record<string, TranscriptEntry[]> = {};
for (const entry of transcript) {
  if (entry.stepId) {
    (byStep[entry.stepId] ??= []).push(entry);
  }
}
set({
  steps: { ...get().steps, [jobId]: steps },
  transcriptByStep: { ...get().transcriptByStep, [jobId]: byStep },
});
```

Without step hydration, the step view is empty after any SSE reconnect — only steps created after reconnection would appear, missing all completed steps from before the disconnect.

The existing `transcript_update` handler is extended to index entries by step:

```typescript
case "transcript_update": {
  // ... existing logic ...
  const entry: TranscriptEntry = {
    // ... existing fields ...
    stepId: payload.stepId as string | undefined,
    stepNumber: payload.stepNumber as number | undefined,
  };

  // Index by step for O(1) lookup
  if (payload.stepId) {
    const jobIndex = get().transcriptByStep[payload.jobId] ?? {};
    const stepEntries = jobIndex[payload.stepId] ?? [];
    set({
      transcriptByStep: {
        ...get().transcriptByStep,
        [payload.jobId]: {
          ...jobIndex,
          [payload.stepId]: [...stepEntries, entry],
        },
      },
    });
  }
  // ... rest unchanged ...
}
```

### 8.3 Component Architecture

```
JobDetailScreen
├── StepListView (default view in "live" tab)
│   ├── StepSearchBar
│   │   └── Filter chips (Errors, Tool calls, Agent messages, Approvals)
│   ├── ResumeBanner (if returning to job with new events)
│   ├── StepContainer (per step)
│   │   ├── StepHeader
│   │   │   ├── Status icon (spinner / check / error)
│   │   │   ├── Title or intent
│   │   │   ├── Duration + tool count
│   │   │   ├── FilesTouchedChips
│   │   │   └── Expand chevron (desktop) / tap-to-sheet (mobile)
│   │   │
│   │   ├── [running] CurrentToolIndicator
│   │   │   └── derived from latest tool_running in step's entry index
│   │   │
│   │   ├── [completed] AgentSummary
│   │   │   └── agent message filtered from step entries, truncated
│   │   │
│   │   └── [expanded] ToolCallList + ReasoningBlock
│   │       └── existing components, filtered by stepId
│   │
│   ├── StreamingDelta (for active step)
│   ├── "Jump to" quick actions (current step / last error)
│   └── OperatorInput (existing chat input)
│
├── RawTranscriptTab (existing TranscriptPanel — power user view)
├── ExecutionTimeline (derives from steps)
├── PlanPanel (unchanged — still from native todo)
└── DiffViewer, LogsPanel, etc. (unchanged)
```

### 8.4 StepContainer Component

```tsx
function StepContainer({ step, isActive }: { step: Step; isActive: boolean }) {
  const isMobile = useIsMobile();
  const [expanded, setExpanded] = useState(false);
  const [sheetOpen, setSheetOpen] = useState(false);

  // O(1) lookup from pre-indexed store
  const stepEntries = useStore(s =>
    s.transcriptByStep[step.jobId]?.[step.stepId] ?? EMPTY_ARRAY
  );

  const currentTool = useMemo(() => {
    if (step.status !== "running") return null;
    const tools = stepEntries.filter(e => e.role === "tool_running");
    return tools.length > 0 ? tools[tools.length - 1] : null;
  }, [stepEntries, step.status]);

  const agentMessage = useMemo(() => {
    const msgs = stepEntries.filter(e => e.role === "agent");
    return msgs.length > 0 ? msgs[msgs.length - 1] : null;
  }, [stepEntries]);

  const toolCalls = useMemo(
    () => stepEntries.filter(e => e.role === "tool_call"),
    [stepEntries]
  );

  const handleToggle = () => {
    if (isMobile) {
      setSheetOpen(true);
    } else {
      setExpanded(!expanded);
    }
  };

  return (
    <>
      <div className={cn(
        "border-l-2 pl-4 py-3 transition-colors",
        isMobile && "min-h-[44px]",
        isActive ? "border-blue-500" : step.status === "completed" ? "border-emerald-500/30" : "border-border",
      )}>
        <StepHeader
          step={step}
          expanded={expanded}
          onToggle={handleToggle}
          hideChevron={isMobile}
        />

        {isActive && currentTool && (
          <div className="mt-1 flex items-center gap-2 text-sm text-muted-foreground">
            <span className="inline-block w-1.5 h-1.5 rounded-full bg-blue-500 animate-pulse" />
            <span className="truncate">
              {currentTool.toolIntent || currentTool.toolDisplay || currentTool.toolName}
            </span>
          </div>
        )}

        {isActive && !currentTool && (
          <StreamingDelta jobId={step.jobId} turnId={step.turnId} />
        )}

        {agentMessage && step.status === "completed" && (
          <div className={cn(
            "mt-2 text-sm text-foreground/90 leading-relaxed",
            isMobile ? "line-clamp-2" : "line-clamp-3",
          )}>
            <AgentMessageBlock content={agentMessage.content} compact />
          </div>
        )}

        {!isMobile && expanded && (
          <div className="mt-3 space-y-2 border-t pt-3">
            <ToolStepList entries={toolCalls} />
            {stepEntries.some(e => e.role === "reasoning") && (
              <ReasoningBlock entries={stepEntries.filter(e => e.role === "reasoning")} />
            )}
          </div>
        )}
      </div>

      {isMobile && (
        <Sheet open={sheetOpen} onClose={() => setSheetOpen(false)} title={step.title || step.intent}>
          <div className="overscroll-contain">
            {agentMessage && (
              <div className="mb-4 text-sm text-foreground/90 leading-relaxed">
                <AgentMessageBlock content={agentMessage.content} />
              </div>
            )}
            <ToolStepList entries={toolCalls} />
            {stepEntries.some(e => e.role === "reasoning") && (
              <ReasoningBlock entries={stepEntries.filter(e => e.role === "reasoning")} />
            )}
          </div>
        </Sheet>
      )}
    </>
  );
}

const EMPTY_ARRAY: TranscriptEntry[] = [];
```

### 8.5 StepHeader Component

```tsx
function StepHeader({ step, expanded, onToggle, hideChevron }: {
  step: Step;
  expanded: boolean;
  onToggle: () => void;
  hideChevron?: boolean;
}) {
  const displayTitle = step.title || step.intent;

  return (
    <div className="flex items-center gap-2 cursor-pointer group" onClick={onToggle}>
      {step.status === "running" ? (
        <Loader2 className="h-4 w-4 shrink-0 animate-spin text-blue-500" />
      ) : step.status === "completed" ? (
        <CheckCircle className="h-4 w-4 shrink-0 text-emerald-500" />
      ) : (
        <XCircle className="h-4 w-4 shrink-0 text-destructive" />
      )}

      <span className="text-sm font-medium truncate flex-1">{displayTitle}</span>

      <FilesTouchedChips step={step} />

      <span className="flex items-center gap-2 shrink-0 text-xs text-muted-foreground">
        {step.toolCount > 0 && <span>{step.toolCount} tools</span>}
        {step.durationMs != null && <span>{formatDuration(step.durationMs)}</span>}
      </span>

      {!hideChevron && (
        <ChevronRight className={cn(
          "h-4 w-4 shrink-0 text-muted-foreground transition-transform",
          expanded && "rotate-90"
        )} />
      )}
    </div>
  );
}
```

### 8.6 StreamingDelta Integration

The existing `streamingMessages` store slice accumulates `agent_delta` tokens keyed by `{jobId}:{turnId}`. The step view reuses this directly:

```tsx
function StreamingDelta({ jobId, turnId }: { jobId: string; turnId: string | null }) {
  const key = turnId ? `${jobId}:${turnId}` : `${jobId}:__default__`;
  const text = useStore(s => s.streamingMessages[key]);

  if (!text) return null;
  return (
    <div className="mt-2 text-sm text-foreground/90 leading-relaxed">
      <span>{text}</span>
      <span className="inline-block w-0.5 h-4 bg-foreground/50 animate-pulse ml-0.5" />
    </div>
  );
}
```

No new streaming infrastructure needed.

### 8.7 Timeline Derivation

```typescript
function selectStepTimeline(jobId: string): TimelineEntry[] {
  const steps = useStore(s => s.steps[jobId] ?? []);

  return steps.map(step => ({
    headline: step.title || step.intent,
    headlinePast: step.title || step.intent,
    summary: "",
    timestamp: step.startedAt,
    active: step.status === "running",
  }));
}
```

This replaces the `_headline_loop` entirely. Step titles ARE the timeline milestones.

### 8.8 Plan Panel — Unchanged

The `PlanPanel` continues to derive from native `manage_todo_list` data via the existing `plans` store slice. The agent's todo tool is the best source for forward-looking plans — steps only show what has happened. The existing `_plan_loop` remains as a fallback when the agent doesn't use native todo tools.

---

## 9. End-to-End Flow

### Scenario: User asks "Fix the authentication bug in /api/login"

```
TIME  EVENT                                   UI UPDATE
────  ─────                                   ─────────

0s    Operator message received               User bubble appears in step list
      role: "operator"

      StepTracker: operator → new step
      → StepStarted {
          step_id: "step-a1b2c3d4e5f6",
          step_number: 1,
          turn_id: null,
          intent: "Fix the authentication bug in /api/login",
          trigger: "operator_message"
        }

      SSE: step_started                       Step #1 appears:
                                               ⟳ Fix the authentication bug in /api/login

1s    transcript_update: role=agent_delta      Streaming delta text appears below step header
      content="I'll look into..."
      turn_id="turn-abc"
                                               ⟳ Fix the authentication bug in /api/login
      StepTracker: new turn_id "turn-abc"        I'll look into the auth...▌
      → step inherits this turnId

3s    transcript_update: role=tool_running     Delta clears, tool indicator appears
      toolName=read_file
      toolIntent="Reading auth handler"         ⟳ Fix the authentication bug in /api/login
      stepId="step-a1b2c3d4e5f6"                 ● Reading auth handler

5s    transcript_update: role=tool_running     New tool starts
      toolName=grep_search
      stepId="step-a1b2c3d4e5f6"                ⟳ Fix the authentication bug...
                                                  ● Searching code

8s    transcript_update: role=tool_call        Edit tool
      toolName=replace_string_in_file
      stepId="step-a1b2c3d4e5f6"                ⟳ Fix the authentication bug...
                                                  ● Editing file

12s   transcript_update: role=tool_running     Test execution
      toolName=run_in_terminal
      toolIntent="Running pytest"
      stepId="step-a1b2c3d4e5f6"                ⟳ Fix the authentication bug...
                                                  ● Running pytest

18s   transcript_update: role=agent            Agent's own message arrives — this IS the summary
      content="I've fixed the JWT validation
       in /api/login — the token expiry check
       was comparing UTC against local
       timestamps. Added normalization
       and a regression test."
      turn_id="turn-abc"
      stepId="step-a1b2c3d4e5f6"

20s   Job enters review state                  Step completes:
      StepTracker: terminal → close step
      → StepCompleted {                          ✓ Fix the authentication bug in /api/login
          step_id: "step-a1b2c3d4e5f6",            4 tools · 18s
          status: "completed",                    I've fixed the JWT validation in /api/login —
          tool_count: 4,                          the token expiry check was comparing
          duration_ms: 18000,                     timestamps in different timezones...
          has_summary: true
        }

20.5s StepTitleGenerator: triggered             Title arrives async:
      → StepTitleGenerated {
          step_id: "step-a1b2c3d4e5f6",          ✓ Fixing JWT timezone validation
          title: "Fixing JWT timezone                4 tools · 18s
            validation"                           I've fixed the JWT validation in /api/login —
        }                                         the token expiry check was comparing
                                                  timestamps in different timezones...
```

### Collapsed View (Default):

```
✓  Fixing JWT timezone validation                    18s · 4 tools  ▸
   ✏️ auth.py
   I've fixed the JWT validation in /api/login — the token expiry
   check was comparing timestamps in different timezones. Added
   UTC normalization and a pytest regression test.
```

### Expanded View (click ▸):

```
✓  Fixing JWT timezone validation                    18s · 4 tools  ▾
   ✏️ auth.py  👁 user.py
   I've fixed the JWT validation in /api/login — the token expiry
   check was comparing timestamps in different timezones. Added
   UTC normalization and a pytest regression test.

   ───────────────────────────────────────────────
    📖 read_file  backend/api/auth.py  L1-120        1.2s
    🔍 grep_search  "JWT"  → 3 matches               0.8s
    ✏️  replace_string_in_file  backend/api/auth.py   0.5s
    ▶  run_in_terminal  pytest tests/test_auth.py     6.1s  ✓
   ───────────────────────────────────────────────
    View changes in this step (1 file)
```

---

## 10. Search, Filters, and Navigation

Long transcripts are navigation problems, not reading problems. Step grouping improves scannability but doesn't provide search, filtering, or deep linking.

### 10.1 Backend — Transcript Search

```python
async def search_transcript(
    self, job_id: str, query: str,
    roles: list[str] | None = None,
    step_id: str | None = None,
    limit: int = 50,
) -> list[EventRow]:
    """Search transcript events by content text match."""
    async with self._session_factory() as session:
        stmt = (
            select(EventRow)
            .where(
                EventRow.job_id == job_id,
                EventRow.kind == DomainEventKind.transcript_updated,
            )
        )
        if roles:
            stmt = stmt.where(EventRow.payload["role"].as_string().in_(roles))
        if step_id:
            stmt = stmt.where(EventRow.payload["step_id"].as_string() == step_id)

        like_pattern = f"%{query}%"
        stmt = stmt.where(
            or_(
                EventRow.payload["content"].as_string().ilike(like_pattern),
                EventRow.payload["tool_name"].as_string().ilike(like_pattern),
                EventRow.payload["tool_intent"].as_string().ilike(like_pattern),
            )
        )
        stmt = stmt.order_by(EventRow.sequence_no).limit(limit)
        result = await session.execute(stmt)
        return list(result.scalars().all())
```

SQLite `LIKE` is adequate — search scope is a single job's events (typically 50–200 rows).

### 10.2 Frontend — Search Bar and Filter Chips

```tsx
function StepSearchBar({ jobId }: { jobId: string }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<TranscriptSearchResult[]>([]);
  const debouncedQuery = useDebounce(query, 300);

  useEffect(() => {
    if (!debouncedQuery || debouncedQuery.length < 2) {
      setResults([]);
      return;
    }
    searchTranscript(jobId, debouncedQuery).then(setResults);
  }, [jobId, debouncedQuery]);

  return (
    <div className="relative">
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border">
        <Search size={14} className="text-muted-foreground shrink-0" />
        <input
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder="Search transcript…"
          className="flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground/60"
        />
        {query && (
          <button onClick={() => setQuery("")} className="text-muted-foreground">
            <X size={14} />
          </button>
        )}
      </div>
      {results.length > 0 && (
        <SearchResultsList results={results} onSelect={handleScrollToEntry} />
      )}
    </div>
  );
}
```

Filter chips operate client-side on the already-loaded transcript array:

```tsx
const FILTER_CHIPS = [
  { label: "Errors", roles: ["tool_call"], filter: (e: TranscriptEntry) => e.toolSuccess === false },
  { label: "Tool calls", roles: ["tool_call"] },
  { label: "Agent messages", roles: ["agent"] },
  { label: "Approvals", roles: ["approval_requested"] },
] as const;
```

Active filters highlight matching steps and dim non-matching ones.

### 10.3 Deep Links

URL format: `/jobs/{jobId}?step={stepId}` or `/jobs/{jobId}?event={seq}`

On mount, `JobDetailScreen` reads query params and:
1. Scrolls `StepListView` to the target step
2. Auto-expands the target step
3. If `event={seq}`, highlights the specific entry within the expanded step

### 10.4 Quick Actions

```tsx
// Sticky bottom bar on StepListView when job is active
<div className="sticky bottom-0 flex gap-2 p-2 bg-card/95 backdrop-blur border-t">
  <button onClick={scrollToActiveStep}>Jump to current step</button>
  <button onClick={scrollToLastError}>Jump to last error</button>
</div>
```

---

## 11. Mobile Adaptation

CodePlane has comprehensive mobile support: `useIsMobile` hook (768px breakpoint), `MobileJobList`, separate desktop/mobile tab bars in `JobDetailScreen`, `Sheet` component (bottom sheets), `MobileSyntaxView`, 44px+ tap targets. The step system integrates with this infrastructure.

### 11.1 Layout Rules

| Element | Desktop | Mobile (<768px) |
|---------|---------|-----------------|
| **Step header** | Inline expand/collapse | Tap opens bottom sheet |
| **Agent summary** | `line-clamp-3` inline | `line-clamp-2` inline; full text in sheet |
| **Tool list** | Inline below step | In bottom sheet only |
| **Expand chevron** | Rotates, details inline | Hidden; entire row tappable to sheet |
| **Search bar** | Above step list | Collapsed behind search icon; expands inline |
| **Filter chips** | Horizontal row | Horizontally scrollable with overflow fade |
| **Step header tap target** | Natural size | `min-h-[44px]` enforced per WCAG guidance |

### 11.2 Mobile Search

On mobile, the search bar is collapsed behind a search icon in the step list header. Tapping it expands the input inline. Results appear inline — tapping a result scrolls to the step and opens its sheet.

### 11.3 Mobile "Jump to"

The sticky bottom bar becomes a floating pill:

```tsx
{isMobile && isJobRunning && (
  <button
    onClick={scrollToActiveStep}
    className="fixed bottom-20 left-1/2 -translate-x-1/2 z-40 px-4 py-2 rounded-full
               bg-primary text-primary-foreground text-sm font-medium shadow-lg
               min-h-[44px]"
  >
    Jump to current step ↓
  </button>
)}
```

### 11.4 Sheet Gesture Handling

The mobile step detail uses `Sheet` with scrollable content. To prevent conflicts between "scroll within sheet" and "drag to dismiss" gestures, the inner scroll container uses `overscroll-behavior: contain`.

---

## 12. Context Provenance

The data for "what files did the agent read/write in this step?" already exists in `tool_call` events. The step system extracts and surfaces it.

### 12.1 File Path Extraction

`StepTracker` tracks files as transcript events arrive:

```python
_READ_TOOLS = frozenset({"read_file", "grep_search", "file_search", "semantic_search", "view_image"})
_WRITE_TOOLS = frozenset({"replace_string_in_file", "create_file", "multi_replace_string_in_file", "create_directory"})

# In on_transcript_event, for tool_call events:
path = _extract_file_path(tool_name, tool_args)
if path:
    if tool_name in _READ_TOOLS and path not in current.files_read:
        current.files_read.append(path)
    elif tool_name in _WRITE_TOOLS and path not in current.files_written:
        current.files_written.append(path)
```

### 12.2 Display

```tsx
function FilesTouchedChips({ step }: { step: Step }) {
  if (!step.filesRead?.length && !step.filesWritten?.length) return null;

  return (
    <div className="flex flex-wrap gap-1 mt-1.5">
      {step.filesWritten?.map(f => (
        <span key={f} className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs bg-emerald-500/10 text-emerald-600">
          <Pencil size={10} />{basename(f)}
        </span>
      ))}
      {step.filesRead?.slice(0, 4).map(f => (
        <span key={f} className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs bg-muted text-muted-foreground">
          <Eye size={10} />{basename(f)}
        </span>
      ))}
      {(step.filesRead?.length ?? 0) > 4 && (
        <span className="text-xs text-muted-foreground">+{step.filesRead!.length - 4} more</span>
      )}
    </div>
  );
}
```

Written files use emerald accent, read files use neutral. Shown in both collapsed and expanded step views.

---

## 13. Step-Level Checkpoints and Diff

CodePlane isolates each job in a Git worktree (`GitService.create_worktree`). The step system records the Git `HEAD` SHA at step boundaries, enabling per-step diff and restore.

### 13.1 SHA Capture

`StepTracker` calls `GitService.get_current_sha(job_id)` (async subprocess — does not block the event loop) at step open and step close. The diff between the two SHAs IS the step's changes.

### 13.2 Step Diff

In the expanded step view:

```tsx
{step.startSha && step.endSha && step.startSha !== step.endSha && (
  <button
    onClick={() => openStepDiff(step)}
    className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground mt-2"
  >
    <GitBranch size={12} />
    View changes in this step
  </button>
)}
```

Opens the existing `DiffViewer` component scoped to the step's SHA range. No new diff viewer needed.

### 13.3 Restore

Restore is destructive. The UI offers it behind a confirm dialog:

```tsx
<ConfirmDialog
  title="Restore to before this step?"
  description={`This will reset the worktree to commit ${step.startSha?.slice(0, 8)}. Changes from this step and all later steps will be undone. The job transcript is preserved.`}
  onConfirm={() => restoreToStep(jobId, step.startSha!)}
/>
```

Backend: `POST /api/jobs/{job_id}/restore` uses `git reset --hard <sha>` (not `git checkout`, which would create a detached HEAD and break subsequent agent session pushes). Blocked while the agent is actively running (returns 409). Job state and transcript are preserved — this is a worktree-level action only.

---

## 14. Artifact Linking

CodePlane's `ArtifactRepository` stores artifacts per job but with no step association.

### 14.1 Schema Change

```python
# Add to ArtifactRow:
step_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
```

Alembic migration: `ALTER TABLE artifacts ADD COLUMN step_id VARCHAR(36)`.

### 14.2 Wiring

When `RuntimeService` creates an artifact, the current step is available from `StepTracker`:

```python
current = self._step_tracker.current_step(job_id)
step_id = current.step_id if current else None
# Pass step_id to artifact_repo.create(...)
```

### 14.3 Display

```tsx
{step.artifactCount > 0 && (
  <div className="flex items-center gap-1.5 mt-1.5 text-xs text-muted-foreground">
    <Paperclip size={12} />
    <span>{step.artifactCount} artifact{step.artifactCount > 1 ? "s" : ""}</span>
  </div>
)}
```

Clicking opens the existing artifacts tab filtered to `stepId`.

---

## 15. Resumability

Users returning to a job (especially via Dev Tunnels from a phone) need a fast "what changed" signal.

### 15.1 Tracking Last Seen

```typescript
// In viewStateStore, persisted via localStorage:
lastSeenSeq: Record<string, number>;  // jobId → last seen transcript sequence number
```

Updated on every `transcript_update` processed while the job detail tab is visible.

### 15.2 Resume Banner

```tsx
function ResumeBanner({ jobId }: { jobId: string }) {
  const lastSeen = useStore(s => s.lastSeenSeq[jobId]);
  const transcript = useStore(selectJobTranscript(jobId));
  const steps = useStore(s => s.steps[jobId] ?? []);

  if (!lastSeen) return null;

  const newEntries = transcript.filter(e => (e.seq ?? 0) > lastSeen);
  if (newEntries.length === 0) return null;

  const newStepCount = new Set(newEntries.map(e => e.stepId).filter(Boolean)).size;
  const hasErrors = newEntries.some(e => e.toolSuccess === false);

  return (
    <div className="flex items-center justify-between px-4 py-2 mb-3 rounded-lg bg-accent/50 border border-border text-sm">
      <div className="flex items-center gap-2">
        <ArrowDown size={14} />
        <span>
          {newStepCount > 0 ? `${newStepCount} new step${newStepCount > 1 ? "s" : ""}` : `${newEntries.length} new events`}
          {hasErrors && <span className="text-destructive ml-1">· errors detected</span>}
        </span>
      </div>
      <button onClick={scrollToFirstNew} className="text-xs font-medium text-primary hover:underline">
        Jump to changes
      </button>
    </div>
  );
}
```

No LLM call — just count new steps and scan for errors.

### 15.3 Unread Indicator

In `JobCard` and `MobileJobList`, a dot when `lastSeenSeq` is behind the latest sequence number.

---

## 16. View State Persistence

### 16.1 What to Persist

| Key | Value | Purpose |
|-----|-------|---------|
| `lastSeenSeq:{jobId}` | `number` | Resume banner |
| `stepViewMode` | `"steps" \| "raw"` | Transcript tab preference |
| `collapsedSteps:{jobId}` | `string[]` | Manual expand/collapse state |

### 16.2 Implementation

A small separate Zustand store with `persist` middleware:

```typescript
import { persist } from "zustand/middleware";

const useViewStateStore = create(
  persist(
    (set, get) => ({
      lastSeenSeq: {} as Record<string, number>,
      stepViewMode: "steps" as "steps" | "raw",
      collapsedSteps: {} as Record<string, string[]>,
    }),
    { name: "codeplane-view-state" }
  )
);
```

Separate from the main store to avoid serializing the full transcript to localStorage on every update.

### 16.3 What NOT to Persist

- Transcript entries (fetched via SSE replay on reconnect)
- Step data (hydrated via `GET /api/jobs/{job_id}/steps`)
- Streaming messages (ephemeral)
- Search query and filter state (ephemeral)

### 16.4 Storage Budget

Each job's view state is ~100 bytes. At 500 jobs: ~50KB — well within localStorage limits. Cross-device sync is not needed for launch; if required later, a simple JSON blob endpoint on the backend would suffice.

---

## 17. Migration Strategy

### Phase 1: Backend Foundation (Non-Breaking)

Steps are generated and persisted with all metadata. No UI changes.

1. Alembic migration → create `steps` table (including `start_sha`, `end_sha`, `files_read`, `files_written`)
2. Alembic migration → add `step_id` column to `artifacts` table
3. Add `step_started`, `step_completed`, `step_title_generated` to `DomainEventKind`
4. Implement `StepTracker` in `RuntimeService`
5. Implement `StepPersistenceSubscriber` → wire to EventBus
6. Tag `TranscriptUpdated` events with `step_id`, `step_number`
7. Wire StepTracker into `_run_followup_turn` (verify/self-review event loop)
8. Wire StepTracker into `send_message` (operator message path)
9. Wire artifact creation to include `step_id`
10. Add SSE mappings for 3 new event types + extend `transcript_update` field map with `step_id`/`step_number`
11. Add `GET /api/jobs/{job_id}/steps`
12. Add `GET /api/jobs/{job_id}/steps/{step_id}/diff`
13. Add `GET /api/jobs/{job_id}/transcript/search`
14. Add `POST /api/jobs/{job_id}/restore` (state-guarded, uses `git reset --hard`)
15. Implement `StepTitleGenerator`

**Validation:** Run a job, verify steps are persisted with correct boundaries, SHA captures, file lists, and artifact links. Existing UI unchanged — new payload fields are ignored by the frontend.

### Phase 2: Frontend Step View (Additive)

Users can opt into the step view.

1. Add `steps` and `transcriptByStep` slices to Zustand store
2. Add `viewStateStore` with `persist` middleware
3. Add 3 SSE event handlers + extend `transcript_update` handler
4. Register `"step_started"`, `"step_completed"`, `"step_title"` in `useSSE.ts` `eventTypes` array
5. Extend `TranscriptEntry` type with optional `stepId`, `stepNumber`
6. Build `StepListView` as a new view option in `JobDetailScreen`
7. Add step hydration `fetchJobSteps(jobId)` to `hydrateJob` and `JobDetailScreen` mount
8. Add `session_resumed` handler reset for `steps` and `transcriptByStep` slices
9. Implement `StepContainer` (responsive), `StepHeader`, `FilesTouchedChips`
10. Implement `StepSearchBar` + filter chips
11. Implement `ResumeBanner` and unread indicator in `JobCard`
12. Add step diff button → opens existing `DiffViewer`
13. Add deep link support (`?step=`, `?event=`)

**Validation:** Both "Steps" and raw transcript views work. Steps view handles desktop and mobile. Resume banner works on return visits. Deep links resolve.

### Phase 3: Unification

Steps become the default view.

1. Make step view the default in the "live" tab
2. Move raw transcript to a secondary tab
3. Derive `ExecutionTimeline` from step titles
4. Remove `_headline_loop` from `ProgressTrackingService`
5. Clean up dead headline state (`_headline_transcript`, `_headline_tool_intents`, `_headline_last_snapshot`, `_headline_history`, `_headline_tasks`) from `start_tracking`, `stop_tracking`, `cleanup`
6. Add `latest_step_title` field to `JobResponse` (populated from `StepRepository`) to replace `progress_headline` on Kanban card previews
7. Keep `_plan_loop` and `feed_native_plan` unchanged (`feed_transcript` still needed for plan extraction)
8. Add "Jump to current step" and "Jump to last error" quick actions
9. Add step restore button (behind confirm dialog)

**Validation:** Full end-to-end on desktop and mobile. Timeline shows step titles. No regressions in raw transcript view.

### Out of Scope

| Feature | Rationale |
|---------|-----------|
| **Branching / fork-from-here** | Requires a lineage model beyond `parent_job_id`. The step list is flat; branching needs a tree structure. |
| **Step-granularity cancel** | Requires SDK-level interrupt hooks that neither adapter supports. Job-level cancel remains the mechanism. |
| **MCP tool registry UI** | Informational display, not part of the transcript system. Can be added independently. |
| **Virtualized step list** | Typical sessions produce 5–15 steps. If step counts grow past ~50, `@tanstack/react-virtual` can be applied using the same pattern as `TranscriptPanel`. |

Three phases, each independently shippable. Phase 1 is invisible to users. Phase 2 is opt-in. Phase 3 changes the default.

---

## 18. Cost Analysis

| System | Cheap model calls / 10min session | Tokens / session | Notes |
|--------|-----------------------------------|-------------------|-------|
| **Current** | ~70 (headline 15s + plan 20s loops) | ~30k–50k | Timer-driven, many wasted calls during idle |
| **This design** | ~5–8 (title per step, with shortcuts) | ~700–1,100 | Title calls are ~100 tokens each |

~40x cheaper because:
- Titles are ~10 tokens out (vs headline/plan calls at ~150–300 tokens)
- Many steps skip the LLM call entirely (short intent reused as title)
- The agent's own message replaces summary generation entirely

### What Costs Nothing

| Component | Why |
|-----------|-----|
| StepTracker | Pure state machine. O(1) per event. |
| Transcript tagging | Adding two fields to an existing dict. |
| Step SSE events | 2 events per step — negligible vs existing event volume. |
| Frontend rendering | Derived from existing transcript data. |
| Summary display | Agent's own message — already in the transcript. |
| Transcript search | SQLite `LIKE` on ~200 rows. Sub-millisecond. |
| File tracking | String accumulation — no I/O, no LLM. |
| SHA capture | One async `git rev-parse HEAD` per step boundary (~1ms). |
| Step diff | `git diff a..b` on demand only. |
| Resume banner | Client-side integer comparison. |
| View state | ~50KB localStorage writes. |
| Artifact linking | One extra column at artifact creation time. |

---

## 19. Design Decisions

### Step granularity → Turn-level

One step per SDK turn. The `turnId` determines granularity automatically. Typical: 5–15 steps per session — neither too coarse nor too fine because the SDK turn IS the natural unit of agent work.

### Agent-declared steps → Not needed

With turnId as the boundary signal, explicit `declare_step` tools are unnecessary. The existing `report_intent` tool populates the step's intent field when the agent uses it; otherwise the first `tool_intent` or operator message text is used.

### Sub-agent visibility → Flat

Sub-agent invocations appear as tool calls within the parent step. The existing `SubAgentSection` in `TranscriptPanel` handles nested rendering in the expanded view.

### Summary source → Agent's own message

No cheap model summaries. The agent writes its own response. The fallback for absent messages is a template string, not an LLM call.

### Streaming → Existing delta mechanism

The `agent_delta` → `streamingMessages` mechanism provides typing animation. The step view plugs in via `turnId` matching.

### Backward compatibility → Optional stepId field

Transcript events carry optional `stepId`. Old frontends ignore it. Steps slice is empty for pre-migration jobs → frontend falls back to raw `TranscriptPanel`.

### Search → Client-side with backend fallback

For loaded jobs, search filters the in-memory array. The backend endpoint exists for large transcripts or future cross-job search.

### Mobile → Bottom sheets

On `<768px`, step detail opens in `Sheet` — consistent with CodePlane's established mobile patterns.

### Checkpoints → SHA-based

Git SHAs at step boundaries, not full snapshots. Diff on demand, restore via `git checkout`. Aligned with existing `GitService`.

### View state → localStorage only

Lightweight, device-local. Cross-device sync (backend JSON blob endpoint) is a future enhancement if needed.

---

## Appendix A: File Change Manifest

### New Files

| File | Purpose |
|------|---------|
| `backend/services/step_tracker.py` | TurnId state machine + file tracking + SHA capture |
| `backend/services/step_title_generator.py` | Event-driven title generation |
| `backend/services/step_persistence.py` | EventBus subscriber → StepRepository |
| `backend/persistence/step_repo.py` | Step table persistence |
| `alembic/versions/0015_add_steps.py` | Migration: `steps` table + `artifacts.step_id` |
| `frontend/src/components/StepListView.tsx` | Step-based transcript view |
| `frontend/src/components/StepContainer.tsx` | Individual step with responsive variants |
| `frontend/src/components/StepHeader.tsx` | Step header with status, title, duration, files |
| `frontend/src/components/StepSearchBar.tsx` | In-transcript search with filter chips |
| `frontend/src/components/ResumeBanner.tsx` | "What changed since last visit" banner |
| `frontend/src/components/FilesTouchedChips.tsx` | Read/written file indicators |
| `frontend/src/store/viewStateStore.ts` | Persisted view state with `zustand/persist` |

### Modified Files

| File | Change |
|------|--------|
| `backend/models/events.py` | 3 event kinds + 3 payload TypedDicts; extend `TranscriptPayloadDict` |
| `backend/models/db.py` | `StepRow`; `step_id` column on `ArtifactRow` |
| `backend/models/api_schemas.py` | `StepPayload`, `StepTitlePayload`, `StepDiffPayload`, `TranscriptSearchResult` |
| `backend/services/sse_manager.py` | 3 SSE event type mappings + 3 payload registry entries + `step_id`/`step_number` in `transcript_update` field map |
| `backend/services/runtime_service.py` | Wire `StepTracker` into main loop, `_run_followup_turn`, and `send_message`; tag transcript events; pass step_id to artifacts |
| `backend/persistence/event_repo.py` | `search_transcript()` method |
| `backend/di.py` | Construct `StepTracker`, `StepTitleGenerator`, `StepPersistenceSubscriber`, `StepRepository` |
| `backend/api/jobs.py` | 4 new endpoints (steps, step diff, transcript search, restore) |
| `frontend/src/store/index.ts` | `steps` + `transcriptByStep` slices; 3 SSE handlers; `session_resumed` step reset; `hydrateJob` step fetch; extend `TranscriptEntry` |
| `frontend/src/api/types.ts` | `Step` type; extend `TranscriptEntry`; add response types |
| `frontend/src/components/JobDetailScreen.tsx` | `StepListView` in live tab; step hydration; deep links; resume banner |
| `frontend/src/components/JobCard.tsx` | Unread indicator dot |
| `frontend/src/hooks/useSSE.ts` | Add `"step_started"`, `"step_completed"`, `"step_title"` to `eventTypes` allowlist |

12 new files, 13 modified files.

### Unchanged Files

| File | Why |
|------|-----|
| `frontend/src/hooks/useIsMobile.ts` | Reused as-is |
| `frontend/src/components/ui/sheet.tsx` | Reused as-is for mobile sheets |
| `frontend/src/components/ExecutionTimeline.tsx` | Phase 3 changes data source, not component structure |
| `frontend/src/components/PlanPanel.tsx` | Plan remains derived from native todo data |
| `frontend/src/components/DiffViewer.tsx` | Reused as-is for step diffs |

---

## Appendix B: StepTracker Implementation

```python
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
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from backend.services.event_bus import EventBus
    from backend.services.git_service import GitService

from backend.models.events import DomainEvent, DomainEventKind

log = structlog.get_logger()

_READ_TOOLS = frozenset({"read_file", "grep_search", "file_search", "semantic_search", "view_image"})
_WRITE_TOOLS = frozenset({"replace_string_in_file", "create_file", "multi_replace_string_in_file", "create_directory"})


def _extract_file_path(tool_name: str, tool_args: str) -> str | None:
    """Best-effort extract of workspace-relative file path from tool args."""
    if not tool_args:
        return None
    try:
        args = json.loads(tool_args) if tool_args.startswith("{") else {}
        return args.get("filePath") or args.get("path") or args.get("query")
    except (json.JSONDecodeError, AttributeError):
        return None


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

    def __init__(self, event_bus: EventBus, git_service: GitService | None = None) -> None:
        self._event_bus = event_bus
        self._git_service = git_service
        self._current: dict[str, _StepState] = {}
        self._counters: dict[str, int] = {}
        self._worktree_paths: dict[str, str] = {}  # job_id → worktree cwd

    def register_worktree(self, job_id: str, worktree_path: str) -> None:
        """Set the worktree path for a job. Called from _execute_session_attempt."""
        self._worktree_paths[job_id] = worktree_path

    def current_step(self, job_id: str) -> _StepState | None:
        return self._current.get(job_id)

    async def on_transcript_event(self, job_id: str, event: DomainEvent) -> None:
        """Process a TranscriptUpdated event."""
        payload = event.payload
        role = payload.get("role", "")
        content = payload.get("content", "")
        turn_id = payload.get("turn_id") or ""
        tool_intent = payload.get("tool_intent", "")

        if role == "agent_delta":
            return

        if not turn_id and role not in ("operator", "divider"):
            log.warning(
                "step_tracker_missing_turn_id",
                job_id=job_id,
                role=role,
                event_id=event.event_id,
            )

        current = self._current.get(job_id)

        new_step_trigger: str | None = None
        intent = ""

        if role == "operator":
            new_step_trigger = "operator_message"
            first_line = content.split("\n")[0].strip()
            intent = first_line[:120] if first_line else "Operator request"

        elif current is None:
            new_step_trigger = "job_start"
            intent = tool_intent or content[:120] or "Starting work"

        elif turn_id and turn_id != current.turn_id:
            new_step_trigger = "turn_change"
            intent = tool_intent or ""

        # Idempotency: if turn_id matches current, no new step
        if new_step_trigger:
            if current:
                await self._close(job_id, current, "completed")
            await self._open(job_id, intent, turn_id, new_step_trigger)
            current = self._current[job_id]

        if current:
            if turn_id and not current.turn_id:
                current.turn_id = turn_id
            if role == "tool_call":
                current.tool_count += 1
                tool_name = payload.get("tool_name", "")
                tool_args = payload.get("tool_args", "")
                path = _extract_file_path(tool_name, tool_args)
                if path:
                    if tool_name in _READ_TOOLS and path not in current.files_read:
                        current.files_read.append(path)
                    elif tool_name in _WRITE_TOOLS and path not in current.files_written:
                        current.files_written.append(path)
            if role == "agent":
                current.last_agent_message = content

    async def on_job_terminal(self, job_id: str, outcome: str) -> None:
        """Close current step when job reaches terminal state."""
        current = self._current.get(job_id)
        if not current:
            return  # Already closed or never opened — idempotent
        status = "completed" if outcome in ("review", "completed") else outcome
        await self._close(job_id, current, status)

    async def _open(
        self, job_id: str, intent: str, turn_id: str | None, trigger: str,
    ) -> None:
        n = self._counters.get(job_id, 0) + 1
        self._counters[job_id] = n
        step_id = f"step-{uuid.uuid4().hex[:12]}"

        start_sha: str | None = None
        if self._git_service:
            cwd = self._worktree_paths.get(job_id)
            if cwd:
                try:
                    start_sha = await self._git_service.rev_parse("HEAD", cwd=cwd)
                except Exception:
                    pass  # No worktree yet, or git error — not fatal

        state = _StepState(
            step_id=step_id,
            step_number=n,
            turn_id=turn_id,
            intent=intent,
            trigger=trigger,
            started_at=datetime.now(UTC),
            start_sha=start_sha,
        )
        self._current[job_id] = state
        await self._event_bus.publish(DomainEvent(
            event_id=DomainEvent.make_event_id(),
            job_id=job_id,
            timestamp=state.started_at,
            kind=DomainEventKind.step_started,
            payload={
                "step_id": step_id,
                "step_number": n,
                "turn_id": turn_id,
                "intent": intent,
                "trigger": trigger,
            },
        ))

    async def _close(
        self, job_id: str, state: _StepState, status: str,
    ) -> None:
        if job_id not in self._current:
            return  # Already closed — idempotent

        now = datetime.now(UTC)
        duration_ms = int((now - state.started_at).total_seconds() * 1000)

        end_sha: str | None = None
        if self._git_service:
            cwd = self._worktree_paths.get(job_id)
            if cwd:
                try:
                    end_sha = await self._git_service.rev_parse("HEAD", cwd=cwd)
                except Exception:
                    pass

        await self._event_bus.publish(DomainEvent(
            event_id=DomainEvent.make_event_id(),
            job_id=job_id,
            timestamp=now,
            kind=DomainEventKind.step_completed,
            payload={
                "step_id": state.step_id,
                "status": status,
                "tool_count": state.tool_count,
                "duration_ms": duration_ms,
                "has_summary": state.last_agent_message is not None,
                "files_read": state.files_read[:20],
                "files_written": state.files_written[:20],
                "start_sha": state.start_sha,
                "end_sha": end_sha,
            },
        ))
        self._current.pop(job_id, None)

    def cleanup(self, job_id: str) -> None:
        """Remove all in-memory state for a job."""
        self._current.pop(job_id, None)
        self._counters.pop(job_id, None)
        self._worktree_paths.pop(job_id, None)
```

---

## Appendix C: SSE Event Type Registry

```python
_SSE_EVENT_TYPE: dict[DomainEventKind, str | None] = {
    # Existing
    DomainEventKind.job_created: "job_state_changed",
    DomainEventKind.workspace_prepared: None,
    DomainEventKind.agent_session_started: None,
    DomainEventKind.log_line_emitted: "log_line",
    DomainEventKind.transcript_updated: "transcript_update",
    DomainEventKind.diff_updated: "diff_update",
    DomainEventKind.approval_requested: "approval_requested",
    DomainEventKind.approval_resolved: "approval_resolved",
    DomainEventKind.job_review: "job_review",
    DomainEventKind.job_completed: "job_completed",
    DomainEventKind.job_failed: "job_failed",
    DomainEventKind.job_canceled: "job_state_changed",
    DomainEventKind.job_state_changed: "job_state_changed",
    DomainEventKind.session_heartbeat: "session_heartbeat",
    DomainEventKind.merge_completed: "merge_completed",
    DomainEventKind.merge_conflict: "merge_conflict",
    DomainEventKind.session_resumed: "session_resumed",
    DomainEventKind.job_resolved: "job_resolved",
    DomainEventKind.job_archived: "job_archived",
    DomainEventKind.job_title_updated: "job_title_updated",
    DomainEventKind.progress_headline: "progress_headline",
    DomainEventKind.model_downgraded: "model_downgraded",
    DomainEventKind.tool_group_summary: "tool_group_summary",
    DomainEventKind.agent_plan_updated: "agent_plan_updated",
    DomainEventKind.telemetry_updated: "telemetry_updated",

    # Step system
    DomainEventKind.step_started: "step_started",
    DomainEventKind.step_completed: "step_completed",
    DomainEventKind.step_title_generated: "step_title",
}
```
