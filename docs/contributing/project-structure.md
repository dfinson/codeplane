# Project Structure

```
codeplane/
├── backend/
│   ├── main.py                        # App factory + CLI (`cpl`)
│   ├── config.py                      # Configuration loading
│   ├── api/                           # Thin route handlers
│   │   ├── approvals.py               #   Approval endpoints
│   │   ├── artifacts.py               #   Artifact download/view
│   │   ├── events.py                  #   SSE event streaming
│   │   ├── health.py                  #   Health check
│   │   ├── jobs.py                    #   Job CRUD + lifecycle
│   │   ├── settings.py               #   Settings + repos + SDKs/models
│   │   ├── terminal.py               #   Terminal session management
│   │   ├── voice.py                   #   Voice transcription
│   │   ├── workspace.py              #   Workspace file browsing
│   │   └── analytics.py              #   Fleet-level analytics endpoints
│   ├── mcp/                           # MCP orchestration server
│   │   └── server.py                  #   MCP tool definitions
│   ├── services/                      # Business logic
│   │   ├── agent_adapter.py           #   AgentAdapterInterface (ABC)
│   │   ├── adapter_registry.py        #   SDK adapter registry
│   │   ├── copilot_adapter.py         #   GitHub Copilot SDK adapter
│   │   ├── claude_adapter.py          #   Claude Code SDK adapter
│   │   ├── runtime_service.py         #   Job execution orchestration
│   │   ├── job_service.py             #   Job CRUD operations
│   │   ├── approval_service.py        #   Approval workflow
│   │   ├── artifact_service.py        #   Artifact management
│   │   ├── git_service.py             #   Git operations
│   │   ├── merge_service.py           #   Branch merging + PR creation
│   │   ├── diff_service.py            #   Diff generation
│   │   ├── event_bus.py               #   Internal event bus
│   │   ├── sse_manager.py             #   SSE event broadcasting
│   │   ├── terminal_service.py        #   Terminal session management
│   │   ├── voice_service.py           #   Whisper transcription
│   │   ├── telemetry.py               #   Token/cost metrics collection
│   │   ├── permission_policy.py       #   Permission mode enforcement
│   │   ├── platform_adapter.py        #   GitHub/Azure DevOps/GitLab
│   │   ├── naming_service.py          #   AI-powered job naming
│   │   ├── progress_tracking_service.py # Agent progress tracking
│   │   ├── summarization_service.py   #   AI summarization
│   │   ├── retention_service.py       #   Job retention/cleanup
│   │   ├── setup_service.py           #   First-time setup + doctor
│   │   ├── tunnel_service.py          #   Dev Tunnels management
│   │   ├── tool_formatters.py         #   Tool call display formatting
│   │   ├── utility_session.py         #   Utility AI sessions
│   │   └── auth.py                    #   Authentication
│   ├── models/                        # Domain, DB, and API schemas
│   │   ├── domain.py                  #   Domain models (Job, Approval, etc.)
│   │   ├── db.py                      #   SQLAlchemy ORM models
│   │   ├── api_schemas.py             #   Pydantic request/response schemas
│   │   └── events.py                  #   Domain event definitions
│   ├── persistence/                   # Repository-pattern DB access
│   │   ├── database.py                #   Database engine + session factory
│   │   ├── repository.py              #   Base repository class
│   │   ├── job_repo.py                #   Job repository
│   │   ├── approval_repo.py           #   Approval repository
│   │   ├── artifact_repo.py           #   Artifact repository
│   │   ├── event_repo.py              #   Event repository
│   │   ├── cost_attribution_repo.py   #   Cost attribution repository
│   │   ├── file_access_repo.py        #   File access repository
│   │   ├── observations_repo.py       #   Observations repository
│   │   ├── telemetry_spans_repo.py    #   Telemetry spans repository
│   │   └── telemetry_summary_repo.py  #   Telemetry summary repository
│   └── tests/                         # pytest (unit + integration)
│       ├── unit/                      #   Fast isolated tests
│       └── integration/               #   Tests with real DB + HTTP
├── frontend/
│   └── src/
│       ├── api/                       # API client + generated types
│       │   ├── client.ts              #   REST API client functions
│       │   ├── types.ts               #   Friendly type aliases
│       │   └── schema.d.ts            #   Generated from OpenAPI (gitignored)
│       ├── components/                # React components
│       │   ├── DashboardScreen.tsx     #   Main dashboard (Kanban + mobile list)
│       │   ├── JobDetailScreen.tsx     #   Job detail with tabs
│       │   ├── JobCreationScreen.tsx   #   Job creation form
│       │   ├── HistoryScreen.tsx       #   Archived jobs browser
│       │   ├── SettingsScreen.tsx      #   Settings + repo management
│       │   ├── TranscriptPanel.tsx     #   AI conversation display
│       │   ├── LogsPanel.tsx           #   Structured logs viewer
│       │   ├── DiffViewer.tsx          #   Code diff display
│       │   ├── ApprovalBanner.tsx      #   Approval request UI
│       │   ├── TerminalDrawer.tsx      #   Terminal container
│       │   ├── CommandPalette.tsx      #   Search overlay (⌘K)
│       │   └── ...                    #   Many more components
│       ├── hooks/                     # Custom React hooks
│       │   ├── useSSE.ts              #   SSE connection management
│       │   ├── useIsMobile.ts         #   Responsive breakpoint detection
│       │   └── ...
│       ├── lib/                       # Utilities
│       └── store/                     # Zustand state management
│           └── index.ts               #   Single store with all slices
├── alembic/                           # Database migrations
├── tools/
│   └── dev_restart.py                 # Graceful server restart (preserves jobs)
├── docs/                              # Documentation site (MkDocs Material)
├── Makefile                           # Build / run / test targets
├── mkdocs.yml                         # Documentation site configuration
├── .env.sample                        # Environment variable template
├── SPEC.md                            # Full product specification
├── CONTRIBUTING.md                    # Contributing guide
├── README.md                          # Project overview
├── LICENSE                            # MIT license
└── pyproject.toml                     # Python project + tool config
```

## Key Directories

### `backend/api/`

Thin FastAPI route handlers. Each file maps to a feature area. Handlers validate input via Pydantic, delegate to services, and return results.

### `backend/services/`

All business logic. The most important service is `RuntimeService`, which orchestrates the entire job execution lifecycle.

### `backend/persistence/`

Repository-pattern database access. Services never interact with SQLAlchemy sessions directly.

### `backend/models/`

Three model layers:

- **`domain.py`** — Pure domain objects (Job, Approval, Artifact)
- **`db.py`** — SQLAlchemy ORM models (database tables)
- **`api_schemas.py`** — Pydantic models (API request/response contracts)

### `frontend/src/store/`

Single Zustand store managing all application state. Components read via selectors, never maintaining local copies of job state.

### `frontend/src/api/`

API client functions and type definitions. Types are generated from the backend's OpenAPI schema — never hand-write types that duplicate `schema.d.ts`.
