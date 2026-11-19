# Backend Architecture Deep Dive

## Research Questions

1. How are agent adapters structured and registered?
2. How do events flow from adapter to domain events to persistence?
3. How is telemetry collected and finalized?
4. What are all domain event types?
5. What is the Job model and its states?
6. How are jobs and events persisted?
7. What are all REST API endpoints?
8. What adapter implementations exist?

---

## 1. Agent Adapter Interface (`backend/services/agent_adapter.py`)

### `AgentAdapterInterface` (ABC)

Abstract base class all SDK adapters must implement.

| Method | Signature | Required |
|--------|-----------|----------|
| `create_session` | `async (config: SessionConfig) -> str` | **abstract** |
| `stream_events` | `async (session_id: str) -> AsyncIterator[SessionEvent]` | **abstract** |
| `send_message` | `async (session_id: str, message: str) -> None` | **abstract** |
| `abort_session` | `async (session_id: str) -> None` | **abstract** |
| `interrupt_session` | `async (session_id: str) -> None` | optional (no-op default) |
| `pause_tools` | `(session_id: str) -> None` | optional (no-op default) |
| `resume_tools` | `(session_id: str) -> None` | optional (no-op default) |
| `complete` | `async (prompt: str) -> CompletionResult` | **abstract** |
| `set_execution_phase` | `(job_id: str, phase: ExecutionPhase) -> None` | optional (no-op default) |

### `CompletionResult` (dataclass, slots=True)

Non-agentic single-turn completion result:

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `text` | `str \| None` | `None` | Response text; None on hard error |
| `input_tokens` | `int` | `0` | Zero = unknown |
| `output_tokens` | `int` | `0` | Zero = unknown |
| `cost_usd` | `float` | `0.0` | |
| `model` | `str` | `""` | |

### SDK-Model Validation

`_SDK_MODEL_PREFIXES` maps SDK to allowed model prefixes:
- `AgentSDK.copilot` → `()` (any model)
- `AgentSDK.claude` → `("claude-",)` (Anthropic models only)

`validate_sdk_model(sdk, model)` raises `SDKModelMismatchError` if incompatible.

### System Prompt

`CODEPLANE_SYSTEM_PROMPT` — appended to all agent sessions. Tells the agent it runs headless, forbids git merge/rebase/reset, and enforces a "Final Message Law" for clean task summaries.

---

## 2. Adapter Registry (`backend/services/adapter_registry.py`)

### `AdapterRegistry`

Factory that creates and caches adapter instances per SDK.

**Constructor:**
```python
__init__(
    approval_service: ApprovalService | None = None,
    event_bus: EventBus | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
)
```

**Key method:** `get_adapter(sdk: AgentSDK | str) -> AgentAdapterInterface`
- Caches adapters in `_adapters: dict[AgentSDK, AgentAdapterInterface]`
- Lazy-imports adapter classes on first use

**Supported SDKs and their adapters:**

| SDK | Adapter Class | Module |
|-----|---------------|--------|
| `AgentSDK.copilot` | `CopilotAdapter` | `backend/services/copilot_adapter/` |
| `AgentSDK.claude` | `ClaudeAdapter` | `backend/services/claude_adapter/` |

Both adapters receive `(approval_service, event_bus, session_factory)` at construction.

---

## 3. Base Adapter (`backend/services/base_adapter.py`)

### `BaseAgentAdapter(AgentAdapterInterface)`

Shared infrastructure all SDK adapters inherit from. Owns:

- **Queue management**: `_queues: dict[str, asyncio.Queue]` per session
- **Session-job mapping**: `_session_to_job: dict[str, str]`
- **Tool pausing**: `_paused_sessions: set[str]`
- **Telemetry**: `_tool_start_times`, `_pending_tool_metadata`, `_current_phases`, `_job_start_times`, `_job_main_models`, `_turn_counters`
- **Retry tracking**: `_retry_trackers: dict[str, RetryTracker]`
- **Transcript ring buffer**: `_transcript_buffers: dict[str, list[dict[str, str]]]` (10 entries per job, ~800 chars each)
- **DB write scheduling**: `_write_tasks` list capped at 20 concurrent

Key methods:
- `_enqueue(session_id, event)` — push SessionEvent to queue + buffer transcript
- `_enqueue_log(session_id, message, level, seq)` — convenience for log events
- `set_job_id(session_id, job_id)` — associate session with job
- `set_execution_phase(job_id, phase)` — update phase for cost tagging
- `_buffer_transcript(session_id, payload)` — ring buffer for motivation context
- `_snapshot_preceding_context(job_id, count=5)` — JSON of last N transcript entries
- `_is_mutative_shell(tool_args_str)` — classmethod checking shell command against known mutative prefixes

Constants:
- `STREAM_EVENT_TIMEOUT_S = 330` (accommodates long LLM generations)
- `COMPLETION_TIMEOUT_S = 180`
- `CLIENT_STOP_TIMEOUT_S = 10`

---

## 4. Adapter Implementations

### `CopilotAdapter` (`backend/services/copilot_adapter/_adapter.py`)

Extends `BaseAgentAdapter`. Bridges the Python Copilot SDK.

- Uses callback-to-iterator bridge via `asyncio.Queue`
- Manages `_sessions: dict[str, CopilotSession]`
- Manages `_fallback_turn_ids: dict[str, str]` for step tracking when SDK lacks turn_id
- `_cleanup_session(session_id)` — stops `CopilotClient` to prevent leaked subprocesses
- `_handle_permission_request(request, invocation, config)` — bridges SDK permission into CodePlane approval system

### `ClaudeAdapter` (`backend/services/claude_adapter/_adapter.py`)

Extends `BaseAgentAdapter`. Bridges the Claude Agent SDK (Python).

- `_consumer_tasks: dict[str, asyncio.Task]` — background tasks consuming SDK message iterators
- `_current_turn_ids: dict[str, str]` — session → turn_id
- `_requested_models / _model_verified` — model downgrade detection
- `_stderr_files / _stderr_file_objects` — stderr capture for debugging
- `_build_can_use_tool(config, session_id)` — builds `can_use_tool` callback mapping Claude tool names to permission kinds (`Bash→shell`, `Edit/Write→write`, `Read/Glob/Grep→read`)
- `set_execution_phase(job_id, phase)` — updates `_current_phases` for cost analytics

---

## 5. SessionEvent and SessionEventKind (`backend/models/domain.py`)

### `SessionEventKind` (StrEnum)

Events emitted by adapters (SDK-level):

| Kind | Description |
|------|-------------|
| `log` | Agent log line |
| `transcript` | Conversation entry (user, assistant, tool_call, tool_result) |
| `file_changed` | File modified in worktree |
| `approval_request` | Permission request from agent |
| `model_downgraded` | Actual model differs from requested |
| `done` | Session completed |
| `error` | Session error |

### `SessionEvent` (dataclass)

```python
kind: SessionEventKind
payload: SessionEventPayload  # Union of typed dicts per kind
```

### Session Event Payloads

| Kind | Payload TypedDict | Key Fields |
|------|-------------------|------------|
| `log` | `LogPayload` | seq, timestamp, level, message |
| `transcript` | `TranscriptPayload` | role, content, turn_id, title, tool_name, tool_args, tool_result, tool_success, tool_issue, tool_intent, tool_title, tool_display, tool_display_full, tool_duration_ms, tool_visibility, tool_call_id |
| `file_changed` | `FileChangedPayload` | path (required) |
| `approval_request` | `ApprovalRequestPayload` | description, proposed_action, approval_id, requires_explicit_approval |
| `model_downgraded` | `ModelDowngradedPayload` | requested_model (req), actual_model (req) |
| `done` | `DonePayload` | result |
| `error` | `ErrorPayload` | message, result |

---

## 6. Domain Events (`backend/models/events.py`)

### `DomainEventKind` (StrEnum) — 40 event types

| Kind | Description |
|------|-------------|
| `JobCreated` | New job created |
| `JobSetupProgress` | Setup step progress (step field) |
| `WorkspacePrepared` | Worktree ready |
| `AgentSessionStarted` | Agent session launched |
| `LogLineEmitted` | Agent log line persisted |
| `TranscriptUpdated` | Conversation entry persisted |
| `DiffUpdated` | Diff snapshot updated |
| `ApprovalRequested` | Approval pending |
| `ApprovalResolved` | Approval resolved |
| `BatchApprovalRequested` | Batch approval pending |
| `BatchApprovalResolved` | Batch approval resolved |
| `JobReview` | Job moved to review state |
| `JobCompleted` | Job completed |
| `JobFailed` | Job failed |
| `JobCanceled` | Job canceled |
| `JobStateChanged` | Generic state transition |
| `SessionHeartbeat` | Keep-alive |
| `MergeCompleted` | Git merge succeeded |
| `MergeConflict` | Git merge conflict |
| `SessionResumed` | Session resumed after pause |
| `JobResolved` | User resolved a job (merge/PR/discard) |
| `JobArchived` | Job archived |
| `JobTitleUpdated` | Title/branch/description updated |
| `ProgressHeadline` | Human-readable progress update |
| `ModelDowngraded` | Model downgrade detected |
| `ToolGroupSummary` | Tool usage summary for a turn |
| `AgentPlanUpdated` | Agent's plan steps changed |
| `ExecutionPhaseChanged` | Phase transition (setup/reasoning/finalization) |
| `TelemetryUpdated` | Telemetry data finalized |
| `StepStarted` | Plan step started |
| `StepCompleted` | Plan step completed |
| `StepTitleGenerated` | Step title generated |
| `StepGroupUpdated` | Step group metadata updated |
| `PlanStepUpdated` | Plan step metadata updated |
| `StepEntriesReassigned` | Transcript entries moved between steps |
| `TurnSummary` | Turn-level summary |
| `ActionClassified` | Action policy classification result |
| `PolicySettingsChanged` | Global policy config changed |

### `DomainEvent` (dataclass)

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `event_id` | `str` | yes | Format: `evt-{uuid_hex[:12]}` |
| `job_id` | `str \| None` | yes | Can be None for global events |
| `timestamp` | `datetime` | yes | UTC |
| `kind` | `DomainEventKind` | yes | |
| `payload` | `EventPayload` (union of ~25 TypedDicts) | yes | |
| `db_id` | `int \| None` | no | Set after persistence; autoincrement ID |

Factory: `DomainEvent.for_job(job_id, kind, payload)` — auto-fills event_id and timestamp.

### Event Translation Map (RuntimeService._translate_event)

SessionEvent → DomainEvent:

| SessionEventKind | DomainEventKind |
|------------------|-----------------|
| `log` | `log_line_emitted` |
| `transcript` | `transcript_updated` |
| `approval_request` | `approval_requested` |
| `error` | `job_failed` |
| `model_downgraded` | `model_downgraded` |
| `done` | *(not translated — handled at _run_job level)* |
| `file_changed` | *(handled internally for diff recalculation, not published)* |

---

## 7. Job Model (`backend/models/domain.py`)

### `Job` (dataclass)

| Field | Type | Required/Default | Notes |
|-------|------|------------------|-------|
| `id` | `str` | **required** | UUID-like |
| `repo` | `str` | **required** | Repository path |
| `prompt` | `str` | **required** | Task description |
| `state` | `JobState` | **required** | Current state |
| `base_ref` | `str` | **required** | Git ref to branch from |
| `branch` | `str \| None` | **required** | Working branch name |
| `worktree_path` | `str \| None` | **required** | Filesystem path to worktree |
| `session_id` | `str \| None` | **required** | Internal session ID |
| `created_at` | `datetime` | **required** | |
| `updated_at` | `datetime` | **required** | |
| `completed_at` | `datetime \| None` | `None` | |
| `pr_url` | `str \| None` | `None` | Pull request URL |
| `merge_status` | `GitMergeOutcome \| None` | `None` | Mechanical merge result |
| `resolution` | `Resolution \| None` | `None` | User-facing disposition |
| `archived_at` | `datetime \| None` | `None` | |
| `title` | `str \| None` | `None` | Human-readable title |
| `description` | `str \| None` | `None` | Job description |
| `worktree_name` | `str \| None` | `None` | |
| `preset` | `Preset` | `Preset.supervised` | Action policy preset |
| `session_count` | `int` | `1` | Incremented on resume |
| `sdk_session_id` | `str \| None` | `None` | SDK-level session ID for resume |
| `model` | `str \| None` | `None` | Requested model |
| `sdk` | `str` | `"copilot"` | SDK backend |
| `failure_reason` | `str \| None` | `None` | Error message |
| `verify` | `bool \| None` | `None` | Per-job verify override |
| `self_review` | `bool \| None` | `None` | Per-job self-review override |
| `max_turns` | `int \| None` | `None` | Per-job max turns override |
| `verify_prompt` | `str \| None` | `None` | Custom verify prompt |
| `self_review_prompt` | `str \| None` | `None` | Custom self-review prompt |
| `version` | `int` | `1` | Optimistic locking |
| `parent_job_id` | `str \| None` | `None` | For follow-up jobs |

### `JobState` (StrEnum)

| State | Terminal? | Description |
|-------|-----------|-------------|
| `preparing` | no | Workspace being set up |
| `queued` | no | Waiting for capacity |
| `running` | no | Agent executing |
| `waiting_for_approval` | no | Blocked on operator decision |
| `review` | no | Agent done, awaiting operator review |
| `completed` | yes | Resolved (merged/PR/discarded) |
| `failed` | yes | Error occurred |
| `canceled` | yes | Canceled by operator |

### State Machine Transitions

```
None → preparing, running, queued
preparing → queued, failed, canceled
queued → running, canceled
running → waiting_for_approval, review, failed, canceled
waiting_for_approval → running, failed, canceled
review → running, completed, canceled
completed → running (resume)
failed → running (resume)
canceled → running (resume)
```

### Related Enums

- **`Resolution`**: `unresolved`, `merged`, `pr_created`, `discarded`, `conflict`
- **`GitMergeOutcome`**: `not_merged`, `merged`, `conflict`, `pr_created`
- **`Preset`**: `autonomous`, `supervised`, `strict`
- **`AgentSDK`**: `copilot`, `claude`
- **`ApprovalResolution`**: `approved`, `rejected`

### `JobSpec` (dataclass) — Job creation parameters

| Field | Type | Default |
|-------|------|---------|
| `repo` | `str` | **required** |
| `prompt` | `str` | **required** |
| `base_ref` | `str \| None` | `None` |
| `branch` | `str \| None` | `None` |
| `title` | `str \| None` | `None` |
| `description` | `str \| None` | `None` |
| `worktree_name` | `str \| None` | `None` |
| `preset` | `Preset` | `Preset.supervised` |
| `model` | `str \| None` | `None` |
| `sdk` | `str \| None` | `None` |
| `verify` | `bool \| None` | `None` |
| `self_review` | `bool \| None` | `None` |
| `max_turns` | `int \| None` | `None` |
| `verify_prompt` | `str \| None` | `None` |
| `self_review_prompt` | `str \| None` | `None` |
| `parent_job_id` | `str \| None` | `None` |
| `parent_job_context` | `str \| None` | `None` |

### `SessionConfig` (dataclass)

| Field | Type | Default |
|-------|------|---------|
| `workspace_path` | `str` | **required** |
| `prompt` | `str` | **required** |
| `job_id` | `str` | `""` |
| `sdk` | `str` | `"copilot"` |
| `model` | `str \| None` | `None` |
| `mcp_servers` | `dict[str, MCPServerConfig]` | `{}` |
| `protected_paths` | `list[str]` | `[]` |
| `blocking_permission_handler` | `Callable \| None` | `None` |
| `resume_sdk_session_id` | `str \| None` | `None` |

---

## 8. RuntimeService (`backend/services/runtime_service.py`)

### Core Event Processing Flow

```
1. Job created via POST /jobs → JobService.create_job()
2. Background: setup_and_start() → setup workspace → preparing→queued
3. start_or_enqueue() → capacity check → _start_job()
4. _start_job() → DB CAS claim → AgentSession.execute() → asyncio task
5. _run_job() main loop:
   a. Initialize telemetry, observer terminal, trail service
   b. Set execution phase → environment_setup → agent_reasoning
   c. _execute_session_attempt():
      - For each SessionEvent from agent_session.execute():
        i.  _process_agent_event() → translate + handle approval + echo suppression
        ii. Annotate with step_id, session_number
        iii. Feed to trail service for activity tracking
        iv. Publish DomainEvent via event_bus
   d. Handle result: downgrade, error, or successful completion
   e. _handle_successful_completion() → finalize diff → verify/review → merge
6. finally: finalize_job_telemetry() → store artifacts → cleanup
```

### `_process_agent_event()` — The Event Translation Core

Returns `(EventAction, DomainEvent | None, error_reason)`:

1. **file_changed** → triggers diff recalculation → `EventAction.skip`
2. **transcript + tool_call** → also triggers diff recalculation (then continues)
3. Calls `_translate_event()` to map SessionEvent → DomainEvent
4. If DomainEvent is None → `EventAction.skip`
5. If `job_failed` → sets error_reason, returns `EventAction.publish`
6. Checks echo suppression for transcript events
7. Handles approval requests → `EventAction.skip` (approved) or `EventAction.abort` (rejected)
8. Otherwise → `EventAction.publish`

### Key Internal Data Structures

| Field | Type | Purpose |
|-------|------|---------|
| `_tasks` | `dict[str, asyncio.Task]` | Running job tasks |
| `_agent_sessions` | `dict[str, AgentSession]` | Active agent wrappers |
| `_heartbeat_tasks` | `dict[str, asyncio.Task]` | Heartbeat tasks |
| `_last_activity` | `dict[str, float]` | Last activity timestamp |
| `_waiting_for_approval` | `set[str]` | Jobs waiting for approval |
| `_session_ids` | `dict[str, str]` | job_id → SDK session_id |
| `_policy_routers` | `dict[str, Any]` | job_id → PolicyRouter |
| `_policy_batchers` | `dict[str, Any]` | job_id → ApprovalBatcher |
| `_echo_suppress` | `dict[str, set[str]]` | SDK echo suppression |
| `_observer_terminals` | `dict[str, str]` | job_id → terminal session ID |

### Extension Points

- **Trail service** (`set_trail_service(svc)`) — late-bound for plan/activity tracking
- **Terminal service** (`set_terminal_service(svc)`) — observer terminals
- **Sister sessions** — pre-warmed session management
- **Step tracker** — plan step tracking

---

## 9. Runtime Telemetry (`backend/services/runtime_telemetry.py`)

### `RuntimeTelemetry`

Constructor:
```python
__init__(
    session_factory: async_sessionmaker[AsyncSession],
    event_bus: EventBus,
    make_job_service: Callable[[AsyncSession], JobService],
    resolve_adapter: Callable[[str], AgentAdapterInterface],
    trail_service: TrailService | None = None,
)
```

### Telemetry Lifecycle

#### `init_telemetry_row(job_id, config)`
1. Looks up job for repo/branch/sdk metadata
2. Creates initial `TelemetrySummaryRepository.init_job()` row
3. Fire-and-forget (called as asyncio task)

#### `finalize_job_telemetry(job_id, wall_start, config)`
Called in `_run_job` finally block. Pipeline:

1. `tel.end_job_span(job_id)` — close OTEL span
2. Emit `ExecutionPhaseChanged(finalization)` event
3. `TelemetrySummaryRepository.finalize(job_id, status, duration_ms)` — update summary row
4. `compute_attribution(session, job_id)` — cost attribution pipeline
5. `compute_latency_attribution(session, job_id)` — latency attribution pipeline
6. `run_analysis(session)` — statistical analysis (fire-and-forget)
7. Publish `TelemetryUpdated` event to signal clients
8. `store_post_completion_artifacts(job_id)` — persist artifacts

#### `store_post_completion_artifacts(job_id)`
Stores as downloadable artifacts:
- Telemetry report (from summary row)
- Agent plan steps (from trail service)
- Approval history (from approval repo)
- Agent logs (from event repo)

---

## 10. Job Persistence (`backend/persistence/job_repo.py`)

### `JobRepository(BaseRepository)`

Uses SQLAlchemy `JobRow` model with optimistic locking (`version` column).

| Method | Signature | Notes |
|--------|-----------|-------|
| `create(job)` | `async (Job) -> Job` | Insert new row |
| `get(job_id)` | `async (str) -> Job \| None` | Lookup by ID |
| `list(state, limit, cursor, include_archived)` | `async (...) -> list[Job]` | Cursor-based pagination |
| `list_all(state, include_archived)` | `async (...) -> list[Job]` | Internal only, no limit |
| `list_ids()` | `async () -> set[str]` | All job IDs |
| `update_state(job_id, new_state, updated_at, completed_at, failure_reason)` | `async (...)` | State transition |
| `update_pr_url(job_id, pr_url)` | `async (...)` | |
| `update_merge_status(job_id, merge_status, pr_url)` | `async (...)` | |
| `update_resolution(job_id, resolution, pr_url)` | `async (...)` | |
| `update_archived_at(job_id, archived_at)` | `async (...)` | |
| `update_sdk_session_id(job_id, sdk_session_id)` | `async (...)` | |
| `update_worktree_path(job_id, worktree_path)` | `async (...)` | |
| `update_worktree(job_id, worktree_path, branch)` | `async (...)` | |
| `update_failure_reason(job_id, reason)` | `async (...)` | |
| `reset_for_resume(job_id, new_session_count, merge_status)` | `async (...)` | Reset terminal → running |
| `reset_for_recovery(job_id, new_session_count, new_state)` | `async (...)` | Server restart recovery |
| `restore_after_failed_resume(job_id, ...)` | `async (...)` | Rollback failed resume |
| `claim_for_start(job_id)` | `async (str) -> bool` | DB-level CAS for double-start prevention |

**Optimistic locking**: `_update_row()` increments `version` on every update.

---

## 11. Event Persistence (`backend/persistence/event_repo.py`)

### `EventRepository(BaseRepository)`

| Method | Signature | Notes |
|--------|-----------|-------|
| `append(event)` | `async (DomainEvent) -> int` | Persist; returns autoincrement DB id |
| `list_after(after_id, job_id, limit=500)` | `async (...) -> list[DomainEvent]` | For SSE reconnection replay |
| `list_by_job(job_id, kinds, limit=2000)` | `async (...) -> list[DomainEvent]` | Filtered by kind |
| `list_all_by_job(job_id, kinds)` | `async (...) -> list[DomainEvent]` | No limit |
| `get_latest_progress_preview(job_id)` | `async (str) -> tuple[str,str] \| None` | Latest headline+summary |
| `list_latest_progress_previews(job_ids)` | `async (list[str]) -> dict[str, tuple[str,str]]` | Batch |
| `search_transcript(job_id, query, roles, step_id, limit=50)` | `async (...) -> list[DomainEvent]` | Full-text search in transcript |

Events are stored as `EventRow` with payload serialized as JSON string.

---

## 12. REST API Endpoints

### Jobs (`backend/api/jobs.py`)

| Method | Path | Response | Description |
|--------|------|----------|-------------|
| POST | `/jobs/suggest-names` | `SuggestNamesResponse` | Generate title/branch/worktree names |
| POST | `/jobs` | `CreateJobResponse` (201) | Create new job |
| GET | `/jobs` | `JobListResponse` | List jobs (state, cursor, archived filters) |
| GET | `/jobs/{job_id}` | `JobResponse` | Get job detail |
| POST | `/jobs/{job_id}/cancel` | `JobResponse` | Cancel job |
| POST | `/jobs/{job_id}/interrupt` | 204 | Interrupt agent (non-destructive) |
| POST | `/jobs/{job_id}/rerun` | `CreateJobResponse` (201) | Rerun from existing config |
| POST | `/jobs/{job_id}/pause` | 204 | Pause job |
| POST | `/jobs/{job_id}/continue` | `CreateJobResponse` (201) | Follow-up job with parent context |
| POST | `/jobs/{job_id}/resume` | `JobResponse` | Resume completed/failed/canceled job |
| GET | `/models` | `ModelListResponse` | List available models |

### Job Artifacts (`backend/api/job_artifacts.py`)

| Method | Path | Response | Description |
|--------|------|----------|-------------|
| GET | `/jobs/{job_id}/logs` | `LogListResponse` | Historical log lines |
| GET | `/jobs/{job_id}/diff` | `DiffListResponse` | Current diff |
| GET | `/jobs/{job_id}/transcript` | `TranscriptListResponse` | Conversation transcript |
| GET | `/jobs/{job_id}/steps` | `StepListResponse` | Plan steps |
| GET | `/jobs/{job_id}/steps/{step_id}/diff` | `StepDiffPayload` | Per-step diff |
| GET | `/jobs/{job_id}/transcript/search` | `TranscriptSearchListResponse` | Transcript search |
| POST | `/jobs/{job_id}/restore` | `RestoreResponse` | Restore worktree to step |
| GET | `/jobs/{job_id}/timeline` | `TimelineListResponse` | Timeline entries |
| GET | `/jobs/{job_id}/snapshot` | `JobSnapshotResponse` | Full job snapshot |
| POST | `/jobs/{job_id}/resolve` | `ResolveJobResponse` | Resolve job (merge/PR/discard) |
| POST | `/jobs/{job_id}/archive` | 204 | Archive job |
| POST | `/jobs/{job_id}/unarchive` | 204 | Unarchive job |
| GET | `/jobs/{job_id}/story` | `StoryResponse` | Story narrative |
| GET | `/jobs/{job_id}/narrative` | `NarrativeResponse` | Rich narrative |

### Job Telemetry (`backend/api/job_telemetry.py`)

| Method | Path | Response | Description |
|--------|------|----------|-------------|
| GET | `/jobs/{job_id}/telemetry` | `JobTelemetryResponse` | Job telemetry data |

### Approvals (`backend/api/approvals.py`)

| Method | Path | Response | Description |
|--------|------|----------|-------------|
| GET | `/jobs/{job_id}/approvals` | `ApprovalListResponse` | List approvals |
| POST | `/approvals/{approval_id}/resolve` | `ApprovalResponse` | Resolve approval |
| POST | `/jobs/{job_id}/approvals/trust` | `TrustJobResponse` | Trust job (auto-approve all) |
| POST | `/jobs/{job_id}/messages` | `SendMessageResponse` | Send operator message |
| POST | `/jobs/{job_id}/batches/resolve` | `ResolveBatchResponse` | Resolve action batch |

### SSE Events (`backend/api/events.py`)

| Method | Path | Response | Description |
|--------|------|----------|-------------|
| GET | `/events` | `StreamingResponse` (SSE) | Live event stream (optional job_id filter, Last-Event-ID reconnection) |

### Settings (`backend/api/settings.py`)

| Method | Path | Response | Description |
|--------|------|----------|-------------|
| GET | `/settings` | `SettingsResponse` | Get settings |
| PUT | `/settings` | `SettingsResponse` | Update settings |
| GET/POST/PUT/DELETE | `/settings/repos/*` | Various | Repo CRUD |
| GET | `/settings/sdks` | `SDKListResponse` | List SDKs |
| GET/POST | `/settings/browse` | `BrowseDirectoryResponse` | Browse filesystem |
| POST | `/settings/cleanup` | `CleanupWorktreesResponse` | Cleanup worktrees |
| GET | `/settings/platforms` | `PlatformStatusListResponse` | Platform status |

### Action Policy Settings (`backend/api/policy_settings.py`, prefix `/settings/policy`)

| Method | Path | Response | Description |
|--------|------|----------|-------------|
| GET | `/settings/policy` | `FullPolicyResponse` | Full policy config |
| PUT | `/settings/policy/preset` | `PolicyConfigResponse` | Update preset |
| PUT | `/settings/policy/config` | `PolicyConfigResponse` | Update config |
| GET/POST/PUT/DELETE | `/settings/policy/path-rules/*` | Various | Path rule CRUD |
| GET/POST/PUT/DELETE | `/settings/policy/action-rules/*` | Various | Action rule CRUD |
| GET/POST/PUT/DELETE | `/settings/policy/cost-rules/*` | Various | Cost rule CRUD |
| GET/POST/PUT/DELETE | `/settings/policy/mcp-servers/*` | Various | MCP server CRUD |
| GET/POST/DELETE | `/settings/policy/trust-grants/*` | Various | Trust grant CRUD |
| GET | `/settings/policy/export` | JSON | Export policy |
| POST | `/settings/policy/import` | JSON | Import policy |

### Analytics (`backend/api/analytics.py`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/analytics/overview` | Aggregate overview |
| GET | `/analytics/models` | Per-model breakdown |
| GET | `/analytics/tools` | Tool performance |
| GET | `/analytics/repos` | Per-repo breakdown |
| GET | `/analytics/jobs` | Per-job telemetry table |
| GET | `/analytics/pricing` | Model pricing lookup |
| GET | `/analytics/cost-drivers/{job_id}` | Per-job cost attribution |
| GET | `/analytics/cost-drivers` | Fleet cost attribution |
| GET | `/analytics/latency-drivers` | Fleet latency attribution |
| GET | `/analytics/file-access/{job_id}` | Per-job file access stats |
| GET | `/analytics/file-access` | Fleet file access |
| GET | `/analytics/turn-economics/{job_id}` | Per-turn cost curve |
| GET | `/analytics/scorecard` | Top-level scorecard |
| GET | `/analytics/model-comparison` | Model comparison with resolutions |
| GET | `/analytics/job-context/{job_id}` | Per-job context |
| GET | `/analytics/observations` | Cost observations/anomalies |
| POST | `/analytics/observations/{id}/dismiss` | Dismiss observation |
| POST | `/analytics/analyse` | Trigger analysis |
| GET | `/analytics/shell-commands` | Shell command breakdown |
| GET | `/analytics/retry-cost` | Retry cost summary |
| GET | `/analytics/edit-efficiency` | Edit efficiency / one-shot rate |
| GET | `/analytics/yield` | Yield metrics |
| GET | `/analytics/model-efficiency` | Model efficiency metrics |
| GET | `/analytics/cache-efficiency` | Cache efficiency |
| GET | `/analytics/export` | CSV export |

### Health (`backend/api/health.py`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Service health status |
| GET | `/sister-sessions/metrics` | Sister session metrics |

### Workspace (`backend/api/workspace.py`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/jobs/{job_id}/workspace` | List worktree files |
| GET | `/jobs/{job_id}/workspace/file` | Read worktree file content |

### Artifacts (`backend/api/artifacts.py`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/jobs/{job_id}/artifacts` | List artifacts |
| GET | `/artifacts/{artifact_id}` | Download artifact file |

### Sharing (`backend/api/share.py`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/jobs/{job_id}/share` | Create share token |
| GET | `/share/{token}/job` | Read-only shared job |
| GET | `/share/{token}/events` | Shared SSE stream |
| GET | `/share/{token}/snapshot` | Shared job snapshot |
| GET | `/share/{token}/telemetry` | Shared job telemetry |

### Preview (`backend/api/preview.py`)

| Method | Path | Description |
|--------|------|-------------|
| * | `/preview/{port}/{path}` | Reverse proxy to local dev server (port 1024-65535) |

### Trail (`backend/api/trail.py`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/jobs/{job_id}/trail` | Audit trail |
| GET | `/jobs/{job_id}/trail/summary` | Trail summary |

### Notifications (`backend/api/notifications.py`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/notifications/vapid-key` | VAPID public key |
| POST | `/notifications/subscribe` | Subscribe to push |
| POST | `/notifications/unsubscribe` | Unsubscribe |

### Terminal (`backend/api/terminal.py`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/terminal/sessions` | Create terminal session |
| GET | `/terminal/sessions` | List sessions |
| DELETE | `/terminal/sessions/{id}` | Kill session |
| GET | `/terminal/observer/{job_id}` | Get observer terminal |
| POST | `/terminal/ask` | AI shell command translation |
| WS | `/terminal/ws/{session_id}` | Terminal WebSocket |

### Utility Sessions (`backend/api/utility_sessions.py`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/utility-sessions/warm` | Pre-warm session |
| DELETE | `/utility-sessions/{token}` | Release session |

### Voice (`backend/api/voice.py`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/voice/transcribe` | Audio transcription |

---

## 13. End-to-End Flow: Job Creation → Completion

```
POST /jobs {repo, prompt, sdk, model, ...}
  │
  ├── JobService.create_job(JobSpec) → Job(state=preparing)
  ├── DB commit (job visible to background tasks)
  │
  └── Background task: setup_and_start(job)
        │
        ├── JobService.setup_workspace(job.id) → create worktree, branch
        │   Publishes: JobSetupProgress events
        │
        ├── State: preparing → queued
        │   Publishes: JobStateChanged
        │
        └── start_or_enqueue(job)
              │
              ├── [Capacity available] → _start_job(job)
              │     │
              │     ├── DB CAS: claim_for_start() → state=running
              │     ├── Publish: JobStateChanged (queued → running)
              │     │
              │     └── asyncio task: _run_job_guarded()
              │           │
              │           ├── Start heartbeat loop
              │           ├── Start trail tracking
              │           ├── Start OTEL span + telemetry summary row
              │           ├── Create observer terminal
              │           ├── Emit ExecutionPhaseChanged(environment_setup)
              │           ├── Setup action policy router
              │           ├── Emit ExecutionPhaseChanged(agent_reasoning)
              │           │
              │           ├── _execute_session_attempt()
              │           │     For each SessionEvent:
              │           │       _process_agent_event() → translate → publish
              │           │       Feed trail service, step tracker
              │           │
              │           ├── _handle_successful_completion()
              │           │     ├── Finalize diff snapshot
              │           │     ├── Run verify / self-review (optional)
              │           │     ├── State: running → review
              │           │     ├── Execute merge resolution (if configured)
              │           │     ├── Publish: JobReview or JobCompleted
              │           │
              │           └── finally:
              │                 ├── finalize_job_telemetry()
              │                 │     ├── End OTEL span
              │                 │     ├── Finalize summary row
              │                 │     ├── Cost attribution pipeline
              │                 │     ├── Latency attribution pipeline
              │                 │     ├── Statistical analysis
              │                 │     ├── Publish TelemetryUpdated
              │                 │     └── Store artifacts (telemetry, plan, approvals, logs)
              │                 ├── Stop trail tracking + finalize
              │                 └── Cleanup job state
              │
              └── [At capacity] → queued (dequeued when slot opens)
```

---

## 14. Key Extension Points and Plugin Hooks

1. **Adapter Registration**: Add new SDK by implementing `AgentAdapterInterface` and adding to `AdapterRegistry._create()`
2. **Event Bus**: `EventBus.subscribe()` — any service can subscribe to all domain events
3. **Trail Service**: Late-bound via `set_trail_service()` — pluggable plan/activity tracking
4. **Step Tracker**: Annotation of transcript events with step IDs
6. **Action Policy**: Pluggable `PolicyRouter` and `ApprovalBatcher` per job
7. **Sister Sessions**: Pre-warmed sessions for faster job startup
8. **Platform Registry**: Detect and adapt to deployment platform
9. **MCP Servers**: Configurable per-policy MCP server definitions
10. **Cost Attribution**: Post-job pipeline computing dimension-based cost breakdown
11. **Statistical Analysis**: Post-job fleet-wide anomaly detection
12. **Artifact Storage**: Extensible via `ArtifactService.store_*` methods
