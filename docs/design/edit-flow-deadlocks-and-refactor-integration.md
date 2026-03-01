# Edit Flow Deadlocks & Refactor Integration

Analysis of deadlock risks in the current edit flow and proposals for
integrating `refactor_*` endpoints into a unified mutation lifecycle.

---

## Part 1: Deadlock Risks in Current Edit Flow

### Current Architecture

The edit flow has three layers of gating:

1. **Plan gate** — `session.active_plan` must exist (set by `refactor_plan`,
   cleared by `checkpoint`)
2. **Edit call budget** — `plan.edit_calls_made < plan.expected_edit_calls`
   (incremented per `refactor_edit` call)
3. **Batch limit** — `session.edits_since_checkpoint < _MAX_EDIT_BATCHES` (2)
   (reset by `checkpoint`)

The only escape valve for all three is `checkpoint`, which always clears
the plan in its `finally` block (even on lint/test failure).

### Risk 1: Ticket Burns on Partial Failure (CONFIRMED BUG)

**Location:** `edit.py` line ~487 — `ticket.used = True` is set *before*
`_resolve_edit()` runs.

**Scenario:**
```
refactor_edit(edits=[edit_A, edit_B, edit_C])
  → edit_A: ticket.used = True → _resolve_edit → write → OK
  → edit_B: ticket.used = True → _resolve_edit → FAILS (bad old_content)
  → edit_C: never reached
```

**Result:** edit_A applied + ticket burned. edit_B ticket burned even though
its edit failed. edit_C never attempted. The file is in a half-edited state.
Agent must checkpoint to get fresh tickets, but the half-edit may cause
lint failures.

**Impact:** HIGH — experienced repeatedly during the domain classifier
implementation. Forced 4 checkpoint→plan→edit cycles for what should have
been 1 call.

**Root cause:** `ticket.used = True` is a side-effect that runs before the
operation it's guarding completes. Classic "mark before success" bug.

### Risk 2: Budget Exhaustion → Lint Failure → New-Plan Blocked

**Scenario:**
```
refactor_plan(expected_edit_calls=1)
refactor_edit(edits=[...])  → budget: 1/1 used
  → lint fails
  → agent needs another edit to fix lint
  → refactor_edit: "Edit call budget exhausted"
  → refactor_plan: "Active plan already exists"
  → DEADLOCK
```

**Current mitigation:** Checkpoint's `finally` block clears the plan even
on failure (line ~68 of checkpoint.py resolved chunk). This *does* break
the deadlock, but the agent must know to call checkpoint to escape —
the error messages don't make this clear.

**Impact:** MEDIUM — the deadlock is recoverable if the agent calls
checkpoint, but the error remediation text says "Checkpoint to start a
new plan" which is correct but doesn't explain that checkpoint will pass
even with lint errors (it will clear the plan regardless).

### Risk 3: refactor_commit Increments edits_since_checkpoint Independently

**Location:** `refactor.py` line ~891 — `session.edits_since_checkpoint += 1`

**Scenario:**
```
refactor_plan(expected_edit_calls=1)
refactor_rename(symbol="Foo", new_name="Bar")
refactor_commit(refactor_id=...)   → edits_since_checkpoint = 1
refactor_edit(plan_id=..., edits=[...])  → edits_since_checkpoint = 2
  → _MAX_EDIT_BATCHES reached
  → no more edits possible
```

**Impact:** LOW-MEDIUM — `_MAX_EDIT_BATCHES=2` is a deliberately tight
limit, but the agent doesn't know that `refactor_commit` consumes one
of those slots. The two systems share the counter but have no
coordination.

### Risk 4: Continuation Tickets Not Gated to Plan

**Location:** `edit.py` line ~510 — continuation tickets are minted and
stored in `session.edit_tickets` but not in `plan.edit_tickets`.

**Scenario:** Continuation tickets work fine within the same plan, but
they're session-scoped. If a plan is cleared and a new plan created,
stale continuation tickets from the old plan are still valid in
`session.edit_tickets` (they just won't pass the plan's target check).

**Impact:** LOW — no actual exploit path since `refactor_edit` also
checks the plan's edit_targets. But it's a design smell.

---

## Part 2: Design Proposals — Fixing Deadlocks

### Design A: Transactional Ticket Consumption

**Problem solved:** Risk 1 (ticket burns on partial failure)

**Change:** Move `ticket.used = True` to *after* successful write.
Roll back on failure.

```python
# BEFORE (current)
ticket.used = True          # ← burns ticket
new_content, meta = _resolve_edit(...)  # ← may fail
full_path.write_text(new_content)

# AFTER (proposed)
new_content, meta = _resolve_edit(...)  # resolve first
full_path.write_text(new_content)       # write
ticket.used = True                      # mark only on success
```

For multi-edit batches, wrap in a transaction:

```python
pending_writes: list[tuple[Path, str, EditTicket]] = []
for edit in updates:
    ticket = validate_ticket(edit)  # check .used, sha256, etc.
    new_content, meta = _resolve_edit(...)
    pending_writes.append((full_path, new_content, ticket))

# All edits resolved successfully — now commit atomically
for path, content, ticket in pending_writes:
    path.write_text(content)
    ticket.used = True
    # mint continuation ticket...
```

**Trade-off:** If the process crashes between `write_text` and `ticket.used`,
the ticket is reusable but the file has changed → SHA mismatch catches it.
Safe.

**Verdict:** ✅ Do this. Zero new API surface. Pure bug fix.

### Design B: Better Checkpoint Recovery Hints

**Problem solved:** Risk 2 (agent confusion on recovery)

**Change:** When checkpoint fails lint, the recovery hint should clearly
state: "Plan has been cleared. You can create a new plan immediately."

No code change needed beyond improving hint text — the `finally` block
already clears plans. This is an instructions-only fix.

**Verdict:** ✅ Do this. Trivial.

### Design C: All-or-Nothing Edit Batches

**Problem solved:** Risk 1 (partial application) — subsumes Design A.

**Change:** Resolve all edits first, then write all files. If any edit
fails to resolve, none are applied.

```python
# Phase 1: resolve all edits (no writes, no ticket burns)
resolved: list[tuple[Path, str, EditTicket, dict]] = []
for edit in updates:
    ticket = validate_ticket(edit)
    content = full_path.read_text()
    verify_sha(content, ticket.sha256)
    new_content, meta = _resolve_edit(content, ...)
    resolved.append((full_path, new_content, ticket, meta))

# Phase 2: write all files + mark tickets (only if ALL resolved)
for path, content, ticket, meta in resolved:
    path.write_text(content)
    ticket.used = True
    # mint continuation ticket...
```

**Same-file chaining:** If two edits target the same file (via
continuation ticket), the second edit must see the result of the first.
Handle by piping content through:

```python
pending_content: dict[str, str] = {}  # path → latest content
for edit in updates:
    content = pending_content.get(path) or full_path.read_text()
    new_content, meta = _resolve_edit(content, ...)
    pending_content[path] = new_content
```

**Verdict:** ✅ Recommended (subsumes Design A). ~30 lines in edit.py.

---

## Part 3: Refactor_* Integration Proposals

### Current State: Two Parallel Mutation Systems

The codebase has **two independent mutation systems** that share only
the `edits_since_checkpoint` counter:

| Aspect | Plan+Edit Flow | Refactor_* Flow |
|--------|---------------|------------------|
| Entry point | `refactor_plan` | `refactor_rename/move/impact` |
| State | `session.active_plan` + `edit_tickets` | `RefactorOps._pending` (in-memory dict) |
| Gating | Plan required, budget checked | Just `_require_recon_and_justification` |
| Application | `refactor_edit` (find-replace) | `refactor_commit` (hunk-based) |
| Ticket system | SHA256-indexed edit tickets | refactor_id (UUID) |
| Counter | `plan.edit_calls_made` | None |
| Shared counter | `edits_since_checkpoint` | `edits_since_checkpoint` |
| Cleanup | checkpoint clears plan + tickets | `_pending` NEVER cleared by checkpoint |

Consequences:
- Agent can interleave plan edits with refactor_commit freely
- `_pending` previews survive across checkpoints (memory leak)
- No coordination between the two systems' budgets
- AGENTS.md prescribes using one OR the other but nothing enforces it

### Design D: Mutual Exclusion (Recommended)

**Philosophy:** Don't merge the two systems — they solve different problems.
Instead, make them *aware* of each other.

**Changes:**

1. **Checkpoint clears `_pending` previews** — add
   `app_ctx.refactor_ops.clear_pending()` to checkpoint's cleanup block.
   Prevents memory leaks and stale previews across checkpoint boundaries.

2. **Block `refactor_commit` when plan is active** — if `session.active_plan`
   exists, `refactor_commit` refuses to apply. Agent committed to a plan;
   finish or cancel it first.

   ```python
   # In refactor_commit (apply mode):
   if session.active_plan is not None:
       raise MCPError(
           code=MCPErrorCode.INVALID_PARAMS,
           message="Cannot apply refactoring while a plan is active.",
           remediation=(
               "Complete your edit plan with refactor_edit + checkpoint, "
               "or cancel it with checkpoint(changed_files=[])."
           ),
       )
   ```

3. **Block `refactor_plan` when previews are pending** — if
   `RefactorOps._pending` has entries, `refactor_plan` refuses.
   Forces agent to commit or cancel previews before starting a plan.

   ```python
   # In refactor_plan:
   if app_ctx.refactor_ops.has_pending():
       raise MCPError(
           message="Pending refactoring previews exist. "
                   "Commit or cancel them first.",
       )
   ```

**Verdict:** ✅ Recommended. ~20 lines across refactor.py + checkpoint.py.
Prevents interleaving without merging two well-separated systems.

### Design E: Refactor_* Under Plan Umbrella

**Philosophy:** Bring `refactor_rename/move` under the plan system.

**Changes:**
- `refactor_plan` accepts `intent` field (edit/rename/move/mixed)
- For rename/move intents, plan stores operation params instead of tickets
- `refactor_commit` requires plan_id

**Verdict:** ❌ Over-engineered. Adds friction to every rename for no
benefit. The two systems serve different use cases.

### Design F: Optional Plan-Awareness

**Philosophy:** `refactor_rename/move` accept optional `plan_id`.

**Verdict:** ⚠️ Fragile. Agents won't use it consistently. Design D
achieves the same safety with less surface area.

---

## Part 4: Summary & Recommendations

### Must-Do (Bug Fixes)

| # | Design | Risk Fixed | Effort |
|---|--------|-----------|--------|
| 1 | **C: All-or-Nothing Edits** | Risk 1 (ticket burns + partial writes) | ~30 lines in edit.py |
| 2 | **B: Better Checkpoint Hints** | Risk 2 (agent recovery confusion) | ~5 lines in checkpoint.py |

### Should-Do (Integration)

| # | Design | Risk Fixed | Effort |
|---|--------|-----------|--------|
| 3 | **D: Mutual Exclusion** | Risk 3 (counter sharing) + memory leaks | ~20 lines across refactor.py + checkpoint.py |

### Skip

| # | Design | Reason |
|---|--------|--------|
| 4 | E: Plan Umbrella | Over-engineered, too much new API surface |
| 5 | F: Optional Plan-Awareness | Fragile, agents won't use it consistently |

### Implementation Order

1. **Design C** — all-or-nothing edits (biggest impact, fixes the confirmed bug)
2. **Design D** — mutual exclusion (prevents interleaving foot-guns)
3. **Design B** — checkpoint hint improvement (trivial, do alongside D)
