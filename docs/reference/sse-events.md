# SSE Events Reference

CodePlane uses Server-Sent Events (SSE) to push real-time updates to the frontend. Connect to `/api/events` to receive the event stream.

## Connection

```
GET /api/events
```

Query parameters:

| Parameter | Description |
|-----------|-------------|
| `job_id` | Filter events for a specific job |
| `Last-Event-ID` | Resume from a specific event ID (reconnection) |

## Reconnection

The SSE connection uses exponential backoff for reconnection:

- **Initial delay:** 1 second
- **Multiplier:** 2×
- **Max delay:** 30 seconds
- **Jitter:** ±500ms
- **Max attempts:** 20

The server replays up to 500 recent events (within 5 minutes) on reconnection using the `Last-Event-ID` header.

## Event Types

### Job State Events

| Event Type | Payload Fields | Description |
|------------|---------------|-------------|
| `job_state_changed` | `jobId`, `previousState`, `newState`, `timestamp` | Job transitioned to a new state |
| `job_review` | `jobId`, `timestamp` | Agent completed, job entered review state |
| `job_completed` | `jobId`, `timestamp` | Job resolved (merged/PR/discarded) |
| `job_failed` | `jobId`, `reason`, `timestamp` | Job execution failed |
| `job_resolved` | `jobId`, `action`, `timestamp` | Job resolved (merged/PR/discarded) |
| `job_archived` | `jobId`, `timestamp` | Job moved to history |
| `job_title_updated` | `jobId`, `title` | AI-generated title assigned |

### Approval Events

| Event Type | Payload Fields | Description |
|------------|---------------|-------------|
| `approval_requested` | `approvalId`, `jobId`, `description`, `timestamp` | New approval needed |
| `approval_resolved` | `approvalId`, `jobId`, `decision`, `timestamp` | Approval was resolved |

### Progress Events

| Event Type | Payload Fields | Description |
|------------|---------------|-------------|
| `progress_headline` | `jobId`, `headline` | Short status text (e.g., "Analyzing codebase") |
| `agent_plan_updated` | `jobId`, `steps` | Agent plan steps updated |

### Transcript Events

| Event Type | Payload Fields | Description |
|------------|---------------|-------------|
| `transcript_update` | `jobId`, `entry` | New message in conversation |
| `tool_group_summary` | `jobId`, `groupId`, `summary` | AI summary of a tool call group |

### Data Events

| Event Type | Payload Fields | Description |
|------------|---------------|-------------|
| `log_line` | `jobId`, `level`, `message`, `timestamp` | Structured log entry |
| `diff_update` | `jobId`, `files` | Changed files snapshot |
| `telemetry_updated` | `jobId` | Metrics data available (fetch via REST) |

### Merge Events

| Event Type | Payload Fields | Description |
|------------|---------------|-------------|
| `merge_completed` | `jobId`, `action`, `result` | Merge succeeded |
| `merge_conflict` | `jobId`, `files` | Merge encountered conflicts |

### System Events

| Event Type | Payload Fields | Description |
|------------|---------------|-------------|
| `session_heartbeat` | `timestamp` | Keep-alive signal (every 5s) |
| `snapshot` | `jobs`, `approvals` | Initial state on connection |
| `session_resumed` | `jobId` | Session restarted after pause |
| `model_downgraded` | `jobId`, `requested`, `actual` | Model fallback occurred |

## Event Format

Each SSE frame follows the standard format:

```
id: 42
event: transcript_update
data: {"jobId":"job-1","entry":{"role":"assistant","content":"Analyzing the codebase..."}}
```

The `id` field is a monotonically increasing integer used for reconnection replay.
