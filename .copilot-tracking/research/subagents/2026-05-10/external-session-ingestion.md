# External Session Ingestion — Architecture Research

## Research Questions

1. What is the current job lifecycle and creation flow?
2. What events does CodePlane track and how are they ingested?
3. What CLI commands exist today? Any import/export capabilities?
4. How coupled is the system to specific agent SDKs?
5. How does the SSE system work? Could external tools push events?
6. What metrics does CodePlane compute? What's the minimum data needed?
7. What are the key database tables and schema?
8. What does SPEC.md say about extensibility?

---

## 1. Job Lifecycle and Creation

### Job Model (`backend/models/domain.py`)

The `Job` dataclass is the central domain object:

**Required fields for creation:**
- `id` — string, derived from LLM-generated `worktree_name` (e.g. `fix-login-bug`)
- `repo` — resolved absolute path to the git repository
- `prompt` — the task description
- `state` — `JobState` enum
- `base_ref` — git branch to base work on
- `created_at`, `updated_at` — timestamps

**Optional fields:**
- `branch` — git branch name for the work
- `worktree_path` — path to the git worktree
- `session_id` — agent SDK session ID
- `title`, `description`, `worktree_name` — LLM-generated metadata
- `preset` — `autonomous | supervised | strict` (default: `supervised`)
- `model` — LLM model name
- `sdk` — `copilot | claude` (default: `copilot`)
- `verify`, `self_review`, `max_turns` — execution options
- `parent_job_id` — for child jobs
- `session_count` — incremented on resume

### Job States (`JobState` enum)

```
preparing → queued → running → review → completed
                       ↕           ↕
              waiting_for_approval  running (rerun)
                       ↓
                   canceled
```

States: `preparing`, `queued`, `running`, `waiting_for_approval`, `review`, `completed`, `failed`, `canceled`

Terminal states (`completed`, `failed`, `canceled`) can transition back to `running` for job resumption.

### Job Creation Flow

1. **REST endpoint** `POST /api/jobs` accepts `CreateJobRequest` (repo, prompt, base_ref, branch, model, sdk, etc.)
2. **JobService.create_job()** validates repo against allowlist, validates SDK-model compatibility, resolves base_ref, generates names (LLM or hash fallback), creates Job in `preparing` state
3. **Background task** `RuntimeService.setup_and_start()`:
   - Creates git worktree via `JobService.setup_workspace()`
   - Transitions to `queued`
   - Calls `start_or_enqueue()` which either starts immediately or queues (capacity limit)
4. **`_start_job()`** creates an asyncio task that:
   - Builds `SessionConfig` with workspace path, prompt, MCP servers, etc.
   - Creates agent session via `AgentAdapterInterface.create_session()`
   - Streams events via `stream_events()` async iterator
   - Processes each `SessionEvent`, converts to `DomainEvent`, publishes to EventBus

### `JobSpec` dataclass (creation input)

```python
@dataclass
class JobSpec:
    repo: str
    prompt: str
    base_ref: str | None = None
    branch: str | None = None
    title: str | None = None
    description: str | None = None
    worktree_name: str | None = None
    preset: Preset = Preset.supervised
    model: str | None = None
    sdk: str | None = None
    verify: bool | None = None
    self_review: bool | None = None
    max_turns: int | None = None
    verify_prompt: str | None = None
    self_review_prompt: str | None = None
    parent_job_id: str | None = None
    parent_job_context: str | None = None
```

### Key Coupling Points

Job creation is **tightly coupled** to:
1. **Repo allowlist** — repo path must be registered and exist on disk
2. **Git worktree creation** — `GitService` creates a real worktree
3. **Agent SDK adapter** — `AdapterRegistry.get_adapter(sdk)` creates a Copilot or Claude adapter
4. **Naming service** — requires LLM to generate job names (falls back to hash)

---

## 2. Event Model and Ingestion

### Domain Event Envelope (`backend/models/events.py`)

```python
@dataclass
class DomainEvent:
    event_id: str          # "evt-{uuid_hex[:12]}"
    job_id: str | None
    timestamp: datetime
    kind: DomainEventKind  # enum of ~40 event types
    payload: EventPayload  # dict with typed shape per kind
    db_id: int | None      # autoincrement ID from EventRow
```

### Event Kinds (full list from `DomainEventKind`)

**Lifecycle events:** `job_created`, `job_setup_progress`, `workspace_prepared`, `agent_session_started`, `job_review`, `job_completed`, `job_failed`, `job_canceled`, `job_state_changed`, `session_resumed`, `job_resolved`, `job_archived`, `job_title_updated`

**Execution events:** `log_line_emitted`, `transcript_updated`, `diff_updated`, `approval_requested`, `approval_resolved`, `batch_approval_requested`, `batch_approval_resolved`, `session_heartbeat`, `merge_completed`, `merge_conflict`, `model_downgraded`

**Progress events:** `progress_headline`, `tool_group_summary`, `agent_plan_updated`, `execution_phase_changed`, `telemetry_updated`

**Step/trail events:** `step_started`, `step_completed`, `step_title_generated`, `step_group_updated`, `plan_step_updated`, `step_entries_reassigned`, `turn_summary`

**Policy events:** `action_classified`, `policy_settings_changed`

**Index events:** `repo_index_progress`, `repo_index_complete`, `structural_warning`

### Key Event Payloads

**TranscriptPayload** — the richest event type:
```python
class TranscriptPayloadDict(TypedDict, total=False):
    seq: int
    timestamp: str
    role: str               # agent | operator | tool_call
    content: str
    title: str | None
    turn_id: str | None
    tool_name: str | None
    tool_args: str | None
    tool_result: str | None
    tool_success: bool | None
    tool_duration_ms: int | None
    # ... and more
```

**TelemetryUpdatedPayload:**
```python
class TelemetryUpdatedPayloadDict(TypedDict, total=False):
    job_id: str
    total_cost_usd: float
    total_tokens: int
    input_tokens: int
    output_tokens: int
```

### Event Flow

1. Agent SDK emits `SessionEvent` (kind: log, transcript, file_changed, approval_request, done, error)
2. `RuntimeService._process_agent_event()` converts `SessionEvent` → `DomainEvent`
3. `EventBus.publish()` fans out to subscribers concurrently
4. Subscribers:
   - **EventRepository** — persists to SQLite `events` table (JSON payload)
   - **SSEManager** — pushes to connected SSE clients
   - **TrailService** — updates activity timeline
   - **StepTracker** — manages step boundaries
   - **RuntimeTelemetry** — updates telemetry counters

### Internal Event Bus (`backend/services/event_bus.py`)

Simple in-process async pub/sub:
```python
class EventBus:
    def subscribe(self, handler: Subscriber) -> None
    def unsubscribe(self, handler: Subscriber) -> None
    async def publish(self, event: DomainEvent) -> None  # fan-out to all subscribers
```

Subscriber type: `Callable[[DomainEvent], Coroutine[Any, Any, None]]`

---

## 3. Current CLI (`backend/cli.py`)

Commands available via `cpl`:
- `cpl up` — start the server (with tunnel, auth, frontend build options)
- `cpl down` — gracefully pause sessions and stop server
- `cpl restart` — down then up
- `cpl version` — print version
- `cpl info` — print connection details and QR code
- `cpl setup` — interactive setup wizard
- `cpl doctor` — health check (deps, auth, SDK, environment)
- `cpl backfill-attribution` — re-run cost attribution for historical jobs

**No import/export capabilities exist.** The CLI is purely operational (start/stop/diagnose). There are no commands to import session data, export jobs, or ingest external events.

---

## 4. Agent Adapter Pattern

### `AgentAdapterInterface` (`backend/services/agent_adapter.py`)

Abstract interface that all SDK adapters implement:

```python
class AgentAdapterInterface(ABC):
    async def create_session(self, config: SessionConfig) -> str
    async def stream_events(self, session_id: str) -> AsyncIterator[SessionEvent]
    async def send_message(self, session_id: str, message: str) -> None
    async def abort_session(self, session_id: str) -> None
    async def interrupt_session(self, session_id: str) -> None
    def pause_tools(self, session_id: str) -> None
    def resume_tools(self, session_id: str) -> None
    async def complete(self, prompt: str) -> CompletionResult
    def set_execution_phase(self, job_id: str, phase: ExecutionPhase) -> None
```

### `SessionConfig` (adapter input)

```python
@dataclass
class SessionConfig:
    workspace_path: str
    prompt: str
    job_id: str = ""
    sdk: str = "copilot"
    model: str | None = None
    mcp_servers: dict[str, MCPServerConfig] = field(default_factory=dict)
    protected_paths: list[str] = field(default_factory=list)
    blocking_permission_handler: Callable | None = None
    resume_sdk_session_id: str | None = None
```

### `SessionEvent` (adapter output)

```python
class SessionEventKind(StrEnum):
    log = "log"
    transcript = "transcript"
    file_changed = "file_changed"
    approval_request = "approval_request"
    model_downgraded = "model_downgraded"
    done = "done"
    error = "error"

@dataclass
class SessionEvent:
    kind: SessionEventKind
    payload: SessionEventPayload  # typed union of payloads per kind
```

### `AdapterRegistry` (`backend/services/adapter_registry.py`)

Lazy-caching factory:
```python
class AdapterRegistry:
    def get_adapter(self, sdk: AgentSDK | str) -> AgentAdapterInterface
```

Currently supports two SDKs:
- `AgentSDK.copilot` → `CopilotAdapter` (wraps `github-copilot-sdk`)
- `AgentSDK.claude` → `ClaudeAdapter` (wraps `claude-code-sdk`)

### Coupling Assessment

The system is **well-abstracted** from specific SDKs. The `AgentAdapterInterface` is a clean boundary. However:
- `RuntimeService` owns the full event loop: it calls `create_session()`, iterates `stream_events()`, and converts each `SessionEvent` into `DomainEvent`s
- The adapter pattern assumes a **live, bidirectional session**: create → stream → send_message → abort
- There's no concept of a **passive/import adapter** that just ingests pre-recorded data

---

## 5. SSE and Streaming

### SSE Endpoint (`backend/api/events.py`)

```
GET /api/events                  # global stream (all jobs)
GET /api/events?job_id={id}      # scoped to one job
```

SSE is **server-to-client only** — the server pushes events to browsers. No client-to-server push via SSE.

### SSE Manager (`backend/services/sse_manager.py`)

- `SSEConnection` — per-client, with an `asyncio.Queue(maxsize=1024)`
- `SSEManager` subscribes to the EventBus and pushes formatted SSE frames to all connections
- Supports replay via `Last-Event-ID` header (up to 500 events, 5-minute window)
- Maps `DomainEventKind` → SSE event types (some events are internal-only, not sent to clients)

### Key Architecture Detail

SSE is **outbound only**. An external tool **cannot** push events via SSE. It would need:
- A REST API endpoint to accept events (does not exist today), OR
- A new adapter that generates `SessionEvent`s from external input, OR
- Direct database writes + EventBus publishing

---

## 6. Metrics and Review

### Telemetry Summary Table (`job_telemetry_summary`)

Denormalized per-job metrics, upserted on every telemetry event:
- `sdk`, `model`, `repo`, `branch`, `status`
- `duration_ms`
- `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`
- `total_cost_usd`, `premium_requests`
- `llm_call_count`, `total_llm_duration_ms`
- `tool_call_count`, `tool_failure_count`, `total_tool_duration_ms`
- `compactions`, `tokens_compacted`
- `approval_count`, `approval_wait_ms`
- `agent_messages`, `operator_messages`
- `context_window_size`, `current_context_tokens`
- `total_turns`, `retry_count`, `retry_cost_usd`
- `file_read_count`, `file_write_count`, `unique_files_read`, `file_reread_count`
- `peak_turn_cost_usd`, `avg_turn_cost_usd`, `cost_first_half_usd`, `cost_second_half_usd`
- `diff_lines_added`, `diff_lines_removed`
- `agent_error_count`, `subagent_cost_usd`

### Telemetry Spans Table (`job_telemetry_spans`)

Per-call records (LLM calls and tool calls):
- `span_type` — `tool` or `llm`
- `name` — tool name or model name
- `started_at`, `duration_ms`
- `tool_category`, `tool_target`, `turn_number`, `execution_phase`
- `is_retry`, `retries_span_id`
- `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `cost_usd`
- `tool_args_json`, `result_size_bytes`, `error_kind`
- `turn_id`, `preceding_context`, `motivation_summary`, `edit_motivations`

### Cost Attribution (`job_cost_attribution`)

Per-job cost breakdown by dimension:
- Dimensions: `phase`, `tool_category`, `turn`, `model`
- Fields: `bucket`, `cost_usd`, `input_tokens`, `output_tokens`, `call_count`

### OTEL Telemetry (`backend/services/telemetry.py`)

In-process OpenTelemetry with:
- Counters: `tokens_input`, `tokens_output`, `cost_usd`, `compactions`, `messages`, `premium_requests`, `approvals`
- Histograms: `llm_duration`, `tool_duration`, `approval_wait`
- Gauges: `context_tokens`, `context_window`, `quota_used/entitlement/remaining`

### "Review" in CodePlane

`review` is a **job state**, not a code review feature:
- When an agent session completes cleanly, the job transitions to `review`
- The operator reviews the work, then resolves: `merged`, `pr_created`, or `discarded`
- Resolution transitions the job to `completed`

### Minimum Data for Useful Metrics

At minimum, to populate a useful job record, you need:
1. **Job metadata**: repo, prompt, model, SDK name
2. **Transcript entries**: role (agent/operator/tool_call), content, timestamps
3. **Token/cost data**: input_tokens, output_tokens, cost_usd per LLM call
4. **Tool call data**: tool_name, duration_ms, success/failure
5. **Duration**: job start and end time

Without transcript entries, the job would exist but be empty. Without token/cost data, analytics would show zeros.

---

## 7. Database Schema

### Key Tables

| Table | Purpose | Key Columns |
|---|---|---|
| `jobs` | Job records | id, repo, prompt, state, base_ref, branch, model, sdk, created_at, ... |
| `events` | Domain event log (append-only) | id (autoincrement), event_id, job_id, kind, timestamp, payload (JSON) |
| `approvals` | Approval requests | id, job_id, description, resolution |
| `artifacts` | Job artifacts (files) | id, job_id, name, type, mime_type, size_bytes, disk_path, phase |
| `diff_snapshots` | Git diff snapshots | id, job_id, diff_json |
| `steps` | SDK turn tracking | id, job_id, step_number, turn_id, intent, title, status, tool_count |
| `job_telemetry_summary` | Denormalized per-job metrics | job_id (PK), all metric columns |
| `job_telemetry_spans` | Individual LLM/tool calls | id, job_id, span_type, name, duration_ms, cost_usd, ... |
| `job_file_access_log` | Per-file read/write access | id, job_id, file_path, access_type |
| `job_cost_attribution` | Cost breakdown by dimension | id, job_id, dimension, bucket, cost_usd |
| `job_latency_attribution` | Latency breakdown | id, job_id, dimension, bucket, wall_clock_ms |
| `cost_observations` | Cross-job anomalies | id, category, severity, title, detail |
| `trail_nodes` | Agent audit trail (intent graph) | id, job_id, seq, kind, intent, step_id, files, ... |
| `policy_config` | Action policy settings | preset, batch_window_seconds |
| `path_rules`, `action_rules`, `cost_rules` | Policy rules | patterns, tiers, reasons |

### Repositories (persistence layer)

All in `backend/persistence/`:
- `job_repo.py` — `JobRepository`
- `event_repo.py` — `EventRepository`
- `artifact_repo.py` — `ArtifactRepository`
- `approval_repo.py` — `ApprovalRepository`
- `step_repo.py` — step tracking
- `telemetry_summary_repo.py` — `TelemetrySummaryRepository`
- `telemetry_spans_repo.py` — `TelemetrySpansRepository`
- `cost_attribution_repo.py` — `CostAttributionRepository`
- `latency_attribution_repo.py` — `LatencyAttributionRepository`
- `file_access_repo.py` — `FileAccessRepository`
- `trail_repo.py` — `TrailNodeRepository`
- `observations_repo.py` — cost observations
- `policy_repo.py` — action policy rules

---

## 8. SPEC.md — Extensibility

### Agent Adapter Architecture (SPEC §4.4)

SPEC explicitly defines the adapter pattern as the extension point:
- `AgentAdapterInterface` is the contract all SDK adapters must implement
- `AdapterRegistry` is a lazy-caching factory
- `RuntimeService` calls `registry.get_adapter(job.sdk)` — different jobs can use different SDKs concurrently
- Adding a new SDK requires implementing the interface and registering in `AdapterRegistry._create()`

### MCP Server (SPEC §26)

The MCP server mirrors the full UI functionality:
- Tool: `codeplane_job` (create, list, get, cancel, rerun, message)
- Tool: `codeplane_approval` (list, resolve)
- Tool: `codeplane_workspace` (list, read)
- Tool: `codeplane_artifact` (list, get)
- Tool: `codeplane_settings` (get, update)
- Tool: `codeplane_repo` (list, get, register, remove)
- Tool: `codeplane_health` (check, cleanup)

MCP notifications: `cpl/job_state_changed`, `cpl/approval_requested`, `cpl/job_completed`, `cpl/agent_message`

### What SPEC Does NOT Cover

- No mention of external session import/export
- No concept of a "passive" or "import" adapter
- No webhook or callback mechanism for external tools to push events
- No mention of GitHub CLI or Claude CLI integration
- No batch/offline ingestion pipeline

---

## 9. Feasibility Analysis — Extension Points for External Ingestion

### Option A: New "Import Adapter" (fits existing architecture)

Create a new `AgentSDK` value (e.g., `external` or `import`) with an adapter that:
- Accepts pre-recorded session data instead of spawning a live agent
- Generates `SessionEvent`s from the imported data
- Feeds them through the standard `RuntimeService` event processing pipeline

**Pros:** Reuses all existing infrastructure (event bus, persistence, SSE, telemetry, trail)
**Cons:** RuntimeService assumes a live session loop — significant refactoring needed

### Option B: New REST API Endpoint for Event Ingestion

Add an endpoint like `POST /api/jobs/{id}/ingest` that:
- Creates a job record in a special state
- Accepts a batch of events (transcript entries, tool calls, metrics)
- Publishes them through the EventBus
- Computes telemetry/attribution after ingestion

**Pros:** Clean separation from live execution; no adapter needed
**Cons:** Needs new API surface, new ingestion logic, duplicate of some RuntimeService work

### Option C: CLI Import Command

Add `cpl import` that:
- Reads a structured file (JSONL, JSON) containing session data
- Creates a job record + events directly in the database
- Runs telemetry/attribution computation afterward

**Pros:** Simple, no running server needed
**Cons:** Bypasses event bus, no live SSE updates, no real-time monitoring

### Option D: Hybrid — REST Import API + CLI Export from External Tools

External tools export sessions to a standard format (JSONL). CodePlane provides:
1. `POST /api/jobs/import` — accepts the export file, creates job + events
2. `cpl import <file>` — offline version of the same
3. A lightweight adapter for real-time streaming from external CLIs

### Critical Gaps to Fill

1. **No SDK-agnostic event format** — events are typed per SDK; need a common interchange format
2. **No import API** — must be built from scratch
3. **Telemetry computation is tightly coupled to the live session flow** — `RuntimeTelemetry` assumes it's called during an active session; would need a batch recomputation path (the `backfill-attribution` CLI command shows this is partially possible)
4. **Job ID generation requires LLM naming** — would need a simpler ID generation path for imports
5. **Worktree requirement** — jobs are assumed to have git worktrees; imported jobs may not

---

## Key Files Reference

- `backend/models/domain.py` — Job, JobSpec, JobState, SessionConfig, AgentSDK
- `backend/models/events.py` — DomainEvent, DomainEventKind, all payload TypedDicts
- `backend/models/api_schemas.py` — REST API request/response schemas (CreateJobRequest, JobResponse, etc.)
- `backend/models/db.py` — SQLAlchemy ORM models (JobRow, EventRow, telemetry tables, etc.)
- `backend/services/agent_adapter.py` — AgentAdapterInterface, SessionEvent, CompletionResult
- `backend/services/adapter_registry.py` — AdapterRegistry (factory)
- `backend/services/job_service.py` — JobService (creation, state transitions)
- `backend/services/runtime_service.py` — RuntimeService (execution lifecycle, event processing)
- `backend/services/runtime_telemetry.py` — RuntimeTelemetry (telemetry init/finalize)
- `backend/services/event_bus.py` — EventBus (in-process pub/sub)
- `backend/services/sse_manager.py` — SSEManager (SSE connection management, event formatting)
- `backend/persistence/event_repo.py` — EventRepository (event persistence)
- `backend/api/jobs.py` — REST route for job creation
- `backend/api/events.py` — SSE streaming endpoint
- `backend/mcp/server.py` — MCP orchestration server
- `backend/cli.py` — CLI commands (up, down, doctor, backfill-attribution)
- `SPEC.md` — Full product specification
