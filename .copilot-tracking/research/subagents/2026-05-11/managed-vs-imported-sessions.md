# Managed vs Imported Sessions: Exhaustive Gap Analysis

## Research Questions

1. What does the managed session lifecycle do at every stage?
2. What does the imported session lifecycle do at every stage?
3. For each of 18 dimensions, what are the exact gaps between the two paths?
4. Claude CLI vs Copilot CLI differences within the imported path.

---

## Dimension-by-Dimension Analysis

### (a) Job Creation & Metadata

**Managed** (`backend/services/job_service.py:create_job`, lines 290-370):
- Creates in `preparing` state
- Calls NamingService for LLM-generated title, description, branch, worktree_name
- Sets: repo, prompt, base_ref (default branch), branch (generated), state=preparing
- Sets: preset, model, sdk, verify, self_review, max_turns, verify_prompt, self_review_prompt, parent_job_id
- Job ID = worktree_name (LLM-generated slug like `fix-login-bug`)
- worktree_path = None initially (set later in setup_workspace)
- source = "managed" (default)

**Imported** (`backend/services/ingest_service.py:_create_job_from_session`, lines 480-585):
- Creates directly in `running` state (skips preparing/queued)
- No NamingService call — no LLM-generated title, description
- Title = None, description = None, worktree_name = None
- prompt = "(imported CLI session)" (placeholder, updated on first UserPromptSubmit)
- Job ID = `{repo_slug}-{sha256(session_id)[:6]}` (deterministic hash)
- worktree_path = cwd (user's working directory, not a managed worktree)
- branch = current git branch at cwd (auto-detected)
- base_ref = HEAD SHA at session start (not default branch name)
- source = "claude_cli" or "copilot_cli"
- external_session_id = session_id (Claude) or conversation_id (Copilot)
- model = from payload (Claude only — Copilot doesn't send it at creation)
- preset = Preset.supervised (default; mapped from Claude permission_mode on first UserPromptSubmit)

**GAPS:**
- **GAP-a1**: No title/description generation at creation. Auto-title fires later from TrailService._maybe_auto_title on first agent message — covers the gap partially, but no description is ever generated.
- **GAP-a2**: No worktree_name is set. UI may display None or empty.
- **GAP-a3**: Prompt starts as placeholder. Updated on first UserPromptSubmit (Claude only). Copilot OTEL path NEVER updates the prompt — no user message hook exists.
- **GAP-a4**: No verify/self_review/max_turns/verify_prompt/self_review_prompt fields set. These features are inaccessible for imported sessions.
- **GAP-a5**: No parent_job_id support for imported sessions.
- **GAP-a6**: Copilot OTEL doesn't capture model at creation (only from chat spans later).

### (b) State Machine

**Managed** (`backend/services/runtime_service.py`):
- preparing → queued → running → (waiting_for_approval ↔ running) → review → completed|failed
- preparing → failed (worktree creation error)
- running → canceled (operator cancel)
- review → completed (after resolution/merge)
- All transitions validated by `validate_state_transition()`

**Imported** (`backend/services/ingest_service.py`):
- Starts directly in `running` (skips preparing and queued entirely)
- running ↔ review (on Stop hook = review, on UserPromptSubmit = back to running)
- review → (finalized via _finalize_session; stays in review — never transitions to completed)
- running → canceled (via abort_session)
- State transitions use raw `repo.update_state()` — does NOT call `validate_state_transition()`

**GAPS:**
- **GAP-b1**: Imported sessions skip `preparing` and `queued` states entirely. No workspace_prepared or setup_progress events.
- **GAP-b2**: Imported sessions never reach `completed` state. _finalize_session transitions to `review` and emits job_review, but never transitions to completed. The job stays in review forever unless manually resolved via the API.
- **GAP-b3**: No `waiting_for_approval` state for imported sessions. PreToolUse hook returns empty dict — no approval blocking.
- **GAP-b4**: State transitions bypass `validate_state_transition()` — no validation that transitions are legal.

### (c) Event Types Emitted

**Managed** emits all of:
- job_created, job_setup_progress, workspace_prepared, agent_session_started
- execution_phase_changed (environment_setup, agent_reasoning, finalization)
- log_line_emitted, transcript_updated, diff_updated
- approval_requested, approval_resolved, batch_approval_requested, batch_approval_resolved
- session_heartbeat (every 30s)
- step_started, step_completed (from StepTracker)
- turn_summary, action_classified (from TrailService)
- plan_step_updated (from PlanManager)
- job_title_updated, progress_headline
- model_downgraded
- job_review, job_completed, job_failed, job_canceled
- telemetry_updated (final)

**Imported** emits:
- job_created ✓
- job_state_changed ✓
- transcript_updated ✓ (via EventProcessor)
- diff_updated ✓ (via EventProcessor → DiffService)
- step_started, step_completed ✓ (via EventProcessor → StepTracker — for events fed through _feed_event)
- step_completed ✓ (directly emitted on Claude Stop hook with accumulated turn data)
- telemetry_updated ✓ (Copilot OTEL chat spans only)
- job_review ✓ (on finalization)
- job_title_updated ✓ (from TrailService auto-title)
- plan_step_updated ✓ (from PlanManager via EventBus subscriber)
- turn_summary ✓ (from ActivityTracker via EventBus)

**GAPS:**
- **GAP-c1**: No job_setup_progress events (no setup phase)
- **GAP-c2**: No execution_phase_changed events (no phase tracking)
- **GAP-c3**: No session_heartbeat events (no heartbeat loop)
- **GAP-c4**: No approval_requested/approval_resolved events (PreToolUse is a no-op)
- **GAP-c5**: No model_downgraded event handling
- **GAP-c6**: No job_completed event (only job_review on finalization)
- **GAP-c7**: No telemetry_updated for Claude CLI sessions (no LLM span data available from hooks)
- **GAP-c8**: No log_line_emitted events (hooks don't provide log data)

### (d) Transcript Entries

**Managed** (via SDK adapter → SessionEvent → _translate_event → DomainEvent):
- role=operator (from send_message)
- role=agent/assistant (from SDK stream)
- role=agent_delta (streaming chunks)
- role=tool_call with: tool_name, tool_args (raw from SDK), tool_result, tool_success, tool_issue, tool_intent, tool_title, tool_display, tool_duration_ms, turn_id
- role=tool_running (streaming tool execution)
- role=tool_output_delta (streaming shell output)
- All enriched with step_number and step_id by StepTracker/TrailService

**Imported — Claude hooks**:
- role=operator (from UserPromptSubmit.prompt)
- role=agent (from Stop.last_assistant_message)
- role=tool_call with: tool_name, tool_args (json.dumps(tool_input)), tool_result (json.dumps(tool_response)), tool_duration_ms, turn_id, seq, timestamp
- Enriched with step_number/step_id via EventProcessor → StepTracker

**Imported — Copilot OTEL**:
- role=tool_call with: tool_name, tool_args (from gen_ai.tool.call.arguments), tool_result (from gen_ai.tool.call.result), turn_id, seq, timestamp
- No operator messages (no user prompt hook in OTEL)
- No agent messages (no response content in OTEL tool spans)

**GAPS:**
- **GAP-d1**: No streaming roles (agent_delta, tool_running, tool_output_delta) — imported sessions only get completed events, not incremental streaming.
- **GAP-d2**: No tool_success/tool_issue/tool_intent/tool_title/tool_display fields in imported transcript entries. These are set by the SDK adapter's tool handling, not available from hooks/OTEL.
- **GAP-d3**: Copilot OTEL has no operator messages and no agent response content — transcript is tool-calls only.
- **GAP-d4**: Claude hooks JSON-encode tool_args/tool_result (`json.dumps()`), while managed sessions may pass raw dicts or strings. Potential format inconsistency.

### (e) Trail/Activity Entries

**Managed**: Trail driven entirely by EventBus → TrailService.handle_event subscriber. All DomainEvents published to the bus flow through the trail pipeline automatically.

**Imported**: Same EventBus path. IngestService publishes DomainEvents to the bus, TrailService subscribes. The trail pipeline treats imported events identically.

**GAPS:**
- **GAP-e1**: Trail works via EventBus subscription — both paths publish to the same bus, so trail entries ARE created for imported sessions. However, imported sessions produce fewer event types (no execution_phase_changed, no heartbeats), so trail coverage is thinner.
- **GAP-e2**: The step_completed event emitted directly by IngestService (Claude Stop hook, line 249) bypasses EventProcessor and publishes directly to EventBus. This works for trail, but the payload shape differs slightly from StepTracker's step_completed (missing start_sha, end_sha, preceding_context, status fields).
- **GAP-e3**: No execution_phase trail nodes for imported sessions (no phase events emitted).

### (f) Diff Computation

**Managed** (`backend/services/runtime_service.py`):
- DiffService receives worktree_path and base_ref from Job record
- Triggered on file_changed events and tool_call events (via _process_agent_event)
- Throttled to 5-second windows via DiffService
- Finalized at job completion (_finalize_diff_safe) — bypasses throttle

**Imported** (`backend/services/ingest_service.py`):
- DiffService receives worktree_path and base_ref from _JobContext
- Triggered via _feed_event → EventProcessor.process_event (same diff logic)
- Also triggered by explicit file_changed events emitted for write tools
- Finalized in _finalize_session (line 607-609) — calls DiffService.finalize directly

**GAPS:**
- **GAP-f1**: base_ref handling differs. Managed uses the default branch name (e.g., "main"). Imported uses HEAD SHA at session start. Both work for diff, but the imported path pins to a specific commit which is more robust (agent commits won't shift the baseline).
- **GAP-f2**: Actually, this is a MANAGED advantage — imported base_ref as HEAD SHA is better. No real gap here. Both paths trigger diff correctly.
- **GAP-f3**: Minor: imported sessions access DiffService via `self._processor._diff_service` (private attribute access) in _finalize_session — fragile coupling.

### (g) Cost/Token Tracking

**Managed** (`backend/services/runtime_telemetry.py`, `backend/services/telemetry.py`):
- `start_job_span()` creates OTEL root span at job start
- `init_telemetry_row()` creates job_telemetry_summary DB row (sdk, model, repo, branch)
- SDK adapter reports tokens/cost per LLM call → telemetry spans recorded
- `end_job_span()` closes OTEL span at job end
- `finalize_job_telemetry()` runs: cost_attribution, latency_attribution, statistical_analysis
- Publishes telemetry_updated event after finalization
- Stores artifacts (telemetry report, plan, approvals, logs)

**Imported — Copilot OTEL**:
- Chat spans emit telemetry_updated events with input_tokens, output_tokens, total_cost_usd, model
- These events flow to EventBus subscribers

**Imported — Claude hooks**:
- No token/cost data available from hooks at all.

**GAPS:**
- **GAP-g1**: No `start_job_span()` / `end_job_span()` calls for imported sessions — no OTEL root span.
- **GAP-g2**: No `init_telemetry_row()` — no job_telemetry_summary DB row created for imported sessions. This means the analytics dashboard shows no telemetry data.
- **GAP-g3**: No `finalize_job_telemetry()` — no cost_attribution, no latency_attribution, no statistical_analysis, no artifact storage.
- **GAP-g4**: Claude CLI has ZERO token/cost data — hooks don't provide it. Complete blind spot.
- **GAP-g5**: Copilot OTEL emits raw telemetry_updated events, but without a summary row, these events aren't aggregated into job-level metrics.
- **GAP-g6**: No telemetry artifacts stored for imported sessions (no post-completion artifact pipeline).

### (h) Step/Plan Tracking

**Managed**:
- StepTracker (wired into RuntimeService) fires step_started/step_completed based on transcript event patterns
- TrailService.handle_event captures plan steps from manage_todo_list/TodoWrite tool calls
- PlanManager generates plan_step_updated events

**Imported**:
- StepTracker is wired into EventProcessor — imported transcript events flow through it identically
- IngestService ALSO emits its own step_completed events on Claude Stop hooks (line 239-258) with accumulated turn data
- TrailService handles imported events via the same EventBus subscriber

**GAPS:**
- **GAP-h1**: Dual step_completed emission on Claude path: StepTracker emits step_completed from transcript event analysis AND IngestService emits step_completed on Stop hooks. This could cause duplicate/conflicting step completion events.
- **GAP-h2**: Copilot OTEL path has no explicit turn boundary signals. Steps are driven purely by StepTracker's transcript pattern analysis, which may miss turn boundaries since there are no agent messages or user prompts in the OTEL data.

### (i) File Change Tracking

**Managed**: SDK adapter emits file_changed SessionEvents directly from the SDK's tool execution callback.

**Imported — Claude hooks**: IngestService explicitly emits file_changed events when PostToolUse has a file write tool (line 214-218).

**Imported — Copilot OTEL**: IngestService explicitly emits file_changed events when execute_tool spans have file write tools (line 337-341).

**GAPS:**
- **GAP-i1**: File path extraction relies on `_extract_file_path()` which checks tool_input for file_path/filePath/path/file keys. This is best-effort and may miss files if tools use non-standard argument names.
- **GAP-i2**: Tool classification relies on `TOOL_CATEGORIES` dict. If a tool isn't in the dict, it defaults to "other" and no file_changed event is emitted even if it writes files.

### (j) Telemetry Spans

**Managed**:
- OTEL root span created per job via `start_job_span()`
- SDK adapter records child spans for each LLM call
- Spans include: sdk, model, repo, branch, job_id
- Persisted to `job_telemetry_spans` table

**Imported**:
- No OTEL root span created
- No child spans created by IngestService
- Copilot OTEL raw spans are consumed but not re-emitted as CodePlane telemetry spans

**GAPS:**
- **GAP-j1**: No OTEL spans created for imported sessions at all. The entire OTEL telemetry pipeline is bypassed.
- **GAP-j2**: Even though Copilot OTEL provides span data, it's only used for telemetry_updated events (token counts), not stored as structured telemetry spans.

### (k) Metrics on Job Record

**Managed**:
- job_telemetry_summary row: total_cost, total_input_tokens, total_output_tokens, duration_ms, sdk, model, repo, branch, status
- Updated in finalize_job_telemetry

**Imported**:
- No job_telemetry_summary row exists
- Duration is not calculated (no wall_start timestamp tracked)
- Model may be set on job record but not on any telemetry row

**GAPS:**
- **GAP-k1**: No metrics persistence for imported sessions. Dashboard analytics are completely missing.
- **GAP-k2**: No duration tracking — no wall_start captured.

### (l) Permissions/Approval

**Managed**:
- PolicyRouter wired into SDK adapter
- Approval flow: tool request → PolicyRouter classifies → ApprovalBatcher groups → batch_approval_requested event → operator resolves → batch_approval_resolved → tool unblocked
- Granular per-action policies (path rules, action rules, cost rules, MCP configs)
- State transitions: running → waiting_for_approval → running

**Imported**:
- PreToolUse hook returns `{}` (empty dict, line 224-225) — no approval blocking
- No PolicyRouter, no ApprovalBatcher, no trust store
- No waiting_for_approval state transition

**GAPS:**
- **GAP-l1**: Imported sessions have NO approval/permission system. All tools execute without CodePlane oversight.
- **GAP-l2**: No policy enforcement means no cost controls, no path restrictions, no action restrictions.
- **GAP-l3**: For Claude CLI, PreToolUse hook COULD return `{"decision": "block", "reason": ...}` to deny tool execution, but this capability is unused.

### (m) Session End/Finalization

**Managed** (`runtime_service.py:_run_job finally block`, lines 657-663):
- Calls `finalize_job_telemetry()` (cost attribution, artifacts)
- Cancels heartbeat
- Calls `trail_service.stop_tracking()` and `trail_service.finalize()` (finalizes plan steps)
- Handles: completed, failed, canceled, model_downgrade paths

**Imported** (`ingest_service.py:_finalize_session`, lines 588-622):
- Transitions to review (not completed)
- Calls DiffService.finalize (bypasses throttle)
- Calls EventProcessor.on_job_terminal (closes step tracker)
- Publishes job_review event
- Calls _cleanup_session (removes in-memory maps, seq counters, turn state)
- Calls sister_sessions.close_job

**GAPS:**
- **GAP-m1**: No telemetry finalization (no cost_attribution, no latency_attribution, no statistical_analysis)
- **GAP-m2**: No artifact storage (no telemetry report, plan, approval history, log artifacts)
- **GAP-m3**: No trail_service.finalize() call — plan steps not marked completed/failed
- **GAP-m4**: No trail_service.stop_tracking()/cleanup() — trail state may leak
- **GAP-m5**: Never transitions to completed — stays in review indefinitely
- **GAP-m7**: No dequeue_next() — won't start next queued job (though this is less relevant for imported sessions)

### (n) Error Handling

**Managed**:
- SessionEventKind.error → DomainEventKind.job_failed → error_reason captured
- _fail_job transitions to failed state, publishes job_failed event
- ensure_terminal_state safety net prevents stuck jobs
- Catches CancelledError for shutdown vs operator cancel

**Imported**:
- No error event handling from hooks. Claude hooks don't send error events.
- If a hook processing raises an exception, it's unhandled — the FastAPI error handler returns 500.
- Double-finalize guard prevents duplicate SessionEnd processing.

**GAPS:**
- **GAP-n1**: No error event translation from CLI. If the external agent crashes, CodePlane doesn't know.
- **GAP-n2**: No ensure_terminal_state safety net — if IngestService crashes mid-session, the job stays in running/review forever.
- **GAP-n3**: No failure_reason capture for imported sessions.
- **GAP-n4**: Server restart doesn't recover imported sessions (no recover_on_startup handling for non-managed sources).

### (o) Turn Tracking

**Managed**: Turn boundaries detected by StepTracker from transcript event patterns (agent messages mark turn boundaries).

**Imported — Claude**:
- Explicit turn boundaries: UserPromptSubmit starts a turn, Stop ends a turn.
- Turn counter incremented on Stop hook (_next_turn_id).
- Accumulated per-turn state: tool names, files read, files written, duration_ms.
- step_completed emitted with turn summary data.

**Imported — Copilot OTEL**:
- No explicit turn boundaries. Turn counter never incremented.
- turn_id always = "turn-1" (since _turn_counters never incremented via OTEL path).
- No step_completed emitted from OTEL path.

**GAPS:**
- **GAP-o1**: Copilot OTEL has no turn boundary tracking. All tool calls appear in "turn-1".
- **GAP-o2**: Copilot OTEL emits no step_completed events. The accumulated turn data (tool_names, files_read, files_written, duration_ms) is never emitted.
- **GAP-o3**: Copilot OTEL has no user prompt tracking — if the user sends multiple prompts, they're invisible.

### (p) Subagent Tracking

**Managed**: SDK adapter emits subagent-related tool calls naturally as tool_call transcript events.

**Imported — Claude**: SubagentStart and SubagentStop hooks are received but return empty dict (lines 275-279). No events emitted, no tracking.

**Imported — Copilot OTEL**: No subagent span handling.

**GAPS:**
- **GAP-p1**: SubagentStart/SubagentStop hooks are completely ignored. No subagent cost or activity tracking for imported sessions.

### (q) Observations

**Managed**: Observations are created by the `statistical_analysis` and `cost_attribution` pipelines that run during `finalize_job_telemetry()`. These produce `CostObservationRow` entries in the `cost_observations` table.

**Imported**: Neither `finalize_job_telemetry()` nor any analysis pipeline runs.

**GAPS:**
- **GAP-q1**: No observations created for imported sessions. Zero cross-job analytics.

### (r) API Endpoints

**Both paths share the same API endpoints.** Key routing decisions:

- `POST /jobs/{job_id}/cancel` — checks `job.source != "managed"` → delegates to `ingest.abort_session()` vs `runtime_service.cancel()`. ✓ Works for both.
- `POST /jobs/{job_id}/messages` — checks `job.source != "managed"` → delegates to `ingest.send_operator_message()` vs `runtime_service.send_message()`. ✓ Works for both.
- `POST /jobs/{job_id}/resolve` — calls `svc.validate_for_resolution()` then `svc.resolve_and_complete()`. These are source-agnostic. ✓ Works for both.
- `GET /jobs/{job_id}` — returns job record. Source-agnostic. ✓
- `GET /jobs/{job_id}/transcript` — queries DomainEvents. Source-agnostic. ✓
- `GET /jobs/{job_id}/diff` — queries diff events. Source-agnostic. ✓
- `POST /jobs/{job_id}/rerun` — creates new managed job from existing config. Will fail for imported sessions (tries to use worktree_path that is the user's cwd, not a CodePlane-managed worktree).
- `POST /jobs/{job_id}/resume` — resumes via RuntimeService. Will attempt to launch a managed SDK session on the imported job's worktree (cwd). May work but changes the session from imported to managed.
- `POST /jobs/{job_id}/interrupt` — only works for managed sessions (checks RuntimeService._agent_sessions).

**GAPS:**
- **GAP-r1**: `POST /jobs/{job_id}/rerun` likely fails for imported sessions — tries to use the original cwd as repo path.
- **GAP-r2**: `POST /jobs/{job_id}/resume` converts an imported job to a managed session — may cause confusion.
- **GAP-r3**: `POST /jobs/{job_id}/interrupt` returns 404 for imported sessions.
- **GAP-r4**: Telemetry endpoints (cost analytics, spans) return empty data for imported sessions (no telemetry rows).

---

## Section 4: Claude CLI vs Copilot CLI Differences

| Dimension | Claude CLI (hooks) | Copilot CLI (OTEL) |
|---|---|---|
| **Job creation trigger** | SessionStart hook with session_id, cwd, model | First OTEL span with conversation_id, process.cwd |
| **User prompts** | UserPromptSubmit hook delivers prompt text | No user prompt data in OTEL |
| **Tool calls** | PostToolUse hook: tool_name, tool_input, tool_response, duration_ms | execute_tool spans: tool_name from span name, args/result from attributes |
| **Agent responses** | Stop hook: last_assistant_message | No agent response content in OTEL |
| **Turn boundaries** | Explicit: UserPromptSubmit starts, Stop ends | None — no turn detection |
| **Token/cost data** | None (hooks don't provide it) | chat spans: input_tokens, output_tokens, cost, model |
| **Model info** | model in SessionStart payload | model from chat span gen_ai.response.model |
| **Session end** | SessionEnd hook | invoke_agent span with duration > 0 |
| **Operator messaging** | Block response in Stop hook return | CopilotSteerClient.send_message() |
| **Abort** | Queue block message for next hook | CopilotSteerClient.abort() |
| **Subagents** | SubagentStart/SubagentStop hooks (ignored) | No subagent span handling |
| **Preset mapping** | permission_mode → Preset mapping on UserPromptSubmit | No preset mapping |
| **Prompt capture** | First UserPromptSubmit replaces placeholder | Never updated from placeholder |
| **step_completed** | Emitted on Stop with accumulated turn data | Never emitted |

---

## Summary: All Gaps Requiring Fixes for Full Parity

### Critical (data loss / broken functionality)

| # | Gap | Impact | Location |
|---|---|---|---|
| GAP-g2 | No telemetry summary row for imported sessions | Analytics dashboard shows nothing | ingest_service.py:_create_job_from_session |
| GAP-g3 | No finalize_job_telemetry | No cost attribution, no artifacts | ingest_service.py:_finalize_session |
| GAP-g4 | Claude CLI has zero token/cost data | Complete cost blind spot | ingest_service.py:ingest_claude_hook |
| GAP-b2 | Never reaches completed state | Jobs stuck in review forever | ingest_service.py:_finalize_session |
| GAP-m3 | No trail_service.finalize() | Plan steps never completed | ingest_service.py:_finalize_session |
| GAP-n2 | No terminal state safety net | Stuck jobs on crash | ingest_service.py (missing) |
| GAP-n4 | No startup recovery | Imported sessions lost on restart | runtime_service.py:recover_on_startup |
| GAP-k1 | No metrics persistence | No analytics for imported jobs | ingest_service.py (missing) |

### High (feature gaps)

| # | Gap | Impact | Location |
|---|---|---|---|
| GAP-l1 | No approval/permission system | All tools uncontrolled | ingest_service.py:PreToolUse handler |
| GAP-o1 | Copilot OTEL no turn tracking | All tools in turn-1 | ingest_service.py:ingest_otel_span |
| GAP-o2 | Copilot OTEL no step_completed | Activity timeline empty | ingest_service.py:ingest_otel_span |
| GAP-a3 | Copilot OTEL never updates prompt | Prompt stays placeholder | ingest_service.py:ingest_otel_span |
| GAP-d2 | No tool_success/tool_issue/etc | Reduced transcript richness | ingest_service.py:PostToolUse/OTEL |
| GAP-c2 | No execution phase events | Phase timeline empty | ingest_service.py (missing) |
| GAP-p1 | Subagent hooks ignored | No subagent tracking | ingest_service.py:SubagentStart/Stop |
| GAP-h1 | Dual step_completed on Claude path | Potential duplicate events | ingest_service.py + EventProcessor |

### Medium (missing features / polish)

| # | Gap | Impact | Location |
|---|---|---|---|
| GAP-c3 | No heartbeats | No session health indicator | ingest_service.py (missing) |
| GAP-a2 | No worktree_name | UI display issue | ingest_service.py:_create_job_from_session |
| GAP-m4 | No trail cleanup | Memory leak | ingest_service.py:_finalize_session |
| GAP-r1 | Rerun fails for imported sessions | Feature unavailable | backend/api/jobs.py |
| GAP-r3 | Interrupt returns 404 | Feature unavailable | backend/api/jobs.py |
| GAP-b4 | State transitions bypass validation | Potential invalid states | ingest_service.py:_transition_state |
| GAP-f3 | Private attribute access for DiffService | Fragile coupling | ingest_service.py:_finalize_session:607 |
| GAP-j1 | No OTEL spans for imported sessions | No tracing/waterfall view | ingest_service.py (missing) |
| GAP-q1 | No observations | No cross-job analytics | ingest_service.py (missing) |
| GAP-a1 | No description generated | Minor UI issue | ingest_service.py (auto-title exists) |
| GAP-m2 | No artifact storage | No downloadable reports | ingest_service.py:_finalize_session |
| GAP-d1 | No streaming transcript roles | No live typing indicator | Structural limitation |
| GAP-c8 | No log_line events | No log tab content | Structural limitation |
