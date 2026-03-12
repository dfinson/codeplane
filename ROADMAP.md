# Tower — Implementation Roadmap

Work is broken into phases. Each phase produces a usable increment. Later phases build on earlier ones.

---

## Phase 1: Foundation

> Backend skeleton, database, domain models, dev tooling.

- [x] FastAPI app factory (`backend/main.py`) with health endpoint
- [x] CLI entry point (`tower up`, `tower init`, `tower version`) via Click
- [x] Global config loading and validation (`~/.tower/config.yaml`)
- [x] SQLAlchemy models and SQLite schema (`jobs`, `events`, `approvals`, `artifacts`, `diff_snapshots`)
- [x] Alembic migration setup and initial migration
- [x] Pydantic API schemas (`models/api_schemas.py`) — all request/response models
- [x] Domain dataclasses and event types (`models/domain.py`, `models/events.py`)
- [x] Repository pattern persistence layer (`persistence/`)
- [x] CI pipeline (lint, type-check, test)
- [x] Frontend Vite + React + TypeScript skeleton with health check fetch

---

## Phase 2: Git & Job CRUD

> Worktree management, repository registration, job creation, state machine.

- [x] `GitService` — worktree creation (main vs secondary), branch creation, cleanup
- [x] Worktree creation error handling (catch failures, transition job to failed)
- [x] Repository registration — add local repos by path, clone remote repos by URL via `git` subprocess
- [x] Repository registration API (`POST /api/settings/repos`, `DELETE /api/settings/repos/{repo_path}`)
- [x] Job CRUD API (`POST /api/jobs`, `GET /api/jobs`, `GET /api/jobs/{id}`)
- [x] Job state machine (all transitions from §12.2)
- [x] Repository allowlist validation
- [x] Job cancel endpoint (`POST /api/jobs/{id}/cancel`)
- [x] Job rerun endpoint (`POST /api/jobs/{id}/rerun`)
- [x] Cursor-based pagination for job list
- [x] Integration tests for git operations and concurrent worktrees

---

## Phase 3: Event Bus & SSE

> Internal pub/sub, SSE streaming, reconnection.

- [x] Internal event bus (async in-process pub/sub)
- [x] SSE endpoint (`GET /api/events`, `GET /api/events?job_id=`)
- [x] SSE manager — connection tracking, broadcast, cleanup
- [x] Event persistence subscriber (write all events to SQLite)
- [x] Reconnection and event replay (`Last-Event-ID`, snapshot fallback)
- [x] Replay bounds (max 500 events, 5-minute window)
- [x] SSE scaling constraint — selective streaming beyond 20 jobs (§5.6)
- [x] Frontend SSE client with exponential backoff reconnection
- [x] Zustand store with central event dispatcher

---

## Phase 4: Agent Integration

> Adapter interface, Copilot SDK integration, runtime service.

- [ ] `AgentAdapterInterface` — abstract base class
- [ ] `FakeAgentAdapter` — test double emitting scripted events
- [ ] `CopilotAdapter` — wraps Python Copilot SDK
- [ ] Callback-to-iterator bridge (SDK callbacks → `AsyncIterator[SessionEvent]`)
- [ ] `SessionConfig` resolution from job + repo config
- [ ] `RuntimeService` — asyncio task management, capacity enforcement, queueing
- [ ] `ExecutionStrategy` interface + `SingleAgentExecutor`
- [ ] Strategy registry and selection
- [ ] Session heartbeat generation (30s interval, 90s warning, 5-min timeout)
- [ ] MCP server discovery (`.vscode/mcp.json` + global config merge)

---

## Phase 5: Frontend Core

> Dashboard, job detail, job creation screens.

- [ ] OpenAPI type generation pipeline (`openapi-typescript`)
- [ ] Type aliases (`src/api/types.ts`)
- [ ] API client module (REST calls)
- [ ] Dashboard — Kanban board (Active, Sign-off, Failed, History columns)
- [ ] Dashboard — mobile job list with filter tabs
- [ ] Responsive breakpoints (1024px, 768px)
- [ ] Job Detail screen — metadata header, state badge, timestamps
- [ ] Job Detail — transcript panel (virtualized, auto-scroll)
- [ ] Job Detail — logs panel (virtualized, level filtering)
- [ ] Job Detail — execution timeline (grouped by phase)
- [ ] Job Creation screen — repo selector, prompt input, advanced options
- [ ] Repository Detail view — resolved MCP/tool config table (local/global/disabled), repo config, active + recent jobs

---

## Phase 6: Diffs, Workspace & Artifacts

> Diff generation, file browsing, artifact collection.

- [ ] `DiffService` — run `git diff base_ref...HEAD`, parse unified diff
- [ ] Diff hunk parser (file status, hunk headers, line classification)
- [ ] Per-job diff throttling (5-second window)
- [ ] Final diff snapshot at job completion
- [ ] DiffViewer component (Monaco DiffEditor, file list, hunk navigation)
- [ ] Workspace browser API (`GET /api/jobs/{id}/workspace`, `/workspace/file`)
- [ ] Workspace browser component (`react-arborist` file tree)
- [ ] `ArtifactService` — collection from `.tower/artifacts/`, diff snapshots, agent summaries
- [ ] Artifact list + download endpoints
- [ ] Artifact viewer component

---

## Phase 7: Approvals & Operator Controls

> Approval routing, operator messages, protected paths.

- [ ] `ApprovalService` — persist requests, await resolution, route decisions to adapter
- [ ] Approval REST endpoints (`GET /api/jobs/{id}/approvals`, `POST /api/approvals/{id}/resolve`)
- [ ] Approval banner component (description, proposed action, approve/reject buttons)
- [ ] Concurrent approval notifications (toasts, badge on Sign-off tab)
- [ ] Operator message injection (`POST /api/jobs/{id}/messages` → `adapter.send_message`)
- [ ] Protected paths configuration → SDK permission rules
- [ ] Aging warning badge for approvals older than 30 minutes

---

## Phase 8: Voice Input

> Local transcription with faster-whisper.

- [ ] `VoiceService` — load model, transcribe audio bytes
- [ ] Voice endpoint (`POST /api/voice/transcribe`)
- [ ] Frontend audio capture (MediaRecorder, WebM/Opus)
- [ ] Voice input button component (press-and-hold)
- [ ] Client-side size limit enforcement
- [ ] "Local transcription" indicator in UI
- [ ] Voice config (`voice.enabled`, `voice.model`, `voice.max_audio_size_mb`)

---

## Phase 9: Operational Hardening

> Graceful shutdown, restart recovery, retention, remote access.

- [ ] Graceful shutdown (SIGTERM/SIGINT handler, cancel running jobs, close SSE)
- [ ] Restart recovery (transition orphaned running/waiting jobs to failed)
- [ ] Retention policy — artifact cleanup, worktree cleanup, daily background task
- [ ] Settings API (`GET/PUT /api/settings/global`, `GET /api/settings/repos`, `GET /api/settings/repos/{repo_path}`)
- [ ] Settings screen (global config editor, repo config list, cleanup action)
- [ ] Dev Tunnel integration (`tower up --tunnel`)
- [ ] Dynamic CORS for tunnel origin
- [ ] Startup warning for `0.0.0.0` binding
- [ ] `rich` terminal status display for `tower up`

---

## Phase 10: Post-Completion & Polish

> PR creation, performance, comprehensive testing.

- [ ] PR creation after successful job (GitHub MCP tools or `gh` CLI)
- [ ] PR URL in `JobSucceeded` payload and Job Detail screen
- [ ] Virtualized rendering for logs and transcript panels (`@tanstack/react-virtual`)
- [ ] Memoized Kanban column selectors (prevent full-board re-renders)
- [ ] Large diff lazy loading in Monaco

---

## Phase 11: MCP Orchestration Server

> Expose Tower's full functionality as an MCP server so external agents can use it as an orchestration layer.

- [ ] MCP server transport — Streamable HTTP on `/mcp`, mounted in FastAPI app
- [ ] MCP tool handlers for Job management (`tower_job_create`, `tower_job_list`, `tower_job_get`, `tower_job_cancel`, `tower_job_rerun`, `tower_job_message`)
- [ ] MCP tool handlers for Approvals (`tower_approval_list`, `tower_approval_resolve`)
- [ ] MCP tool handlers for Workspace & Artifacts (`tower_workspace_list`, `tower_workspace_read`, `tower_artifact_list`, `tower_artifact_get`)
- [ ] MCP tool handlers for Configuration (`tower_settings_get`, `tower_settings_update`, `tower_repo_list`, `tower_repo_get`, `tower_repo_register`, `tower_repo_remove`)
- [ ] MCP tool handlers for Observability (`tower_health`, `tower_cleanup_worktrees`)
- [ ] Server-to-client notifications via event bus subscription (`tower/job_state_changed`, `tower/approval_requested`, `tower/job_completed`, `tower/agent_message`)
- [ ] Schema derivation from existing Pydantic models
- [ ] MCP server configuration (`mcp_server.enabled`, `mcp_server.path`)
- [ ] Dev Tunnel authentication for remote MCP connections
- [ ] Integration tests for MCP tool calls end-to-end
- [ ] Comprehensive unit tests (state machine, diff parser, config, approval logic)
- [ ] Integration tests (git service, concurrent jobs, approval flow, SSE replay, restart recovery)
- [ ] End-to-end tests (Playwright)
