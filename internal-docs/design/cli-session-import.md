---
title: "CLI Session Import: Real-Time Observation and Steering of Native CLI Sessions"
status: draft
---

# CLI Session Import

> Users run `copilot` or `claude` in their own terminal. CodePlane observes
> every event in real time, builds the same job view as a managed session, and
> can send messages back to the running agent.

---

## 1. Problem

CodePlane currently requires launching agent sessions through its own
orchestration layer (`RuntimeService` → `AdapterRegistry` → SDK adapter).
Users who prefer running the Copilot or Claude CLIs natively get none of
CodePlane's visibility — no transcript, no timeline, no metrics, no operator
messaging.

The goal is **full parity**: an imported CLI session is
indistinguishable from a managed session in the UI. Same state machine,
same review flow, same merge/PR/discard resolution actions. CodePlane
captures the repo path and branch from session start data and operates
on the user's working directory directly.

---

## 2. Data Sources (Empirically Verified)

### 2.1 Copilot CLI (v1.0.44)

| Channel | Mode | Data |
|---------|------|------|
| OTEL File Exporter | Real-time JSONL (no batching) | Single global file for ALL sessions (not per-session). Sessions distinguished by `gen_ai.conversation_id`. Spans: `invoke_agent`, `chat <model>`, `execute_tool`. Attributes: `gen_ai.usage.input_tokens`, `output_tokens`, `github.copilot.cost`, `gen_ai.request.model`, `gen_ai.tool.call.arguments`, `gen_ai.tool.call.result`, `github.copilot.turn_id`. **Note**: No `cwd` or workspace path attribute exists in any span (empirically verified) — see §13.5 for workarounds |
| GitHub Steer API | Cloud relay, 3s poll | `POST /agents/tasks/{taskId}/steer` — user messages, permission responses, mode switch, abort |

**User setup** (one-time, in shell profile):

```bash
export COPILOT_OTEL_FILE_EXPORTER_PATH=$HOME/.copilot/codeplane-otel.jsonl
export OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true
```

Launch sessions with `copilot --remote` to enable steering.

### 2.2 Claude CLI (v2.1.81+)

| Channel | Mode | Data |
|---------|------|------|
| HTTP Hooks | Real-time synchronous POST | 28 event types including `SessionEnd`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop`, `SubagentStart/Stop`, `TaskCreated/Completed`, `WorktreeCreate/Remove`, etc. Payloads include `session_id`, `cwd`, `tool_name`, `tool_input`, `tool_response`, `duration_ms`. **Note:** `SessionStart` and `Setup` only support `command` and `mcp_tool` hooks — NOT `http` (Claude Code restriction). |
| Hook Responses | Synchronous return | `Stop` → `{"decision":"block","reason":"..."}` injects operator messages. `PreToolUse` → `{"permissionDecision":"deny"}` blocks tools. `PostToolUse` → `{"additionalContext":"..."}` injects context |
| OTEL OTLP | Standard OTLP protocol (gRPC or HTTP) | Metrics: `claude_code.cost.usage` (USD), `claude_code.token.usage` (tokens), `claude_code.lines_of_code.count`. Events via logs protocol: `claude_code.user_prompt`, `claude_code.tool_result`, `claude_code.api_request`. Standard attributes include `session.id`. Requires an OTLP receiver endpoint — see §13.8 |

**User setup** (one-time):

`~/.claude/settings.json`:
```json
{
  "hooks": {
    "PostToolUse":       [{"type": "http", "url": "http://localhost:9418/api/hooks/claude"}],
    "UserPromptSubmit":  [{"type": "http", "url": "http://localhost:9418/api/hooks/claude"}],
    "Stop":              [{"type": "http", "url": "http://localhost:9418/api/hooks/claude"}],
    "SessionStart":      [{"type": "command", "command": "cpl hook"}],
    "SessionEnd":        [{"type": "http", "url": "http://localhost:9418/api/hooks/claude"}],
    "PreToolUse":        [{"type": "http", "url": "http://localhost:9418/api/hooks/claude"}],
    "SubagentStart":     [{"type": "http", "url": "http://localhost:9418/api/hooks/claude"}],
    "SubagentStop":      [{"type": "http", "url": "http://localhost:9418/api/hooks/claude"}]
  }
}
```

Plus OTEL (requires an OTLP receiver — see §13.8):
```bash
export CLAUDE_CODE_ENABLE_TELEMETRY=1
export OTEL_METRICS_EXPORTER=otlp
export OTEL_LOGS_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_PROTOCOL=http/json
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:9418
```

---

## 3. Architecture

```
┌──────────────────────┐     ┌────────────────────────┐
│  Copilot OTEL File   │     │  Claude HTTP Hooks      │
│  Watcher (tail JSONL)│     │  (FastAPI POST handler)  │
└──────────┬───────────┘     └──────────┬─────────────┘
           │                            │
           ▼                            ▼
    ┌──────────────────────────────────────────┐
    │             IngestService                │
    │  ┌────────────────┐ ┌──────────────────┐ │
    │  │ OtelSpanMapper │ │ ClaudeHookMapper │ │
    │  │ (JSONL spans → │ │ (hook payloads → │ │
    │  │  DomainEvents) │ │  DomainEvents)   │ │
    │  └────────────────┘ └──────────────────┘ │
    └──────────────────┬───────────────────────┘
                       │
                       ▼
    ┌──────────────────────────────────────────┐
    │     Existing EventBus + SSE Pipeline     │
    │  (publishes to SSEManager, persists to   │
    │   EventRepository — unchanged)           │
    └──────────────────┬───────────────────────┘
                       │
                       ▼
    ┌──────────────────────────────────────────┐
    │   Frontend (unchanged except source      │
    │   badge on job cards)                    │
    └──────────────────────────────────────────┘
```

### Steering (messages TO the agent)

```
CodePlane UI → operator types message
       │
       ├─── Claude: store pending message → next Stop hook fires →
       │    return {"decision":"block","reason":"<message>"} →
       │    Claude reads it as next instruction
       │
       └─── Copilot: POST /agents/tasks/{taskId}/steer →
            {"content":"<message>","type":"user"} →
            CommandPoller picks up within 3s →
            injected as session.send()
```

---

## 4. Domain Model Changes

### 4.1 `backend/models/domain.py`

**`JobSource` enum** (new):

```python
class JobSource(StrEnum):
    """How the job was created."""
    managed = "managed"          # CodePlane launched the agent
    copilot_cli = "copilot_cli"  # Imported from native Copilot CLI
    claude_cli = "claude_cli"    # Imported from native Claude CLI
```

**`Job` dataclass** — add two fields:

```python
@dataclass
class Job:
    ...
    source: str = "managed"           # JobSource value
    external_session_id: str | None = None  # CLI's own session/task ID
```

**State machine** — imported jobs use the same states as managed jobs.
`IngestService` populates `repo`, `branch`, `base_ref`, and
`worktree_path` (set to the CLI's `cwd`) at session start, so the
existing review and resolution machinery works unmodified.

```
None → running → review → completed   (normal clean exit)
                        → running      (operator sends follow-up)
              → failed                 (session errored)
              → canceled               (user aborted)
```

`None → running` is already valid in `_VALID_TRANSITIONS`. No state
machine changes needed.

**How fields are populated**:

| Field | Managed session | Imported session |
|-------|----------------|------------------|
| `repo` | User selects in UI | Detected from `cwd` (`git rev-parse --show-toplevel`) |
| `branch` | CodePlane creates | Detected from `cwd` (`git rev-parse --abbrev-ref HEAD`) |
| `base_ref` | User selects in UI | Detected (`git symbolic-ref refs/remotes/origin/HEAD`, fallback `main`) |
| `worktree_path` | CodePlane creates worktree | CLI's `cwd` — from Claude hook `SessionStart.cwd` (confirmed: `cwd` is a common input field on every hook event). For Copilot: `cwd` is **not** present in OTEL spans (empirically verified v1.0.44). Must be inferred from file paths in the first `execute_tool` span's `gen_ai.tool.call.arguments`, or from `OTEL_RESOURCE_ATTRIBUTES=process.cwd=<path>` (requires adding to user setup) |
| `prompt` | User enters in UI | First user message from session (hook `UserPromptSubmit` or OTEL content capture) |

Because `repo`, `branch`, `base_ref`, and `worktree_path` are all
populated, `DiffService` and PR creation work unchanged. `MergeService`
requires import-aware handling — see below.

**No destructive operations for imported sessions**: When `source !=
"managed"`, CodePlane did not create the working directory or the
branch, so it must never delete them. This applies to ALL resolution
paths, not just discard:

- **Discard**: Mark job as discarded. Do NOT call `remove_worktree`
  or `git branch -D`. Leave the filesystem as-is.
- **Merge / Smart Merge**: The user's CLI session already committed
  changes to their branch. CodePlane pushes the branch and does a
  remote merge (fast-forward or merge commit via the forge API / `git
  push`). It does NOT checkout/stash/merge in the user's working
  directory — `_preserved_worktree` and `_checkout_and_merge` operate
  on `repo_path` which for imported sessions IS the user's live
  checkout. After merge, do NOT call `_post_merge_cleanup` (no
  worktree removal, no branch deletion).
- **Create PR**: Push the branch to origin, create PR. Do NOT call
  `_cleanup_worktree_only`. Branch stays — the user is still on it.
- **On default branch (branch == base_ref)**: There is nothing to
  merge — the changes are already where they belong. Skip merge
  entirely, transition to `completed` with resolution `merged`.

Implementation: `MergeService.resolve_job()` checks `job.source` and
routes to a new `_resolve_imported()` method that handles push +
remote merge without touching the local working directory.

The existing `_auto_merge` (post-session) path also needs the same
guard: when `IngestService._finalize_session()` triggers
`MergeService.try_merge_back()`, it must use the imported-session
path.

### 4.2 `backend/models/db.py` — `JobRow`

Add columns:

```python
source: Mapped[str] = mapped_column(String, nullable=False, default="managed", server_default="managed")
external_session_id: Mapped[str | None] = mapped_column(String, nullable=True)
```

### 4.3 `alembic/versions/` — new migration

```python
"""Add source and external_session_id to jobs."""

def upgrade():
    op.add_column("jobs", sa.Column("source", sa.String(), nullable=False, server_default="managed"))
    op.add_column("jobs", sa.Column("external_session_id", sa.String(), nullable=True))

def downgrade():
    op.drop_column("jobs", "external_session_id")
    op.drop_column("jobs", "source")
```

### 4.4 `backend/models/api_schemas.py`

**`JobResponse`** — add:

```python
class JobResponse(CamelModel):
    ...
    source: str = "managed"
    external_session_id: str | None = None
```

**`JobResponse.from_domain()`** — wire the two new fields.

### 4.5 `backend/models/events.py`

No new `DomainEventKind` values needed. Imported sessions emit the same
event types as managed sessions:

- `transcript_updated` — tool calls and agent messages
- `telemetry_updated` — tokens, cost, model
- `step_started` / `step_completed` — timeline activities
- `job_state_changed` — session lifecycle
- `diff_updated` — git changes (via existing `DiffService` git watcher)

### 4.6 `backend/persistence/job_repo.py`

Wire `source` and `external_session_id` in `_to_domain()` and `create()`.

---

## 5. New Backend Services

### 5.1 `backend/services/ingest_service.py` (new file)

Central coordinator for imported sessions. Responsibilities:

- On first event, detect git metadata from the CLI's working directory
  (`repo`, `branch`, `base_ref` via `GitService`)
- Auto-register the repo in CodePlane's allowlist if not already present
  (imported sessions bypass the manual "add repo" flow)

```python
class IngestService:
    def __init__(
        self,
        event_bus: EventBus,
        session_factory: async_sessionmaker[AsyncSession],
        config: CPLConfig,
        git_service: GitService | None = None,
        diff_service: DiffService | None = None,
        merge_service: MergeService | None = None,
        trail_service: TrailService | None = None,
        job_service: JobService | None = None,
    ) -> None: ...

    async def ingest_otel_span(self, span: dict) -> None:
        """Process a single OTEL JSONL span from the Copilot file watcher."""

    async def ingest_claude_hook(self, event_type: str, payload: dict) -> dict:
        """Process a Claude hook POST. Returns the hook response body."""

    async def send_operator_message(self, job_id: str, message: str) -> None:
        """Queue an operator message for delivery to the agent."""

    async def abort_session(self, job_id: str) -> None:
        """Abort the external session (steer API for Copilot, hook block for Claude)."""

    async def _create_job_from_session(self, cwd: str, source: str, session_id: str) -> Job:
        """Detect git state from cwd, create Job record with full metadata.

        1. git rev-parse --show-toplevel → repo path
        2. Auto-register repo in allowlist if missing
        3. Detect branch, base_ref
        4. Create and persist Job record (returns immediately)
           → on completion: register_worktree(repo_name, cwd)
           If it never finishes, the job works fine without it.
        """

    async def _finalize_session(self, job_id: str) -> None:
        """Transition to review, run post-session merge flow via MergeService."""
```

### 5.2 `backend/services/otel_file_watcher.py` (new file)

Async file tailer for the Copilot OTEL JSONL file.

```python
class OtelFileWatcher:
    """Tails COPILOT_OTEL_FILE_EXPORTER_PATH, parses JSONL, routes to IngestService."""

    def __init__(self, path: str, ingest_service: IngestService) -> None: ...

    async def start(self) -> None:
        """Begin tailing. Called from lifespan startup."""

    async def stop(self) -> None:
        """Stop tailing. Called from lifespan shutdown."""
```

Implementation: `asyncio` loop with `os.stat()` polling for file size
changes, reads new bytes, splits on newlines, parses JSON, calls
`ingest_service.ingest_otel_span()`.

### 5.3 `backend/services/copilot_steer.py` (new file)

Thin wrapper around the GitHub steer API.

```python
class CopilotSteerClient:
    """Sends steering commands to a Copilot CLI --remote session."""

    def __init__(self, github_token: str) -> None: ...

    async def send_message(self, task_id: str, message: str) -> None:
        """POST /agents/tasks/{task_id}/steer with type=user."""

    async def abort(self, task_id: str) -> None:
        """POST /agents/tasks/{task_id}/steer with type=abort."""
```

API endpoint: `https://api.enterprise.githubcopilot.com/agents/tasks/{taskId}/steer`

Auth: Bearer token from the user's GitHub/Copilot token (same token
CodePlane already uses).

---

## 6. New API Routes

### 6.1 `backend/api/hooks.py` (new file)

Claude hook receiver endpoint:

```python
router = APIRouter(tags=["hooks"])

@router.post("/hooks/claude")
async def claude_hook(
    request: Request,
    ingest: FromDishka[IngestService],
) -> JSONResponse:
    """Receive Claude CLI hook events. Returns hook response for steering."""
    body = await request.json()
    event_type = body.get("hookEventName", "")
    response_body = await ingest.ingest_claude_hook(event_type, body)
    return JSONResponse(content=response_body)
```

### 6.2 Existing routes — `backend/api/approvals.py` and `backend/api/jobs.py`

No new router file needed. The existing endpoints gain a conditional
branch based on `job.source`:

- `POST /jobs/{job_id}/messages` (plural, in `approvals.py`) — when
  `source != "managed"`, delegate to `IngestService.send_operator_message()`
  instead of `RuntimeService`.
- `POST /jobs/{job_id}/cancel` (in `jobs.py`) — when `source != "managed"`,
  delegate to `IngestService.abort_session()` instead of `RuntimeService`.

---

## 7. DI & Lifecycle Changes

### 7.1 `backend/di.py`

Register new providers:

```python
# IngestService
IngestService(
    event_bus=event_bus,
    session_factory=session_factory,
    config=config,
    git_service=git_service,
    diff_service=diff_service,
    merge_service=merge_service,
    trail_service=trail_service,
    job_service=job_service,
)

# OtelFileWatcher (only if COPILOT_OTEL_FILE_EXPORTER_PATH is set)
OtelFileWatcher(path=otel_path, ingest_service=ingest_service)

# CopilotSteerClient (only if GitHub token is available)
CopilotSteerClient(github_token=token)
```

### 7.2 `backend/lifespan.py`

Add to startup:

```python
if config.copilot_otel_path:
    watcher = container.get(OtelFileWatcher)
    await watcher.start()
```

Add to shutdown:

```python
if watcher:
    await watcher.stop()
```

### 7.3 `backend/config.py` — `CPLConfig`

Add optional config field:

```python
copilot_otel_path: str | None = None  # COPILOT_OTEL_FILE_EXPORTER_PATH
```

Read from environment at config load time.

### 7.4 `backend/app_factory.py`

Mount the new routers:

```python
from backend.api.hooks import router as hooks_router
from backend.api.ingest import router as ingest_router

app.include_router(hooks_router, prefix="/api")
app.include_router(ingest_router, prefix="/api")
```

---

## 8. Frontend Changes

### 8.1 `frontend/src/store/types.ts` — `JobSummary`

Add field:

```typescript
export interface JobSummary {
  ...
  source?: string;            // "managed" | "copilot_cli" | "claude_cli"
}
```

### 8.2 `frontend/src/api/types.ts`

No change — types are generated from OpenAPI schema. Run `openapi-typescript`
after backend changes.

### 8.3 `frontend/src/components/JobHeaderCard.tsx`

- Show a "CLI Import" badge next to the SDK badge when `source !== "managed"`
- Show the external session ID as a tooltip

### 8.4 `frontend/src/components/JobDetailScreen.tsx`

**Full parity — no conditional hiding of actions.** All buttons work:

- Merge / Smart Merge — pushes branch and does remote merge (does NOT
  checkout/stash in the user's working directory — see §4.1)
- Create PR — pushes branch to origin, creates PR (does NOT remove
  worktree or delete branch)
- Discard — marks job as discarded, leaves filesystem untouched
- Continue / Send message — routes through `IngestService` for steering
- Cancel/Abort — routes through `IngestService`

The operator message input in `CuratedFeed` / `TranscriptPanel` works
unchanged — the existing `/jobs/{job_id}/message` endpoint delegates to
`IngestService` when `source != "managed"`.

### 8.5 `frontend/src/components/JobListScreen.tsx` (or `JobCard`)

- Show a "CLI" indicator on imported job cards
- Filter/sort: imported jobs appear in the normal job list

### 8.6 No SSE changes

The frontend's SSE handler table (`sseHandlers`) and the `useSSE` hook
require zero changes. Imported sessions emit the same event types as
managed sessions.

---

## 9. Span-to-DomainEvent Mapping

### 9.1 Copilot OTEL Spans → DomainEvents

| OTEL Span Type | DomainEvent | Payload mapping |
|---------------|-------------|-----------------|
| `execute_tool` | `transcript_updated` | `tool_name` = span name suffix, `tool_args` = `gen_ai.tool.call.arguments`, `tool_result` = `gen_ai.tool.call.result`, `role` = `tool`, `turn_id` = `github.copilot.turn_id` |
| `chat <model>` | `transcript_updated` | `content` = captured message content, `role` = `assistant`, `turn_id` = `github.copilot.turn_id` |
| `chat <model>` | `telemetry_updated` | `input_tokens` = `gen_ai.usage.input_tokens`, `output_tokens` = `gen_ai.usage.output_tokens`, `total_cost_usd` = `github.copilot.cost`, `model` = `gen_ai.response.model` |
| `invoke_agent` (end) | `job_review` + `job_state_changed` (→ review) | Session ended cleanly — triggers post-session merge flow |
| Error on any span | `job_failed` | `reason` from span status |

### 9.2 Claude Hooks → DomainEvents

| Hook Event | DomainEvent | Payload mapping |
|-----------|-------------|-----------------|
| `SessionStart` | `job_created` + `job_state_changed` (→ running) | `session_id`, `cwd` |
| `UserPromptSubmit` | `transcript_updated` | `role` = `user`, `content` = input |
| `PostToolUse` | `transcript_updated` | `role` = `tool`, `tool_name`, `tool_args` = `tool_input`, `tool_result` = `tool_response`, `duration_ms` |
| `Stop` | (turn boundary) | Emit pending `step_completed`, check for pending operator messages |
| `SessionEnd` | `job_review` + `job_state_changed` (→ review) | Session over — triggers post-session merge flow |
| `SubagentStart` | `step_started` | Subagent as a new activity section |
| `TaskCreated` | `agent_plan_updated` | Map to plan steps |

OTEL OTLP metrics (`claude_code.cost.usage`, `claude_code.token.usage`)
→ `telemetry_updated` events, accumulated per job.

---

## 10. What Doesn't Change

| Component | Why unchanged |
|-----------|--------------|
| `EventBus` / `SSEManager` | Imported sessions produce standard `DomainEvent` objects |
| `EventRepository` | Persists events identically regardless of source |
| `TrailService` | Consumes `DomainEvent`s — source-agnostic |
| `StepTracker` | Driven by `DomainEvent`s — source-agnostic |
| `SidecarSessionManager` | Not applicable (no managed sidecar sessions) |
| `DiffService` | Works — `worktree_path` points to CLI's `cwd`. For Copilot: depends on resolving `cwd` (see §13.5) |
| `MergeService` | Needs a new `_resolve_imported()` path. Existing local merge/checkout/stash logic assumes CodePlane owns the worktree — for imported sessions it must push + remote-merge instead. See §4.1 "No destructive operations" |
| Frontend SSE handlers | All existing handlers work unchanged |
| Frontend store shape | Only additive field (`source`) |

---

## 11. Capability Matrix

| Feature | Managed | Copilot CLI Import | Claude CLI Import |
|---------|---------|-------------------|-------------------|
| Live transcript | Yes | Yes (OTEL spans) | Yes (hooks) |
| Activity timeline | Yes | Yes | Yes |
| Token/cost metrics | Yes | Yes | Yes |
| Model identification | Yes | Yes | Yes |
| Tool call detail | Yes | Yes (content capture ON) | Yes |
| Diff view | Yes | Yes (git watch on cwd) | Yes (git watch on cwd) |
| Operator messages | Yes | Yes (steer API, ~3s latency) | Yes (hook response, 0ms) |
| Tool blocking | Yes | No | Yes (PreToolUse deny) |
| Cancel/abort | Yes | Yes (steer abort) | Yes (hook block) |
| Streaming text | Yes | No (complete messages) | No (complete messages) |
| Merge/resolve | Yes (local) | Yes (push + remote) | Yes (push + remote) |
| PR creation | Yes | Yes | Yes |
| Approval flow | Yes | No | Yes (PreToolUse hook) |
| Worktree cleanup on discard | Yes (delete) | No (user's directory) | No (user's directory) |

---

## 12. File Change Summary

### New files

| File | Purpose |
|------|---------|
| `backend/services/ingest_service.py` | Central ingestion coordinator |
| `backend/services/otel_file_watcher.py` | Async JSONL file tailer for Copilot OTEL |
| `backend/services/copilot_steer.py` | GitHub steer API client |
| `backend/api/hooks.py` | Claude hook receiver route |
| `alembic/versions/NNNN_add_job_source.py` | DB migration |

### Modified files

| File | Change |
|------|--------|
| `backend/models/domain.py` | Add `JobSource` enum, `source` + `external_session_id` fields to `Job` |
| `backend/models/db.py` | Add `source` + `external_session_id` columns to `JobRow` |
| `backend/models/api_schemas.py` | Add `source`, `external_session_id` to `JobResponse` |
| `backend/persistence/job_repo.py` | Wire new fields in `_to_domain()` and `create()` |
| `backend/config.py` | Add `copilot_otel_path` config field |
| `backend/di.py` | Register `IngestService`, `OtelFileWatcher`, `CopilotSteerClient` |
| `backend/lifespan.py` | Start/stop `OtelFileWatcher` |
| `backend/app_factory.py` | Mount hooks router |
| `backend/api/approvals.py` | Delegate to `IngestService` for imported jobs when sending operator messages |
| `backend/api/jobs.py` | Route operator message/cancel to `IngestService` for imported jobs. Skip worktree/branch deletion on discard when `source != "managed"` |
| `backend/cli.py` | Add `cpl hook` subcommand — reads hook JSON from stdin, POSTs to `/api/hooks/claude`. Required because `SessionStart` only supports `command` hooks, not `http` |
| `backend/services/merge_service/_service.py` | Add `_resolve_imported()` path: push + remote merge (no local checkout/stash). Guard `_post_merge_cleanup` and `_discard` against imported sessions |
| `frontend/src/store/types.ts` | Add `source` to `JobSummary` |
| `frontend/src/components/JobHeaderCard.tsx` | CLI import badge |
| `frontend/src/components/JobDetailScreen.tsx` | No functional changes (full parity) — all action buttons work for imported sessions |

### Unchanged files (confirmed)

| File | Why |
|------|-----|
| `backend/services/runtime_service.py` | Only manages CodePlane-launched sessions |
| `backend/services/agent_adapter.py` | Interface for managed adapters only |
| `backend/services/base_adapter.py` | Base class for managed adapters only |
| `backend/services/adapter_registry.py` | Only creates managed adapters |
| `backend/services/event_bus.py` | Generic — accepts any `DomainEvent` |
| `backend/services/diff_service.py` | `calculate_diff()` calls `git add -N` on untracked files — modifies the user's index for imported sessions. Consider suppressing intent-to-add for `source != "managed"` or documenting this as expected behavior |
| `backend/services/sse_manager.py` | Generic — routes any SSE event |
| `backend/api/events.py` | SSE endpoint is source-agnostic |
| `backend/models/events.py` | No new event kinds needed |
| `frontend/src/hooks/useSSE.ts` | SSE client is source-agnostic |
| `frontend/src/store/sseHandlers.ts` | Handler table is source-agnostic |

---

## 13. Open Questions

1. **OTEL file is global, not per-session**: The Copilot OTEL file
   exporter writes ALL sessions to a single JSONL file (empirically
   verified v1.0.44). Sessions are interleaved and distinguished by
   `gen_ai.conversation_id`. The `OtelFileWatcher` must demux spans
   by this attribute, maintaining a map of `conversation_id → job_id`.
   New conversation IDs trigger job creation; existing IDs route events
   to the corresponding job. On Copilot restart, the same file continues
   growing with new conversation IDs. The watcher should NOT attempt to
   infer session boundaries from timing gaps.

2. **Session recovery after CodePlane restart**: On startup, the file
   watcher seeks to the end of the OTEL file (or to a persisted byte
   offset) to avoid replaying historical spans. For Claude hooks: every
   hook event includes `session_id` in the common input fields.
   `IngestService` looks up by `external_session_id` on each event. If
   found, resumes ingestion. If not found (session started before
   CodePlane launched), creates a new job from the current event forward.
   Events that fired before restart are lost — there is no replay
   mechanism for hooks.

3. **GitHub token for steer API**: CodePlane needs the user's Copilot
   token to call the steer endpoint. This could be sourced from `gh auth
   token` or configured explicitly. Steering is optional — observation
   works without it.

4. **User working on main**: If the CLI session is on the default branch
   directly (no feature branch), there is nothing to merge — the changes
   are already where they belong. `IngestService` detects this at session
   start (`branch == base_ref`). The review screen shows "already on
   target branch" and offers PR creation or mark-complete as the primary
   actions. `MergeService` skips merge entirely and transitions to
   `completed` with resolution `merged`.

5. **Copilot OTEL has no `cwd` attribute**: Empirically verified
   (v1.0.44). The `invoke_agent` and `chat` spans contain only GenAI
   semantic convention attributes (`gen_ai.usage.*`, `github.copilot.cost`,
   `gen_ai.conversation.id`, etc.) — no workspace path. Two fallback
   approaches:
   - *Infer from file paths*: Parse `gen_ai.tool.call.arguments` from the
     first `execute_tool` span to extract an absolute file path, then
     derive the git root via `git rev-parse --show-toplevel <dir>`.
     Requires content capture to be ON.
   - *Explicit resource attribute*: Document that users must set
     `OTEL_RESOURCE_ATTRIBUTES=process.cwd=/path/to/repo` alongside
     `COPILOT_OTEL_FILE_EXPORTER_PATH`. This is more reliable but adds
     setup friction. The watcher should try both (resource attribute
     first, then file path inference, then mark job as `needs_cwd` for
     manual resolution).

6. **Job ID generation for imported sessions**: Managed jobs use the
   repo slug as the job ID stem. Imported sessions from the same repo
   would collide. Add a 6-character hex suffix:
   `{repo_slug}-{hex_suffix}`. The suffix is derived from a hash of
   `external_session_id` for deterministic dedup.

7. **Concurrent imported sessions on same repo**: Unlike managed
   sessions (which run in separate worktrees), imported sessions share
   the user's live working directory. Two concurrent CLI sessions in the
   same repo are inherently conflicting at the git level — CodePlane
   doesn't need to solve this (the CLIs themselves will conflict on
   file writes). CodePlane's job is to track each session independently
   via its `external_session_id`.

8. **Claude OTLP metrics endpoint**: Claude Code exports cost/token
   metrics via standard OTLP protocol (gRPC or HTTP/protobuf or
   HTTP/json). The design doc's original `/api/otlp` route needs to be
   a proper OTLP receiver. Options:
   - *Embedded receiver*: Use `opentelemetry-sdk` +
     `opentelemetry-exporter-otlp-proto-http` to embed a minimal OTLP
     HTTP receiver in FastAPI at `/v1/metrics`. Parse
     `ExportMetricsServiceRequest` protobuf, extract
     `claude_code.cost.usage` and `claude_code.token.usage` data points,
     filter by `session.id`, and emit `telemetry_updated` events.
   - *Sidecar collector*: Run an OpenTelemetry Collector that receives
     OTLP, filters by metric name, and forwards to CodePlane's internal
     REST API. More operationally complex but standard.
   The embedded receiver approach is simpler for single-user deployments.
   Claude sets `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:<port>`.

9. **Session end detection for Copilot**: Copilot OTEL has no explicit
   "session ended" signal. The `invoke_agent` span end marks session
   completion. The watcher must detect span end events (spans have start
   time + duration; when the root `invoke_agent` span appears with a
   non-zero duration, the session is over). No idle timeout needed — the
   span's own duration field is the signal.

---

## 10. Claude CLI Redesign: File-Tailing (Supersedes §2.2 Hooks)

The original hook-based architecture for Claude CLI (§2.2) is replaced
entirely by a file-tailing approach that mirrors the Copilot
`SessionStateWatcher`. Hooks are reduced to a single purpose: delivering
operator messages to a running session.

### 10.1 Local Session Files (Empirically Verified)

Claude CLI writes full session transcripts to local JSONL files:

```text
~/.claude/projects/{encoded-cwd}/{session-id}.jsonl        # main session
~/.claude/projects/{encoded-cwd}/{session-id}/subagents/agent-{hash}.jsonl  # child agents
```

Where `encoded-cwd` = the absolute repo path with `/` replaced by `-`
(e.g., `/home/dave01/wsl-repos/codeplane` →
`-home-dave01-wsl-repos-codeplane`).

Files are appended in real time as the session runs. Each line is a
JSON object with a `type` field:

| `type` | Content |
|--------|---------|
| `queue-operation` | Internal bookkeeping (enqueue/dequeue). Skip. |
| `user` (message.role=user) | User prompt. Contains `cwd`, `gitBranch`, `sessionId`, `version`, `entrypoint`. |
| `assistant` (message.role=assistant) | Agent response. `message.content` is an array of blocks: `text`, `tool_use`, `thinking`. `message.usage` contains per-turn token counts. |
| `attachment` | Context injection. Skip. |
| `last-prompt` | Session ended cleanly. Deterministic end signal. |

Assistant messages contain a `usage` field:

```json
{
  "input_tokens": 10,
  "output_tokens": 237,
  "cache_read_input_tokens": 24867,
  "cache_creation_input_tokens": 6823,
  "service_tier": "standard"
}
```

### 10.2 Architecture: `ClaudeSessionStateWatcher`

Single service: `backend/services/claude_session_watcher.py`

**Discovery** (polling every 2s):

- For each repo in `config.repos`, compute watch path:
  `~/.claude/projects/{repo_path.replace('/', '-')}/`
- List `*.jsonl` files in that directory
- New file → parse session ID from filename → create Job → start tail
- On startup: check existing files — `last-prompt` present = dead (skip),
  no `last-prompt` = scan `/proc` for matching claude process → alive =
  tail, dead = skip

**Tail loop** (polling every 300ms per session):

- Read from last persisted offset to EOF
- Parse JSONL lines, switch on type:
  - `user` → transcript event (role=operator), extract prompt on first msg
  - `assistant` → parse content blocks:
    - `text` → transcript (role=agent)
    - `tool_use` → transcript (role=tool_call, name, input)
    - `thinking` → transcript (role=thinking)
  - Next `user` turn with `tool_result` blocks → transcript (role=tool_result)
  - `last-prompt` → finalize session
- Extract telemetry from `message.usage` on every assistant turn
- Accumulate per-job, flush atomically with tail offset
- Emit `step_completed` per assistant turn (tool names + file paths)
- Persist tail offset every 64KB of progress

**Liveness detection** (deterministic, no heuristics):

- Primary: `last-prompt` event → session over, finalize immediately
- Crash: after N idle polls without `last-prompt`, scan
  `/proc/*/cmdline` for `claude` process containing the session-id.
  No match → finalize with error. Match → keep waiting.

### 10.3 Operator Messages (Stop Hook)

Claude CLI has no steer API equivalent. No sockets, no REST endpoint,
no IPC. The only mechanism to inject a message into a running session
the user started is the synchronous `Stop` hook response.

`ClaudeSessionStateWatcher` owns a pending-message queue:
`_pending_messages: dict[str, list[str]]` (job_id → messages).

**Endpoint:** `POST /api/hooks/claude/stop`

- Claude POSTs on every Stop event (end of agent turn)
- Handler: lookup session → check pending messages → if any, return
  `{"decision": "block", "reason": combined_messages}` → else return `{}`

**Auto-configuration:** On startup, CodePlane writes to
`~/.claude/settings.json`:

```json
{
  "hooks": {
    "Stop": [{"type": "http", "url": "http://localhost:{PORT}/api/hooks/claude/stop"}]
  }
}
```

Merges with existing user settings. Removes on shutdown.

### 10.4 Job Creation

When a new `.jsonl` file is discovered:

1. Parse first `user` message for `cwd`, `gitBranch`, `sessionId`
2. Resolve git root from `cwd`
3. Resolve `base_ref` = current HEAD SHA
4. Generate deterministic job ID: `{repo_slug}-{sha256(session_id)[:12]}`
5. Persist Job with `source=claude_cli`, `state=running`
6. Start tail from offset 0

### 10.5 What Gets Deleted

- `IngestService.ingest_claude_hook()` — all Claude hook handling
- `IngestService._create_job_from_session()` for Claude
- `IngestService` turn accumulators/counters for Claude
- `IngestService._pending_messages` for Claude
- Multi-hook config (SessionStart, PostToolUse, SessionEnd, SubagentStart, SubagentStop)
- The catch-all `POST /api/hooks/claude` endpoint

### 10.6 Comparison: Copilot vs Claude Watcher

| Concern | Copilot (`SessionStateWatcher`) | Claude (`ClaudeSessionStateWatcher`) |
|---------|------|--------|
| Discovery source | `~/.copilot/session-store.db` (SQLite) | `~/.claude/projects/{cwd}/*.jsonl` (directory listing) |
| Event stream | `~/.copilot/session-state/{id}/events.jsonl` | `~/.claude/projects/{cwd}/{id}.jsonl` |
| Event format | Copilot SDK `SessionEvent` types | Raw Claude API messages (user/assistant/tool_use/tool_result) |
| Token usage | `assistant.usage` event type | `message.usage` field on assistant turns |
| Session end | `session.shutdown` event | `last-prompt` event |
| Liveness probe | Steer API `GET /agents/tasks/{id}` → 404 | `/proc` scan for claude process |
| Operator messaging | Steer API `POST` (real-time, any time) | Stop hook response (only at turn boundaries) |
| Subagents | Not tracked | `{id}/subagents/agent-*.jsonl` files |
