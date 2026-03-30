# Job States Reference

Every job in CodePlane follows a state machine that governs its lifecycle.

## State Machine

```
                    ┌──────────┐
                    │  queued   │
                    └────┬─────┘
                         │ agent session starts
                         ▼
                    ┌──────────┐
              ┌─────│ running  │◄────────────────────┐
              │     └────┬─────┘─────┐               │
              │          │           │               │
    approval  │   agent  │    error/ │        approve│
    requested │   done   │   cancel  │               │
              │          │           │               │
              ▼          ▼           ▼               │
    ┌─────────────┐ ┌────────┐ ┌──────────┐         │
    │  waiting_   │ │ review │ │  failed   │         │
    │for_approval │ │        │ │           │         │
    └──────┬──────┘ └───┬────┘ └──────────┘         │
           │            │           ▲                │
    reject │     resolve│           │                │
           └────────────┼───────────┘                │
                        ▼                            │
                 ┌───────────┐                       │
                 │ completed │───────────────────────┘
                 └───────────┘
```

- **Approve** transitions `waiting_for_approval` → `running`
- **Reject** transitions `waiting_for_approval` → `failed`

## States

| State | Description | Terminal? |
|-------|-------------|-----------|
| `queued` | Job created, waiting to start | No |
| `running` | Agent is actively executing | No |
| `waiting_for_approval` | Agent paused, waiting for operator to approve/reject an action | No |
| `review` | Agent completed successfully, awaiting operator review (merge/PR/discard) | No |
| `completed` | Job resolved — changes merged, PR created, or discarded | Yes |
| `failed` | Job failed due to error, timeout, or heartbeat loss | Yes |
| `canceled` | Job was canceled by the operator | Yes |

## Valid Transitions

| From | To | Trigger |
|------|----|---------|
| `queued` | `running` | Agent session starts |
| `queued` | `canceled` | Operator cancels before start |
| `running` | `waiting_for_approval` | Agent requests permission for risky action |
| `running` | `review` | Agent completes task successfully |
| `running` | `failed` | Error, timeout, or heartbeat loss |
| `running` | `canceled` | Operator cancels |
| `waiting_for_approval` | `running` | Operator approves |
| `waiting_for_approval` | `failed` | Operator rejects |
| `waiting_for_approval` | `canceled` | Operator cancels |
| `review` | `completed` | Operator resolves (merge/PR/discard) |
| `review` | `running` | Operator creates follow-up job |
| `review` | `canceled` | Operator cancels |
| `completed` | `running` | Operator reruns |
| `failed` | `running` | Operator reruns |
| `canceled` | `running` | Operator reruns |

## Restart Recovery

If the CodePlane server restarts while jobs are running:

- All `running` and `waiting_for_approval` jobs are marked as `failed`
- The failure reason is set to `"server_restart"`
- Jobs can be rerun after recovery

## Heartbeat Watchdog

The agent session emits heartbeats every 30 seconds. If a heartbeat is missed:

- **After 90 seconds:** Warning logged
- **After 5 minutes:** Job fails with reason `"heartbeat_timeout"`

## Job IDs

Jobs use sequential IDs in the format `job-{N}` (e.g., `job-1`, `job-2`, `job-3`), backed by an internal SQLite autoincrement sequence.
