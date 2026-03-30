---
hide:
  - navigation
---

# MCP Server

CodePlane exposes an [MCP](https://modelcontextprotocol.io/) server that lets external agents orchestrate coding jobs, handle approvals, browse workspaces, and manage repositories programmatically. This enables **agent-to-agent orchestration** — your outer planning agent can delegate coding tasks to CodePlane and monitor them without human intervention.

**Endpoint:** `http://localhost:8080/mcp` (Streamable HTTP transport)

---

## Connecting an External Agent

Any MCP-compatible client can connect to CodePlane's server. Point your client at the `/mcp` endpoint:

### VS Code / GitHub Copilot

Add to `.vscode/mcp.json` in your project (or your global MCP settings):

```json
{
  "servers": {
    "codeplane": {
      "type": "http",
      "url": "http://localhost:8080/mcp"
    }
  }
}
```

### Claude Desktop

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "codeplane": {
      "type": "http",
      "url": "http://localhost:8080/mcp"
    }
  }
}
```

### Cursor

Add to your Cursor MCP settings:

```json
{
  "mcpServers": {
    "codeplane": {
      "url": "http://localhost:8080/mcp"
    }
  }
}
```

When **remote access** is enabled (`--remote`), replace `localhost:8080` with your tunnel URL. Authentication uses the same tunnel password cookie as the web UI.

---

## Tools

CodePlane exposes **7 MCP tools**, each using an `action` parameter to multiplex related operations under a single tool name — keeping the tool count low for LLM clients.

### `codeplane_job` — Manage Coding Jobs

| Action | Required Params | Optional Params | Description |
|--------|----------------|-----------------|-------------|
| `create` | `repo`, `prompt` | `base_ref`, `branch`, `model`, `sdk` | Create a new job |
| `list` | — | `state`, `limit` (default 50), `cursor` | List jobs with optional state filter |
| `get` | `job_id` | — | Get job details |
| `cancel` | `job_id` | — | Cancel a running job |
| `rerun` | `job_id` | — | Rerun a completed/failed job |
| `message` | `job_id`, `content` | — | Send a message to a running job (max 10,000 chars) |

### `codeplane_approval` — Manage Approvals

| Action | Required Params | Description |
|--------|----------------|-------------|
| `list` | `job_id` | List pending approvals for a job |
| `resolve` | `approval_id`, `resolution` | Approve or reject (`approved` / `rejected`) |

### `codeplane_workspace` — Browse Job Worktree

| Action | Required Params | Optional Params | Description |
|--------|----------------|-----------------|-------------|
| `list` | `job_id` | `path`, `cursor`, `limit` (max 200) | List directory contents |
| `read` | `job_id`, `path` | — | Read file contents (max 5 MB) |

Path validation enforces relative paths within the worktree — no `.git` access or `..` escapes.

### `codeplane_artifact` — Access Job Artifacts

| Action | Required Params | Description |
|--------|----------------|-------------|
| `list` | `job_id` | List artifacts for a job |
| `get` | `artifact_id` | Get artifact content |

### `codeplane_repo` — Manage Repositories

| Action | Required Params | Description |
|--------|----------------|-------------|
| `list` | — | List all registered repositories |
| `get` | `repo_path` | Get repository details (path, origin URL, base branch, platform) |
| `register` | `source` | Register a local path or remote Git URL (`clone_to` required for URLs) |
| `remove` | `repo_path` | Unregister a repository |

### `codeplane_settings` — Global Settings

| Action | Description |
|--------|-------------|
| `get` | Retrieve all settings |
| `update` | Update any combination of settings (see below) |

Updatable settings: `max_concurrent_jobs`, `permission_mode`, `auto_push`, `cleanup_worktree`, `delete_branch_after_merge`, `artifact_retention_days`, `max_artifact_size_mb`, `auto_archive_days`, `verify`, `self_review`, `max_turns`, `verify_prompt`, `self_review_prompt`.

### `codeplane_health` — Health & Maintenance

| Action | Description |
|--------|-------------|
| `check` | Returns status, version, uptime, active/queued job counts |
| `cleanup` | Remove worktrees for completed jobs |

---

## Server-Initiated Notifications

When connected via Streamable HTTP (SSE stream on `GET /mcp`), the server pushes real-time notifications:

| Notification | Payload | Fires When |
|-------------|---------|------------|
| `cpl/job_state_changed` | `job_id`, `old_state`, `new_state` | A job transitions state |
| `cpl/approval_requested` | `approval_id`, `job_id`, `description` | An approval is pending |
| `cpl/job_completed` | `job_id`, `resolution` | A job reaches a terminal state |
| `cpl/agent_message` | `job_id`, `content` | The agent sends a message |

---

## Example: Create and Monitor a Job

```json
// 1. Create a job
{
  "tool": "codeplane_job",
  "action": "create",
  "repo": "/repos/my-app",
  "prompt": "Add input validation to the signup endpoint"
}

// 2. Check job status
{
  "tool": "codeplane_job",
  "action": "get",
  "job_id": "job-1"
}

// 3. List and resolve pending approvals
{
  "tool": "codeplane_approval",
  "action": "list",
  "job_id": "job-1"
}
{
  "tool": "codeplane_approval",
  "action": "resolve",
  "approval_id": "abc-123",
  "resolution": "approved"
}

// 4. Browse the result
{
  "tool": "codeplane_workspace",
  "action": "list",
  "job_id": "job-1"
}
{
  "tool": "codeplane_workspace",
  "action": "read",
  "job_id": "job-1",
  "path": "src/signup.ts"
}
```

---

## MCP Server Discovery (Agent-Side)

CodePlane also acts as an MCP **client** — it discovers external MCP servers and makes them available to the coding agent during job execution. Servers are merged from two sources:

1. **Repo-level:** `.vscode/mcp.json` in the repository (VS Code / Copilot convention)
2. **Global:** `tools.mcp` in `~/.codeplane/config.yaml`

Repo-level wins on name conflicts. Individual servers can be disabled per-repo via `.codeplane.yml`:

```yaml
tools:
  mcp:
    disabled:
      - postgres
```

See [Configuration > MCP Server Discovery](configuration.md#mcp-server-discovery) for full setup details.
