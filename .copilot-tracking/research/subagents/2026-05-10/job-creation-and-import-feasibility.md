# Job Creation & Import Feasibility Research

## Research Questions

1. How is a job created? What validation happens? What workspace setup is required?
2. Can any of this be bypassed for an imported (historical/non-live) job?
3. What are the exact schemas, DB models, and API contracts?
4. How does EventBus and SSE work? Is persistence coupled to live sessions?

---

## 1. Job Creation Flow

### API Entry Point: `POST /api/jobs` (backend/api/jobs.py L140-190)

The endpoint receives a `CreateJobRequest`, constructs a `JobSpec`, calls `svc.create_job()`, commits the session, then fire-and-forgets a background task `runtime_service.setup_and_start(job)`.

```
CreateJobRequest → JobSpec → svc.create_job() → session.commit() → background: setup_and_start()
```

### Validation Steps in `JobService.create_job()` (backend/services/job_service.py L280-370)

1. **Repo allowlist check** — `validate_repo(spec.repo)` resolves and checks against `config.repos` glob patterns. Raises `RepoNotAllowedError`.
2. **SDK-model compatibility** — `validate_sdk_model(resolved_sdk, spec.model)` checks SDK+model pairing. Raises `SDKModelMismatchError`.
3. **Base ref resolution** — If `base_ref` is None, calls `git_service.get_default_branch(resolved_repo)`. **Requires GitService and a real git repo.**
4. **Name generation** — Calls `_resolve_job_name()` which either uses pre-computed names from frontend or calls NamingService (LLM). Falls back to `task-{sha256[:8]}` hash.
5. **Collision checks** — Checks worktree_name against existing job IDs and worktree names.

### Hardcoded Assumptions for Live Sessions

- **`GitService` is REQUIRED** — `if self._git is None: raise ServiceInitError`. Job creation cannot proceed without a live git repo.
- **Initial state is always `preparing`** — Hardcoded: `initial_state = JobState.preparing`.
- **Background setup is assumed** — After create, `runtime_service.setup_and_start()` creates worktree, starts agent.

### `setup_workspace()` (backend/services/job_service.py L400-480)

Called as background task after job creation:
1. Validates job is in `preparing` state
2. Calls `git_service.create_worktree()` — **requires real git repo on disk**
4. Transitions `preparing → queued`
5. Publishes `job_setup_progress` events

### What Happens After Workspace Setup

`RuntimeService.setup_and_start()` does the full pipeline:
- `setup_workspace()` (preparing → queued)
- Starts agent session (queued → running)
- Streams agent events through the EventBus

---

## 2. Schemas

### `CreateJobRequest` (backend/models/api_schemas.py L39-64)

| Field | Type | Required | Default | Constraint |
|---|---|---|---|---|
| `repo` | `str` | **Yes** | — | — |
| `prompt` | `str` | **Yes** | — | — |
| `base_ref` | `str \| None` | No | None | — |
| `branch` | `str \| None` | No | None | — |
| `title` | `str \| None` | No | None | — |
| `description` | `str \| None` | No | None | — |
| `worktree_name` | `str \| None` | No | None | — |
| `preset` | `Preset \| None` | No | None | — |
| `model` | `str \| None` | No | None | — |
| `sdk` | `str \| None` | No | None | Validated against `AgentSDK` enum |
| `verify` | `bool \| None` | No | None | — |
| `self_review` | `bool \| None` | No | None | — |
| `max_turns` | `int \| None` | No | None | `ge=1, le=10` |
| `verify_prompt` | `str \| None` | No | None | `max_length=5000` |
| `self_review_prompt` | `str \| None` | No | None | `max_length=5000` |
| `session_token` | `str \| None` | No | None | `max_length=64` |

### `CamelModel` (backend/models/schemas/base.py L12-30)

Base Pydantic model with:
- `alias_generator=to_camel` — all fields serialize as camelCase
- `populate_by_name=True` — accepts both snake_case and camelCase
- `_ensure_utc_datetimes` model validator — ensures naive datetimes get UTC timezone

### `CreateJobResponse` (backend/models/api_schemas.py L168-175)

| Field | Type |
|---|---|
| `id` | `str` |
| `state` | `JobState` |
| `title` | `str \| None` |
| `branch` | `str \| None` |
| `worktree_path` | `str \| None` |
| `sdk` | `str` (default "copilot") |
| `created_at` | `datetime` |

### `JobResponse` (backend/models/api_schemas.py L178-260)

| Field | Type | Notes |
|---|---|---|
| `id` | `str` | — |
| `repo` | `str` | — |
| `prompt` | `str` | — |
| `title` | `str \| None` | — |
| `description` | `str \| None` | — |
| `state` | `JobState` | — |
| `base_ref` | `str` | — |
| `worktree_path` | `str \| None` | — |
| `branch` | `str \| None` | — |
| `preset` | `Preset \| None` | — |
| `created_at` | `datetime` | — |
| `updated_at` | `datetime` | — |
| `completed_at` | `datetime \| None` | — |
| `pr_url` | `str \| None` | — |
| `merge_status` | `GitMergeOutcome \| None` | — |
| `resolution` | `Resolution \| None` | — |
| `archived_at` | `datetime \| None` | — |
| `failure_reason` | `str \| None` | — |
| `progress_headline` | `str \| None` | Derived from events, not stored on job |
| `progress_summary` | `str \| None` | Derived from events, not stored on job |
| `model` | `str \| None` | — |
| `sdk` | `str` (default "copilot") | — |
| `worktree_name` | `str \| None` | — |
| `verify` | `bool \| None` | — |
| `self_review` | `bool \| None` | — |
| `max_turns` | `int \| None` | — |
| `verify_prompt` | `str \| None` | — |
| `self_review_prompt` | `str \| None` | — |
| `parent_job_id` | `str \| None` | — |
| `total_cost_usd` | `float \| None` | Injected at query time from telemetry |
| `total_tokens` | `int \| None` | Injected at query time from telemetry |
| `input_tokens` | `int \| None` | Injected at query time from telemetry |
| `output_tokens` | `int \| None` | Injected at query time from telemetry |

Has `from_domain(job, **overrides)` classmethod for construction.

### `JobSpec` Dataclass (backend/models/domain.py L600-630)

| Field | Type | Default |
|---|---|---|
| `repo` | `str` | **required** |
| `prompt` | `str` | **required** |
| `base_ref` | `str \| None` | None |
| `branch` | `str \| None` | None |
| `title` | `str \| None` | None |
| `description` | `str \| None` | None |
| `worktree_name` | `str \| None` | None |
| `preset` | `Preset` | `Preset.supervised` |
| `model` | `str \| None` | None |
| `sdk` | `str \| None` | None |
| `verify` | `bool \| None` | None |
| `self_review` | `bool \| None` | None |
| `max_turns` | `int \| None` | None |
| `verify_prompt` | `str \| None` | None |
| `self_review_prompt` | `str \| None` | None |
| `parent_job_id` | `str \| None` | None |
| `parent_job_context` | `str \| None` | None |

---

## 3. Database Models

### `JobRow` (backend/models/db.py L20-68)

All columns:

| Column | SQL Type | Nullable | Default | Notes |
|---|---|---|---|---|
| `id` | String PK | No | — | Also the worktree_name |
| `repo` | String | No | — | Resolved absolute path |
| `prompt` | Text | No | — | — |
| `state` | String | No | — | JobState enum value |
| `base_ref` | String | No | — | — |
| `branch` | String | Yes | — | — |
| `worktree_path` | String | Yes | — | Absolute path to git worktree |
| `session_id` | String | Yes | — | SDK session ID |
| `pr_url` | String | Yes | — | — |
| `merge_status` | String | Yes | — | — |
| `resolution` | String | Yes | — | — |
| `archived_at` | TZDateTime | Yes | — | — |
| `title` | String | Yes | — | — |
| `description` | Text | Yes | — | — |
| `worktree_name` | String | Yes | — | — |
| `permission_mode` | String | No | "full_auto" | Legacy; now uses preset |
| `preset` | String | No | server_default "supervised" | — |
| `session_count` | Integer | No | 1 | — |
| `sdk_session_id` | String | Yes | — | — |
| `model` | String | Yes | — | — |
| `failure_reason` | String | Yes | — | — |
| `sdk` | String | No | "copilot" | — |
| `verify` | Boolean | Yes | — | — |
| `self_review` | Boolean | Yes | — | — |
| `max_turns` | Integer | Yes | — | — |
| `verify_prompt` | Text | Yes | — | — |
| `self_review_prompt` | Text | Yes | — | — |
| `created_at` | TZDateTime | No | — | — |
| `updated_at` | TZDateTime | No | — | — |
| `completed_at` | TZDateTime | Yes | — | — |
| `version` | Integer | No | 1 | Optimistic locking |
| `parent_job_id` | String FK→jobs.id | Yes | — | — |
| `story_text` | Text | Yes | — | — |
| `review_story_json` | Text | Yes | — | — |
| `review_story_hash` | String(64) | Yes | — | — |
| `structural_cycle_count` | Integer | Yes | — | — |
| `structural_changes_touch_tests` | Boolean | Yes | — | — |
| `structural_change_count` | Integer | Yes | — | — |
| `structural_merge_confidence` | String(10) | Yes | — | — |
| `trail_state_snapshot` | Text | Yes | — | JSON blob |

### `EventRow` (backend/models/db.py L71-80)

| Column | SQL Type | Nullable | Notes |
|---|---|---|---|
| `id` | Integer PK | No | Autoincrement, used as SSE Last-Event-ID |
| `event_id` | String UNIQUE | No | UUID |
| `job_id` | String FK→jobs.id | No | — |
| `kind` | String | No | DomainEventKind value |
| `timestamp` | TZDateTime | No | — |
| `payload` | Text (JSON) | No | Event-specific data |

Index on `job_id`.

### `JobTelemetrySummaryRow` (backend/models/db.py L150+)

Denormalized per-job telemetry with ~35 numeric columns (tokens, costs, tool counts, etc.). Keyed by `job_id` FK.

### Other Tables

- `ApprovalRow` — individual approval requests per job
- `ArtifactRow` — artifacts (diffs, logs, summaries) per job
- `DiffSnapshotRow` — periodic diff snapshots per job
- `StepRow` — individual agent steps/turns per job

---

## 4. EventBus (backend/services/event_bus.py)

**Purely in-process async pub/sub.** No persistence, no queues, no external transport.

- Maintains a `list[Subscriber]` where `Subscriber = Callable[[DomainEvent], Coroutine]`
- `publish()` fans out to all subscribers via `asyncio.gather()` with `return_exceptions=True`
- Subscriber errors are logged but don't block other subscribers
- `subscribe(handler)` / `unsubscribe(handler)` for registration

### Event Persistence

Events are persisted by a separate `PersistenceSubscriber` that subscribes to the EventBus and writes to the `events` table. The EventBus itself is purely transient.

### `DomainEvent` (backend/models/events.py)

```python
@dataclass
class DomainEvent:
    event_id: str          # UUID
    job_id: str
    timestamp: datetime
    kind: DomainEventKind  # ~35 event types
    payload: dict[str, Any]
```

---

## 5. SSE Streaming (backend/api/events.py + backend/services/sse_manager.py)

### Endpoint: `GET /api/events`

- Optional `job_id` query param scopes to single job
- `Last-Event-ID` header or query param for reconnection replay
- Returns `StreamingResponse` with `text/event-stream` media type

### Connection Model

`SSEConnection` holds a per-client `asyncio.Queue`. `SSEManager`:
- Subscribes to the EventBus
- Translates `DomainEvent` → SSE-formatted strings
- Pushes to each registered connection's queue (filtered by job_id if scoped)
- Sends heartbeats every 5s on idle

### Reconnection Replay

- Client sends `Last-Event-ID` (the integer `EventRow.id`)
- Backend replays from DB: `EventRepository.list_since(numeric_id)`
- Bounded: max 500 events, max 5 minutes age
- If gap too large: sends `snapshot` event with full state of all active jobs

### SSE Event Mapping

Domain events map to SSE event types via `_SSE_EVENT_TYPE` dict. Some events are internal-only (mapped to `None`). High-frequency events (`log_line`, `transcript_update`, `diff_update`, `session_heartbeat`) are suppressed in selective mode when >20 active jobs.

---

## 6. Job States & Lifecycle (SPEC.md §6.1, §12)

### States (backend/models/domain.py L16-24)

`preparing` → `queued` → `running` → `review` → `completed`

With branches to: `waiting_for_approval`, `failed`, `canceled`

Terminal states (`completed`, `failed`, `canceled`) can transition back to `running` for resume.

### Valid Transitions (backend/models/domain.py L97-130)

```
None → {preparing, running, queued}
preparing → {queued, failed, canceled}
queued → {running, canceled}
running → {waiting_for_approval, review, failed, canceled}
waiting_for_approval → {running, failed, canceled}
review → {running, completed, canceled}
completed → {running}
failed → {running}
canceled → {running}
```

---

## 7. Import/Export: Current State

### No import/export functionality exists.

- No CLI command for importing jobs
- No API endpoint for importing historical data
- No mention of "import" or "export" for jobs in SPEC.md
- The SPEC mentions MCP orchestration (§26) but only for creating *live* jobs
- Archiving exists (hide from Kanban) but is just `archived_at` timestamp

### Hardcoded Live-Session Assumptions That Block Import

1. **GitService required** — `create_job()` raises `ServiceInitError` if no GitService
2. **Repo must be in allowlist** — `validate_repo()` checks against registered repos; an imported job's repo path won't exist
3. **Worktree creation required** — `setup_workspace()` calls `git_service.create_worktree()` on a real git repo
4. **State machine starts at `preparing`** — Cannot insert a job directly into `completed` or `review`
5. **Background agent session assumed** — `runtime_service.setup_and_start()` fires after creation
6. **Events come from live agent** — All transcript, diff, telemetry events are generated by a running agent session in real-time
7. **Job ID = worktree_name** — The job ID is derived from the worktree name, which is generated for live workspace management

### What Would Need to Change for Import

To import a historical/completed job:

1. **New endpoint or service method** — bypass `create_job()` entirely; insert `JobRow` directly into DB with all fields pre-populated
2. **Skip workspace setup** — no worktree creation, no agent session
3. **Allow direct state insertion** — insert job in any state (e.g., `completed`, `review`)
4. **Bypass repo validation** — imported repo path may not exist locally
5. **Bulk insert events** — insert `EventRow` records directly for transcript, diff, telemetry
6. **Populate telemetry summary** — insert/update `JobTelemetrySummaryRow` with imported cost/token data
7. **Emit SSE notification** — optionally publish a synthetic event so the frontend picks up the new job

---

## 8. SPEC.md Key Sections

### §6.1 Job Lifecycle
Full lifecycle: validate → create worktree → persist → start agent → stream events → review → resolve.

### §12 Job States
8 states with explicit transition table. `preparing` is the entry point for all new jobs.

### §12.3 Rerun
Creates a NEW job record copying repo/prompt/base_ref. Original is not mutated.

### §26 MCP Orchestration
External agents can create/manage jobs via MCP tools, but all go through the same `create_job()` pipeline — no bypass for imported data.

### No extensibility hooks
No plugin system, no custom event types, no import/export CLI commands.

---

## Follow-on Questions

- Is there a `JobRepository.create()` method that does raw insertion, or does it also enforce constraints?
- What does `RuntimeService.setup_and_start()` do exactly — could we skip it entirely for imports?
- How is `JobTelemetrySummaryRow` populated — is it only via telemetry events or can it be seeded directly?
