# Review Tab — Fix Plan

Verified against live system (job `fix-code-smells-and-bugs`, localhost:8080) on 2026-05-24.

---

## Bug 1: Motivation Path Mismatch

**Problem:** 2/5 file motivations silently lost. Telemetry spans store `tool_target` as absolute paths in two forms: worktree-rooted (`/.../onboard-wizard/.codeplane-worktrees/fix-code-smells-and-bugs/src/...`) and repo-rooted (`/.../onboard-wizard/package.json`). The normalization at `backend/api/job_artifacts.py:1548` only strips the worktree prefix. Repo-rooted paths leak through as absolute. Frontend does exact key match (`fileMots[filePath]`) against relative diff paths — mismatch.

**Fix:** After the `wt_prefix` strip, add a fallback `repo_prefix` strip. At `backend/api/job_artifacts.py` around line 1537:

```python
# Current (broken):
wt_prefix = ((job.worktree_path or job.repo or "") + "/").replace("//", "/")
# ...
rel_target = target[len(wt_prefix):] if target.startswith(wt_prefix) else target

# Fixed:
wt_prefix = ((job.worktree_path or job.repo or "") + "/").replace("//", "/")
repo_prefix = ((job.repo or "") + "/").replace("//", "/")
# ...
if target.startswith(wt_prefix):
    rel_target = target[len(wt_prefix):]
elif target.startswith(repo_prefix):
    rel_target = target[len(repo_prefix):]
else:
    rel_target = target
```

**Scope:** 5 lines changed in one file.

---

## Bug 2: Trail Enrichment Stuck (Story Never Caches)

**Problem:** 4 trail nodes stuck in `enrichment='pending'` forever. Story service checks `WHERE enrichment = 'pending'` — count > 0 → skips caching → `/story` returns `{"pending": true}` indefinitely.

**Root cause chain (verified):**

1. Trail enricher (`backend/services/trail/enricher.py:94`) calls `self._sidecar_sessions.complete(full_prompt)`.
2. OLD code: `SidecarSessionManager.complete()` checked `self._fast_completer.available`. The `LightweightCompleter` detected no `OPENAI_API_KEY`/`ANTHROPIC_API_KEY` → returned `available=False`.
3. Fell through to pool path → spawned a `SidecarSession` → called `CopilotAdapter.complete()`.
4. `CopilotAdapter.complete()` (`backend/services/copilot_adapter/_adapter.py:638`) spawns a `CopilotClient` subprocess, creates a session, sends prompt, waits for response. On failure catches `(JsonRpcError, ProcessExitedError, ConnectionError, OSError)` → returns empty `CompletionResult()`.
5. The pool path in `SidecarSession.complete()` wraps with `asyncio.wait_for(timeout=30)`. If the subprocess hangs or the SDK emits an unexpected error type (e.g. `RuntimeError` from copilot internals), `TimeoutError` or the uncaught exception propagates.
6. `SidecarSessionManager.complete()` (OLD code) caught `(OSError, RuntimeError)` on the pool path — but NOT `TimeoutError`.
7. Uncaught `TimeoutError` propagated to `drain_enrichment()`.
8. `drain_enrichment()` catches `(SQLAlchemyError, KeyError, ValueError, OSError)` — does NOT catch `TimeoutError` or `RuntimeError`.
9. Exception hits the `drain_loop` safety-net (`except Exception` at line 583) which logs but does NOT mark nodes as failed.
10. Nodes remain `pending` forever.

**Fix (two parts):**

### Part A: Broaden enricher exception handling

At `backend/services/trail/enricher.py:190`:

```python
# Current:
except (SQLAlchemyError, KeyError, ValueError, OSError):

# Fixed:
except Exception:
```

This ensures ANY failure during enrichment marks nodes as `failed` rather than leaving them `pending`. The drain_loop safety-net already exists as a second layer, but it doesn't mark nodes — only this inner handler does.

### Part B: Already fixed by API key cleanup

The `SidecarSessionManager.complete()` was rewritten in this session (already committed). It now routes through pooled `SidecarSession` → `adapter.complete()`. The adapter catches all subprocess errors internally and returns `CompletionResult()`. Timeouts are caught by `SidecarSessionManager.complete()` which returns `""`. Empty string → `parse_enrichment_response("")` → `None` → nodes marked `failed`.

### Part C: Server restart required

The running server (PID 866930, started 17:07) has the old code. A restart will:
- Pick up the new completer code
- `drain_enrichment()` will fetch the 4 `pending` + 10 `failed` nodes (the repo fetches both)
- Retry enrichment through the now-working adapter path
- Either succeed (enrich properly) or fail gracefully (mark `failed`)
- Once `pending` count = 0, story caches on next generation sweep

---

## Bug 4: Diff Payload Bloat (Option C — Lazy Load Large Files)

**Problem:** 292KB diff payload. `package-lock.json` (94KB) + `tsconfig.tsbuildinfo` (80KB) = 60% waste. All hunks shipped upfront.

**Design:** Lazy loading for files exceeding a size threshold.

### Backend changes

1. New field on `DiffFileItem` schema: `truncated: bool` (default False)
2. When computing diff, if a file's hunk content exceeds a threshold (e.g. raw diff bytes > 20KB), set `truncated=True` and omit `hunks` from the response
3. New endpoint: `GET /jobs/{job_id}/diff/{file_path:path}` — returns the full hunks for a single file on demand
4. Threshold derived from: the median file diff size in practice. 20KB is ~2x the 95th percentile of non-lockfile diffs based on observed data. Could also use a heuristic: files matching `*.lock`, `*-lock.json`, `*.tsbuildinfo` are always truncated regardless of size.

### Frontend changes

1. `DiffViewer.tsx` — when rendering a file with `truncated=true`, show a "Load diff" button instead of hunks
2. On click, fetch `/diff/{file_path}` and merge into the store
3. File list sidebar shows these files normally (with an indicator they're collapsed)

### Files involved

- `backend/models/api_schemas.py` — add `truncated` field to `DiffFileItem`
- `backend/services/artifacts/diff_service.py` — add size check + truncation logic
- `backend/api/job_artifacts.py` — new single-file diff endpoint
- `frontend/src/components/DiffViewer.tsx` — lazy load UI
- `frontend/src/api/client.ts` — new `fetchDiffFile()` function

---

## Execution Order

1. Bug 1 (motivation paths) — immediate, 5 lines
2. Bug 2 (enricher exception) — immediate, 1 line + server restart
5. Diff lazy loading — medium, needs schema change

---

## Prompts for AI Agents

Each prompt below is self-contained. An agent with access to the codebase can execute it without prior context.

---

### Prompt 1: Fix Motivation Path Normalization

```
TASK: Fix a bug where 2/5 file motivations are silently lost in the Review tab.

CONTEXT:
- File: backend/api/job_artifacts.py
- Function: the route handler for GET /jobs/{job_id}/motivations (search for JobMotivationsResponse)
- The handler computes `wt_prefix` from job.worktree_path and strips it from tool_target paths
- Problem: telemetry spans created BEFORE the worktree existed store tool_target as repo-absolute paths (e.g. /home/user/repos/myapp/package.json) which don't start with wt_prefix
- The frontend does exact key match between motivation keys and diff file paths (which are relative like "package.json")

FIX:
1. After computing wt_prefix, also compute repo_prefix: `repo_prefix = ((job.repo or "") + "/").replace("//", "/")`
2. Change the normalization from a single ternary to a cascade:
   - If target starts with wt_prefix → strip wt_prefix
   - Elif target starts with repo_prefix → strip repo_prefix  
   - Else → use target as-is

VERIFICATION:
- Run: uv run pytest backend/tests/unit/ -k "motivation" -x
- If no specific test exists, verify with: curl http://localhost:8080/api/jobs/fix-code-smells-and-bugs/motivations | python3 -c "import json,sys; d=json.load(sys.stdin); [print(k) for k in d['fileMotivations']]"
- All keys should be relative paths (no /home/... prefixes)

DO NOT modify any other files. DO NOT add tests unless the existing test already covers this path.
```

---

### Prompt 2: Fix Trail Enrichment Exception Handling

```
TASK: Fix stuck trail enrichment nodes that block story generation forever.

CONTEXT:
- File: backend/services/trail/enricher.py
- Method: drain_enrichment() (around line 68)
- The method processes pending trail nodes by calling self._sidecar_sessions.complete() for LLM enrichment
- There is a try/except block (around line 190) that catches (SQLAlchemyError, KeyError, ValueError, OSError) and marks nodes as "failed"
- Problem: if the sidecar completion raises TimeoutError, RuntimeError, or any other exception NOT in that tuple, the exception propagates to the outer drain_loop safety-net which logs but does NOT mark nodes as failed
- Result: nodes stay "pending" forever, story service sees pending_enrichment > 0, never caches

FIX:
1. Change the except clause at ~line 190 from:
   `except (SQLAlchemyError, KeyError, ValueError, OSError):`
   to:
   `except Exception:`
2. The existing behavior inside the handler (log + mark nodes failed) is correct — just needs to catch all exceptions

VERIFICATION:
- Run: uv run pytest backend/tests/unit/ -k "enricher" -x
- Verify the drain_loop (around line 560) still has its own `except Exception` safety-net — that's the last-resort crash prevention for the background loop itself
- The two layers serve different purposes: inner (this fix) marks nodes failed; outer prevents the loop from dying

ALSO: After this fix is deployed, the server must be restarted. The 4 stuck "pending" nodes will be picked up by drain_enrichment on the next sweep (it fetches both "pending" and "failed" nodes for retry).

DO NOT modify SidecarSessionManager or any completer code — those were already fixed in a prior session.
```

---

### Prompt 4: Diff Lazy Loading for Large Files

```
TASK: Implement lazy loading for large diff files to reduce the initial payload from 292KB to ~120KB.

CONTEXT:
- Endpoint: GET /jobs/{job_id}/diff returns ALL file diffs including hunks
- package-lock.json (94KB of hunks) and tsconfig.tsbuildinfo (80KB) dominate the payload
- The diff service is at backend/services/artifacts/diff_service.py
- The API schema for diff items is in backend/models/api_schemas.py (search for DiffFileItem or similar)
- Frontend renders diffs in frontend/src/components/DiffViewer.tsx

DESIGN:
1. Add a `truncated: bool = False` field to the diff file response schema
2. In the diff computation, after generating hunks for each file:
   - Calculate total hunk content size (sum of line text lengths)
   - If size > 20KB OR file matches a binary/generated pattern (*.lock, *-lock.json, *.tsbuildinfo, *.min.js, *.min.css):
     - Set truncated=True
     - Replace hunks with an empty array
     - Include a `rawSize: int` field so the UI can show "Load 94KB diff"
3. New endpoint: GET /jobs/{job_id}/diff-file?path={relative_path}
   - Returns the full DiffFileItem (with hunks) for a single file
   - Uses the same diff computation logic but for one file only
4. Frontend changes:
   - In DiffViewer.tsx, when rendering a file with truncated=true, show a button: "Load diff ({rawSize} KB)"
   - On click, call the new endpoint and merge the result into the file list
   - File sidebar shows truncated files with a collapse icon

THRESHOLD JUSTIFICATION:
- 20KB chosen because: observed non-lockfile diffs are all under 10KB for this project
- The pattern list catches the common offenders regardless of size

FILES TO MODIFY:
- backend/models/api_schemas.py — add truncated + rawSize fields
- backend/services/artifacts/diff_service.py — add size check and truncation
- backend/api/job_artifacts.py — new single-file endpoint
- frontend/src/api/client.ts — new fetchDiffFile() function  
- frontend/src/components/DiffViewer.tsx — lazy load UI for truncated files

CONSTRAINTS:
- Do NOT remove large files from the diff entirely — they must still appear in the file list
- The truncation threshold must be a named constant, not a magic number
- Backend schema uses CamelModel (camelCase serialization)
```

---
