# CodePlane — Product Specification

---

## Table of Contents

1. [Product Overview](#1-product-overview)
2. [Technology Architecture](#2-technology-architecture)
3. [Frontend](#3-frontend)
4. [Backend](#4-backend)
5. [Live Update Model (SSE)](#5-live-update-model-sse)
6. [Execution Runtime](#6-execution-runtime)
7. [Deployment Model](#7-deployment-model)
8. [Repository and Workspace Model](#8-repository-and-workspace-model)
9. [Voice Input and Transcription](#9-voice-input-and-transcription)
10. [Configuration Model](#10-configuration-model)
11. [Canonical Internal Event Model](#11-canonical-internal-event-model)
12. [Job States](#12-job-states)
13. [Execution Phases](#13-execution-phases)
14. [User Interface](#14-user-interface)
15. [Data Model](#15-data-model)
16. [Persistence](#16-persistence)
17. [REST API](#17-rest-api)
18. [Approval System](#18-approval-system)
19. [Diff Model](#19-diff-model)
20. [Observability](#20-observability)
21. [Security Model](#21-security-model)
22. [Engineering Constraints and Pitfalls](#22-engineering-constraints-and-pitfalls)
23. [Sequence Diagrams](#23-sequence-diagrams)
24. [Execution Strategy Model](#24-execution-strategy-model)
25. [Ross Review: Open Questions](#25-ross-review-open-questions)
26. [MCP Orchestration Server](#26-mcp-orchestration-server)

---

## 1. Product Overview

CodePlane is a control plane for running and supervising coding agents.

It allows an operator to launch automated coding tasks against real repositories and observe everything the agent does in real time.

The system provides visibility into execution progress, code changes, logs, artifacts, and agent reasoning so work can be reviewed and controlled as it happens.

Operators can intervene at any time by sending instructions, approving risky actions, canceling runs, or rerunning failed tasks.

The interface can be accessed locally or remotely through Dev Tunnels, allowing jobs to be monitored and controlled from another device such as a phone.

CodePlane turns autonomous coding agents into something observable, controllable, and safe to operate.

### Core Capabilities

| Capability | Description |
|---|---|
| Job orchestration | Launch coding tasks against local repositories |
| Live monitoring | Watch agent reasoning, logs, and code changes as they happen |
| Approval gating | Intercept and approve or reject risky actions before they execute |
| Operator intervention | Send messages, cancel, or rerun jobs at any time |
| Workspace isolation | Every job gets its own isolated worktree under `.codeplane-worktrees/` |
| Remote access | Dev Tunnels exposes the UI over HTTPS for phone/remote control |
| Voice input | Speak prompts, operator instructions, and terminal commands into the browser |
| Artifact inspection | Browse files, diffs, and produced outputs from every job |
| Integrated terminal | PTY-backed terminal sessions with optional AI agent assistance |

---

## 2. Technology Architecture

CodePlane is a two-tier application.

```
┌──────────────────────────────────────────────────┐
│                  Operator Browser                │
│          React + TypeScript Frontend             │
│   REST (commands/queries) + SSE (live events)   │
└────────────────────┬─────────────────────────────┘
                     │ HTTP / SSE
┌────────────────────▼─────────────────────────────┐
│             FastAPI Backend (Python)             │
│  REST API · SSE stream · Job orchestration       │
│  Git workspace mgmt · Agent adapter registry    │
│  Approval routing · Persistence coordinator     │
└──────┬──────────────┬──────────────┬─────────────┘
       │              │              │
  ┌────▼────┐   ┌─────▼─────┐  ┌───▼───────┐
  │ SQLite  │   │ Git repos │  │Agent SDKs │
  │   DB    │   │/worktrees │  │(pluggable)│
  └─────────┘   └───────────┘  └───────────┘
```

| Tier | Technology |
|---|---|
| Frontend | React 18, TypeScript, Vite |
| Backend | Python 3.11+, FastAPI, Uvicorn |
| Persistence | SQLite (via SQLAlchemy) |
| Agent runtime | Pluggable SDK adapters behind `AgentAdapterInterface`; ships with Copilot SDK and Claude Agent SDK |
| Workspace isolation | Git worktrees |
| Voice transcription | faster-whisper |
| Remote access | Dev Tunnels (HTTPS) |
| State management | Zustand |
| UI primitives | Radix UI (headless) |
| Diff / code viewer | Monaco Editor (`@monaco-editor/react`) |
| Virtualization | `@tanstack/react-virtual` |
| Drag and drop | `@dnd-kit/core` + `@dnd-kit/sortable` |
| Markdown | `react-markdown` + `remark-gfm` |
| Syntax highlighting | Shiki (`@shikijs/rehype`) |
| File tree | `react-arborist` |
| Toasts | Sonner |
| Icons | Lucide React |
| API type generation | openapi-typescript (from FastAPI OpenAPI spec) |
| Package management | uv (Python), npm (Frontend) |

### 2.1 Frontend Serving Model

In production mode, the FastAPI backend serves the built frontend as static files from a `/static` mount point. The Vite dev server (`localhost:5173`) is used only during frontend development.

This means:

- Only one port is exposed in production (`8080`)
- The tunnel exposes a single port
- CORS is not needed in production (same origin)
- During development, CORS allows `http://localhost:5173`

---

## 3. Frontend

### 3.1 Responsibilities

- Operator console UI
- Job dashboards (Kanban / list)
- Job detail views
- Live execution monitoring
- Diff visualization
- Artifact inspection
- Workspace browsing
- Approval and operator controls
- Voice input capture

### 3.2 Communication Model

| Channel | Direction | Purpose |
|---|---|---|
| REST API | Client → Server | Commands and queries |
| SSE (`/api/events`) | Server → Client | Live runtime updates |

The frontend never polls for state. All live updates arrive via SSE. REST calls are used exclusively for actions (create job, send message, approve, cancel, etc.) and one-time data fetches.

### 3.3 Application State

Application state has a single source of truth managed by a Zustand store.

SSE events are processed centrally through a single event dispatcher that updates the store. Components subscribe to the store via selectors and never maintain their own copies of job state.

State slices:

| Slice | Contents |
|---|---|
| `jobs` | All job summaries (id, repo, state, created_at, updated_at) |
| `activeJob` | Full detail of currently viewed job |
| `approvals` | Pending approval requests |
| `settings` | Application settings |
| `ui` | Transient UI state (selected panel, filters, etc.) |

### 3.4 Component Hierarchy

```
App
├── Router
│   ├── DashboardScreen
│   │   ├── KanbanBoard (desktop)
│   │   │   ├── KanbanColumn [Active]
│   │   │   ├── KanbanColumn [Sign-off]
│   │   │   ├── KanbanColumn [Failed]
│   │   │   └── KanbanColumn [History]
│   │   └── JobList (mobile)
│   ├── JobDetailScreen
│   │   ├── JobMetadataHeader
│   │   ├── ApprovalBanner
│   │   ├── TranscriptPanel
│   │   ├── LogsPanel
│   │   ├── DiffViewer
│   │   ├── WorkspaceBrowser
│   │   ├── ArtifactViewer
│   │   ├── ExecutionTimeline
│   │   └── TerminalPanel (inline pane, job-scoped)
│   │       └── AssistPanel (optional sidecar)
│   ├── JobCreationScreen
│   │   ├── RepoSelector
│   │   ├── PromptInput
│   │   ├── VoiceInputButton
│   │   └── AdvancedOptions
│   └── SettingsScreen
│       ├── RepoManager
│       │   ├── AddRepoForm
│       │   └── RepoList
│       ├── RepoDetailView
│       │   ├── RepoHeader
│       │   ├── MCPConfigTable
│       │   ├── RepoConfigPanel
│       │   └── RepoJobList
│       ├── GlobalConfigEditor
│       └── RepoConfigList
├── TerminalDrawer (global, persists across navigation)
│   ├── TerminalPanel (per tab)
│   └── AssistPanel (optional sidecar)
└── SSEProvider (global)
```

### 3.5 SSE Client

The SSE client lives in a singleton provider mounted at the app root.

Behavior:

- Connects to `/api/events` on mount
- Tracks the last received `event_id`
- On disconnect, reconnects automatically with `Last-Event-ID` header
- Dispatches each received event to the central store
- Exposes connection status to UI components

#### Reconnection Strategy

| Parameter | Value |
|---|---|
| Initial delay | 1 second |
| Backoff multiplier | 2x |
| Maximum delay | 30 seconds |
| Jitter | ±500 ms (random) |
| Maximum attempts | 20 |
| Fallback | Show persistent "Disconnected" banner with manual reconnect button |

Connection status is exposed as a Zustand slice with values: `connected`, `reconnecting`, `disconnected`. The UI renders a status indicator in the app header.

### 3.6 TypeScript Domain Models (Generated)

All frontend types are **generated** from the FastAPI OpenAPI schema. The Pydantic models in Section 4.6 are the single source of truth.

#### Code Generation

TypeScript types are generated using `openapi-typescript`:

```bash
npx openapi-typescript http://localhost:8080/openapi.json -o src/api/schema.d.ts
```

This runs as part of the frontend build pipeline:

```json
// package.json scripts
{
  "generate:api": "openapi-typescript http://localhost:8080/openapi.json -o src/api/schema.d.ts",
  "dev": "npm run generate:api && vite",
  "build": "npm run generate:api && tsc && vite build"
}
```

During CI or when the backend isn't running, the committed `schema.d.ts` is used as-is. Developers regenerate it after any Pydantic model change.

#### Convenience Type Aliases

The generated types use path-based access. A thin `src/api/types.ts` file re-exports them as friendly aliases:

```typescript
import type { components } from "./schema";

export type Job = components["schemas"]["JobResponse"];
export type JobState = Job["state"];
export type LogLine = components["schemas"]["LogLinePayload"];
export type TranscriptEntry = components["schemas"]["TranscriptPayload"];
export type ApprovalRequest = components["schemas"]["ApprovalResponse"];
export type DiffFile = components["schemas"]["DiffFileModel"];
export type DiffHunk = components["schemas"]["DiffHunkModel"];
export type DiffLine = components["schemas"]["DiffLineModel"];
export type Artifact = components["schemas"]["ArtifactResponse"];
export type ExecutionPhase = components["schemas"]["ExecutionPhase"];
export type WorkspaceEntry = components["schemas"]["WorkspaceEntry"];
```

All component code imports from `src/api/types.ts`, never from `schema.d.ts` directly.

### 3.7 Performance Guidelines

- Large log and transcript lists must use virtualized rendering (`@tanstack/react-virtual`)
- Diff viewer uses Monaco `DiffEditor`; large files are loaded on demand, not all at once
- Kanban board must not re-render all columns when a single job updates; use memoized selectors per column

---

## 4. Backend

### 4.1 Responsibilities

- REST API endpoints
- SSE event streaming
- Job orchestration
- Agent session lifecycle management
- Git workspace management
- Artifact collection
- Approval routing
- Runtime monitoring
- Persistence coordination
- Voice transcription

### 4.2 Module Structure

```
backend/
├── main.py                    # FastAPI app factory
├── config.py                  # Configuration loading
├── api/
│   ├── jobs.py                # Job CRUD and control endpoints
│   ├── events.py              # SSE streaming endpoint
│   ├── artifacts.py           # Artifact retrieval endpoints
│   ├── workspace.py           # File browsing endpoints
│   ├── approvals.py           # Approval resolution endpoints
│   ├── voice.py               # Voice transcription endpoint
│   ├── health.py              # Health check endpoint
│   └── settings.py            # Settings management endpoints
├── services/
│   ├── job_service.py         # Job lifecycle orchestration
│   ├── runtime_service.py     # Long-running job execution manager
│   ├── git_service.py         # Git worktree and branch operations
│   ├── agent_adapter.py       # Agent adapter (interface + Copilot impl)
│   ├── event_bus.py           # Internal event bus
│   ├── sse_manager.py         # SSE connection management
│   ├── approval_service.py    # Approval request persistence and routing
│   ├── artifact_service.py    # Artifact storage and retrieval
│   ├── diff_service.py        # Diff generation and parsing
│   ├── merge_service.py       # Merge-back, PR creation, and conflict handling
│   ├── permission_policy.py   # Permission mode evaluation for SDK requests
│   ├── platform_adapter.py    # Per-platform integration (GitHub, etc.)
│   ├── retention_service.py   # Artifact and worktree retention cleanup
│   ├── setup_service.py       # Interactive dependency setup
│   ├── summarization_service.py # Post-session LLM summarization
│   ├── telemetry.py           # Observability and telemetry
│   ├── utility_session.py     # Utility LLM sessions (naming, summaries)
│   ├── auth.py                # Authentication helpers
│   └── voice_service.py       # faster-whisper transcription
├── models/
│   ├── db.py                  # SQLAlchemy models
│   ├── domain.py              # Domain dataclasses/Pydantic models
│   ├── events.py              # Canonical event types
│   └── api_schemas.py         # Pydantic request/response schemas
├── persistence/
│   ├── database.py            # Database engine and session management
│   ├── repository.py          # Base repository pattern
│   ├── job_repo.py            # Job persistence
│   ├── event_repo.py          # Event persistence
│   ├── artifact_repo.py       # Artifact metadata persistence
│   └── approval_repo.py       # Approval request persistence
└── tests/
    ├── unit/
    └── integration/
```

### 4.3 API Routes Must Not Contain Orchestration Logic

API route handlers are thin. They:

1. Validate and parse input
2. Delegate to a service
3. Return the result

No orchestration logic, no direct database access, and no git operations belong in route handlers.

### 4.4 Agent Adapter

The agent runtime is wrapped behind an interface so the system is not tightly coupled to any specific SDK. Multiple SDKs are supported simultaneously via the `AdapterRegistry`.

```python
from abc import ABC, abstractmethod
from typing import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum

class AgentSDK(StrEnum):
    """Supported agent SDK backends."""
    copilot = "copilot"
    claude = "claude"

@dataclass
class SessionConfig:
    workspace_path: str
    prompt: str
    mcp_servers: dict[str, MCPServerConfig]  # discovered from repo config files
    protected_paths: list[str]               # from per-repo config; used by permission policy
    permission_mode: PermissionMode = "full_auto" # full_auto | observe_only | review_and_approve
    sdk: str = "copilot"                     # which SDK adapter to use

@dataclass
class MCPServerConfig:
    command: str
    args: list[str]
    env: dict[str, str] | None = None

class SessionEventKind(str, Enum):
    log = "log"
    transcript = "transcript"
    file_changed = "file_changed"
    approval_request = "approval_request"
    done = "done"
    error = "error"

@dataclass
class SessionEvent:
    kind: SessionEventKind
    payload: dict

class AgentAdapterInterface(ABC):

    @abstractmethod
    async def create_session(self, config: SessionConfig) -> str:
        """Create a session, return session_id."""

    @abstractmethod
    async def stream_events(self, session_id: str) -> AsyncIterator[SessionEvent]:
        """Stream events from a running session."""

    @abstractmethod
    async def send_message(self, session_id: str, message: str) -> None:
        """Send a follow-up message into a running session."""

    @abstractmethod
    async def abort_session(self, session_id: str) -> None:
        """Abort the current message processing. Session remains valid."""
```

#### Adapter Registry

`AdapterRegistry` is a lazy-caching factory that creates and returns the appropriate adapter for a given `AgentSDK` value. Adapters are instantiated on first `get_adapter()` call and reused for subsequent requests with the same SDK.

```python
class AdapterRegistry:
    def get_adapter(self, sdk: AgentSDK | str) -> AgentAdapterInterface: ...
    @property
    def default_adapter(self) -> AgentAdapterInterface: ...
```

`RuntimeService` calls `registry.get_adapter(job.sdk)` before starting each job, so different jobs can use different SDKs concurrently.

#### Copilot Adapter

The `CopilotAdapter` wraps the Python Copilot SDK (`pip install github-copilot-sdk`, import as `from copilot import CopilotClient`). A `FakeAgentAdapter` is used in tests.

##### Copilot SDK Method Mapping

| Adapter method | SDK method | Notes |
|---|---|---|
| `create_session()` | `client.create_session(config)` | Returns `CopilotSession` |
| `stream_events()` | `session.on(handler)` | Callback-based; adapter bridges to async iterator (see below) |
| `send_message()` | `session.send(MessageOptions)` | Uses `mode="immediate"` for mid-session injection |
| `abort_session()` | `session.abort()` | Aborts current message; session stays alive |

##### Callback-to-Iterator Bridge

The Copilot SDK uses a callback-based API: `SessionHooks` (`on_pre_tool_use`, `on_post_tool_use`, `on_session_start`, etc.) and `on_permission_request` are registered at session creation. The adapter bridges these into the `AsyncIterator[SessionEvent]` pattern by:

1. Creating an `asyncio.Queue` per session
2. Registering an SDK callback via `session.on(handler)` that pushes `SessionEvent` items onto the queue
3. `stream_events()` yields from the queue until the session completes or the subprocess exits
4. On subprocess crash, the SDK raises `ProcessExitedError` on pending futures; the adapter catches this and emits an error `SessionEvent`

This keeps the rest of the system (strategies, runtime service, event bus) decoupled from SDK callback mechanics.

#### Claude Adapter

The `ClaudeAdapter` wraps the Claude Agent SDK (`pip install claude-code-sdk`, import as `import claude_code_sdk`). The SDK internally spawns the Claude Code CLI as a subprocess and provides a native async iterator API.

##### Claude SDK Method Mapping

| Adapter method | SDK method / pattern | Notes |
|---|---|---|
| `create_session()` | `ClaudeSDKClient(options)` | Creates a client; session state is managed by the client |
| `stream_events()` | `async for message in client.query()` | Native async iterator — no callback bridge needed |
| `send_message()` | `client.query(prompt)` | Starts a new conversational turn |
| `abort_session()` | `client.interrupt()` + `client.disconnect()` | Interrupts current turn, then disconnects |

##### Permission Mode Mapping

| CodePlane mode | Claude SDK mode | Behavior |
|---|---|---|
| `full_auto` | `bypassPermissions` | All tools auto-approved |
| `observe_only` | `plan` | Only read-only tools allowed; `can_use_tool` callback denies writes |
| `review_and_approve` | `default` | `can_use_tool` callback routes to `ApprovalService` |

##### Message Iterator Pattern

Unlike Copilot's callback bridge, the Claude SDK yields messages natively as an async iterator. The adapter consumes messages in a background task that translates them into `SessionEvent` items:

- `AssistantMessage` with `TextBlock` → `SessionEvent(transcript, {role: "agent", content: text})`
- `ToolUseBlock` → log event (tool started) + start time tracking
- `ToolResultBlock` → `SessionEvent(transcript, {role: "tool_call", ...})` + telemetry
- `ResultMessage` → telemetry recording + `SessionEvent(done/error, ...)`
- `SystemMessage` → log event only

#### SDK-Model Compatibility

Each SDK only supports models from its provider ecosystem. Validation is performed at job creation time before any resources are allocated:

| SDK | Accepted model prefixes | Examples |
|---|---|---|
| `copilot` | _(any)_ | `gpt-4o`, `claude-sonnet-4-20250514`, `o1-preview` |
| `claude` | `claude-` | `claude-sonnet-4-20250514`, `claude-3-opus-20240229` |

If the user requests a model incompatible with the selected SDK (e.g., `gpt-4o` with `claude`), the API returns `400 Bad Request` with a descriptive error message. When no model is specified, the SDK uses its own default.

### 4.5 Session Config Resolution

The `RuntimeService` constructs a `SessionConfig` directly from the `Job` record and resolved config (global + per-repo). There is no intermediate aggregation object. The inputs are:

- `workspace_path` — from `Job.worktree_path` (set by `GitService` during workspace prep)
- `prompt` — from `Job.prompt`
- `mcp_servers` — discovered from the repo's MCP config files (see §10.2.1)
- `protected_paths` — from per-repo config, translated to SDK-native permission rules by the adapter

### 4.6 Pydantic API Schemas

All API request and response bodies are defined as Pydantic models in `models/api_schemas.py`. These models are the **single source of truth** for the API contract. FastAPI auto-generates an OpenAPI schema from them, and TypeScript types are generated from that schema (see Section 3.6).

All response models use camelCase serialization to match frontend conventions:

```python
from pydantic import BaseModel, Field, ConfigDict
from pydantic.alias_generators import to_camel
from typing import Literal
from datetime import datetime
from enum import Enum


class CamelModel(BaseModel):
    """Base model that serializes field names to camelCase."""
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


# --- Jobs ---

class CreateJobRequest(BaseModel):    # Request models use snake_case (Python convention)
    repo: str
    prompt: str
    base_ref: str | None = None
    branch: str | None = None            # default: agent decides based on prompt
    permission_mode: PermissionMode | None = None  # full_auto | observe_only | review_and_approve
    model: str | None = None              # LLM model override

class CreateJobResponse(CamelModel):
    id: str
    state: str
    title: str | None = None
    branch: str | None = None
    worktree_path: str | None = None
    created_at: datetime

class JobResponse(CamelModel):
    id: str
    repo: str
    prompt: str
    title: str | None = None
    state: str
    base_ref: str
    worktree_path: str | None
    branch: str | None
    permission_mode: PermissionMode | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    pr_url: str | None = None
    merge_status: str | None = None
    resolution: str | None = None
    archived_at: datetime | None = None
    failure_reason: str | None = None
    model: str | None = None
    worktree_name: str | None = None

class JobListResponse(CamelModel):
    items: list[JobResponse]
    cursor: str | None
    has_more: bool


# --- Messages ---

class SendMessageRequest(BaseModel):
    content: str

class SendMessageResponse(CamelModel):
    seq: int
    timestamp: datetime


# --- Approvals ---

class ApprovalResolution(str, Enum):
    approved = "approved"
    rejected = "rejected"

class ResolveApprovalRequest(BaseModel):
    resolution: ApprovalResolution

class ApprovalResponse(CamelModel):
    id: str
    job_id: str
    description: str
    proposed_action: str | None
    requested_at: datetime
    resolved_at: datetime | None
    resolution: ApprovalResolution | None


# --- Artifacts ---

class ArtifactType(str, Enum):
    diff_snapshot = "diff_snapshot"
    agent_summary = "agent_summary"
    custom = "custom"

class ArtifactResponse(CamelModel):
    id: str
    job_id: str
    name: str
    type: ArtifactType
    mime_type: str
    size_bytes: int
    phase: ExecutionPhase
    created_at: datetime

class ArtifactListResponse(CamelModel):
    items: list[ArtifactResponse]


# --- Workspace ---

class WorkspaceEntryType(str, Enum):
    file = "file"
    directory = "directory"

class WorkspaceEntry(CamelModel):
    path: str
    type: WorkspaceEntryType
    size_bytes: int | None = None    # None for directories

class WorkspaceListResponse(CamelModel):
    items: list[WorkspaceEntry]
    cursor: str | None
    has_more: bool


# --- Settings ---

class UpdateSettingsRequest(BaseModel):
    \"\"\"Structured settings update — only include fields to change.\"\"\"
    max_concurrent_jobs: int | None = Field(None, ge=1, le=10)
    permission_mode: PermissionMode | None = None
    auto_push: bool | None = None
    cleanup_worktree: bool | None = None
    delete_branch_after_merge: bool | None = None
    artifact_retention_days: int | None = Field(None, ge=1, le=365)
    max_artifact_size_mb: int | None = Field(None, ge=1, le=10_000)
    auto_archive_days: int | None = Field(None, ge=1, le=365)
    terminal_assist_sdk: str | None = None           # copilot | claude
    terminal_assist_model: str | None = None         # model override; null = SDK default

class SettingsResponse(CamelModel):
    max_concurrent_jobs: int
    permission_mode: str
    auto_push: bool
    cleanup_worktree: bool
    delete_branch_after_merge: bool
    artifact_retention_days: int
    max_artifact_size_mb: int
    auto_archive_days: int
    terminal_assist_sdk: str
    terminal_assist_model: str | None


# --- Voice ---

class TranscribeResponse(BaseModel):
    text: str


# --- Health ---

class HealthStatus(str, Enum):
    healthy = "healthy"

class HealthResponse(CamelModel):
    status: HealthStatus
    version: str
    uptime_seconds: float
    active_jobs: int
    queued_jobs: int


# --- SSE Payload Models ---
# These models define the shape of SSE event payloads.
# They appear in the OpenAPI schema so TypeScript types
# are generated for them alongside the REST models.

class ExecutionPhase(str, Enum):
    environment_setup = "environment_setup"
    agent_reasoning = "agent_reasoning"
    finalization = "finalization"
    post_completion = "post_completion"

class LogLevel(str, Enum):
    debug = "debug"
    info = "info"
    warn = "warn"
    error = "error"

class LogLinePayload(CamelModel):
    job_id: str
    seq: int
    timestamp: datetime
    level: LogLevel
    message: str
    context: dict | None = None

class TranscriptRole(str, Enum):
    agent = "agent"
    operator = "operator"
    tool_call = "tool_call"
    reasoning = "reasoning"
    divider = "divider"

class TranscriptPayload(CamelModel):
    job_id: str
    seq: int
    timestamp: datetime
    role: TranscriptRole
    content: str

class DiffLineType(str, Enum):
    context = "context"
    addition = "addition"
    deletion = "deletion"

class DiffLineModel(CamelModel):
    type: DiffLineType
    content: str

class DiffHunkModel(CamelModel):
    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    lines: list[DiffLineModel]

class DiffFileStatus(str, Enum):
    added = "added"
    modified = "modified"
    deleted = "deleted"
    renamed = "renamed"

class DiffFileModel(CamelModel):
    path: str
    status: DiffFileStatus
    additions: int
    deletions: int
    hunks: list[DiffHunkModel]

class JobStateChangedPayload(CamelModel):
    job_id: str
    previous_state: str | None
    new_state: str
    timestamp: datetime

class DiffUpdatePayload(CamelModel):
    job_id: str
    changed_files: list[DiffFileModel]

class SessionHeartbeatPayload(CamelModel):
    job_id: str
    session_id: str
    timestamp: datetime

class SnapshotPayload(CamelModel):
    jobs: list[JobResponse]
    pending_approvals: list[ApprovalResponse]
```

### 4.7 Internal Event Bus

The `EventBus` is the backbone of the backend. All subsystems communicate through it.

- Services publish domain events to the bus
- Persistence layer subscribes and persists events
- SSE manager subscribes and pushes events to connected clients
- Job state machine subscribes and applies transitions

The event bus is an in-process async pub/sub. It is not a message broker. All subscribers run in the same process.

---

## 5. Live Update Model (SSE)

### 5.1 Endpoint

```
GET /api/events
```

Optional query parameter to scope to a single job:

```
GET /api/events?job_id={job_id}
```

### 5.2 SSE Event Format

Each event follows the standard SSE wire format:

```
id: {event_id}
event: {event_type}
data: {json_payload}

```

### 5.3 Event Types

| Event Type | Payload Summary |
|---|---|
| `job_state_changed` | `{ job_id, previous_state, new_state, timestamp }` |
| `log_line` | `{ job_id, seq, timestamp, level, message, context }` |
| `transcript_update` | `{ job_id, seq, timestamp, role, content }` |
| `diff_update` | `{ job_id, changed_files: DiffFile[] }` |
| `approval_requested` | `{ job_id, approval_id, description, proposed_action }` |
| `approval_resolved` | `{ job_id, approval_id, resolution, timestamp }` |
| `session_heartbeat` | `{ job_id, session_id, timestamp }` |
| `snapshot` | `{ jobs: JobResponse[], pending_approvals: ApprovalResponse[] }` |
| `job_resolved` | `{ jobId, resolution, prUrl?, conflictFiles? }` |
| `job_archived` | `{ jobId }` |
| `merge_completed` | `{ jobId, branch, baseRef, strategy }` |
| `merge_conflict` | `{ jobId, branch, baseRef, conflictFiles, fallback }` |
| `session_resumed` | `{ jobId, sessionNumber }` |
| `job_title_updated` | `{ jobId, title?, branch? }` |
| `progress_headline` | `{ jobId, headline }` |
| `model_downgraded` | `{ jobId, requestedModel, actualModel }` |
| `tool_group_summary` | `{ jobId, turnId, summary }` |
| `job_review` | `{ jobId, prUrl?, mergeStatus?, resolution? }` |
| `job_failed` | `{ jobId, reason }` |

### 5.3.1 Domain Event to SSE Event Mapping

The `SSEManager` translates internal domain events into SSE events as follows:

| Domain Event | SSE Event | Notes |
|---|---|---|
| `JobCreated` | `job_state_changed` | `previous_state: null, new_state: running or queued` |
| `WorkspacePrepared` | _(none)_ | Internal only; workspace info is in the job response |
| `AgentSessionStarted` | _(none)_ | Internal only |
| `LogLineEmitted` | `log_line` | 1:1 mapping |
| `TranscriptUpdated` | `transcript_update` | 1:1 mapping |
| `DiffUpdated` | `diff_update` | 1:1 mapping |
| `ApprovalRequested` | `approval_requested` + `job_state_changed` | Two SSE events emitted |
| `ApprovalResolved` | `approval_resolved` + `job_state_changed` | Two SSE events emitted |
| `JobReview` | `job_review` + `job_state_changed` | Two SSE events emitted |
| `JobFailed` | `job_failed` + `job_state_changed` | Two SSE events emitted |
| `JobCanceled` | `job_state_changed` | `new_state: canceled` |
| `JobStateChanged` | `job_state_changed` | 1:1 mapping |
| `SessionHeartbeat` | `session_heartbeat` | 1:1 mapping |
| `MergeCompleted` | `merge_completed` | 1:1 mapping |
| `MergeConflict` | `merge_conflict` | 1:1 mapping |
| `SessionResumed` | `session_resumed` | 1:1 mapping |
| `JobResolved` | `job_resolved` | 1:1 mapping |
| `JobArchived` | `job_archived` | 1:1 mapping |
| `JobTitleUpdated` | `job_title_updated` | 1:1 mapping |
| `ProgressHeadline` | `progress_headline` | 1:1 mapping |
| `ModelDowngraded` | `model_downgraded` | 1:1 mapping |
| `ToolGroupSummary` | `tool_group_summary` | 1:1 mapping |

### 5.4 Reconnection and Replay

- Every SSE event carries a monotonically increasing `id`
- The client sends `Last-Event-ID` on reconnect
- The backend replays all events with `id > Last-Event-ID` from the event log in SQLite
- Replay is bounded: events older than the job's terminal state are not replayed

#### Replay Bounds

To prevent unbounded replay for long-running jobs, the backend enforces:

| Constraint | Value |
|---|---|
| Maximum replay events | 500 per job |
| Maximum replay age | 5 minutes of wall-clock time |

If the client has been disconnected longer than the replay window, the backend sends a `snapshot` event first containing the current state of all active jobs, followed by recent events within the window. The client applies the snapshot to its store, then processes the delta events.

```
event: snapshot
data: { "jobs": [...], "pending_approvals": [...] }

```

The `jobs` array contains full `JobResponse` objects (same shape as `GET /api/jobs/{id}`). The `pending_approvals` array contains full `ApprovalResponse` objects. This gives the client enough data to fully reconstruct its store without additional REST calls.

### 5.5 SSE Manager

The `SSEManager` service:

- Maintains the set of open SSE connections
- Subscribes to the internal event bus
- Serializes events to SSE wire format
- Broadcasts or routes events to appropriate connections
- Handles client disconnection cleanup

### 5.6 SSE Scaling Constraint

Broadcasting all job events to all connected clients does not scale beyond approximately 20 concurrent jobs per operator session. This is acceptable for the single-developer deployment model.

When more than 20 jobs are active concurrently, the SSE manager switches to a **selective streaming** mode:

| Condition | Behavior |
|---|---|
| Job card is open (Job Detail screen) | Full event streaming for that job continues normally |
| Dashboard view with >20 active jobs | Job cards receive only `job_state_changed` events (no logs, transcripts, or diffs) |
| Dashboard with >20 active jobs | A "Refresh page for latest updates" banner is shown above the Kanban board |

This means:

- An operator viewing a specific job always gets the full live experience regardless of total job count
- The dashboard degrades gracefully by showing state transitions only, not the full event firehose
- The operator can click into any job to restore full streaming for that job
- Below the 20-job threshold, behavior is unchanged — all events stream to all views

---

## 6. Execution Runtime

### 6.1 Job Lifecycle

When a job is created:

1. `JobService` validates the request
2. `GitService` creates the worktree and branch
3. `JobService` persists a `JobCreated` event and a `WorkspacePrepared` event
4. `RuntimeService` is asked to run the job
5. `RuntimeService` is asked to run the job and creates an asyncio task
6. The task starts the agent session and consumes yielded events
7. Each event is translated into a domain event and published to the event bus
8. When the session completes, the job transitions to `review`, `failed`, or `canceled`

### 6.2 Runtime Service

The `RuntimeService` manages all active job tasks.

- Tracks running asyncio tasks by `job_id`
- Enforces `max_concurrent_jobs` from global config
- Enqueues jobs if at capacity (state: `queued`)
- Starts queued jobs when capacity opens
- Resolves the appropriate adapter per-job via `AdapterRegistry.get_adapter(job.sdk)`
- Provides a `cancel(job_id)` method that cancels the asyncio task and calls `adapter.abort_session()`

### 6.3 Operator Message Injection

When an operator sends a message to a running job:

1. `POST /api/jobs/{job_id}/messages` received
2. Route delegates to `JobService.send_operator_message()`
3. Service calls `adapter.send_message(session_id, message)`
4. A `TranscriptUpdated` event is published with `role="operator"`

### 6.4 Approval Pause

When the agent SDK raises a permission request (e.g., Copilot SDK calls `on_permission_request`):

1. Adapter translates to `ApprovalRequested` domain event
2. Event bus delivers to `ApprovalService`
3. `ApprovalService` persists the request; adapter holds the SDK callback pending
4. Job transitions to `waiting_for_approval`
5. `ApprovalRequested` SSE event sent to frontend
6. Operator approves or rejects via `POST /api/approvals/{approval_id}/resolve`
7. `ApprovalService` records resolution; adapter returns the decision to the SDK callback

### 6.5 Graceful Shutdown

When the backend process receives `SIGTERM` or `SIGINT` (e.g., operator presses Ctrl+C):

1. Stop accepting new job creation requests (return `503 Service Unavailable`)
2. For each running job:
   a. Call `adapter.abort_session(session_id)`
   b. Publish a `JobCanceled` event with `reason: "server_shutdown"`
   c. Transition the job to `canceled`
3. Close all SSE connections
4. Allow up to 10 seconds for in-flight requests to complete
5. Close the SQLite connection
6. Exit

Queued jobs remain in `queued` state and are picked up on next startup.

### 6.6 Restart Recovery

On startup, before accepting requests, the backend runs recovery:

1. Query for all jobs in `running` or `waiting_for_approval` state
2. Transition each to `failed` with a `JobFailed` event containing `reason: "process_restarted"`
3. Log each recovered job as a warning

The system does not attempt to reconnect to orphaned agent sessions. Asyncio tasks from a previous process cannot be reconstructed. The operator can rerun any recovered job using the rerun button.

Queued jobs are re-evaluated against capacity and started if slots are available.

---

## 7. Deployment Model

CodePlane runs entirely on a single developer machine.

```
Developer Machine
├── CodePlane Backend (FastAPI on localhost:8080, serves frontend static files)
├── SQLite database (~/.codeplane/data.db)
├── Artifact storage (~/.codeplane/artifacts/)
├── Global config (~/.codeplane/config.yaml)
├── Application logs (~/.codeplane/logs/)
├── Local git repositories (/repos/...)
└── Dev Tunnels (HTTPS tunnel for remote access)
```

In production mode, the backend serves the built React frontend as static files. Only one port (`8080`) is exposed. The Vite dev server on port `5173` is used only during frontend development.

### 7.1 Tunnel

Dev Tunnels exposes the local application over HTTPS, enabling remote access from phones and other devices.

- Requires the `devtunnel` CLI installed and authenticated on the machine
- Exposes the local port over HTTPS at `https://{tunnel}-{port}.{region}.devtunnels.ms`
- Tunnel URLs are provisioned by the Dev Tunnels relay
- Password auth is always enabled when tunneling — remote access requires the password printed in the startup banner
- HTTPS is enforced by the Dev Tunnels relay
- The backend enforces CORS to prevent cross-origin abuse from other browser tabs

### 7.2 Startup

The system is started with a single command:

```bash
cpl up
```

This command:

1. Loads and validates global config
2. Initializes the SQLite database (runs migrations)
3. Runs restart recovery (see Section 6.6)
4. Loads the faster-whisper model (if voice is enabled)
5. Starts the FastAPI server
6. Optionally starts Dev Tunnels

### 7.3 CLI

The `cpl` CLI is a Python entry point installed via `uv`:

```bash
uv pip install -e .
```

The entry point is defined in `pyproject.toml`:

```toml
[project.scripts]
cpl = "backend.main:cli"
```

#### CLI Commands

| Command | Description |
|---|---|
| `cpl up` | Start the server |
| `cpl up --port 9090` | Start on a custom port |
| `cpl up --remote` | Start with Dev Tunnels for remote access |
| `cpl up --dev` | Start in development mode (CORS allows localhost:5173) |
| `cpl init` | Create `~/.codeplane/config.yaml` with defaults |
| `cpl version` | Print version |

---

## 8. Repository and Workspace Model

### 8.1 Worktree Creation

Every job gets its own isolated worktree. The main worktree (repo root) is never used for job execution.

When a job starts:

1. Backend resolves the repository root from the config
2. `JobService` generates a `worktree_name` using the utility LLM (kebab-case, 3-30 chars, e.g. `fix-null-pointer`). If naming fails, a deterministic fallback `task-{sha256(prompt)[:8]}` is used
3. `GitService.create_worktree()` creates a worktree directory at:
   ```
   {repo_root}/{worktrees_dirname}/{worktree_name}/
   ```
   Default `worktrees_dirname`: `.codeplane-worktrees`
4. A branch is created from `base_ref`:
   - If `branch` was provided in the job creation request, that name is used as-is
   - Otherwise, the LLM-generated branch name is used
5. Before creation, stale worktree registrations are cleaned up (`git worktree prune`, force-remove if directory exists, delete stale branch)

#### Worktree Creation Failure

If `git worktree add` fails (e.g., disk full, permissions error, corrupt repo state), `GitService.create_worktree()` raises `GitError` which `JobService` catches. The job is persisted in `failed` state with a descriptive error message including git stderr output. The operator can resolve the underlying issue and rerun the job.

Example — single job:

```
/repos/service-a/.codeplane-worktrees/fix-null-pointer/   ← job-104 works here
```

Example — two concurrent jobs on the same repo:

```
/repos/service-a/.codeplane-worktrees/fix-null-pointer/   ← job-104
/repos/service-a/.codeplane-worktrees/add-pagination/     ← job-105
```

### 8.2 Branch Naming

Branch names are either explicitly provided by the operator at job creation or chosen by the agent as a preflight step. The agent picks a conventional name based on the prompt.

Examples:

```
fix/null-pointer-in-user-service
feat/add-pagination-to-orders-api
chore/upgrade-react-to-19
```

### 8.3 Workspace Cleanup

On job completion (success, failure, or cancel):

- Worktree directories are retained for artifact inspection and diff browsing
- Worktrees are not automatically deleted; cleanup depends on the completion config (`completion.cleanup_worktree`)
- A batch cleanup command is available via: `POST /api/settings/cleanup-worktrees`

Jobs have a `completion.strategy` config that controls what happens after success:

- **`manual`** (default): the job is left as `unresolved` for operator decision via the Review column
- **`auto_merge`**: the existing escalation runs automatically — fast-forward merge is attempted first, then a regular merge, and if both fail a PR is created as a fallback
- **`pr_only`**: a PR is always created immediately upon success

### 8.4 Protected Paths

If a per-repository config defines `protected_paths`, the adapter translates these into SDK-native permission rules at session creation time. Any write to a protected path triggers the SDK's built-in permission request flow, which the adapter routes to the operator via the approval system (Section 18).

### 8.5 Concurrent Jobs on the Same Repository

Multiple jobs may target the same repository concurrently.

Every job gets its own worktree under `.codeplane-worktrees/`, providing full isolation regardless of concurrency. No job ever uses the main worktree.

Isolation between concurrent jobs is guaranteed by Git worktrees: each job works in its own worktree directory with its own branch and cannot interfere with another job's files. The main branch of the repository is never written to directly.

### 8.6 Repository Safety Enforcement

To prevent the agent from accidentally modifying unrelated files:

1. `GitService` creates the worktree before the agent session starts
2. The adapter sets `workspace_path` in the SDK's session config, which scopes agent operations to the worktree. The SDK's own subprocess inherits this scoping
3. The permission policy evaluates file operations against the worktree boundary (see §18)
4. Pushing to remote and creating PRs are allowed — these are useful agent capabilities. Push protection is not enforced at the git config level

### 8.7 Pull Request Creation After Successful Job

When a job completes successfully, CodePlane instructs the agent to create a pull request as a **post-completion step** if the GitHub CLI (`gh`) or GitHub MCP tools are available.

The flow:

1. Job reaches `review` state
2. During the `post_completion` phase, the agent is instructed to push the branch and open a PR using whichever GitHub tooling is available:
   - **GitHub MCP server** (if configured in `.vscode/mcp.json` or `tools.mcp` global config) — the agent uses the MCP `create_pull_request` tool
   - **`gh` CLI** (if available on `$PATH`) — the agent runs `gh pr create` with the branch name, prompt-derived title, and a summary body
3. If neither `gh` CLI nor GitHub MCP tools are available, the step is skipped silently and the branch remains on disk for the operator to push manually
4. The PR URL (if created) is included in the `JobReview` event payload and displayed on the Job Detail screen

This is a best-effort operation — if PR creation fails (e.g., no remote configured, auth issues), the job is still considered successful. The failure is logged as a warning.

Completed branches are **not** auto-deleted after merge. The branch remains on disk until the operator explicitly cleans it up or the retention policy removes the worktree.

### 8.8 Job Resolution

When a job succeeds with `completion.strategy = "manual"`, it enters
the Review column with `resolution = "unresolved"` in the `review` state. The operator resolves it via:

    POST /api/jobs/{id}/resolve
    Body: { "action": "merge" | "create_pr" | "discard" }

**Merge**: Attempts fast-forward merge, then regular merge. On success, resolution
becomes `merged`, job transitions to `completed`, worktree is cleaned up, and branch is deleted. On conflict, the
merge is aborted (worktree stays clean), resolution becomes `conflict`, and the job
stays in `review` with a conflict badge showing the affected files.

**Create PR**: Pushes the branch to origin and creates a PR via `gh pr create`.
Resolution becomes `pr_created`, job transitions to `completed`, worktree is cleaned up (branch kept on remote).

**Discard**: Removes the worktree and deletes the branch. Resolution becomes
`discarded`, job transitions to `completed`.

Jobs with `resolution = "conflict"` can be further resolved with `create_pr` or
`discard` (but not `merge`).

Terminal jobs can be archived to hide them from the Kanban board:

    POST /api/jobs/{id}/archive     → 204
    POST /api/jobs/{id}/unarchive   → 204

Resolved jobs are auto-archived after `retention.auto_archive_days` (default: 7 days).

### 8.9 Repository Registration

Operators manage the set of repositories CodePlane can work with through the web UI or by editing the global config directly.

#### Adding a Local Repository

The operator provides an absolute path on the developer machine (e.g. `/repos/service-a`). The backend validates that:

1. The path exists and is a directory
2. The directory is a valid Git repository (contains `.git`)
3. The path is not already registered

On success the path is appended to the `repos` list in the global config and persisted.

#### Adding a Remote Repository

The operator provides a remote URL (HTTPS or SSH) — for example `https://github.com/org/repo.git`. The backend:

1. Resolves a local clone directory from the `clone_to` field in the request (or derives one from the URL)
2. Runs `git clone <url> <target_dir>` via subprocess
3. Authentication relies entirely on the machine's existing Git credential configuration (SSH keys, credential helpers, `.netrc`, etc.). CodePlane never stores or prompts for credentials
4. On success the cloned path is appended to the global config `repos` list
5. On failure (auth error, network issue, invalid URL) a descriptive error is returned — the `repos` list is not modified

#### Removal

Operators can remove a repository from the `repos` list. This only removes the config entry — it does **not** delete the directory from disk. Active jobs targeting the removed repo continue to completion.

#### Configuration

Registered repositories are stored in the global config:

```yaml
repos:
  - /repos/service-a             # local path
  - /repos/service-b
```

For remote repositories, the clone target is specified per-request via the `clone_to` field in the register API, not as a global config setting.

---

## 9. Voice Input and Transcription

### 9.1 Overview

CodePlane supports voice input for dictating prompts and operator messages. Audio is captured in the browser, uploaded to the local backend, and transcribed locally using `faster-whisper`. No audio data is transmitted to external services.

### 9.2 Privacy Guarantee

All voice transcription runs locally on the developer machine:

- Audio is uploaded only to `localhost` (or via the authenticated tunnel)
- The `faster-whisper` library performs inference locally using downloaded model weights
- `faster-whisper` does not phone home or transmit data to external servers
- The UI displays a "Local transcription" indicator next to the microphone button
- Audio blobs are discarded after transcription; they are not persisted

### 9.3 Workflow

1. Operator presses and holds the microphone button in the browser
2. Browser requests microphone permission (`getUserMedia`)
3. `MediaRecorder` records audio chunks while button is held
4. On release, recording stops
5. Chunks are combined into a single audio blob (WebM/Opus or WAV)
6. Blob uploaded via `POST /api/voice/transcribe` as `multipart/form-data`
7. Backend transcribes and returns `{ text: "..." }`
8. Transcribed text is inserted into the active prompt or message input field

### 9.4 Frontend Implementation

```typescript
async function recordAndTranscribe(): Promise<string> {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const recorder = new MediaRecorder(stream);
  const chunks: Blob[] = [];

  recorder.ondataavailable = (e) => chunks.push(e.data);

  await new Promise<void>((resolve) => {
    recorder.onstop = () => resolve();
    // caller triggers recorder.stop() on button release
  });

  const blob = new Blob(chunks, { type: "audio/webm" });
  const form = new FormData();
  form.append("audio", blob, "recording.webm");

  const res = await fetch("/api/voice/transcribe", { method: "POST", body: form });
  const { text } = await res.json();
  return text;
}
```

### 9.5 Voice Input Contexts

Voice input is available in three contexts:

| Context | Description |
|---|---|
| Job creation prompt | Dictate the initial task prompt |
| Operator message | Dictate a mid-run instruction to send to the agent |
| Terminal session | Dictate a command (pasted into the terminal) or an assist message (inserted into the assist chat input). See §14.6.3 |

### 9.6 Transcription Engine

Local transcription uses `faster-whisper`.

| Model | RAM | Latency (approx.) | Use case |
|---|---|---|---|
| `tiny.en` | ~150 MB | 100–300 ms | Low-latency, English only |
| `base.en` | 300–400 MB | 300–800 ms | Default, better accuracy |

Default model: `base.en`

### 9.7 Configuration

Voice is always enabled with hardcoded defaults. The `max_audio_size_mb` is fixed at 10 MB. These values are not user-configurable.

### 9.8 Backend Endpoint

```
POST /api/voice/transcribe
Content-Type: multipart/form-data

Field: audio (binary, max 10 MB)
```

Response:

```json
{
  "text": "Fix the null pointer exception in the user service."
}
```

The endpoint rejects uploads exceeding `max_audio_size_mb` with `413 Payload Too Large`.

The frontend enforces the same limit client-side: it checks the recording size before uploading and shows a warning toast if the limit is exceeded, without sending the request.

### 9.9 Transcription Service

```python
from faster_whisper import WhisperModel

class VoiceService:
    def __init__(self, model_name: str = "base.en"):
        self._model = WhisperModel(model_name, device="cpu", compute_type="int8")

    def transcribe(self, audio_bytes: bytes) -> str:
        segments, _ = self._model.transcribe(audio_bytes)
        return " ".join(seg.text.strip() for seg in segments)
```

The model is loaded once at startup and reused across requests.

---

## 10. Configuration Model

### 10.1 Overview

Configuration exists at three layers:

| Layer | Location | Scope |
|---|---|---|
| Global | `~/.codeplane/config.yaml` | Machine-level runtime behavior |
| Per-repository | `{repo_root}/.codeplane.yml` | Repository-specific overrides |
| Per-job | Job creation payload | Single-job overrides |

### 10.2 Global Configuration

File: `~/.codeplane/config.yaml`

```yaml
server:\n  host: 127.0.0.1\n  port: 8080\n\nruntime:\n  max_concurrent_jobs: 2\n  worktrees_dirname: .codeplane-worktrees\n  permission_mode: full_auto        # full_auto | observe_only | review_and_approve\n  utility_model: gpt-4o-mini        # cheap/fast model for naming, summaries\n  default_sdk: copilot              # copilot | claude — SDK used when not overridden per-job\n\nterminal:\n  assist:\n    sdk: copilot                    # copilot | claude — SDK for terminal agent assistance\n    model: null                     # model override; null = use SDK default\n\nretention:\n  artifact_retention_days: 30\n  max_artifact_size_mb: 100\n  cleanup_on_startup: false\n  auto_archive_days: 7\n\ncompletion:\n  strategy: manual               # manual | auto_merge | pr_only\n  auto_push: true                   # push branch to remote on success\n  cleanup_worktree: true            # remove worktree after resolution\n  delete_branch_after_merge: true   # delete branch after merge\n\nlogging:\n  level: info\n  file: ~/.codeplane/logs/server.log\n  max_file_size_mb: 50\n  backup_count: 3\n\nrate_limits:\n  max_sse_connections: 5\n\nplatforms:                          # per-platform auth and repo binding\n  github:\n    auth: cli                       # cli | token\n    repos:\n      - /repos/service-a\n\nrepos:\n  - /repos/service-a\n  - /repos/service-b\n\ntools:\n  mcp:\n    github:\n      command: npx\n      args: [\"-y\", \"@modelcontextprotocol/server-github\"]\n    postgres:\n      command: uvx\n      args: [\"mcp-postgres\"]\n      env:\n        DATABASE_URL: \"${DATABASE_URL}\"\n```

Entries support glob patterns via Python's `glob.glob`. Each pattern is expanded at startup and re-expanded when the config is reloaded. Only directories that are valid git repositories (contain `.git`) are included after expansion.

#### 10.2.1 MCP Server Discovery

CodePlane discovers MCP servers from two sources, merged as a union:

1. `.vscode/mcp.json` in the repo — `"servers"` key (VS Code / Copilot convention)
2. `tools.mcp` block in CodePlane's global config (`~/.codeplane/config.yaml`)

If the same server name appears in both, the repo-level `.vscode/mcp.json` takes precedence.

Entries are normalized into `MCPServerConfig` and passed to the SDK.

Example `.vscode/mcp.json` (already in the repo):

```json
{
  "servers": {
    "github": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"]
    },
    "postgres": {
      "type": "stdio",
      "command": "uvx",
      "args": ["mcp-postgres"],
      "env": { "DATABASE_URL": "${DATABASE_URL}" }
    }
  }
}
```

The per-repo `.codeplane.yml` can optionally disable specific discovered servers:

```yaml
tools:
  mcp:
    disabled:
      - postgres
```

This keeps CodePlane out of the business of defining MCP servers and lets developers use the same config they already have.

### 10.3 Per-Repository Configuration

File: `{repo_root}/.codeplane.yml`

```yaml
base_branch: main

protected_paths:
  - infra/
  - .github/workflows/

tools:
  mcp:
    disabled:
      - postgres
```

| Field | Description |
|---|---|
| `base_branch` | Branch to create worktree from |
| `protected_paths` | Paths that require approval before modification |
| `tools.mcp.disabled` | MCP servers discovered in the repo to exclude from sessions |

### 10.4 Per-Job Overrides

Provided in the job creation request body:

```json
{
  "repo": "/repos/service-a",
  "prompt": "Fix the null pointer exception in UserService.java",
  "base_ref": "main",
  "sdk": "claude",
  "model": "claude-sonnet-4-20250514"
}
```

| Field | Required | Description |
|---|---|---|
| `repo` | Yes | Path to repository (must be in allowlist) |
| `prompt` | Yes | Task description for the agent |
| `base_ref` | No | Override base branch/commit |
| `sdk` | No | Override SDK for this job (`copilot`, `claude`); defaults to `runtime.default_sdk` |
| `model` | No | Override model for this job; must be compatible with the selected SDK (see §4.4) |

---

## 11. Canonical Internal Event Model

All runtime activity is represented as structured domain events. Every event has a shared envelope:

```python
class DomainEventKind(str, Enum):
    job_created = "JobCreated"
    workspace_prepared = "WorkspacePrepared"
    agent_session_started = "AgentSessionStarted"
    log_line_emitted = "LogLineEmitted"
    transcript_updated = "TranscriptUpdated"
    diff_updated = "DiffUpdated"
    approval_requested = "ApprovalRequested"
    approval_resolved = "ApprovalResolved"
    job_succeeded = "JobSucceeded"
    job_failed = "JobFailed"
    job_canceled = "JobCanceled"
    job_state_changed = "JobStateChanged"
    session_heartbeat = "SessionHeartbeat"
    merge_completed = "MergeCompleted"
    merge_conflict = "MergeConflict"
    session_resumed = "SessionResumed"
    job_resolved = "JobResolved"
    job_archived = "JobArchived"
    job_title_updated = "JobTitleUpdated"
    progress_headline = "ProgressHeadline"
    model_downgraded = "ModelDowngraded"
    tool_group_summary = "ToolGroupSummary"

@dataclass
class DomainEvent:
    event_id: str       # UUID
    job_id: str
    timestamp: datetime
    kind: DomainEventKind
    payload: dict
```

### 11.1 Event Types

| Event Kind | Trigger | Key Payload Fields |
|---|---|---|
| `JobCreated` | Job creation request accepted | `repo`, `prompt`, `base_ref` |
| `WorkspacePrepared` | Worktree and branch created | `worktree_path`, `branch` |
| `AgentSessionStarted` | Agent session created | `session_id` |
| `LogLineEmitted` | Agent or system log output | `seq`, `level`, `message`, `context` |
| `TranscriptUpdated` | Agent reasoning or operator message | `seq`, `role`, `content` |
| `DiffUpdated` | File changes detected in worktree | `changed_files` (list of DiffFile) |
| `ApprovalRequested` | SDK permission request intercepted | `approval_id`, `description`, `proposed_action` |
| `ApprovalResolved` | Operator approves or rejects | `approval_id`, `resolution` |
| `JobSucceeded` | Session completed successfully | `pr_url`, `merge_status`, `resolution` |
| `JobFailed` | Session terminated with error | `reason` |
| `JobCanceled` | Operator canceled the job | `reason` |
| `JobStateChanged` | Job transitions between states | `previous_state`, `new_state` |
| `SessionHeartbeat` | Periodic heartbeat from running session | `session_id` |
| `MergeCompleted` | Merge-back succeeded | `branch`, `base_ref`, `strategy` |
| `MergeConflict` | Merge-back hit conflicts | `branch`, `conflict_files`, `fallback` |
| `SessionResumed` | Agent session resumed after failure | `session_number` |
| `JobResolved` | Operator resolved a succeeded job | `resolution`, `pr_url`, `conflict_files` |
| `JobArchived` | Job moved to archive | _(none)_ |
| `JobTitleUpdated` | LLM generated or updated job title | `title`, `branch` |
| `ProgressHeadline` | Agent progress summary | `headline` |
| `ModelDowngraded` | Requested model unavailable, fallback used | `requested_model`, `actual_model` |
| `ToolGroupSummary` | AI-generated summary for tool group | `turn_id`, `summary` |

### 11.2 Event Consumers

| Consumer | Events consumed | Action |
|---|---|---|
| `JobStateMachine` | All state-relevant events | Applies state transitions |
| `PersistenceSubscriber` | All events | Persists to SQLite event log |
| `SSEManager` | All events | Pushes to connected SSE clients |
| `ApprovalService` | `ApprovalRequested` | Persists request, awaits operator resolution |
| `DiffService` | `WorkspacePrepared`, `JobSucceeded` | Generates and stores diff snapshots |
| `ArtifactService` | `JobSucceeded` | Collects and stores artifacts |
| `TimelineBuilder` | All events | Updates job timeline view |

---

## 12. Job States

### 12.0 Job ID Generation

Job IDs use a sequential integer with a `job-` prefix, backed by SQLite autoincrement. The `jobs` table uses `TEXT` primary key but the value is always `job-{N}` where `N` is the next integer from an internal sequence.

Examples: `job-1`, `job-104`, `job-2057`

Sequential integers are preferred over UUIDs because:
- Branch names remain human-readable (`fix/null-pointer-in-user-service`)
- Job IDs are easy to reference in conversation
- There is only one instance of CodePlane; global uniqueness is not required

### 12.1 States

| State | Description |
|---|---|
| `queued` | Job accepted but not yet started (at capacity) |
| `running` | Agent session is active |
| `waiting_for_approval` | Session paused, awaiting operator decision |
| `review` | Agent session exited cleanly; awaiting operator review and resolution |
| `completed` | Operator resolved the job (merged, PR created, or discarded) |
| `failed` | Session terminated with an error |
| `canceled` | Operator canceled the job |

Jobs in `review` carry a **resolution status** that tracks how the job's changes were handled: `unresolved` (awaiting operator decision), `conflict` (merge attempted but conflicts detected). Jobs move to `completed` when the operator resolves them with `merged`, `pr_created`, or `discarded`.

### 12.2 State Transition Table

| From | Event | To |
|---|---|---|
| _(none)_ | `JobCreated` + capacity available | `running` |
| _(none)_ | `JobCreated` + at capacity | `queued` |
| `queued` | Capacity opens | `running` |
| `queued` | `JobCanceled` | `canceled` |
| `running` | `ApprovalRequested` | `waiting_for_approval` |
| `running` | `JobReview` (agent done) | `review` |
| `running` | `JobFailed` | `failed` |
| `running` | `JobCanceled` | `canceled` |
| `waiting_for_approval` | `ApprovalResolved` (approved) | `running` |
| `waiting_for_approval` | `ApprovalResolved` (rejected) | `failed` |
| `waiting_for_approval` | `JobCanceled` | `canceled` |
| `review` | Operator resolves (merge/PR/discard) | `completed` |
| `review` | Operator resumes with instructions | `running` |
| `review` | `JobCanceled` | `canceled` |

Terminal states (`completed`, `failed`, `canceled`) can transition back to `running` for job resumption.

After reaching `review`, the job enters a resolution lifecycle managed by `POST /api/jobs/{id}/resolve`. A successful resolution (merge, PR, discard) transitions the job to `completed`. Resolution transitions: `unresolved` → `merged|pr_created|discarded|conflict`, `conflict` → `pr_created|discarded`.

### 12.3 Rerun

Rerunning a job creates a new job record. The original job is not mutated. The new job copies the original's `repo`, `prompt`, and `base_ref`.

---

## 13. Execution Phases

| Phase | Description | Example events |
|---|---|---|
| `environment_setup` | Workspace creation, branch, dependency install | `WorkspacePrepared`, `LogLineEmitted` |
| `agent_reasoning` | Agent reads code, thinks, plans, and writes changes | `TranscriptUpdated`, `DiffUpdated`, `ApprovalRequested` |
| `finalization` | Final diff snapshot, artifact collection | `DiffUpdated`, `JobReview` |
| `post_completion` | Operator reviews, approves, or reruns | _(no agent events)_ |

Artifacts and timeline entries carry the phase in which they were produced. The frontend uses phase labels to group the execution timeline.

---

## 14. User Interface

### 14.1 Dashboard

**Desktop layout: Kanban board** (viewport width ≥ 1024px)

Columns:

| Column | States shown |
|---|---|
| Active | `queued`, `running` |
| Review | `waiting_for_approval`, `review` |
| Failed | `failed`, `canceled` |
| History | `completed` (excludes archived) |

Each card displays: job ID, repository name, prompt excerpt, elapsed time, and status badge.

The History column shows the most recent 50 jobs by default. A "Load more" button fetches the next page via `GET /api/jobs?state=completed,canceled&limit=50&cursor={last_id}`. The column uses virtualized rendering for smooth scrolling.

**Mobile layout: Filtered job list** (viewport width < 1024px)

A single scrollable list of jobs. Filter tabs at the top correspond to the Kanban columns. Tapping a job opens the Job Detail screen.

#### Responsive Breakpoints

| Breakpoint | Layout |
|---|---|
| ≥ 1024px | Kanban board (4 columns) |
| 768px – 1023px | Kanban board (2 columns, stacked) |
| < 768px | Mobile job list with filter tabs |

The Job Detail screen uses a single-column stacked layout on viewports below 768px. Panels (Transcript, Logs, Diff, etc.) become collapsible accordion sections.

### 14.2 Job Detail Screen

Sections:

| Section | Contents |
|---|---|
| **Job Metadata Header** | Job ID, repo, branch, state badge, started/completed timestamps |
| **Approval Banner** | Shown only in `waiting_for_approval` state. Displays description, proposed action, and Approve/Reject buttons |
| **Transcript Panel** | Scrolling list of agent reasoning messages and operator injections. Auto-scrolls to bottom on new entries |
| **Logs Panel** | Raw log output with level filtering (debug/info/warn/error). Virtualized list |
| **Diff Viewer** | Per-file diffs with syntax highlighting, additions/deletions counts, and hunk navigation |
| **Workspace Browser** | File tree of the worktree. Click a file to view its contents |
| **Artifact Viewer** | List of collected artifacts with type badges and download links. Always visible as a tab; shows an empty state when no artifacts are collected yet |
| **Execution Timeline** | Chronological list of key events grouped by phase |
| **Terminal Pane** | Inline xterm.js terminal scoped to the job's worktree. Only shown when the job has a worktree. See §14.6 |

#### Concurrent Approval Notifications

When multiple jobs are simultaneously in `waiting_for_approval` state:

- The Sign-off column on the dashboard clearly shows each pending approval with its job ID
- On mobile, a persistent badge on the "Sign-off" filter tab shows the count of pending approvals
- If the operator is viewing a different job's detail screen, a toast notification appears for new approval requests with a "View" link

### 14.3 Job Creation Screen

Fields:

| Field | Type | Notes |
|---|---|---|
| Repository | Dropdown | Only repositories in allowlist |
| Prompt | Textarea + voice button | Task description |
| Base reference | Text | Default: repo's `base_branch` |

### 14.4 Settings Screen

Sections:

- **Repository management** — Add repositories from local disk (browse/type path) or from a remote hub (paste URL). List of registered repos with remove action. Clone progress indicator for remote repos
- Global config viewer/editor (YAML text editor with validation)
- Repository config list (per-repo `.codeplane.yml` viewer)
- Worktree cleanup action
- Voice model selector
- **Terminal Assistance** — Default SDK and model for agent assistance in terminal sessions (see §14.6.2)

| Field | Type | Notes |
|---|---|---|
| Assistance SDK | Dropdown (`copilot` / `claude`) | Which SDK powers the terminal assist agent |
| Default Model | Dropdown + "Auto" option | Model used for assist; filtered to models compatible with selected SDK. "Auto" uses the SDK's default model |

### 14.5 Repository Detail View

Accessible by clicking a repository in the Settings Screen repo list or from the repo selector anywhere in the UI. Displays the full resolved configuration for a single repository.

Sections:

| Section | Contents |
|---|---|
| **Header** | Repository name (derived from path), absolute path on disk, remote origin URL (if any) |
| **Tool / MCP Configuration** | Table of all MCP servers available to this repo. Each row shows: server name, command, source badge (`local` — from `.vscode/mcp.json`, `global` — from global config, `disabled` — blocked by `.codeplane.yml`). Inherited global servers are shown with a dimmed style if not overridden |
| **Repo Config** | Rendered view of the repo's `.codeplane.yml` — `base_branch`, `protected_paths`, `tools.mcp.disabled`. Shows defaults for fields not explicitly set |
| **Active Jobs** | List of currently active jobs targeting this repo (links to Job Detail) |
| **Recent Jobs** | Last 10 completed/failed/canceled jobs for this repo |

#### MCP / Tool Resolution Display

For each MCP server, the view shows the resolution chain so the operator understands where the config comes from:

```
┌─────────────┬──────────────────┬──────────┐
│ Server      │ Command          │ Source   │
├─────────────┼──────────────────┼──────────┤
│ github      │ npx -y @model... │ local    │  ← .vscode/mcp.json (overrides global)
│ postgres    │ uvx mcp-postgres │ disabled │  ← global, disabled by .codeplane.yml
│ filesystem  │ npx -y @model... │ global   │  ← inherited from global config
└─────────────┴──────────────────┴──────────┘
```

### 14.6 Terminal Sessions

CodePlane provides integrated terminal sessions backed by backend PTY processes connected via WebSocket. There are two distinct contexts with different scoping rules.

#### 14.6.1 Global Terminal (Drawer)

The global terminal is a persistent bottom drawer rendered at the `App` level (outside `<Routes>`), so it survives page navigation. It supports:

- **Multiple session tabs** — each tab is an independent PTY session
- **Resize via drag** — drag handle at the top edge; min 150px, max 70% of viewport
- **Maximize / minimize** — toolbar buttons to expand or collapse
- **Keyboard shortcut** — `Ctrl+`` `` ` toggles the drawer open/closed
- **Create / close sessions** — `+` button creates a new session; `×` closes one

Global terminal sessions have no `jobId`. They default their `cwd` to the user's home directory (or the first registered repository).

Job-scoped terminal sessions (those created from the Job Detail screen) do **not** appear in the global drawer tab bar. The drawer filters its session list to exclude any session with a non-null `jobId`.

#### 14.6.2 Per-Job Terminal (Inline Pane)

When a job has a worktree, the Job Detail screen shows a "Terminal" tab alongside Live, Files, Changes, and Artifacts. Clicking this tab creates a terminal session scoped to the job's worktree directory.

Per-job terminals:

- Render **inline** within the Job Detail tab content area (not as a drawer)
- Are scoped to the job's `worktreePath` as `cwd`
- Carry a `jobId` in their session metadata
- Do **not** open or interact with the global terminal drawer
- Are stored in the global Zustand store for WebSocket lifecycle management, but are filtered out of the drawer's tab list

#### 14.6.3 Agent Assistance

Both global and per-job terminal sessions can optionally activate an **Agent Assist** sidecar — a lightweight conversational AI agent scoped to the terminal's working directory.

**Activation**: A robot icon (🤖) in the terminal toolbar toggles the assist panel. When activated, the terminal area splits horizontally:

```
┌──────────────────────────────────────────────┐
│ [Tab1] [Tab2] [+]              [🎤] [🤖] [−]│  ← toolbar: voice, assist toggle, minimize
├────────────────────────┬─────────────────────┤
│                        │  Agent Assist       │
│   Terminal (xterm)     │  ┌───────────────┐  │
│   $ ls -la             │  │ Chat history   │  │
│   $ npm test           │  │ (streaming)    │  │
│                        │  ├───────────────┤  │
│                        │  │ Ask... [🎤][↵]│  │
└────────────────────────┴─────────────────────┘
```

**Agent Assist behavior**:

- The assist agent is a **separate, lightweight agent instance** — not a full job agent. It does not create worktrees, branches, or jobs.
- Scoped to the terminal session's `cwd` for file context.
- Has **read access** to the terminal's scrollback buffer, which is sent as context with each assist message.
- Responses stream as markdown via SSE.
- The agent uses the SDK and model configured in global settings under `terminal.assist` (see §10.2). Per-job terminals inherit the same configuration.
- Assist chat history is ephemeral — cleared when the terminal session is closed.

**Voice input in assist**: The assist chat input includes a microphone button (🎤) that uses the same voice transcription flow as job creation (§9). Transcribed text is inserted into the assist input field.

**Voice input in terminal toolbar**: A microphone button in the terminal toolbar (outside the assist panel) transcribes speech and pastes the result directly into the terminal as typed input.

#### 14.6.4 Terminal Session Lifecycle

| Event | Behavior |
|---|---|
| Session created | Backend spawns a PTY process, returns session ID. Frontend connects via WebSocket |
| Session closed (tab ×) | Frontend sends `DELETE /api/terminal/sessions/{id}`, backend kills PTY |
| Page navigation (global) | Drawer persists — sessions remain connected |
| Page navigation (per-job) | Session remains in store; reconnects if user returns to job |
| Server restart | All PTY sessions are lost; frontend shows "disconnected" state |

---

## 15. Data Model

### 15.1 SQLite Schema

#### jobs

```sql
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    repo TEXT NOT NULL,
    prompt TEXT NOT NULL,
    state TEXT NOT NULL,
    base_ref TEXT NOT NULL,
    branch TEXT,
    worktree_path TEXT,
    session_id TEXT,
    pr_url TEXT,
    merge_status TEXT,
    resolution TEXT,
    archived_at TEXT,
    title TEXT,
    worktree_name TEXT,
    permission_mode TEXT NOT NULL DEFAULT 'full_auto',
    session_count INTEGER NOT NULL DEFAULT 1,
    sdk_session_id TEXT,
    model TEXT,
    sdk TEXT NOT NULL DEFAULT 'copilot',
    failure_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);
```

#### events

```sql
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    job_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    payload TEXT NOT NULL,        -- JSON
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);
CREATE INDEX idx_events_job_id ON events(job_id);
CREATE INDEX idx_events_id ON events(id);
```

#### approvals

```sql
CREATE TABLE approvals (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    description TEXT NOT NULL,
    proposed_action TEXT,
    requested_at TEXT NOT NULL,
    resolved_at TEXT,
    resolution TEXT,
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);
```

#### artifacts

```sql
CREATE TABLE artifacts (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    name TEXT NOT NULL,
    type TEXT NOT NULL,              -- 'diff_snapshot', 'agent_summary', 'custom'
    mime_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    disk_path TEXT NOT NULL,
    phase TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);
```

#### Artifact Types

| Type | Description | Collection trigger |
|---|---|---|
| `diff_snapshot` | Final unified diff of all changes | `JobSucceeded` or `JobFailed` |
| `agent_summary` | Agent's self-reported summary of work done | `JobSucceeded` |
| `custom` | Files placed by the agent in `.codeplane/artifacts/` inside the worktree | `JobSucceeded` |

Custom artifacts are collected by scanning `{worktree_path}/.codeplane/artifacts/` at job completion. Each file found is registered as an artifact with `type: custom`.

#### diff_snapshots

```sql
CREATE TABLE diff_snapshots (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    snapshot_at TEXT NOT NULL,
    diff_json TEXT NOT NULL,      -- serialized list of DiffFile
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);
CREATE INDEX idx_diff_snapshots_job_id ON diff_snapshots(job_id);
```

---

## 16. Persistence

### 16.1 Storage Layout

```
~/.codeplane/
├── config.yaml
├── data.db            # SQLite database
└── artifacts/
    └── {job_id}/
        └── {artifact_id}-{name}
```

### 16.2 Migrations

Schema migrations are managed with Alembic. The backend runs `alembic upgrade head` at startup before accepting requests.

### 16.3 Persistence Layer Design

All database access is mediated through repository classes. No SQLAlchemy sessions are used directly in services or route handlers.

```python
class JobRepository:
    def create(self, job: Job) -> Job: ...
    def get(self, job_id: str) -> Job | None: ...
    def list(self, state: str | None = None) -> list[Job]: ...
    def update_state(self, job_id: str, new_state: str, updated_at: datetime) -> None: ...

class EventRepository:
    def append(self, event: DomainEvent) -> None: ...
    def list_after(self, after_id: int, job_id: str | None = None) -> list[DomainEvent]: ...

class ArtifactRepository:
    def create(self, artifact: Artifact) -> Artifact: ...
    def list_for_job(self, job_id: str) -> list[Artifact]: ...
    def get(self, artifact_id: str) -> Artifact | None: ...
```

### 16.4 Retention Policy

Artifacts and associated data accumulate over time. The retention policy prevents unbounded growth:

- **Artifact retention**: Artifacts older than `retention.artifact_retention_days` (default: 30 days) are deleted from disk and database
- **Maximum artifact size**: Individual artifacts exceeding `retention.max_artifact_size_mb` (default: 100 MB) are rejected at collection time
- **Cleanup schedule**: Retention cleanup runs once daily as a background task. It can also be triggered manually via `POST /api/settings/cleanup-worktrees`
- **Cleanup on startup**: If `retention.cleanup_on_startup` is `true`, cleanup runs during startup after recovery

Retention cleanup removes:
1. Artifact files from `~/.codeplane/artifacts/{job_id}/`
2. Artifact metadata from the `artifacts` table
3. Diff snapshots from the `diff_snapshots` table
4. Worktree directories for jobs in terminal states older than the retention period

Job records and events are never deleted. They serve as an audit log.
```

---

## 17. REST API

All endpoints are prefixed with `/api`.

Authentication is handled by password-based session cookies when accessed remotely via tunnel (see §21.1). The backend binds to `127.0.0.1` by default, so direct access is limited to the local machine. Localhost requests bypass authentication.

### 17.1 Health Check

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/health` | Service health and status |

The health endpoint does **not** require authentication. It returns:

```json
{
  "status": "healthy",
  "version": "0.1.0",
  "uptime_seconds": 3621.5,
  "active_jobs": 1,
  "queued_jobs": 0
}
```

### 17.2 Jobs

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/jobs` | Create a new job |
| `GET` | `/api/jobs` | List jobs (filterable, paginated) |
| `GET` | `/api/jobs/{job_id}` | Get full job detail |
| `POST` | `/api/jobs/{job_id}/cancel` | Cancel a running or queued job |
| `POST` | `/api/jobs/{job_id}/rerun` | Create a new job from this job's config |
| `POST` | `/api/jobs/{job_id}/messages` | Send an operator message to a running job |

#### Pagination

List endpoints support cursor-based pagination:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `limit` | integer | 50 | Maximum items to return (max: 200) |
| `cursor` | string | _(none)_ | Opaque cursor from previous response |
| `state` | string | _(none)_ | Comma-separated state filter |

Response envelope for list endpoints:

```json
{
  "items": [...],
  "cursor": "eyJpZCI6ICJqb2ItNTAifQ",
  "has_more": true
}
```

#### Create Job — Request

```json
POST /api/jobs
{
  "repo": "/repos/service-a",
  "prompt": "Fix the null pointer in UserService",
  "base_ref": "main",
  "sdk": "claude",
  "model": "claude-sonnet-4-20250514"
}
```

The `sdk` field selects the agent SDK adapter (default: `runtime.default_sdk` from config). The `model` field overrides the LLM model; it must be compatible with the selected SDK (see §4.4). Incompatible combinations return `400 Bad Request`.

#### Create Job — Response

```json
201 Created
{
  "id": "job-104",
  "state": "running",
  "branch": "fix/null-pointer-in-userservice",
  "worktreePath": "/repos/service-a",
  "sdk": "claude",
  "createdAt": "2025-01-01T12:00:00Z"
}
```

#### Send Operator Message — Request

```json
POST /api/jobs/{job_id}/messages
{
  "content": "Also add unit tests for the fix."
}
```

#### Send Operator Message — Response

```json
200 OK
{
  "seq": 5,
  "timestamp": "2025-01-01T12:10:00Z"
}
```

The endpoint returns `409 Conflict` if the job is not in `running` state.

### 17.3 Events (SSE)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/events` | SSE stream for all jobs |
| `GET` | `/api/events?job_id={id}` | SSE stream scoped to one job |

### 17.4 Approvals

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/jobs/{job_id}/approvals` | List approvals for a job |
| `POST` | `/api/approvals/{approval_id}/resolve` | Approve or reject |

#### Resolve Approval — Request

```json
POST /api/approvals/{approval_id}/resolve
{
  "resolution": "approved"    // or "rejected"
}
```

### 17.5 Artifacts

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/jobs/{job_id}/artifacts` | List artifacts for a job |
| `GET` | `/api/artifacts/{artifact_id}` | Download artifact file |

### 17.6 Workspace

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/jobs/{job_id}/workspace` | List files in job's worktree (paginated, max 200 entries) |
| `GET` | `/api/jobs/{job_id}/workspace/file` | Get file contents (`?path=relative/path`) |

### 17.7 Voice

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/voice/transcribe` | Upload audio, receive transcript |

### 17.8 Settings

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/settings` | Get current settings |
| `PUT` | `/api/settings` | Update settings (structured, partial) |
| `GET` | `/api/settings/repos` | List registered repos |
| `GET` | `/api/settings/repos/{repo_path}` | Get detailed repo config with resolved MCP servers |
| `POST` | `/api/settings/repos` | Register a repository (local path or remote URL) |
| `DELETE` | `/api/settings/repos/{repo_path}` | Remove a repository from the allowlist |
| `POST` | `/api/settings/cleanup-worktrees` | Clean up completed job worktrees |

#### `POST /api/settings/repos` — Register Repository

Request body:

```json
{
  "source": "/repos/service-c"
}
```

`source` is either an absolute local path or a remote Git URL (HTTPS/SSH). The backend auto-detects the type:

- **Local path** (starts with `/` or `~`): validated as an existing Git repository
- **Remote URL** (contains `://` or matches `git@`): cloned to the target specified by `clone_to` (or derived from URL) via `git clone` subprocess

Response (`201 Created`):

```json
{
  "path": "/home/user/codeplane-repos/org/repo",
  "source": "https://github.com/org/repo.git",
  "cloned": true
}
```

Errors: `400` if path doesn't exist or isn't a git repo, `409` if already registered, `502` if clone fails (auth/network).

#### `GET /api/settings/repos/{repo_path}` — Repository Detail

Returns the fully resolved configuration for a single repository, including the MCP server resolution chain.

Response (`200 OK`):

```json
{
  "path": "/repos/service-a",
  "origin_url": "https://github.com/org/service-a.git",
  "base_branch": "main",
  "protected_paths": ["infra/", ".github/workflows/"],
  "mcp_servers": [
    {
      "name": "github",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "source": "local",
      "overrides_global": true
    },
    {
      "name": "postgres",
      "command": "uvx",
      "args": ["mcp-postgres"],
      "source": "disabled",
      "disabled_by": ".codeplane.yml"
    },
    {
      "name": "filesystem",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem"],
      "source": "global",
      "overrides_global": false
    }
  ],
  "active_job_count": 1,
  "recent_jobs": [{"id": "job-42", "state": "succeeded", "prompt": "..."}]
}
```

`source` is one of: `local` (from `.vscode/mcp.json`), `global` (inherited from global config), `disabled` (present but blocked by `.codeplane.yml`).

### 17.9 SDKs

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/sdks` | List available agent SDKs and their status |

Returns all registered SDKs, their installation/configuration status, and which is the default:

```json
{
  "default": "copilot",
  "sdks": [
    {
      "id": "copilot",
      "name": "GitHub Copilot",
      "enabled": true,
      "status": "ready"
    },
    {
      "id": "claude",
      "name": "Claude Agent SDK",
      "enabled": true,
      "status": "ready"
    }
  ]
}
```

`status` is one of: `ready` (installed and configured), `not_installed` (SDK package missing), `not_configured` (package installed but credentials missing).

### 17.10 Terminal Sessions

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/terminal/sessions` | Create a new PTY session |
| `DELETE` | `/api/terminal/sessions/{id}` | Kill a PTY session and clean up |
| `GET` | `/api/terminal/sessions/{id}/ws` | WebSocket connection for terminal I/O |
| `POST` | `/api/terminal/sessions/{id}/assist` | Send a message to the session's assist agent |
| `GET` | `/api/terminal/sessions/{id}/assist/stream` | SSE stream of assist agent responses |

#### `POST /api/terminal/sessions` — Create Session

Request body:

```json
{
  "cwd": "/repos/service-a",
  "jobId": null
}
```

`cwd` defaults to the first registered repository when `null`. `jobId` is set when creating a job-scoped terminal.

Response (`201 Created`):

```json
{
  "id": "term-abc123",
  "cwd": "/repos/service-a",
  "jobId": null
}
```

#### `POST /api/terminal/sessions/{id}/assist` — Assist Message

Request body:

```json
{
  "message": "How do I fix the failing test in auth_test.py?",
  "includeScrollback": true
}
```

When `includeScrollback` is `true`, the backend reads the last N lines (default 200) from the PTY scrollback buffer and includes them as context for the assist agent.

Response (`200 OK`):

```json
{
  "assistMessageId": "assist-msg-42"
}
```

The actual response content streams via the SSE endpoint.

#### `GET /api/terminal/sessions/{id}/assist/stream` — Assist Response Stream

SSE endpoint that streams assist agent responses. Each event contains a chunk of the agent's markdown response:

```
event: assist_chunk
data: {"assistMessageId": "assist-msg-42", "content": "The test is failing because...", "done": false}

event: assist_chunk
data: {"assistMessageId": "assist-msg-42", "content": "", "done": true}
```

The assist agent uses the SDK and model configured in `terminal.assist` from global settings (§10.2). If no configuration is set, it falls back to `runtime.default_sdk` with that SDK's default model.

### 17.12 Connection Limits

SSE connections are limited to `max_sse_connections` concurrent connections (default: 5).

Voice transcription uploads are limited to `max_audio_size_mb` (default: 10 MB).

No per-request rate limiting is applied. CodePlane runs on a single developer machine accessed by one browser and optionally one phone — throttling REST requests adds complexity without value.

### 17.13 Error Responses

All errors return a consistent envelope:

```json
{
  "error": {
    "code": "JOB_NOT_FOUND",
    "message": "Job job-999 does not exist."
  }
}
```

| HTTP Status | Condition |
|---|---|
| 400 | Validation error in request |
| 403 | Repository not in allowlist, or protected path violation |
| 404 | Resource not found |
| 409 | State conflict (e.g., cancel an already-completed job) |
| 413 | Payload too large (voice upload exceeds `max_audio_size_mb`) |
| 500 | Internal server error |

#### Error Codes

| Code | Used by | Description |
|---|---|---|
| `VALIDATION_ERROR` | 400 | Request body failed Pydantic validation |
| `REPO_NOT_ALLOWED` | 403 | Repository path not in allowlist |
| `PROTECTED_PATH` | 403 | Operation targets a protected path |
| `JOB_NOT_FOUND` | 404 | Job ID does not exist |
| `APPROVAL_NOT_FOUND` | 404 | Approval ID does not exist |
| `ARTIFACT_NOT_FOUND` | 404 | Artifact ID does not exist |
| `FILE_NOT_FOUND` | 404 | Workspace file path does not exist |
| `STATE_CONFLICT` | 409 | Action invalid for current job state |
| `PAYLOAD_TOO_LARGE` | 413 | Upload exceeds size limit |
| `INTERNAL_ERROR` | 500 | Unexpected server error |

---

## 18. Approval System

### 18.1 Purpose

Approval gates intercept risky operations before they execute. This ensures the operator maintains control over destructive or irreversible actions.

### 18.2 Hard-Gated Commands

Certain shell commands are **always** routed to the operator for approval, regardless of the active permission mode — even in `auto` mode. These commands are irreversible or bypass CodePlane's managed workflows:

| Pattern | Reason |
|---------|--------|
| `git merge …` | Bypasses CodePlane merge controls |
| `git pull …` | Implicitly merges remote changes |
| `git rebase …` | Rewrites history / merges changes |
| `git cherry-pick …` | Merges a specific commit |
| `git reset --hard …` | Destructive — discards uncommitted work |

Hard-gated commands return `ask` from the permission policy. The agent system prompt also instructs the agent not to run these commands; the hard gate is a defence-in-depth backstop.

### 18.3 Permission Modes

CodePlane supports three permission modes that control how the SDK's permission requests are handled.

| Mode | Behavior |
|------|----------|
| `full_auto` (default) | Full trust: auto-approve **all** operations including reads, writes, shells, MCP tools, and URL fetches. The agent operates without any approval interruptions. **Exception:** hard-gated commands (§18.2) still require approval. |
| `observe_only` | Allow reads within the worktree and read-only shell commands (grep, find, ls, cat, etc.). Deny all writes, URL fetches, and mutating operations. |
| `review_and_approve` | Auto-approve reads and read-only shell commands. Require operator approval for all writes, non-read-only shells, URL fetches, mutating MCP tools, and custom tools. |

#### Full Auto Mode Rules

| Request Kind | Decision |
|-------------|----------|
| `read` | ✅ approve |
| `write` (within workspace) | ✅ approve |
| `write` (outside workspace) | ✅ approve |
| `shell` | ✅ approve |
| `url` | ✅ approve |
| `mcp` | ✅ approve |
| `memory` | ✅ approve |

#### Observe Only Mode Rules

| Request Kind | Within Workspace | Outside Workspace |
|-------------|------------------|-------------------|
| `read` | ✅ approve | ❌ deny |
| `write` | ❌ deny | ❌ deny |
| `shell` (grep/find/ls/cat…) | ✅ approve | ✅ approve |
| `shell` (other) | ❌ deny | ❌ deny |
| `url` | ❌ deny | ❌ deny |
| `mcp` (read-only) | ✅ approve | ✅ approve |
| `mcp` (mutating) | ❌ deny | ❌ deny |
| `memory` | ✅ approve | ✅ approve |

#### Review & Approve Mode Rules

| Request Kind | Decision |
|-------------|----------|
| `read` | ✅ approve |
| `write` | ❓ ask |
| `shell` (grep/find/ls/cat…) | ✅ approve |
| `shell` (other) | ❓ ask |
| `url` | ❓ ask |
| `mcp` (read-only) | ✅ approve |
| `mcp` (mutating) | ❓ ask |
| `custom-tool` | ❓ ask |
| `memory` | ✅ approve |

#### Configuration

Permission mode is resolved with this priority chain (first match wins):

1. **Per-job** — `permission_mode` field in `POST /api/jobs` request body
2. **Per-repo** — `permission_mode` key in `.codeplane.yml`
3. **Global** — `runtime.permission_mode` in CodePlane's `config.yaml`

Default: `full_auto`

Example `.codeplane.yml`:
```yaml
permission_mode: full_auto

protected_paths:
  - infra/
  - .github/workflows/
```

Example global config:
```yaml
runtime:
  max_concurrent_jobs: 2
  permission_mode: full_auto
```

### 18.4 Delegation to the Agent Runtime

The underlying agent SDK (Copilot SDK, Claude Code, etc.) decides **which** actions surface a permission request. CodePlane's permission policy then evaluates the request against the active mode to decide whether to auto-approve, deny, or route to the operator.

CodePlane's role is to:

1. **Evaluate** the SDK's permission request against the active permission mode
2. **Auto-approve** requests that the policy allows (the SDK proceeds immediately)
3. **Deny** requests that the policy blocks (in `observe_only` mode)
4. **Route** remaining requests to the operator via the UI (in `review_and_approve` mode; the SDK blocks until resolved)
5. **Relay** the operator's decision back to the SDK
6. **Persist** approval requests and resolutions for auditability
7. **Feed repo-level config** (like `protected_paths`) into the policy at session creation time

#### How `protected_paths` Maps to Policy

The per-repo `protected_paths` list (Section 10.3) is evaluated by the permission policy at the CodePlane level. In `full_auto` mode, any write targeting a protected path prefix is escalated to the operator regardless of whether it's inside the workspace.

### 18.5 Approval Request Object

```json
{
  "id": "appr-88",
  "job_id": "job-104",
  "description": "Agent wants to delete all files in /tmp/build",
  "proposed_action": "rm -rf /tmp/build",
  "requested_at": "2025-01-01T12:05:00Z"
}
```

The adapter normalizes whichever fields the SDK provides into this common shape. Fields the SDK doesn't supply are omitted.

### 18.6 Approval Flow

1. SDK raises a permission request (e.g., Copilot SDK calls `on_permission_request`)
2. Adapter evaluates the permission policy:
   - **Hard-gated commands (§18.2):** always returns `ask` — routes to operator regardless of mode
   - **`full_auto` mode:** returns `approved` immediately for all non-hard-gated operations — SDK proceeds, no event emitted
   - **`observe_only` mode:** evaluates request kind; denies writes, non-read-only shells, URL fetches, and mutating MCP calls
   - **`review_and_approve` mode:** evaluates request kind; auto-approves reads and read-only shells, routes everything else to operator
3. If auto-approved: SDK proceeds immediately, no operator interaction
4. If denied (observe_only mode): SDK is told the action is denied, no operator interaction
5. If operator approval required (review_and_approve mode):
   a. `ApprovalService` persists the request
   b. `approval_request` event emitted
   c. Job transitions to `waiting_for_approval`
   d. `ApprovalRequested` SSE event sent to frontend
   e. Frontend renders approval banner on Job Detail screen
   f. Operator clicks Approve or Reject
   g. `POST /api/approvals/{id}/resolve` called
   h. `ApprovalService` persists the resolution and unblocks the adapter's Future
   i. Adapter returns the decision to the SDK
   j. `ApprovalResolved` domain event published
   k. Job transitions back to `running`

The SDK's `on_permission_request` callback is **async** — it blocks the SDK at the callback level while waiting for the operator's response. This ensures the action does not proceed until approved.

### 18.7 Approval Timeout

Approval requests do not expire automatically. The job remains in `waiting_for_approval` state indefinitely until the operator responds. This is intentional for a single-operator tool.

The frontend provides visibility:

- Pending approvals older than 30 minutes show an "Aging" warning badge
- The dashboard sorts approvals by age (oldest first)
- A persistent toast notification remains visible if the operator is on a different screen

---

## 19. Diff Model

### 19.1 Diff Generation

Diffs are generated relative to the base branch using git:

```bash
git diff {base_ref}...HEAD
```

This command is run inside the job's worktree. The output is standard unified diff format.

### 19.2 Hunk Parsing

The `DiffService` parses raw unified diff output into structured `DiffFileModel` and `DiffHunkModel` Pydantic objects (see Section 4.6).

Parser responsibilities:

- Extract changed file paths (old and new)
- Detect file status (added, modified, deleted, renamed)
- Parse hunk headers (`@@ -a,b +c,d @@`)
- Classify each line as context, addition, or deletion
- Count additions and deletions per file

### 19.3 Diff Updates

`DiffUpdated` events are emitted:

- When the agent writes a file (debounced)
- When a validation phase completes
- When the job reaches a terminal state

#### Debounce Behavior

Diff recalculation uses **per-job throttling** (not trailing-edge debounce):

- After a file-change event from the agent adapter, the `DiffService` schedules a diff recalculation
- If a diff was calculated for this job within the last 5 seconds, the request is skipped
- At most one diff recalculation runs per job per 5-second window
- The final diff at job completion always runs regardless of throttle state

Diff recalculation is triggered by the adapter's `file_changed` events (emitted when the agent uses file-write or file-edit tools), not by filesystem watching. This avoids race conditions with partial file writes.

### 19.4 Diff Snapshots

At job completion, a final diff snapshot is stored in the `diff_snapshots` table. This snapshot represents the full set of changes produced by the job.

Diff snapshots are not exposed via a dedicated REST endpoint. The frontend receives live diffs via SSE `diff_update` events during execution, and the final diff is available as a `diff_snapshot` artifact via `GET /api/jobs/{job_id}/artifacts`. Historical intermediate snapshots are stored for future features (e.g., diff timeline playback) but are not served in v1.

### 19.5 Frontend Diff Rendering

The `DiffViewer` component:

- Lists all changed files with status icons and line count badges
- Expands a file to show its hunks
- Renders each line with syntax highlighting and color coding (green for additions, red for deletions, gray for context)
- Supports collapsing unchanged context blocks
- Shows a summary bar: total files changed, total additions, total deletions

---

## 20. Observability

### 20.1 Job Health

The Job Detail screen exposes:

- Current state with color-coded badge
- Session heartbeat timestamp (updated by `session_heartbeat` SSE events every 30 seconds)
- If no heartbeat received in 90 seconds, a "Session unresponsive" warning is shown

### 20.2 Runtime Logs

Logs are streamed in real time via SSE `log_line` events.

Backend logging uses human-readable format via Python's `structlog` library with console renderer:

```python
log.info("job_started", job_id=job_id, repo=repo)
# Output: 2025-01-01 12:00:00 [info] job_started  job_id=job-104 repo=/repos/service-a
```

Every log line carries:

- `timestamp`
- `level`
- `message`
- `job_id` (when applicable)
- Additional structured context fields

#### Log Rotation

Backend logs are written to `~/.codeplane/logs/server.log` using Python's `RotatingFileHandler`:

| Parameter | Default |
|---|---|
| Max file size | 50 MB (`logging.max_file_size_mb`) |
| Backup count | 3 (`logging.backup_count`) |
| Format | Human-readable (`structlog` console renderer) |

This produces files: `server.log`, `server.log.1`, `server.log.2`, `server.log.3`.

#### Terminal Output

Stdout does not show raw log lines. Instead, `cpl up` renders a live status display using `rich` showing:

- Server URL and tunnel URL (if active)
- Active jobs table: job ID, repo, state, elapsed time
- Aggregate metrics: jobs running, queued, succeeded, failed
- Last few significant events (job created, completed, failed, approval requested)

This provides an at-a-glance operational view without overwhelming the terminal. Full logs remain in `~/.codeplane/logs/server.log`.

### 20.3 Failure Diagnostics

When a job enters `failed` state, the Job Detail screen shows:

- The error message from the `JobFailed` event payload
- The traceback (if available)
- The last log lines before failure
- The last transcript entries

### 20.4 Session Heartbeat

The adapter generates a `session_heartbeat` domain event every 30 seconds for each running session. The SDK itself may not provide periodic heartbeats, so the adapter maintains its own timer per active session. The frontend uses these to display session health status.

#### Heartbeat Watchdog

If no heartbeat is received for a running session within 90 seconds:

1. The frontend shows a "Session unresponsive" warning badge
2. The backend logs a warning: `log.warning("session_unresponsive", job_id=job_id, last_heartbeat=...)`
3. After 5 minutes without a heartbeat, the backend auto-cancels the job with `reason: "heartbeat_timeout"` and transitions it to `failed`
4. The operator can rerun the job if desired

---

## 21. Security Model

### 21.1 Authentication

CodePlane uses **password-based authentication** for remote access and **localhost trust** for local access:

1. **Localhost binding**: The backend binds to `127.0.0.1` by default, making it accessible only from the local machine. Localhost requests bypass password auth entirely.
2. **Password auth for remote access**: When `--remote` is used, a password is required. It is set explicitly (`--password`, `CPL_DEVTUNNEL_PASSWORD` env var / `.env`) or auto-generated. Remote clients must authenticate via the login page; sessions use httpOnly cookies with 24h expiry.
3. **Tunnel relay transport**: Remote traffic reaches the server through a Dev Tunnel relay URL. Password authentication remains the application-level access control.

This means:

- Local access requires no credentials
- Remote access requires the password printed in the operator's terminal
- Dev Tunnels provides the HTTPS relay path; application access is still gated by password auth
- Rate limiting (5 attempts/min/IP) protects the login endpoint against brute-force
- If the server is intentionally bound to `0.0.0.0` (e.g., for LAN access), a startup warning is emitted noting that no authentication is enforced

### 21.2 Repository Allowlist

The `repos` list in global config is the authoritative allowlist.

- All job creation requests are validated against this list
- Any path traversal attempts (e.g., `../../etc/passwd`) are rejected
- The backend resolves all paths to their canonical absolute form before comparison

### 21.3 Filesystem Protections

- The backend never serves files outside of whitelisted repository paths or the artifact storage directory
- All file-read endpoints validate the requested path against the job's worktree root
- Directory traversal attacks are prevented by canonicalizing paths and asserting prefix membership

### 21.4 Approval Gating

Risky operations require explicit operator approval before execution. This prevents autonomous destructive actions, especially when the agent misunderstands intent.

### 21.5 CORS

The backend enforces CORS:

- In production mode (single port): CORS is not needed (same origin)
- In development mode (`--dev`): `http://localhost:5173` is allowed
- When tunnel is active: the tunnel origin is dynamically added to the allowed origins list

### 21.6 Connection Limits

SSE connections are capped at `max_sse_connections` (default: 5) to prevent resource exhaustion from too many open connections. No per-request rate limiting is applied.

### 21.7 Tunnel Security

When Dev Tunnels is active:

- Remote clients connect through the `devtunnels.ms` relay URL, protected by password auth
- Tunnel URLs should be treated as sensitive operational details even though password auth is mandatory
- HTTPS is enforced by the Dev Tunnels relay
- Password auth is mandatory
- Session cookies are set with `Secure; HttpOnly; SameSite=Lax`
- Rate limiting prevents brute-force attacks on the login endpoint
- Localhost requests bypass auth entirely (same-machine access is trusted)

---

## 22. Engineering Constraints and Pitfalls

### 22.1 Backend Rules

| Rule | Rationale |
|---|---|
| API routes must not contain orchestration logic | Routes should delegate to services; mixing concerns makes testing and refactoring difficult |
| Agent SDK must be wrapped behind an adapter | Prevents tight coupling to SDK types; enables testing with fakes |
| Git operations must be isolated behind `GitService` | Prevents git logic from spreading across the codebase; enables mocking |
| Long-running jobs must be managed by `RuntimeService` | Central task management enables cancellation, capacity enforcement, and recovery |
| Job state transitions must be explicit | Prevents invalid state changes and makes the state machine auditable |
| Important state must be persisted | Enables restart recovery and event replay |
| Logging must include structured context | Enables filtering and correlation |

### 22.2 Frontend Rules

| Rule | Rationale |
|---|---|
| Application state must have a single source of truth | Prevents inconsistency between components |
| SSE events must be processed centrally | Prevents duplicate state updates and race conditions |
| Components must not duplicate job state | Components should read from the store, not maintain local copies |
| Large lists must avoid excessive re-renders | Virtualization required for log and transcript panels |
| Domain models must be strongly typed | TypeScript interfaces prevent entire classes of runtime errors |

### 22.3 Testing Requirements

| Test type | Coverage target |
|---|---|
| Unit tests | Job state machine transitions |
| Unit tests | Approval service logic |
| Unit tests | Diff parser (hunk parsing, line classification) |
| Unit tests | Config loading and validation |
| Integration tests | Git service: worktree creation, branch creation, cleanup |
| Integration tests | Concurrent jobs on same repository |
| Integration tests | Approval flow end-to-end |
| Integration tests | Job restart recovery (simulate process restart) |
| Integration tests | SSE reconnection and event replay |

### 22.4 Style Requirements

**Backend:**

- Python 3.11+
- `mypy` with strict mode for type checking
- `ruff` for linting and formatting
- `pytest` for testing

**Frontend:**

- TypeScript strict mode
- ESLint with React and TypeScript rules
- Prettier for formatting
- Vitest for unit tests
- Playwright for end-to-end tests

---

## 23. Sequence Diagrams

All diagrams use participants:

- **Operator** — human at the UI
- **React UI** — frontend application
- **FastAPI** — backend application
- **JobRuntime** — RuntimeService + asyncio task
- **AgentSDK** — adapter-wrapped agent runtime
- **GitWorkspace** — GitService + worktree
- **Persistence** — SQLite via repositories
- **SSEStream** — SSEManager + client connection

---

### 23.1 Job Creation and Workspace Initialization

```
Operator -> React UI: Fill job form, click Create
React UI -> FastAPI: POST /api/jobs
FastAPI -> FastAPI: Validate repo in allowlist
FastAPI -> GitWorkspace: create_worktree(repo, base_ref, job_id)
GitWorkspace --> FastAPI: worktree_path, branch
FastAPI -> Persistence: persist JobCreated event
FastAPI -> Persistence: persist WorkspacePrepared event
FastAPI -> JobRuntime: enqueue(job)
JobRuntime -> FastAPI: job accepted (state=running or queued)
FastAPI --> React UI: 201 { job_id, state, branch, worktree_path }
React UI -> SSEStream: subscribe /api/events?job_id={id}
JobRuntime -> AgentSDK: create_session(workspace_path, prompt)
AgentSDK --> JobRuntime: session_id
JobRuntime -> Persistence: persist AgentSessionStarted
JobRuntime -> SSEStream: job_state_changed (queued->running)
SSEStream --> React UI: job_state_changed event
React UI -> React UI: Update job state badge
```

---

### 23.2 Agent Execution Lifecycle

```
JobRuntime -> AgentSDK: stream_events(session_id)
loop [SDK emits events]
    AgentSDK --> JobRuntime: SessionEvent(kind="transcript", ...)
    JobRuntime -> Persistence: persist TranscriptUpdated
    JobRuntime -> SSEStream: transcript_update
    SSEStream --> React UI: transcript_update
    React UI -> React UI: Append to TranscriptPanel

    AgentSDK --> JobRuntime: SessionEvent(kind="log", ...)
    JobRuntime -> Persistence: persist LogLineEmitted
    JobRuntime -> SSEStream: log_line
    SSEStream --> React UI: log_line
    React UI -> React UI: Append to LogsPanel

    AgentSDK --> JobRuntime: SessionEvent(kind="file_changed", ...)
    JobRuntime -> GitWorkspace: generate_diff(worktree, base_ref)
    GitWorkspace --> JobRuntime: DiffFile[]
    JobRuntime -> Persistence: persist DiffUpdated
    JobRuntime -> SSEStream: diff_update
    SSEStream --> React UI: diff_update
    React UI -> React UI: Refresh DiffViewer
end

AgentSDK --> JobRuntime: SessionEvent(kind="done")
JobRuntime -> GitWorkspace: final_diff(worktree, base_ref)
JobRuntime -> Persistence: persist DiffUpdated (final)
JobRuntime -> Persistence: persist JobSucceeded
JobRuntime -> SSEStream: job_state_changed (running->succeeded)
SSEStream --> React UI: job_state_changed
React UI -> React UI: Show succeeded badge, enable rerun
```

---

### 23.3 Approval Pause and Resolution

```
AgentSDK --> JobRuntime: on_permission_request callback invoked
JobRuntime -> FastAPI: ApprovalRequested domain event
FastAPI -> Persistence: persist ApprovalRequested
FastAPI -> Persistence: update job state = waiting_for_approval
FastAPI -> SSEStream: approval_requested event
FastAPI -> SSEStream: job_state_changed (running->waiting_for_approval)
SSEStream --> React UI: approval_requested
SSEStream --> React UI: job_state_changed
React UI -> React UI: Show ApprovalBanner with proposed action

Operator -> React UI: Click "Approve"
React UI -> FastAPI: POST /api/approvals/{id}/resolve { resolution: "approved" }
FastAPI -> Persistence: persist ApprovalResolved
FastAPI -> Persistence: update job state = running
FastAPI -> AgentSDK: Return PermissionRequestResult(approved) to pending callback
FastAPI -> SSEStream: job_state_changed (waiting_for_approval->running)
SSEStream --> React UI: job_state_changed
React UI -> React UI: Hide ApprovalBanner, show running state
AgentSDK -> AgentSDK: Resume execution
```

---

### 23.4 Job Cancellation

```
Operator -> React UI: Click "Cancel Job"
React UI -> FastAPI: POST /api/jobs/{job_id}/cancel
FastAPI -> JobRuntime: cancel(job_id)
JobRuntime -> AgentSDK: abort_session(session_id)
AgentSDK --> JobRuntime: session aborted
JobRuntime -> Persistence: persist JobCanceled
JobRuntime -> Persistence: update job state = canceled
JobRuntime -> SSEStream: job_state_changed (running->canceled)
SSEStream --> React UI: job_state_changed
React UI -> React UI: Show canceled badge
FastAPI --> React UI: 200 OK
```

If job is `waiting_for_approval` at cancel time:

```
FastAPI -> Persistence: persist ApprovalResolved (resolution=rejected, reason=canceled)
FastAPI -> JobRuntime: cancel(job_id)
JobRuntime -> AgentSDK: abort_session(session_id)
[continues as above]
```

---

### 23.5 Job Rerun

```
Operator -> React UI: Click "Rerun"
React UI -> FastAPI: POST /api/jobs/{job_id}/rerun
FastAPI -> Persistence: get original job config
FastAPI -> FastAPI: Create new job (same repo, prompt, base_ref)
FastAPI -> GitWorkspace: create_worktree(repo, base_ref, new_job_id)
FastAPI -> Persistence: persist JobCreated (new job)
FastAPI -> Persistence: persist WorkspacePrepared (new job)
FastAPI -> JobRuntime: enqueue(new_job)
FastAPI --> React UI: 201 { new_job_id, state, branch }
React UI -> React UI: Navigate to new job detail
[continues as Job Creation flow]
```

---

### 23.6 SSE Reconnection and Event Replay

```
React UI -> SSEStream: GET /api/events?job_id={id}
SSEStream --> React UI: [stream opens]
React UI -> React UI: Track last_event_id from each "id:" field

note: Network interruption
React UI -> React UI: SSE connection closed
React UI -> React UI: Wait 1s (exponential backoff)
React UI -> SSEStream: GET /api/events?job_id={id}
                       Header: Last-Event-ID: {last_event_id}
SSEStream -> Persistence: EventRepository.list_after(last_event_id, job_id)
Persistence --> SSEStream: [missed events]
SSEStream --> React UI: [replay missed events in order]
SSEStream --> React UI: [continue live stream]
React UI -> React UI: Apply replayed events to store (idempotent)
React UI -> React UI: UI updated to current state
```

---

### 23.7 Voice Input Transcription Flow

```
Operator -> React UI: Press and hold microphone button
React UI -> Browser: getUserMedia({ audio: true })
Browser --> React UI: MediaStream
React UI -> Browser: new MediaRecorder(stream)
React UI -> Browser: recorder.start()

loop [while button held]
    Browser --> React UI: ondataavailable(chunk)
    React UI -> React UI: chunks.push(chunk)
end

Operator -> React UI: Release microphone button
React UI -> Browser: recorder.stop()
React UI -> React UI: blob = new Blob(chunks, { type: "audio/webm" })
React UI -> FastAPI: POST /api/voice/transcribe (multipart: audio=blob)
FastAPI -> VoiceService: transcribe(audio_bytes)
VoiceService -> faster-whisper: model.transcribe(audio_bytes)
faster-whisper --> VoiceService: segments
VoiceService --> FastAPI: "Fix the null pointer in UserService"
FastAPI --> React UI: 200 { text: "Fix the null pointer in UserService" }
React UI -> React UI: Insert text into prompt/message input
```

---

## 24. Execution Strategy Model

Jobs delegate execution to the `RuntimeService`, which manages the agent session lifecycle. The execution model uses a single-agent approach: one job maps to one agent session.

### 24.1 Single-Agent Execution

Each job runs as a single agent session:

1. `RuntimeService` constructs a `SessionConfig` from the job record and resolved config
2. Calls `adapter.create_session(config)` to start the agent
3. Consumes `adapter.stream_events(session_id)` and translates each `SessionEvent` into a domain event
4. Publishes domain events to the event bus
5. When the session completes, the job transitions to `succeeded`, `failed`, or `canceled`

Operator messages are forwarded via `adapter.send_message()`. Cancellation calls `adapter.abort_session()`.

### 24.2 How the RuntimeService Manages Jobs

The `RuntimeService`:

1. Tracks running asyncio tasks by `job_id`
2. Enforces `max_concurrent_jobs` from global config
3. Enqueues jobs if at capacity (state: `queued`)
4. Starts queued jobs when capacity opens
5. Provides a `cancel(job_id)` method that cancels the asyncio task and calls `adapter.abort_session()`

### 24.3 Future Strategy Examples

The following strategies are **not implemented** but the architecture is designed to support them:

| Strategy | Description |
|---|---|
| `planner_executor` | A planner agent decomposes the task, then an executor agent implements each step |
| `executor_reviewer` | An executor agent produces changes, then a reviewer agent validates them |
| `parallel_executors` | Multiple executor agents work on independent subtasks concurrently |
| `human_in_the_loop` | The agent pauses after each step for operator review before continuing |

---

## 25. Ross Review: Open Questions

This section documents genuinely open questions that require further investigation or decision-making. Items that had clear suggested approaches have been promoted into the main specification as design decisions.

---

### 25.1 Agent SDK Integration

**Context from research:** The Python Copilot SDK (`github/copilot-sdk`) wraps the Copilot CLI via JSON-RPC over a subprocess. Sessions are created with a `SessionConfig` that accepts `available_tools`, `excluded_tools`, `mcp_servers`, `agent_mode`, and callback hooks. The SDK provides:

- **Event interception** via `SessionHooks` (`on_pre_tool_use`, `on_post_tool_use`, `on_session_start`, `on_session_end`, `on_error_occurred`)
- **Permission handling** via `on_permission_request` (blocking callback, returns `PermissionRequestResult`)
- **Tool filtering** via `available_tools` / `excluded_tools` lists
- **MCP server config** with per-server tool filtering
- **Agent modes**: `autopilot`, `interactive`, `plan`, `shell`

**Resolved:** The approval-pause mechanism uses the SDK's blocking `on_permission_request` callback. CodePlane holds the pending callback and resolves it when the operator responds.

**Resolved:** Session cancellation uses `session.abort()` which aborts the current message processing. The session remains valid after abort. Operator message injection uses `session.send(MessageOptions)` with `mode="immediate"` to send a follow-up message while the session is active.

**Resolved:** Subprocess crash detection relies on EOF/broken pipe on the JSON-RPC stdout stream. The SDK's background read loop detects the closed stream, polls the process exit code, captures stderr, and raises `ProcessExitedError` on all pending futures. The adapter catches this exception and emits a `JobFailed` event with the error details. No heartbeat mechanism exists — crash detection is immediate via the broken pipe.

---

### 25.2 Runtime Failure Scenarios

**Resolved:** `GitService.create_worktree()` catches exceptions from `git worktree add` (disk full, permissions, corrupt state) and transitions the job to `failed` with a descriptive error including git stderr output. The operator resolves the underlying issue and reruns. See Section 8.1.

---

### 25.3 SSE Scalability Constraint

**Resolved:** Documented as a known constraint. Beyond ~20 concurrent jobs, the SSE manager switches to selective streaming: only `job_state_changed` events are broadcast to the dashboard, while the currently open Job Detail screen continues to receive full event streaming. A "Refresh page for latest updates" banner is shown on the dashboard. See Section 5.6.

---

### 25.4 Branch Cleanup and PR Integration

**Resolved:** CodePlane offers to create a pull request after a successful job using the GitHub MCP server or `gh` CLI, whichever is available. If neither is available, the step is skipped and the branch remains on disk for manual push. Completed branches are **not** auto-deleted after merge. See Section 8.7.

---

## 26. MCP Orchestration Server

CodePlane exposes an **MCP (Model Context Protocol) server** that mirrors the full UI functionality, enabling external agents to use CodePlane as an orchestration layer. An outer agent connects to CodePlane's MCP server and drives job lifecycle, approval workflows, workspace inspection, and configuration — all through MCP tool calls.

---

### 26.1 Purpose

The MCP orchestration server turns CodePlane from a human-operated dashboard into a programmable control plane. An orchestrating agent can:

- Create and monitor coding jobs across multiple repositories
- Handle approval requests on behalf of a human or policy engine
- Inspect diffs, workspace files, and artifacts produced by inner agents
- Inject operator messages to steer running agents
- Manage repository registration and global settings

This enables hierarchical agent architectures where a planning agent delegates implementation tasks to CodePlane-managed coding agents and reacts to their progress in real time.

---

### 26.2 Transport

The MCP server uses **Streamable HTTP** transport, served from the same FastAPI process on a dedicated path:

- **Endpoint**: `POST /mcp` (message endpoint), `GET /mcp` (SSE stream for server-initiated notifications)
- **Authentication**: None for local connections; remote access is secured via tunnel authentication (see §7, §21)
- **Discovery**: Standard MCP capabilities negotiation on `initialize`

Running in-process avoids a separate deployment unit and shares the service layer, database connections, and event bus with the REST API.

---

### 26.3 Tool Surface

Every REST API capability is exposed as an MCP tool. Related operations are collapsed into a single tool with an `action` parameter, keeping the tool count low for LLM clients. Tool names use the `codeplane_` prefix. Each tool carries an `annotations.title` for human-readable display.

#### Job Management

| Tool | Title | Actions | Maps to |
|---|---|---|---|
| `codeplane_job` | Manage Coding Jobs | create, list, get, cancel, rerun, message | `POST/GET /api/jobs`, `POST /api/jobs/{id}/cancel`, etc. |

#### Approvals

| Tool | Title | Actions | Maps to |
|---|---|---|---|
| `codeplane_approval` | Manage Approvals | list, resolve | `GET /api/jobs/{id}/approvals`, `POST /api/approvals/{id}/resolve` |

#### Workspace & Artifacts

| Tool | Title | Actions | Maps to |
|---|---|---|---|
| `codeplane_workspace` | Browse Job Worktree | list, read | `GET /api/jobs/{id}/workspace`, `GET /api/jobs/{id}/workspace/file` |
| `codeplane_artifact` | Access Job Artifacts | list, get | `GET /api/jobs/{id}/artifacts`, `GET /api/artifacts/{id}` |

#### Configuration

| Tool | Title | Actions | Maps to |
|---|---|---|---|
| `codeplane_settings` | Global Settings | get, update | `GET/PUT /api/settings/global` |
| `codeplane_repo` | Manage Repositories | list, get, register, remove | `GET/POST/DELETE /api/settings/repos` |

#### Observability

| Tool | Title | Actions | Maps to |
|---|---|---|---|
| `codeplane_health` | Health & Maintenance | check, cleanup | `GET /api/health`, `POST /api/settings/cleanup-worktrees` |

---

### 26.4 Notifications (Server → Client)

The MCP server pushes **notifications** to connected clients for key domain events:

| Notification | Trigger |
|---|---|
| `cpl/job_state_changed` | Job transitions to a new state |
| `cpl/approval_requested` | A running job requests operator approval |
| `cpl/job_completed` | Job reaches `succeeded` or `failed` terminal state |
| `cpl/agent_message` | Agent produces a transcript message |

Notifications are sourced from the internal event bus — the same events that drive the SSE stream and the frontend. The orchestrating agent can subscribe to these to react without polling.

---

### 26.5 Implementation Approach

1. **Thin MCP handler layer**: Each MCP tool handler validates input, calls the corresponding service method, and returns the result. Same principle as the REST route handlers.
2. **Shared service layer**: MCP handlers call the same `JobService`, `ApprovalService`, `GitService`, etc. used by the REST API. No duplication of business logic.
3. **Schema reuse**: Tool input/output schemas are derived from the existing Pydantic models in `api_schemas.py`.
4. **Event bus integration**: Notifications are powered by subscribing to the internal event bus, same as `SSEManager`.

---

### 26.6 Configuration

The MCP server is always enabled and mounted at `/mcp`. These are hardcoded constants, not user-configurable.

---

### 26.7 Security Considerations

- Local connections (localhost) require no authentication — same trust model as the REST API.
- Remote access is secured via tunnel authentication with mandatory password auth (see §7, §21).
- Tool calls are subject to the same validation as REST requests (repository allowlist, state machine rules).
- Rate limiting and capacity enforcement from `RuntimeService` apply equally to MCP-initiated jobs.
- The MCP server does **not** expose raw database access, shell execution, or filesystem paths outside registered repositories.
