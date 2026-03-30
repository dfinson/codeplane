# REST API Reference

CodePlane exposes a REST API on the same port as the web UI (default `8080`). All endpoints are prefixed with `/api`.

## Health

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/health` | Health check |

## Jobs

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/jobs` | List jobs (supports `state`, `limit`, `cursor`, `archived` query params) |
| `POST` | `/api/jobs` | Create a new job |
| `GET` | `/api/jobs/{job_id}` | Get job details |
| `POST` | `/api/jobs/{job_id}/cancel` | Cancel a running job |
| `POST` | `/api/jobs/{job_id}/rerun` | Rerun a completed/failed job |
| `POST` | `/api/jobs/{job_id}/messages` | Send operator message to agent |
| `POST` | `/api/jobs/{job_id}/resolve` | Resolve a completed job (merge/PR/discard) |
| `POST` | `/api/jobs/{job_id}/pause` | Pause a running job |
| `POST` | `/api/jobs/{job_id}/resume` | Resume a paused job (optional instruction body) |
| `POST` | `/api/jobs/{job_id}/continue` | Create follow-up job with new instruction |
| `POST` | `/api/jobs/{job_id}/archive` | Archive a job to history |
| `POST` | `/api/jobs/{job_id}/unarchive` | Restore a job from history |
| `POST` | `/api/jobs/{job_id}/suggest-names` | Get AI-suggested job titles |

### Create Job Request

```json
{
  "prompt": "Add input validation to the registration endpoint",
  "repositoryPath": "/path/to/repo",
  "sdk": "copilot",
  "model": "gpt-4o"
}
```

### Resolve Job Request

```json
{
  "action": "merge"
}
```

Valid actions: `merge`, `smart_merge`, `create_pr`, `discard`, `agent_merge`

## Job Data

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/jobs/{job_id}/logs` | Get structured logs (query: `level`, `limit`, `session`) |
| `GET` | `/api/jobs/{job_id}/transcript` | Get conversation transcript (query: `limit`) |
| `GET` | `/api/jobs/{job_id}/timeline` | Get execution timeline (query: `limit`) |
| `GET` | `/api/jobs/{job_id}/diff` | Get changed files with diffs |
| `GET` | `/api/jobs/{job_id}/telemetry` | Get token usage and cost metrics |
| `GET` | `/api/jobs/{job_id}/snapshot` | Full state hydration for a single job |

## Approvals

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/jobs/{job_id}/approvals` | List approval requests for a job |
| `POST` | `/api/approvals/{approval_id}/resolve` | Approve or reject (`{"decision": "approved"}`) |
| `POST` | `/api/jobs/{job_id}/trust` | Auto-approve all pending for this job |

## Artifacts

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/jobs/{job_id}/artifacts` | List artifacts |
| `GET` | `/api/artifacts/{artifact_id}` | Get artifact content |

## Workspace

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/jobs/{job_id}/workspace` | List workspace files (query: `path`, `depth`) |
| `GET` | `/api/jobs/{job_id}/workspace/file` | Get file content (query: `path`) |

## Terminal

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/terminal/sessions` | Create terminal session |
| `GET` | `/api/terminal/sessions` | List active sessions |
| `DELETE` | `/api/terminal/sessions/{session_id}` | Close a session |
| `WebSocket` | `/api/terminal/ws` | Terminal I/O stream |
| `POST` | `/api/terminal/ask` | Translate natural language to a shell command (AI) |

## Voice

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/voice/transcribe` | Transcribe audio (multipart form) |

## Settings & Configuration

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/settings` | Get current settings |
| `PUT` | `/api/settings` | Update settings |
| `GET` | `/api/settings/repos` | List registered repositories |
| `POST` | `/api/settings/repos` | Register a new repository |
| `DELETE` | `/api/settings/repos/{repo_path}` | Unregister a repository |
| `GET` | `/api/settings/repos/{repo_path}` | Get repository details |
| `GET` | `/api/sdks` | List available SDKs |
| `GET` | `/api/models` | List available models (query: `sdk`) |
| `GET` | `/api/platforms/status` | Check auth status for all detected git hosting platforms |

## Analytics

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/analytics/overview` | Aggregate analytics (query: `period` in days) |
| `GET` | `/api/analytics/models` | Per-model cost and usage breakdown |
| `GET` | `/api/analytics/tools` | Tool performance stats |
| `GET` | `/api/analytics/repos` | Per-repo cost and usage breakdown |
| `GET` | `/api/analytics/jobs` | Paginated job telemetry (query: `period`, `sdk`, `model`, `status`, `repo`, `sort`, `limit`, `offset`) |
| `GET` | `/api/analytics/pricing` | Model pricing lookup from LiteLLM (query: `models`) |

## SSE Event Stream

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/events` | SSE event stream (query: `job_id`, `Last-Event-ID`) |

See [SSE Events](sse-events.md) for event type documentation.

## Directory Browsing

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/settings/browse` | Browse filesystem directories (query: `path`) |
