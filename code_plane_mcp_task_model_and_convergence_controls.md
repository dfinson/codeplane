# CodePlane MCP: Task Model, Convergence Controls, and Ledger Design

## Scope

This document specifies **how CodePlane, acting strictly as an MCP server**, models tasks, enforces convergence-related guarantees, and persists an operation ledger.

CodePlane is **not an agent** and **not an orchestrator**. It does not plan, retry, or decide strategies. Its role is to make work **bounded, observable, restart-safe, and auditable**.

---

## Core Design Principle

> CodePlane never relies on agent discipline.
>
> It enforces *mechanical constraints* that make non-convergent behavior visible, finite, and auditable.

---

## Task Model (MCP-Scoped)

### Definition

A **Task** is a **correlation envelope** for MCP calls.

A task exists to:
- group related MCP operations
- apply execution limits
- survive daemon restarts
- produce structured outcomes

A task does **not**:
- own control flow
- store agent reasoning
- perform retries
- infer success or failure

---

### Task Lifecycle

Tasks are explicitly opened and closed by the MCP client.

States:

| State | Meaning |
|------|--------|
| OPEN | Task active; MCP calls may be correlated |
| CLOSED_SUCCESS | Task ended cleanly |
| CLOSED_FAILED | Task aborted due to limits or invariants |
| CLOSED_INTERRUPTED | Daemon restart or client disconnect |

CodePlane never reopens a task implicitly.

---

### Persisted Task State

Durable (persisted):

```yaml
task_id: string
opened_at: timestamp
closed_at: timestamp | null
state: OPEN | CLOSED_*
repo_snapshot:
  git_head: sha
  index_version: int
limits:
  max_mutations: int
  max_test_runs: int
  max_duration_sec: int
counters:
  mutation_count: int
  test_run_count: int
last_mutation_fingerprint: string | null
last_failure_fingerprint: string | null
```

Not persisted:
- prompts
- agent intent
- reasoning traces
- retry logic

---

## Convergence Controls (Server-Enforced)

CodePlane enforces **hard mechanical bounds** on work.

### 1. Mutation Budget

Every state-mutating MCP call increments `mutation_count`.

If `mutation_count > max_mutations`:
- mutation is rejected
- task transitions to `CLOSED_FAILED`
- structured error is returned

This guarantees edit loops are finite.

---

### 2. Test Execution Budget

Test runs are first-class operations.

If `test_run_count > max_test_runs`:
- further test calls are rejected
- silent retries are impossible

---

### 3. Failure Fingerprinting

Deterministic failures are fingerprinted using:
- failing test names
- normalized exception type
- normalized stack trace
- exit code

The fingerprint is returned in every test failure response.

If the same fingerprint occurs after a mutation:
- CodePlane flags non-progress
- repetition becomes mechanically detectable

CodePlane does **not** decide what to do next.

---

### 4. Mutation Fingerprinting

Each mutation returns:

```yaml
mutation_fingerprint:
  files_changed_hash
  diff_stats
  symbol_changes
```

If two consecutive mutations produce identical fingerprints:
- mutation is marked as no-op
- budget still increments
- stalled edits become explicit

---

## Restart Semantics

### Daemon Restart

On restart:
- all OPEN tasks are marked `CLOSED_INTERRUPTED`
- repository state is reconciled from Git
- indexes are revalidated incrementally
- no task resumes implicitly

Clients must explicitly open a new task.

This guarantees:
- no mixed state
- no replayed side effects
- no phantom progress

---

## Operation Ledger

### v1 vs v1.5 Scope

CodePlane deliberately distinguishes between **v1 (minimal, SQLite-only)** logging and **v1.5 (optional artifact expansion)**.

v1 focuses on *mechanical accountability* only.
v1.5 exists solely to improve developer ergonomics if real pain appears.


### Purpose

The ledger provides **mechanical accountability**, not observability or surveillance.

It exists to answer:
- what happened
- in what order
- under what limits
- with what effects

---

### Primary Persistence

The ledger is stored in a **local, append-only SQLite database** owned by the CodePlane daemon.

Example location:

```
~/.codeplane/state/codeplane.db
```

This database is authoritative.

---

### Logical Schema

#### v1 Ledger Schema (SQLite Only)

```sql
tasks (
  task_id TEXT PRIMARY KEY,
  opened_at TIMESTAMP,
  closed_at TIMESTAMP,
  state TEXT,
  repo_head_sha TEXT,
  limits_json TEXT
);

operations (
  op_id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT,
  timestamp TIMESTAMP,
  duration_ms INTEGER,
  op_type TEXT,
  success BOOLEAN,

  -- repo boundaries
  repo_before_hash TEXT,
  repo_after_hash TEXT,

  -- mutation summary (no content)
  changed_paths TEXT,           -- JSON array of file paths
  diff_stats TEXT,              -- files_changed, insertions, deletions
  short_diff TEXT,              -- e.g. "+ foo.py", "- bar.ts", "~ baz.go"

  -- convergence signals
  mutation_fingerprint TEXT,
  failure_fingerprint TEXT,
  failure_class TEXT,
  failing_tests TEXT,
  limit_triggered TEXT,

  FOREIGN KEY(task_id) REFERENCES tasks(task_id)
);
```


```sql
tasks (
  task_id TEXT PRIMARY KEY,
  opened_at TIMESTAMP,
  closed_at TIMESTAMP,
  state TEXT,
  repo_head_sha TEXT,
  limits_json TEXT
);

operations (
  op_id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT,
  timestamp TIMESTAMP,
  op_type TEXT,
  inputs_hash TEXT,
  outputs_hash TEXT,
  repo_before_hash TEXT,
  repo_after_hash TEXT,
  mutation_fingerprint TEXT,
  failure_fingerprint TEXT
);
```

The ledger is append-only.

---

### Optional Artifact Store (v1.5, Deferred)

v1 **does not** include filesystem artifacts.

v1.5 MAY optionally add an artifact store **only if needed**, storing:

- full test logs
- full diffs / patches
- tool stdout / stderr

Artifacts are:
- stored on the filesystem
- referenced by `artifact_id` + hash in SQLite
- short-lived (hours or days, not weeks)

Ledger remains authoritative.
Artifacts are disposable.


Optionally enabled for debugging:

```
~/.codeplane/ledger/YYYY-MM-DD.ndjson
```

This mirror is derived and non-authoritative.

---

## Retention Policy

### v1 (Default)

- SQLite ledger only
- retain 7–14 days or last 500 tasks
- single cleanup mechanism via SQL

### v1.5 (If Artifacts Enabled)

- artifacts retained 24–72 hours
- aggressively GCed
- missing artifacts never invalidate ledger integrity


Default retention:
- keep last 7–14 days of tasks
- or last 500 tasks
- whichever comes first

Configurable via daemon configuration.

No indefinite retention.

---

## Audit Model

### Intended Auditors

- developers
- agent/tool authors
- CodePlane maintainers

### Explicitly Not For

- compliance surveillance
- user monitoring
- model training

Audit means **post-mortem clarity**, not oversight.

---

## What CodePlane Explicitly Does Not Do

- no retries
- no backoff
- no strategy shifts
- no planning
- no success inference

All intelligence lives above MCP.

---

## Resulting Guarantees

This design ensures:
- infinite loops are finite
- retries are countable
- failures are fingerprintable
- progress is provable
- restarts are safe
- audits are complete

Convergence is structurally enforced.

---

## Summary

CodePlane’s task model exists to **bound and observe work**, not to direct it.

It transforms agent misbehavior from:

> "the model got stuck"

into:

> "the system hit a deterministic, explainable limit."

That is the entire point.

