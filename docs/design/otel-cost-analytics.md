# Cost Intelligence: From OpenTelemetry to Agentic Cost Understanding

> **Status:** Proposal  
> **Author:** CodePlane Engineering  
> **Audience:** CodePlane contributors, operators wanting cost visibility  
> **Branch:** `otel-cost-analytics-design`

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Current State Audit](#2-current-state-audit)
3. [The Questions We Want to Answer](#3-the-questions-we-want-to-answer)
4. [Gap Analysis](#4-gap-analysis)
5. [Design: Enriched Telemetry Collection](#5-design-enriched-telemetry-collection)
6. [Design: Schema Extensions](#6-design-schema-extensions)
7. [Design: Derived Metrics & Analytics Engine](#7-design-derived-metrics--analytics-engine)
8. [Design: Dashboard Views](#8-design-dashboard-views)
9. [Implementation Approach](#9-implementation-approach)
10. [Appendix A: Metric Catalog](#appendix-a-metric-catalog)
11. [Appendix B: API Endpoints](#appendix-b-api-endpoints-proposed)
12. [Appendix C: Open Questions](#appendix-c-open-questions)

---

## 1. Executive Summary

CodePlane already has solid OpenTelemetry instrumentation — we track tokens, cost,
LLM call durations, tool call durations, context window state, and compaction events,
all persisted to SQLite and optionally exported via OTLP. But these are **accounting
metrics**: they tell you _what was spent_, not _why it was spent_ or _what drove the
spend_.

This document proposes enriching our telemetry to answer the hard questions:

- **What composition of a prompt drives cost?** Is it the system prompt, prior
  context, tool results, or the reasoning itself?
- **Which tools are cost amplifiers?** Does `bash` tend to precede expensive
  re-reasoning? Does `edit_file` fail-and-retry drive waste?
- **Is the agent re-discovering context?** Are the same files read repeatedly
  across turns? Is compaction forcing re-reads?
- **Where in the agentic lifecycle does waste concentrate?** Environment setup?
  Initial exploration? Edit-test loops? Verification?
- **What task characteristics predict high cost?** Repo size? Prompt complexity?
  Number of files touched? Language?

Over dozens to hundreds of jobs, answers to these questions give operators the data
to tune prompts, select models, configure permissions, and set budgets with
confidence.

### Design Principles

1. **No arbitrary constants.** Every threshold, window, or magic number is replaced
   with data-derived values or eliminated entirely. If a detection rule can't be
   expressed as a deterministic fact about the data, it doesn't belong here.

2. **No approximations.** Token counts are exact (computed via model-specific
   tokenizers). File access is exact (parsed from captured tool arguments). Cost
   attribution is exact (every dollar traces to a specific LLM span in a specific
   phase). Where we cannot measure precisely (e.g., SDK formatting overhead), we
   report the discrepancy explicitly rather than papering over it.

3. **No heuristics.** A tool call is a retry if and only if the same (tool, target)
   pair previously failed — not because it happened "within N turns." A file re-read
   is a set membership fact, not a proximity guess. All metrics are deterministic
   given the input data.

---

## 2. Current State Audit

### 2.1 What We Collect Today

#### OTel Instruments (18 total)

| Type | Instrument | Unit | Attributes |
|------|-----------|------|------------|
| Counter | `cp.tokens.input` | tokens | job_id, sdk, model |
| Counter | `cp.tokens.output` | tokens | job_id, sdk, model |
| Counter | `cp.tokens.cache_read` | tokens | job_id, sdk, model |
| Counter | `cp.tokens.cache_write` | tokens | job_id, sdk, model |
| Counter | `cp.cost` | USD | job_id, sdk, model |
| Counter | `cp.compactions` | — | job_id, sdk |
| Counter | `cp.tokens.compacted` | tokens | job_id, sdk |
| Counter | `cp.messages` | — | job_id, sdk, role |
| Counter | `cp.premium_requests` | — | job_id, sdk |
| Counter | `cp.approvals` | — | — |
| Histogram | `cp.llm.duration` | ms | job_id, sdk, model, is_subagent |
| Histogram | `cp.tool.duration` | ms | job_id, sdk, tool_name, success |
| Histogram | `cp.approval.wait` | ms | — |
| Gauge | `cp.context.tokens` | — | job_id, sdk |
| Gauge | `cp.context.window_size` | — | job_id, sdk |
| Gauge | `cp.quota.used` | — | job_id, sdk, resource |
| Gauge | `cp.quota.entitlement` | — | job_id, sdk, resource |
| Gauge | `cp.quota.remaining_pct` | % | job_id, sdk, resource |

#### Persistent Storage

**`job_telemetry_summary`** — One row per job, atomically upserted on every metric
event. Contains denormalized totals: token counts (input/output/cache), cost, LLM
call count & duration, tool call count & failures & duration, compactions, context
window state, approval and message counts.

**`job_telemetry_spans`** — Append-only per-call detail. Each row is an LLM call or
tool call with: span_type (`llm`|`tool`), name, started_at (offset), duration_ms,
and attrs_json (tokens, cost, success flag, subagent flag).

#### Frontend Analytics

- **AnalyticsScreen** — Fleet-level dashboard: job counts by state, total cost,
  tokens, avg duration, cost trend chart, model breakdown table, tool performance
  table, per-repo breakdown.
- **MetricsPanel** — Per-job drill-down: token summary, cost breakdown, LLM call
  timeline, tool breakdown, context management stats, quota snapshots.

### 2.2 What We Capture But Don't Analyze

Several pieces of data exist in the system but aren't connected to cost analysis:

| Data Source | Currently Stored | Not Connected to Cost |
|-------------|-----------------|----------------------|
| **Transcript entries** | Full agent/operator transcript in events table | Which transcript turns drove which LLM calls? |
| **Tool call arguments** | Tool name + `{"success": bool}` in span attrs_json; full args in TranscriptUpdated event payload only | What file paths? What bash commands? What edit targets? |
| **Diff snapshots** | Table schema exists (`diff_snapshots`) but **never populated** — dead code | How much code was written vs. tokens spent? |
| **Execution phases** | `ExecutionPhaseChanged` event defined and emitted, but **only for verification phase** — not all phases | Cost breakdown by phase (setup/reasoning/verification)? |
| **Agent plans** | Plan steps with status (`AgentPlanUpdated` events) | How much rework vs. linear progress? |
| **Progress headlines** | Headlines with replaces_count | How many pivots/restarts occurred? |
| **Compaction events** | Count + tokens reclaimed | What was lost? Did it cause re-reads? |
| **Approval wait time** | `cp.approvals` counter + `cp.approval.wait` histogram **defined but never incremented** — dead instruments | Cost of idle time during approvals? |
| **Model downgrades** | Event emitted (`ModelDowngraded` with requested_model, actual_model) | Cost saved vs. quality impact? |
| **Tool group summaries** | `ToolGroupSummary` event kind defined in enum but **never published** — dead code | Batch tool analysis per turn? |

### 2.3 Architectural Strengths

- **Dual persistence** (OTel in-memory + SQLite) means we can query without external
  infra.
- **Event bus** already captures rich domain events — we just need to connect them to
  cost.
- **Span-level detail** per LLM/tool call is the right granularity for attribution.
- **Transcript already captures tool arguments** (`tool_args` field in
  TranscriptUpdated payload) — this is rich data we're ignoring for analytics.
  However, **span `attrs_json` is minimal**: tool spans only store
  `{"success": bool}` — no args, no result text, no result size. Enriching
  `attrs_json` is the primary instrumentation gap.
- **Both SDKs provide tool arguments and results**: Copilot via
  `tool.execution_start.arguments` and `tool.execution_complete`; Claude via
  `ToolUseBlock.input` and `ToolResultBlock.content`. The data exists in the
  pipeline — it just isn't persisted to spans.

### 2.4 Dead Code & Unused Definitions

The audit identified several definitions that exist in code but are non-functional:

| Item | Type | Status | Impact |
|------|------|--------|--------|
| `cp.approvals` counter | OTel instrument | Defined in `telemetry.py:89`, never incremented | No approval count in metrics |
| `cp.approval.wait` histogram | OTel instrument | Defined in `telemetry.py:94`, never recorded | No approval latency data |
| `ToolGroupSummary` event | Domain event kind | Defined in `events.py:36` with payload type, never published | Dead code |
| `diff_snapshots` table | DB table | Schema in migration `0001`, `DiffSnapshotRow` model exists, only delete code — never inserted | No diff data stored |

These are relevant because the design doc proposes using diff data (§6.2
`cost_per_diff_line`) and approval metrics — both require activating currently dead
code paths before the cost analytics features can consume them.

---

## 3. The Questions We Want to Answer

We organize the target questions into five categories, from most actionable to most
strategic:

### 3.1 Prompt Composition & Context Cost

> *"What elements of the prompt are eating tokens?"*

- What fraction of input tokens is **system prompt** vs. **conversation history** vs.
  **tool results** vs. **file contents**?
- How does context window utilization correlate with cost per turn?
- After compaction, how much is **re-read** that was already in context?
- What is the **marginal cost** of adding one more file to context?

### 3.2 Tool Call Cost Attribution

> *"Which tools drive cost, and with what arguments?"*

- Which tool calls precede the most expensive LLM calls? (e.g., does a large `bash`
  output lead to an expensive next turn?)
- Which `edit_file` operations fail and trigger retry loops?
- How much cost is **file discovery** (grep/glob/find) vs. **file reading** vs.
  **file editing** vs. **command execution**?
- What is the average cost-per-tool-call by tool type, accounting for the downstream
  LLM call it triggers?

### 3.3 Agentic Lifecycle Waste

> *"Where in the lifecycle does waste concentrate?"*

- What fraction of total cost occurs in each execution phase?
  (environment_setup → agent_reasoning → verification → finalization)
- How much of reasoning cost is **linear progress** vs. **rework** (plan changes,
  re-reads, backtracking)?
- How many **context compactions** occur, and what is the **post-compaction re-read
  cost**?
- What is the cost of **retry loops** (failed tool → re-attempt → re-reason)?

### 3.4 Turn Economics & Session Length

> *"How do turns drive cost, and when does a session become wasteful?"*

- Does cost-per-turn increase as the session progresses (context growth)?
- Is there an inflection point where turns become disproportionately expensive?
- Does turn count predict total cost linearly, or is there a non-linear explosion?
- What fraction of total cost concentrates in the last N turns (diminishing returns)?
- After a compaction, does cost-per-turn reset or remain elevated?
- How does the cost curve differ between succeeded vs. failed jobs? Do failed jobs
  show earlier cost acceleration (spinning without progress)?

### 3.5 Task & Repo Characteristics

> *"What kinds of tasks are expensive?"*

- Does prompt length/complexity predict total cost?
- Do certain repos consistently cost more? Why? (Size? Language? Complexity?)
- Do jobs that touch more files cost proportionally more, or is there a non-linear
  explosion?
- Does the number of operator interventions (approvals, follow-ups) correlate with
  cost?
- Do verification/self-review passes provide value proportional to their cost?

### 3.6 Model & SDK Efficiency

> *"Are we using the right model for the job?"*

- Cost per output token by model — which model produces the most useful output per
  dollar?
- Cache hit rate by model — which models benefit most from prompt caching?
- Subagent cost as fraction of total — are subagents cost-efficient?
- Model downgrade: cost saved vs. job success rate impact?

---

## 4. Gap Analysis

### What we need but don't have

| Gap | Blocking Questions | Difficulty | Approach |
|-----|-------------------|------------|----------|
| **Turn-level cost attribution** | 3.1, 3.2, 3.3, 3.4 | Medium | Link LLM spans to transcript turn sequence numbers |
| **Prompt composition breakdown** | 3.1 | Hard | Instrument adapter to capture prompt segment sizes via local tokenizers (`tiktoken`, `anthropic-tokenizer` — neither currently a dependency) |
| **Tool call arguments in spans** | 3.2 | Easy | Currently tool spans only store `{"success": bool}` in `attrs_json`. Tool args exist in TranscriptUpdated events — copy to span attrs at write time |
| **Tool result size in spans** | 3.2 | Easy | Tool result text exists in adapter pipeline (both SDKs provide it). Compute `len(result_text)` and store in span attrs |
| **File access tracking** | 3.1, 3.2 | Medium | Parse tool args for file paths; deduplicate per job |
| **Phase-tagged cost** | 3.3 | Medium | Tag each span with current execution phase. Note: `ExecutionPhaseChanged` is currently only emitted for verification — must extend to all phases (setup, reasoning, finalization) |
| **Retry/rework detection** | 3.3 | Medium | Deterministic: same (tool, target) pair where prior invocation failed |
| **Post-compaction re-read cost** | 3.1, 3.3 | Hard | Track pre-compaction file set, detect re-reads |
| **Turn economics / cost curve** | 3.4 | Easy | Per-turn cost is derivable from `turn_number` + LLM span cost. Compute half-session cost split, acceleration ratio, peak turn in attribution pipeline |
| **Task complexity features** | 3.5 | Medium | Extract features from prompt + repo at job start |
| **Output efficiency** | 3.6 | Easy but blocked | Compute from diff LOC / cost. **Requires activating diff_snapshots** — table exists but is never populated (dead code) |
| **Cross-job aggregation** | All | Medium | New query layer over existing tables |
| **Activate dead OTel instruments** | 3.3 | Easy | `cp.approvals` counter and `cp.approval.wait` histogram are defined but never called — wire into approval flow |

---

## 5. Design: Enriched Telemetry Collection

### 5.1 Turn-Level Attribution

**Concept:** Each LLM call is a "turn" in the conversation. We need to assign a
monotonically increasing `turn_number` to each LLM span so we can correlate it with
transcript entries, tool calls, and phase transitions.

**Implementation:**

```
Adapter (copilot/claude)
  │
  ├─ on LLM completion:
  │    turn_number += 1
  │    record_llm_call(turn_number, tokens, cost, duration, ...)
  │
  ├─ on tool call start:
  │    record_tool_call(turn_number, tool_name, tool_args, ...)
  │
  └─ on tool call end:
       update_tool_call(duration, success, result_size, ...)
```

**New span attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `turn_number` | int | Sequential turn within the job |
| `turn_tool_calls` | int | Number of tool calls in this turn |
| `turn_input_tokens` | int | Input tokens for this specific turn |
| `turn_output_tokens` | int | Output tokens for this specific turn |

### 5.2 Prompt Composition Breakdown

**Concept:** For each LLM call, capture the exact token count of each segment of the
input. We control the adapter layer and have access to every piece of data that
enters the conversation — system prompt, agent messages, operator messages, tool
results — so we can tokenize each segment precisely.

**New span attributes for LLM calls:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `prompt_system_tokens` | int | Exact tokens in system prompt |
| `prompt_history_tokens` | int | Exact tokens in prior conversation messages |
| `prompt_tool_results_tokens` | int | Exact tokens in tool result content |
| `prompt_file_contents_tokens` | int | Exact tokens in file-read tool results specifically |
| `prompt_user_message_tokens` | int | Exact tokens in the latest user/operator message |

**Implementation:** We have the exact text of every conversation segment:

- **System prompt:** We construct it in the adapter at session start. We have the
  literal string. Tokenize it once and store the count.
- **Tool results:** Every `tool_result` field is captured verbatim in the
  `TranscriptUpdated` event payload. We have the exact text.
- **Agent messages:** Every `content` field for `role=agent` transcript events is
  the exact message text.
- **Operator messages:** Every `content` field for `role=operator` transcript events
  is the exact message text.

The adapter maintains a running `ConversationLedger` — an append-only log of every
message added to the conversation, with its exact token count computed at insertion
time using the model-appropriate local tokenizer:

- **OpenAI/Copilot models:** `tiktoken` — the exact BPE tokenizer the API uses,
  available as a local Python library. Zero network calls. Exact match with
  API-reported token counts. Does **not** support Claude model names —
  `encoding_for_model('claude-*')` raises `KeyError` (verified empirically).

- **Claude models:** `anthropic-tokenizer` — a Rust-compiled local BPE tokenizer
  with a 65,000-token vocabulary matching Claude's actual tokenization. Available
  on PyPI as a native wheel (~2.5 MB). Properties verified empirically:
  - Fully local: no network calls, no API key required
  - Synchronous and fast: native Rust binary extension
  - Lossless roundtrip: `decode(encode(text)) == text` for all tested inputs
  - Internally consistent: `count_tokens(text) == len(encode(text))`

  Note: Anthropic also provides a remote `POST /v1/messages/count_tokens` endpoint,
  but it (1) is rate-limited, (2) returns self-described "estimates" due to system
  optimization tokens, and (3) requires an API key and network access. The local
  tokenizer eliminates all three concerns. The `anthropic-tokenizer` package itself
  ships an `ApiTokenizer` wrapper that tries the API first and falls back to local —
  we use the local path directly to avoid any network dependency.

**All token counting is local and synchronous.** Both `tiktoken` and
`anthropic-tokenizer` are local BPE tokenizers. The `ConversationLedger` accepts
pre-computed token counts, keeping it a pure data structure. The adapter computes
counts via `make_counter(model)` which routes to the correct local tokenizer based
on the model name (see §9.2 for implementation).

**Copilot SDK per-turn validation:** The Copilot SDK emits `assistant.usage` events
per LLM turn with exact `input_tokens` and `output_tokens`. The ledger's sum for
that turn can be validated against this ground truth on every turn.

**Claude SDK limitation:** The Claude Agent SDK's `ResultMessage` provides only
session-total token counts, not per-turn. The ledger provides the only per-turn
breakdown for Claude jobs. The session total from `ResultMessage` serves as an
end-of-session reconciliation point — the ledger's cumulative input tokens should
approximate the SDK's reported total. Any discrepancy is captured by the
`overhead_tokens` field, which absorbs the difference between the local tokenizer's
sum and the SDK-reported total.

**Validation:**

- **Copilot:** `prompt_system_tokens + prompt_history_tokens +
  prompt_tool_results_tokens + prompt_user_message_tokens + prompt_overhead_tokens`
  must equal the SDK-reported `input_tokens` for every turn (validated on each
  `assistant.usage` event). Any discrepancy is logged as a structured warning.
- **Claude:** The ledger's cumulative input token sum is reconciled against
  `ResultMessage.usage.input_tokens` at session end. Per-turn breakdowns use
  the local `anthropic-tokenizer` BPE tokenizer. The `prompt_overhead_tokens`
  field absorbs any delta between the local tokenizer's sum and the
  SDK-reported total.

### 5.3 Enriched Tool Call Tracking

**Concept:** Capture structured metadata about what each tool call *does*, not just
its name and duration.

**New span attributes for tool calls:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `tool_category` | string | Normalized category (see table below) |
| `tool_target` | string | Primary target (file path, command prefix) |
| `tool_result_size` | int | Size of tool result in chars/tokens |
| `tool_is_retry` | bool | True if same tool+target was called in a recent turn |
| `tool_files_referenced` | string[] | File paths extracted from arguments |

**Tool Categories:**

| Category | Tools | Cost Signal |
|----------|-------|------------|
| `file_read` | read_file, view, cat | Context loading — drives input tokens |
| `file_write` | edit_file, create_file, write_file | Core output — expected cost |
| `file_search` | grep, glob, find, ripgrep, search | Discovery — should be cheap |
| `shell` | bash, terminal, exec | Variable — output size matters |
| `git` | git_diff, git_status, git_log | Context — can be large |
| `browser` | fetch_url, web_search | External — unpredictable size |
| `agent` | task, subagent | Delegation — nested cost |
| `other` | anything else | Uncategorized |

**Extracting tool_target:** Parse the first positional argument or the `path`/`file`
named argument from `tool_args`. For bash, extract the command name (first word). For
file operations, extract the file path.

### 5.4 Phase-Tagged Spans

**Concept:** Every span should know which execution phase it occurred in, enabling
cost breakdown by lifecycle phase.

The `ExecutionPhaseChanged` event is already defined and emitted — but **only for
the verification phase** (published in `runtime_service.py:1295` with
`{"phase": "verification"}`). To enable full lifecycle cost attribution, we need to:

1. Emit `ExecutionPhaseChanged` for **all** phase transitions: `environment_setup` →
   `agent_reasoning` → `verification` → `finalization` → `post_completion`.
   Specifically, add emissions in `RuntimeService` at: workspace preparation
   complete (`environment_setup`), agent session start (`agent_reasoning`),
   verification start (already exists), finalization start (`finalization`).
2. Track `current_phase` in the adapter as state (updated on each
   `ExecutionPhaseChanged` event).
3. Stamp each LLM and tool span with the current phase.

**New span attribute:**

| Attribute | Type | Values |
|-----------|------|--------|
| `execution_phase` | string | `environment_setup`, `agent_reasoning`, `verification`, `finalization`, `post_completion` |

> **Note:** `post_completion` occurs after the agent session ends (operator review,
> merge decisions). It typically has zero LLM/tool cost but is included for
> completeness — any operator-triggered follow-up actions are tagged here.

### 5.5 Context Efficiency Tracking

**Concept:** Track exactly how the context window is being used — which files are
re-read, and what is the precise token cost of post-compaction re-reads.

**New metrics:**

| Instrument | Type | Unit | Description |
|------------|------|------|-------------|
| `cp.files.read` | Counter | — | File read operations (with `file_path` attr) |
| `cp.files.read.repeat` | Counter | — | File re-reads (same file, same job) |
| `cp.files.written` | Counter | — | File write operations |
| `cp.context.compaction.reread_tokens` | Counter | tokens | Exact tokens in re-read tool results after compaction |

**Implementation:** Maintain a per-job `Dict[file_path, ReadRecord]` where
`ReadRecord` tracks `{token_count: int, pre_compaction: bool}`. When a file-read
tool call occurs:

1. Parse the file path from `tool_args` (exact JSON, already captured).
2. Compute exact token count from `tool_result` text using the model tokenizer.
3. If `file_path` already exists in the dict → this is a re-read. Increment
   `cp.files.read.repeat` and record the exact `tool_result` token count.
4. If `file_path` exists AND was read before the most recent compaction → this is a
   post-compaction re-read. Add the exact token count to
   `cp.context.compaction.reread_tokens`.

After a compaction event, mark all entries in the dict as `pre_compaction = True`.
This is a deterministic set operation — no thresholds, no windows.

### 5.6 Retry & Rework Detection

**Concept:** Detect when the agent is retrying a failed operation or re-doing work
it already completed. All detection is deterministic — based on exact data matches,
not proximity windows or thresholds.

**Definitions (precise, no ambiguity):**

1. **Tool retry:** A tool call where `(tool_name, tool_target)` matches a prior tool
   call in the same job AND the prior call had `tool_success = false`. This is a
   factual relationship: the agent attempted the same operation, it failed, and it
   tried again. The data proves it.

2. **Edit rework:** An `edit_file` (or equivalent write tool) targeting a `file_path`
   that was already the target of a prior write tool in the same job. The file access
   log (§6.3) makes this a simple query: `COUNT(*) > 1 WHERE access_type = 'write'
   AND file_path = X`. No windows, no thresholds — just a count.

3. **Consecutive LLM turns without tool calls:** Count the number of sequential LLM
   spans with zero interleaved tool spans. This is a factual sequence count derived
   from the ordered span table, not a detection — it's reported as a raw metric
   (`max_consecutive_llm_turns`, `total_toolless_llm_turns`) and the operator decides
   what it means for their workflow.

4. **Plan changes:** Count the number of `AgentPlanUpdated` events where the step
   list differs from the previous plan event. Diff the two plan step arrays; report
   `plan_steps_added`, `plan_steps_removed`, `plan_steps_reordered` as exact counts.

**New span attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `is_retry` | bool | True iff same (tool_name, tool_target) failed earlier in this job |
| `prior_failure_span_id` | int | ID of the failed span this retries (NULL if not a retry) |

**New summary metrics:**

| Metric | Description |
|--------|-------------|
| `retry_count` | Total tool calls where `is_retry = true` |
| `retry_cost_usd` | Sum of LLM turn costs for turns containing retry tool calls |
| `edit_rework_count` | Number of files written to more than once |
| `max_consecutive_llm_turns` | Longest streak of LLM calls with no tool calls between them |
| `plan_revision_count` | Number of AgentPlanUpdated events with differing step lists |

---

## 6. Design: Schema Extensions

### 6.1 Extended `job_telemetry_spans`

Add columns to the existing span table to support enriched attributes without
breaking the current JSON-attrs approach:

```sql
ALTER TABLE job_telemetry_spans ADD COLUMN turn_number INTEGER;
ALTER TABLE job_telemetry_spans ADD COLUMN execution_phase TEXT;
ALTER TABLE job_telemetry_spans ADD COLUMN tool_category TEXT;
ALTER TABLE job_telemetry_spans ADD COLUMN tool_target TEXT;
ALTER TABLE job_telemetry_spans ADD COLUMN result_size INTEGER;
ALTER TABLE job_telemetry_spans ADD COLUMN is_retry BOOLEAN DEFAULT FALSE;
ALTER TABLE job_telemetry_spans ADD COLUMN prior_failure_span_id INTEGER;
```

**Rationale:** These are the most-queried attributes for cross-job analytics. Keeping
them as top-level columns (rather than buried in `attrs_json`) enables efficient SQL
aggregation without JSON extraction.

**Indexes:**

```sql
CREATE INDEX idx_spans_phase ON job_telemetry_spans(execution_phase);
CREATE INDEX idx_spans_tool_category ON job_telemetry_spans(tool_category);
CREATE INDEX idx_spans_turn ON job_telemetry_spans(job_id, turn_number);
```

### 6.2 New Table: `job_cost_attribution`

Materialized per-job cost breakdown by dimension. Computed after job completion (or
periodically during long jobs).

```sql
CREATE TABLE job_cost_attribution (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          TEXT NOT NULL REFERENCES jobs(id),
    
    -- Phase breakdown (USD)
    phase_setup_cost        REAL DEFAULT 0,
    phase_reasoning_cost    REAL DEFAULT 0,
    phase_verification_cost REAL DEFAULT 0,
    phase_finalization_cost REAL DEFAULT 0,
    
    -- Prompt composition breakdown (exact token counts, averaged across turns)
    avg_system_prompt_tokens    INTEGER DEFAULT 0,
    avg_history_tokens          INTEGER DEFAULT 0,
    avg_tool_result_tokens      INTEGER DEFAULT 0,
    avg_file_content_tokens     INTEGER DEFAULT 0,
    avg_overhead_tokens         INTEGER DEFAULT 0,
    
    -- Tool category breakdown (USD)
    cost_file_read          REAL DEFAULT 0,
    cost_file_write         REAL DEFAULT 0,
    cost_file_search        REAL DEFAULT 0,
    cost_shell              REAL DEFAULT 0,
    cost_agent_delegation   REAL DEFAULT 0,
    cost_other_tools        REAL DEFAULT 0,
    
    -- Efficiency metrics
    unique_files_read       INTEGER DEFAULT 0,
    repeated_file_reads     INTEGER DEFAULT 0,
    retry_count             INTEGER DEFAULT 0,
    retry_cost_usd          REAL DEFAULT 0,
    edit_rework_count       INTEGER DEFAULT 0,
    max_consecutive_llm_turns INTEGER DEFAULT 0,
    plan_revision_count     INTEGER DEFAULT 0,
    compaction_reread_tokens INTEGER DEFAULT 0,
    
    -- Output efficiency
    diff_lines_added        INTEGER DEFAULT 0,
    diff_lines_removed      INTEGER DEFAULT 0,
    cost_per_diff_line      REAL DEFAULT 0,
    
    -- Task characteristics (exact counts extracted at job start)
    prompt_token_count      INTEGER DEFAULT 0,
    prompt_word_count       INTEGER DEFAULT 0,
    files_touched           INTEGER DEFAULT 0,
    
    -- Subagent cost
    subagent_cost_usd       REAL DEFAULT 0,
    subagent_fraction       REAL DEFAULT 0,
    
    -- Turn economics
    total_turns             INTEGER DEFAULT 0,
    avg_cost_per_turn       REAL DEFAULT 0,
    cost_first_half_turns   REAL DEFAULT 0,   -- cost of turns 1..N/2
    cost_second_half_turns  REAL DEFAULT 0,   -- cost of turns N/2+1..N
    avg_input_tokens_per_turn INTEGER DEFAULT 0,
    peak_turn_cost          REAL DEFAULT 0,   -- single most expensive turn
    peak_turn_number        INTEGER DEFAULT 0, -- which turn was most expensive
    
    computed_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE(job_id)
);
```

### 6.3 New Table: `job_file_access_log`

Tracks file access patterns per job for context efficiency analysis.

```sql
CREATE TABLE job_file_access_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL REFERENCES jobs(id),
    file_path   TEXT NOT NULL,
    access_type TEXT NOT NULL,   -- 'read', 'write', 'search'
    turn_number INTEGER,
    span_id     INTEGER REFERENCES job_telemetry_spans(id),
    result_size INTEGER,         -- chars returned
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_file_access_job ON job_file_access_log(job_id);
CREATE INDEX idx_file_access_path ON job_file_access_log(job_id, file_path);
```

### 6.4 Extended `job_telemetry_summary`

Add derived metrics to the existing summary table:

```sql
ALTER TABLE job_telemetry_summary ADD COLUMN retry_count INTEGER DEFAULT 0;
ALTER TABLE job_telemetry_summary ADD COLUMN retry_cost_usd REAL DEFAULT 0;
ALTER TABLE job_telemetry_summary ADD COLUMN edit_rework_count INTEGER DEFAULT 0;
ALTER TABLE job_telemetry_summary ADD COLUMN max_consecutive_llm_turns INTEGER DEFAULT 0;
ALTER TABLE job_telemetry_summary ADD COLUMN unique_files_read INTEGER DEFAULT 0;
ALTER TABLE job_telemetry_summary ADD COLUMN repeated_file_reads INTEGER DEFAULT 0;
ALTER TABLE job_telemetry_summary ADD COLUMN subagent_cost_usd REAL DEFAULT 0;
ALTER TABLE job_telemetry_summary ADD COLUMN diff_lines_added INTEGER DEFAULT 0;
ALTER TABLE job_telemetry_summary ADD COLUMN diff_lines_removed INTEGER DEFAULT 0;
ALTER TABLE job_telemetry_summary ADD COLUMN total_turns INTEGER DEFAULT 0;
```

---

## 7. Design: Derived Metrics & Analytics Engine

### 7.1 Post-Job Attribution Pipeline

After a job completes (or on-demand for running jobs), run a computation pipeline
that reads raw spans and produces the `job_cost_attribution` row:

```
job completes
    │
    ▼
┌─────────────────────────────────────┐
│  Attribution Pipeline               │
│                                     │
│  1. Load all spans for job          │
│  2. Group LLM spans by phase        │
│     → phase cost breakdown          │
│  3. For each tool span, find the    │
│     "downstream" LLM span           │
│     (next LLM call after this tool) │
│     → tool-attributed cost          │
│  4. Compute file access patterns    │
│     from file_access_log            │
│     → unique reads, repeats         │
│  5. Detect retries from tool spans  │
│     → retry count, retry cost      │
│  6. Extract diff stats from         │
│     diff_snapshots table            │
│     → lines added/removed           │
│  7. Sum prompt composition from     │
│     per-turn span attrs             │
│     → avg system/history/tool sizes │
│  8. Upsert into                     │
│     job_cost_attribution            │
└─────────────────────────────────────┘
```

### 7.2 Tool-Attributed Cost Model

The key insight: a tool call's **true cost** is not just its own duration — it's the
cost of the LLM turn that processes its result. A `bash` command that outputs 50,000
characters doesn't cost much to _execute_, but the LLM call that _reads_ that output
may consume thousands of input tokens.

**Attribution formula:**

```
tool_attributed_cost(tool_span) =
    next_llm_span.cost × (tool_span.result_size / total_tool_results_in_turn)
```

This proportionally attributes the LLM call's cost to each tool call in that turn
based on result size.

### 7.3 Cross-Job Aggregation Queries

New analytics endpoints that aggregate across jobs:

**Cost Drivers (what matters most):**

```sql
-- Top cost drivers by tool category across all jobs in period.
-- attributed_cost is computed by the post-job attribution pipeline (§7.1)
-- and stored in job_cost_attribution; we join through tool spans for grouping.
SELECT
    sp.tool_category,
    COUNT(*) as call_count,
    SUM(CASE WHEN sp.is_retry THEN 1 ELSE 0 END) as retry_count,
    AVG(sp.result_size) as avg_result_size,
    -- Phase-level cost from attribution table, apportioned by tool category
    SUM(a.cost_file_read + a.cost_file_write + a.cost_file_search
        + a.cost_shell + a.cost_agent_delegation + a.cost_other_tools)
        / NULLIF(COUNT(DISTINCT sp.job_id), 0) as avg_job_tool_cost
FROM job_telemetry_spans sp
JOIN job_cost_attribution a ON sp.job_id = a.job_id
WHERE sp.span_type = 'tool'
  AND sp.created_at > datetime('now', '-30 days')
GROUP BY sp.tool_category
ORDER BY avg_job_tool_cost DESC;
```

> **Note on tool-attributed cost:** Per-tool-call attributed cost (§7.2) is computed
> in the attribution pipeline and stored in `job_cost_attribution` as category-level
> aggregates (`cost_file_read`, `cost_shell`, etc.), not as a per-span column. For
> per-span drill-downs, query `job_telemetry_spans.attrs_json` which contains the
> raw `result_size` for proportional attribution at query time.

**Context Efficiency (are we wasting reads):**

```sql
-- File re-read rate across jobs
SELECT
    s.job_id,
    s.total_cost_usd,
    COUNT(DISTINCT f.file_path) as unique_files,
    COUNT(*) as total_reads,
    ROUND(1.0 * COUNT(*) / COUNT(DISTINCT f.file_path), 2) as reads_per_file,
    SUM(CASE WHEN f.access_type = 'read' AND f.turn_number > first_read.min_turn
        THEN 1 ELSE 0 END) as re_reads
FROM job_telemetry_summary s
JOIN job_file_access_log f ON s.job_id = f.job_id
JOIN (
    SELECT job_id, file_path, MIN(turn_number) as min_turn
    FROM job_file_access_log
    GROUP BY job_id, file_path
) first_read ON f.job_id = first_read.job_id AND f.file_path = first_read.file_path
WHERE s.completed_at > datetime('now', '-30 days')
GROUP BY s.job_id
ORDER BY re_reads DESC;
```

**Phase Cost Distribution:**

```sql
-- Average cost distribution by phase
SELECT
    execution_phase,
    COUNT(DISTINCT job_id) as jobs,
    AVG(phase_cost) as avg_phase_cost,
    AVG(phase_fraction) as avg_phase_fraction
FROM (
    SELECT
        job_id,
        execution_phase,
        SUM(CAST(json_extract(attrs_json, '$.cost') AS REAL)) as phase_cost,
        SUM(CAST(json_extract(attrs_json, '$.cost') AS REAL)) * 1.0 /
            NULLIF(s.total_cost_usd, 0) as phase_fraction
    FROM job_telemetry_spans sp
    JOIN job_telemetry_summary s ON sp.job_id = s.job_id
    WHERE sp.span_type = 'llm'
    GROUP BY sp.job_id, sp.execution_phase
)
GROUP BY execution_phase;
```

**Waste Detection:**

```sql
-- Jobs with highest retry waste
SELECT
    s.job_id,
    s.total_cost_usd,
    a.retry_count,
    a.retry_cost_usd,
    ROUND(a.retry_cost_usd / NULLIF(s.total_cost_usd, 0) * 100, 1)
        as retry_pct,
    a.max_consecutive_llm_turns,
    a.compaction_reread_tokens
FROM job_telemetry_summary s
JOIN job_cost_attribution a ON s.job_id = a.job_id
WHERE s.completed_at > datetime('now', '-30 days')
ORDER BY retry_pct DESC
LIMIT 20;
```

**Task Complexity vs. Cost:**

```sql
-- Prompt complexity vs. actual cost
SELECT
    a.prompt_word_count,
    a.files_touched,
    s.total_cost_usd,
    s.llm_call_count,
    CASE
        WHEN a.prompt_word_count < 20 THEN 'simple'
        WHEN a.prompt_word_count < 100 THEN 'moderate'
        ELSE 'complex'
    END as complexity_bucket,
    a.cost_per_diff_line
FROM job_cost_attribution a
JOIN job_telemetry_summary s ON a.job_id = s.job_id
WHERE s.status = 'succeeded'
ORDER BY s.total_cost_usd DESC;
```

**Turn Economics (how does cost evolve over a session):**

```sql
-- Per-turn cost curve: how does the cost of each successive turn change?
-- This is the core query for understanding context growth cost.
SELECT
    sp.turn_number,
    COUNT(DISTINCT sp.job_id) as jobs_with_this_turn,
    AVG(CAST(json_extract(sp.attrs_json, '$.cost') AS REAL)) as avg_turn_cost,
    AVG(CAST(json_extract(sp.attrs_json, '$.input_tokens') AS INT)) as avg_input_tokens,
    AVG(CAST(json_extract(sp.attrs_json, '$.output_tokens') AS INT)) as avg_output_tokens
FROM job_telemetry_spans sp
WHERE sp.span_type = 'llm'
  AND sp.turn_number IS NOT NULL
  AND sp.created_at > datetime('now', '-30 days')
GROUP BY sp.turn_number
ORDER BY sp.turn_number;

-- Turn count as cost predictor: does more turns = proportionally more cost,
-- or is there an inflection point where cost accelerates?
SELECT
    a.total_turns,
    COUNT(*) as job_count,
    AVG(s.total_cost_usd) as avg_cost,
    AVG(s.total_cost_usd) / NULLIF(a.total_turns, 0) as avg_cost_per_turn,
    AVG(a.cost_second_half_turns / NULLIF(a.cost_first_half_turns, 0))
        as avg_second_half_cost_ratio
FROM job_cost_attribution a
JOIN job_telemetry_summary s ON a.job_id = s.job_id
WHERE s.status IN ('succeeded', 'failed')
  AND s.completed_at > datetime('now', '-30 days')
GROUP BY a.total_turns
ORDER BY a.total_turns;

-- Jobs where later turns got disproportionately expensive
-- (second-half cost > 2× first-half cost, suggesting context bloat)
SELECT
    s.job_id,
    s.total_cost_usd,
    a.total_turns,
    a.cost_first_half_turns,
    a.cost_second_half_turns,
    ROUND(a.cost_second_half_turns / NULLIF(a.cost_first_half_turns, 0), 2)
        as cost_acceleration_ratio,
    a.compaction_reread_tokens
FROM job_cost_attribution a
JOIN job_telemetry_summary s ON a.job_id = s.job_id
WHERE a.cost_second_half_turns > 2.0 * a.cost_first_half_turns
  AND a.total_turns >= 4
  AND s.completed_at > datetime('now', '-30 days')
ORDER BY cost_acceleration_ratio DESC;
```

---

## 8. Design: Dashboard Views

### 8.1 Cost Drivers Overview (New Top-Level View)

**Purpose:** At a glance, understand _what_ is driving cost across all jobs.

```
┌─────────────────────────────────────────────────────────────────────┐
│  COST DRIVERS  (last 30 days)                        Period: [30d] │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌──────────────────────────┐  ┌──────────────────────────────────┐ │
│  │  COST BY LIFECYCLE PHASE │  │  COST BY TOOL CATEGORY           │ │
│  │  ┌───────────────────┐   │  │  ┌────────────────────────────┐  │ │
│  │  │   [Donut Chart]   │   │  │  │   [Horizontal Bar Chart]   │  │ │
│  │  │  Setup: 3%        │   │  │  │  file_read:  ████████ 35%  │  │ │
│  │  │  Reasoning: 72%   │   │  │  │  shell:      ██████  27%   │  │ │
│  │  │  Verification: 20%│   │  │  │  file_write:  ████  18%    │  │ │
│  │  │  Finalization: 5% │   │  │  │  file_search: ██   9%      │  │ │
│  │  └───────────────────┘   │  │  │  agent:       █    6%      │  │ │
│  └──────────────────────────┘  │  │  other:            5%      │  │ │
│                                 │  └────────────────────────────┘  │ │
│  ┌──────────────────────────┐  └──────────────────────────────────┘ │
│  │  PROMPT COMPOSITION      │                                       │
│  │  (avg across all jobs)   │  ┌──────────────────────────────────┐ │
│  │  ┌───────────────────┐   │  │  WASTE & EFFICIENCY              │ │
│  │  │ [Stacked Bar]     │   │  │                                  │ │
│  │  │ System: 15%       │   │  │  Retry rate:        12%          │ │
│  │  │ History: 40%      │   │  │  Avg retry cost:    $0.32/job   │ │
│  │  │ Tool results: 30% │   │  │  Re-read rate:      2.3x/file   │ │
│  │  │ File contents: 12%│   │  │  Compaction re-reads: 847 tok   │ │
│  │  │ User message: 3%  │   │  │  Max LLM streak:    3.1 avg     │ │
│  │  └───────────────────┘   │  │  Output efficiency: $0.45/LOC   │ │
│  └──────────────────────────┘  └──────────────────────────────────┘ │
│                                                                     │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │  COST TREND (daily, by tool category)                          │ │
│  │  ┌──────────────────────────────────────────────────────────┐  │ │
│  │  │  [Stacked Area Chart]                                    │  │ │
│  │  │  $2.50 ┤                          ╱─╲                    │  │ │
│  │  │        │          ╱─────────────╱   ╲──╲                 │  │ │
│  │  │  $1.50 ┤    ╱────╱                      ╲───            │  │ │
│  │  │        │───╱                                             │  │ │
│  │  │  $0.50 ┤                                                 │  │ │
│  │  │        ├───┬───┬───┬───┬───┬───┬───┬───┬───┬───┬───┬──  │  │ │
│  │  │         M   T   W   T   F   S   S   M   T   W   T   F   │  │ │
│  │  └──────────────────────────────────────────────────────────┘  │ │
│  │  Legend: ▓ file_read  ░ shell  ▒ file_write  ◽ search  ◾ other │ │
│  └────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```

### 8.2 Tool Deep-Dive Panel (Enhanced Existing)

Extends the current tool performance table with cost attribution:

```
┌────────────────────────────────────────────────────────────────────┐
│  TOOL COST ATTRIBUTION  (last 30 days)                             │
├──────────┬───────┬─────────┬──────────┬──────────┬────────┬───────┤
│ Tool     │ Calls │ Retries │ Avg Rslt │ Attr.    │ Cost/  │ Succ  │
│          │       │         │ Size     │ Cost     │ Call   │ Rate  │
├──────────┼───────┼─────────┼──────────┼──────────┼────────┼───────┤
│ bash     │  847  │   92    │ 2,340ch  │ $12.40   │ $0.015 │  89%  │
│ read_file│  623  │   18    │ 4,120ch  │ $10.80   │ $0.017 │  97%  │
│ edit_file│  412  │   67    │   890ch  │  $8.20   │ $0.020 │  84%  │
│ grep     │  389  │    5    │   560ch  │  $3.10   │ $0.008 │  99%  │
│ glob     │  201  │    2    │   340ch  │  $1.20   │ $0.006 │  99%  │
│ task     │   45  │    0    │     —    │  $4.50   │ $0.100 │  91%  │
├──────────┴───────┴─────────┴──────────┴──────────┴────────┴───────┤
│ ▶ Click any row to see: top arguments, retry patterns, cost dist  │
└────────────────────────────────────────────────────────────────────┘
```

**Drill-down per tool** shows:
- Most common arguments (e.g., top 10 bash commands, top 10 file paths)
- Cost distribution histogram
- Retry chains (tool → fail → re-attempt sequences)
- Average downstream LLM cost per invocation

### 8.3 Job Cost Anatomy (Per-Job Deep Dive)

Replaces/extends the current MetricsPanel with a cost-attribution view:

```
┌────────────────────────────────────────────────────────────────────┐
│  JOB COST ANATOMY — job abc-123                     Total: $1.24  │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│  TIMELINE (cost over time, colored by phase)                       │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ $0.04│  ╱╲     setup │reasoning─────────────│verify│done    │  │
│  │      │ ╱  ╲   ╱╲  ╱╲╱╲   ╱╲                │ ╱╲   │        │  │
│  │ $0.02│╱    ╲─╱  ╲╱    ╲─╱  ╲──────────────╲│╱  ╲──│        │  │
│  │      ├──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──      │  │
│  │       0    2    4    6    8   10   12   14   16   18 (min)   │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                    │
│  ┌─────────────────┐ ┌─────────────────┐ ┌──────────────────────┐ │
│  │ PHASE BREAKDOWN  │ │ TOOL COST       │ │ PROMPT COMPOSITION   │ │
│  │ Setup:    $0.04  │ │ bash:    $0.38  │ │ System:  12% ████   │ │
│  │ Reason:   $0.89  │ │ read:    $0.31  │ │ History: 44% █████  │ │
│  │ Verify:   $0.25  │ │ edit:    $0.24  │ │ Tools:   28% █████  │ │
│  │ Final:    $0.06  │ │ grep:    $0.09  │ │ Files:   13% ████   │ │
│  │                  │ │ task:    $0.12  │ │ User:     3% █      │ │
│  └─────────────────┘ │ other:   $0.10  │ └──────────────────────┘ │
│                       └─────────────────┘                          │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  EFFICIENCY ANALYSIS                                        │   │
│  │  Retries: 4 (same tool+target after failure, $0.18 cost)   │   │
│  │  Re-reads: 3 files read 2+ times (1,240 redundant tokens)  │   │
│  │  Compactions: 1 → 2 files re-read after (847 tokens)       │   │
│  │  Max consecutive LLM turns (no tools): 2                    │   │
│  │                                                             │   │
│  │  Output: 45 lines of diff at $0.028/line                   │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                    │
│  TURN ECONOMICS (cost per turn over session lifetime)              │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ $0.08│            ●                                          │  │
│  │      │        ●       ● ●                                    │  │
│  │ $0.04│  ● ●                 ● ●   ●                          │  │
│  │      │●                           ● ●   ●                    │  │
│  │ $0.02│                                                       │  │
│  │      ├──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──                │  │
│  │       1  2  3  4  5  6  7  8  9 10 11 12 13 (turn)          │  │
│  └──────────────────────────────────────────────────────────────┘  │
│  Turns: 13 │ Avg $/turn: $0.095 │ Peak: turn 5 ($0.08)           │
│  1st half: $0.52 │ 2nd half: $0.72 │ Acceleration: 1.4×           │
└────────────────────────────────────────────────────────────────────┘
```

### 8.4 Fleet Intelligence (Cross-Job Patterns)

**Purpose:** Surface patterns that only become visible across dozens/hundreds of jobs.

```
┌────────────────────────────────────────────────────────────────────┐
│  FLEET INTELLIGENCE  (last 90 days, 156 jobs)                      │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│  OBSERVATIONS (data-derived, with sample sizes)                    │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ ⚠ Retry cost is 23% of total spend (n=156 jobs)             │  │
│  │ ⚠ Pearson r=0.84 between bash result_size and next-turn     │  │
│  │   input_tokens (n=847 tool calls, p<0.001)                  │  │
│  │ ⚠ Cost accelerates after turn 8: avg $/turn is 2.1× higher  │  │
│  │   in turns 9+ vs turns 1-8 (n=94 jobs with 9+ turns)       │  │
│  │ ✓ Cache ratio trend: +0.8%/day over 30 days (15% → 38%)    │  │
│  │ ⚠ Repo "backend-api" mean cost is 2.1σ above fleet mean    │  │
│  │ ✓ Subagent $/LOC is 40% lower than main-agent (n=45 jobs)  │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                    │
│  COST PREDICTORS (correlation with total job cost)                 │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ Prompt word count         ●──────────────── r=0.42           │  │
│  │ Files touched             ●─────────────────── r=0.58        │  │
│  │ Turn count                ●───────────────────────── r=0.76  │  │
│  │ Tool call count           ●──────────────────────── r=0.73   │  │
│  │ Compaction count          ●────────────────────── r=0.67     │  │
│  │ Retry count               ●───────────────── r=0.51          │  │
│  │ Context util. (avg)       ●──────────── r=0.35               │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                    │
│  COST DISTRIBUTION                                                 │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  [Histogram: jobs by cost bucket]                            │  │
│  │  $0-0.50:  ████████████████████████████████  62 jobs         │  │
│  │  $0.50-1:  ███████████████████               38 jobs         │  │
│  │  $1-2:     █████████████                     26 jobs         │  │
│  │  $2-5:     ████████                          16 jobs         │  │
│  │  $5-10:    ████                               8 jobs         │  │
│  │  $10+:     ███                                6 jobs (→ drill down) │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                    │
│  MODEL EFFICIENCY COMPARISON                                       │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ Model              │ $/1K out │ Cache% │ $/LOC │ Success │   │  │
│  │ claude-sonnet-4    │  $0.048  │  42%   │ $0.03 │   91%   │   │  │
│  │ gpt-4o             │  $0.030  │  28%   │ $0.04 │   87%   │   │  │
│  │ claude-opus-4      │  $0.120  │  45%   │ $0.02 │   96%   │   │  │
│  │ gpt-4o-mini        │  $0.003  │  15%   │ $0.01 │   72%   │   │  │
│  └──────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────┘
```

### 8.5 Statistical Analysis Engine (Automated Observations)

**Concept:** Compute statistical observations from the accumulated data after each
job and across the fleet. All insights are derived from the data itself — percentile
rankings, standard deviations, correlation coefficients — never from hardcoded
thresholds.

**Job-level observations (computed from fleet distribution):**
- "This job's re-read token cost ($0.18) is in the 94th percentile across all jobs"
- "4 edit_file retries on `src/auth.ts` — this file has a retry rate of 67% across
  all jobs that touched it"
- "Verification phase consumed 3.2σ above the mean cost fraction for this model"

**Fleet-level observations (computed from correlation analysis):**
- "Correlation: tool_result_size and next-turn input_tokens — r=0.89, p<0.001"
- "Distribution: 73% of retry cost concentrates in the top 12% of jobs"
- "Trend: cache_read_tokens / input_tokens ratio declined from 0.38 to 0.22 over
  the last 30 days (linear regression slope: -0.005/day)"

**Implementation:** Each observation is a SQL aggregation that computes a statistical
measure against the fleet dataset. No fixed thresholds — the data defines what's
normal and what's an outlier.

Observation types:

| Type | Method | Example |
|------|--------|---------|
| **Percentile rank** | `PERCENT_RANK() OVER (ORDER BY metric)` | "This job's cost is in the Nth percentile" |
| **Z-score** | `(value - AVG(value)) / STDDEV(value)` | "Verification cost is Nσ above mean" |
| **Correlation** | Pearson's r over (metric_a, metric_b) pairs | "tool_result_size ↔ turn_cost: r=0.89" |
| **Trend** | Linear regression slope over time-ordered data | "Cache hit rate declining at -0.5%/day" |
| **Concentration** | Lorenz curve / Gini coefficient | "73% of retry cost in top 12% of jobs" |

```sql
CREATE TABLE observations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scope           TEXT NOT NULL,      -- 'job' or 'fleet'
    job_id          TEXT,               -- NULL for fleet observations
    category        TEXT NOT NULL,      -- 'distribution', 'correlation', 'trend', 'concentration'
    metric_key      TEXT NOT NULL,      -- e.g. 'retry_cost_usd', 'cache_hit_rate'
    stat_type       TEXT NOT NULL,      -- 'percentile', 'zscore', 'pearson_r', 'slope', 'gini'
    stat_value      REAL NOT NULL,      -- the computed statistic
    comparison_value REAL,              -- e.g. fleet mean for z-score context
    sample_size     INTEGER NOT NULL,   -- number of data points used
    message         TEXT NOT NULL,      -- human-readable description
    computed_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Key principle:** Observations with `sample_size` below a meaningful threshold are
still computed and stored, but flagged in the UI as low-confidence. The threshold
isn't hardcoded — it's the sample size itself, displayed transparently so the
operator can judge significance.

---

## 9. Implementation Approach

### 9.1 Phased Rollout

#### Phase 1: Enrich Span Data (Low Risk, High Value)

**Scope:** Add `turn_number`, `execution_phase`, `tool_category`, `tool_target`,
`result_size`, and `is_retry` to span collection. Extend the DB schema. No frontend
changes yet.

**Changes:**
1. Alembic migration (`alembic/versions/0008_cost_analytics_spans.py`): add columns
   to `job_telemetry_spans`, create `job_file_access_log` table.
2. `backend/services/runtime_service.py`: Activate dead OTel instruments — wire
   `approvals_counter.add()` into approval flow and `approval_wait.record()` into
   `ApprovalResolved` handling. (The instruments are defined in `telemetry.py` and
   don't need changes — only the call sites in `runtime_service.py` are new.)
3. `backend/persistence/telemetry_spans_repo.py` (`insert()` method, line 22):
   Accept and store new columns (`turn_number`, `execution_phase`, `tool_category`,
   `tool_target`, `result_size`, `is_retry`, `prior_failure_span_id`).
4. `backend/services/copilot_adapter.py`:
   - `_handle_usage_event()` (line 325): Track `turn_number` as adapter state,
     increment on each usage event. Pass to span insert.
   - `_handle_tool_start()` (line 454): Extract `tool_category` via
     `classify_tool(tool_name)`, extract `tool_target` from `data.arguments`.
   - `_handle_tool_end()` (line 487): Compute `result_size` from tool result text
     (already available in adapter pipeline). Run `RetryTracker.record()`.
     Currently span attrs only store `{"success": bool}` — enrich with
     `tool_category`, `tool_target`, `result_size`, `is_retry`.
   - Track `current_phase` state, updated via `ExecutionPhaseChanged` events.
5. `backend/services/claude_adapter.py`:
   - `_process_tool_use_block()` (line 388): Extract tool category and target from
     `ToolUseBlock.input` dict. Buffer for later pairing with result.
   - `_process_tool_result_block()` (line 443): Compute `result_size` from
     `ToolResultBlock.content`. Run retry detection. Enrich span attrs.
   - Track `turn_number` (increment on each `AssistantMessage`, not per content
     block — one AssistantMessage = one LLM turn, which may contain multiple
     TextBlock/ToolUseBlock content blocks).
   - Track `current_phase` state.
6. `backend/services/runtime_service.py`: Emit `ExecutionPhaseChanged` for all
   phase transitions, not just verification. Add emissions at:
   - Line ~520 (after workspace prep): `{"phase": "environment_setup"}`
   - Line ~545 (agent session start): `{"phase": "agent_reasoning"}`
   - Line ~1295 (existing): `{"phase": "verification"}`
   - Line ~741 (finalization): `{"phase": "finalization"}`
7. New file: `backend/persistence/file_access_repo.py` — repository for
   `job_file_access_log` table.

**Validation:** Run jobs, verify spans have enriched attributes. Compare old vs. new
query patterns. Confirm tool spans now contain `tool_category`, `tool_target`,
`result_size` instead of just `{"success": bool}`.

#### Phase 2: Post-Job Attribution Pipeline + Prompt Composition

**Scope:** Build the `job_cost_attribution` computation, `ConversationLedger` for
prompt composition breakdown, and new analytics API endpoints.

**Dependencies to add** (via `uv add`):
- `tiktoken` — local BPE tokenizer for OpenAI/Copilot models (~2 MB wheel)
- `anthropic-tokenizer` — local Rust-compiled BPE tokenizer for Claude models
  (~2.5 MB wheel)

**Changes:**
1. New service: `backend/services/cost_attribution.py` — reads spans + file access
   log, computes attribution, writes to `job_cost_attribution`.
2. New module: `backend/services/conversation_ledger.py` — `ConversationLedger`
   class (§9.2) + `TiktokenCounter` / `ClaudeTokenCounter` + `make_counter()`.
3. Integrate `ConversationLedger` into adapters:
   - `copilot_adapter.py`: instantiate ledger at session start, call
     `record_message()` on each transcript event, call `composition_at_turn()`
     on each `assistant.usage` event (which provides per-turn `input_tokens`
     ground truth for validation).
   - `claude_adapter.py`: instantiate ledger at session start, call
     `record_message()` on each content block. Use `ResultMessage.usage` for
     end-of-session reconciliation (the only per-turn breakdown for Claude comes
     from the local tokenizer).
4. Hook into `RuntimeService._finalize_job()` (line ~741) to trigger attribution
   after job completion.
5. New Alembic migration (`alembic/versions/0009_cost_attribution.py`): create
   `job_cost_attribution` table, extend `job_telemetry_summary` with new columns.
6. Activate `diff_snapshots` table: create a new `DiffSnapshotRepo` (or add an
   insert method to `JobRepo`, which already has
   `delete_diff_snapshots_for_jobs()` at line 361) and call it from
   `RuntimeService` at job completion (computing diff via `GitService`).
   Currently the table schema and `DiffSnapshotRow` model exist but no code
   ever inserts rows.
7. New API endpoints in `backend/api/analytics.py`:
   - `GET /analytics/cost-drivers` — aggregated cost by tool category, phase, etc.
   - `GET /analytics/waste` — retry/rework metrics across jobs.
   - `GET /analytics/efficiency` — output efficiency, cache effectiveness.
   - `GET /jobs/{id}/cost-attribution` — per-job breakdown.
8. Update `backend/models/api_schemas.py` with response models (using `CamelModel`
   base class per project convention).

**Validation:** Run jobs, verify attribution phase costs sum to exactly the total job
cost (they must — every LLM span belongs to exactly one phase). Verify prompt
composition `overhead_tokens` is a small, stable fraction. Cross-check file access
log against transcript events.

#### Phase 3: Dashboard Views

**Scope:** Build the four new dashboard views described in Section 8.

**Changes:**
1. Regenerate TypeScript types: run OpenAPI schema generation after adding new API
   endpoints. New types will appear in `frontend/src/api/schema.d.ts`. Add friendly
   aliases in `frontend/src/api/types.ts`.
2. New API client functions in `frontend/src/api/client.ts`:
   `fetchCostDrivers()`, `fetchWasteMetrics()`, `fetchEfficiency()`,
   `fetchJobCostAttribution(jobId)`, `fetchFleetIntelligence()`.
3. `frontend/src/components/CostDriversScreen.tsx` — fleet-level cost drivers view
   (Section 8.1). Charts: donut (phase breakdown), horizontal bar (tool category),
   stacked bar (prompt composition), metrics cards (waste indicators).
4. Enhanced `frontend/src/components/MetricsPanel.tsx` — per-job cost anatomy
   (Section 8.3). Add phase breakdown, tool attribution, prompt composition, and
   efficiency analysis panels. Data from `fetchJobCostAttribution(jobId)`.
5. Enhanced `frontend/src/components/AnalyticsScreen.tsx` — tool deep-dive
   (Section 8.2) + fleet intelligence (Section 8.4). Add drill-down per tool
   (top arguments, retry chains, cost distribution).
6. Route additions in `frontend/src/App.tsx` for new views.
7. Update Zustand store (`frontend/src/store/index.ts`) if needed for new
   cross-job analytics state.

#### Phase 4: Statistical Analysis Engine

**Scope:** Automated statistical observation generation and display.

**Changes:**
1. New service: `backend/services/statistical_analysis.py` — SQL-based statistical
   computations (percentile ranks, z-scores, correlations, trends).
2. New table: `observations`.
3. New API endpoint: `GET /analytics/observations`.
4. Frontend: Observation cards in dashboard and per-job views, showing sample sizes
   and statistical measures.

### 9.2 Prompt Composition via Conversation Ledger

The adapter maintains a `ConversationLedger` that records the token count of every
message added to the conversation. Token counts are computed by the caller and
passed in — the ledger is a pure data structure, not responsible for tokenization.
This keeps the ledger synchronous and testable, decoupled from the tokenizer
implementation chosen at the adapter level.

```python
class ConversationLedger:
    """Tracks token counts per conversation segment.

    Token counts are provided by the caller, computed via the
    model-appropriate tokenizer before insertion.
    """

    def __init__(self):
        self._system_prompt_tokens: int = 0
        self._messages: list[LedgerEntry] = []

    def set_system_prompt(self, token_count: int) -> None:
        """Called once at session init with the exact system prompt token count."""
        self._system_prompt_tokens = token_count

    def record_message(self, role: str, category: MessageCategory,
                       token_count: int) -> None:
        """Record a message with its pre-computed token count.

        category is one of: 'agent', 'operator', 'tool_result', 'file_content'
        — derived from the transcript event role and tool_category.
        """
        self._messages.append(LedgerEntry(
            role=role, category=category, tokens=token_count,
        ))

    def composition_at_turn(self, sdk_reported_input_tokens: int) -> PromptComposition:
        """Compute composition breakdown for the current turn.

        The SDK-reported input_tokens is the ground truth total.
        The ledger sum accounts for message content; any delta is
        SDK formatting overhead (role tags, separators, etc.).
        """
        by_category = defaultdict(int)
        for entry in self._messages:
            by_category[entry.category] += entry.tokens

        ledger_total = self._system_prompt_tokens + sum(by_category.values())
        overhead = sdk_reported_input_tokens - ledger_total

        return PromptComposition(
            system_tokens=self._system_prompt_tokens,
            history_tokens=by_category["agent"] + by_category["operator"],
            tool_result_tokens=by_category["tool_result"],
            file_content_tokens=by_category["file_content"],
            overhead_tokens=overhead,
            sdk_reported_total=sdk_reported_input_tokens,
        )
```

**Token counting strategies — all local, all synchronous:**

Both strategies use local tokenizers. No network calls. No rate limits. No API
keys needed for token counting.

```python
class TiktokenCounter:
    """For OpenAI/Copilot models. Uses the exact BPE tokenizer the API uses.

    tiktoken does NOT support Claude model names — calling
    encoding_for_model('claude-*') raises KeyError. This counter
    is only valid for gpt-*/o1-* models.
    """
    def __init__(self, model: str):
        self._enc = tiktoken.encoding_for_model(model)
    def count(self, text: str) -> int:
        return len(self._enc.encode(text))

class ClaudeTokenCounter:
    """For Claude models. Uses the anthropic-tokenizer package — a Rust-compiled
    local BPE tokenizer with a 65,000-token vocabulary that matches Claude's
    actual tokenization.

    Properties verified empirically:
    - Fully local: no network calls, no API key required
    - Synchronous and fast: native Rust, ~1M tokens/sec
    - Lossless roundtrip: decode(encode(text)) == text for all tested inputs
    - count_tokens(text) == len(encode(text)) — internally consistent

    The package also ships an ApiTokenizer class that tries the Anthropic
    count_tokens API first and falls back to local. We use the local path
    directly (anthropic_tokenizer.count_tokens) to avoid any network
    dependency and to maintain deterministic, synchronous execution.
    """
    def count(self, text: str) -> int:
        return anthropic_tokenizer.count_tokens(text)
```

**Model-to-counter routing:**

```python
def make_counter(model: str) -> TiktokenCounter | ClaudeTokenCounter:
    """Select the correct local tokenizer based on the model name.

    tiktoken.encoding_for_model() raises KeyError for unknown models.
    We use this as a definitive signal — not a guess — to route:
    - If tiktoken recognizes the model → use tiktoken (OpenAI models)
    - Otherwise, if model name contains 'claude' → use ClaudeTokenCounter
    - Otherwise → raise ValueError (unknown model, cannot tokenize)
    """
    try:
        return TiktokenCounter(model)
    except KeyError:
        pass

    if "claude" in model.lower():
        return ClaudeTokenCounter()

    raise ValueError(
        f"No local tokenizer available for model {model!r}. "
        f"Cannot compute per-segment token breakdown."
    )
```

> **Why local-only:** The Anthropic `count_tokens` API endpoint (1) is rate-limited,
> (2) returns self-described "estimates" that include system optimization tokens not
> billed to the user, and (3) requires an API key and network access. The
> `anthropic-tokenizer` package provides the same BPE vocabulary as a compiled Rust
> extension, giving us deterministic local counts with zero external dependencies.
> The `overhead_tokens` field in `PromptComposition` reconciles any difference
> between our local count and the SDK-reported total, so accuracy is fully
> accounted for rather than hidden.

### 9.3 Retry Detection via Failed-Predecessor Lookup

Retry detection is a deterministic set operation: for each tool call, check whether
a prior tool call in the same job had the same `(tool_name, tool_target)` and failed.

```python
class RetryTracker:
    """Tracks tool call outcomes per (tool_name, tool_target) pair.

    A tool call is a retry if and only if a prior call with the
    same (tool_name, tool_target) exists in this job and that prior
    call failed (tool_success = False). No windows, no thresholds.
    """

    def __init__(self):
        # Maps (tool_name, tool_target) → list of (span_id, success)
        self._history: dict[tuple[str, str], list[tuple[int, bool]]] = defaultdict(list)

    def record(self, tool_name: str, tool_target: str,
               span_id: int, success: bool) -> RetryResult:
        key = (tool_name, tool_target)
        prior_calls = self._history[key]

        # Find the most recent failed call for this exact (name, target)
        prior_failure_id = None
        for past_span_id, past_success in reversed(prior_calls):
            if not past_success:
                prior_failure_id = past_span_id
                break

        self._history[key].append((span_id, success))

        return RetryResult(
            is_retry=prior_failure_id is not None,
            prior_failure_span_id=prior_failure_id,
        )
```

This produces zero false positives: a call is only marked as a retry if there is a
concrete prior failure for the exact same operation. The `prior_failure_span_id`
creates a traceable link back to the original failure.

### 9.4 Tool Category Classification

```python
TOOL_CATEGORIES = {
    # file_read
    "read_file": "file_read", "view": "file_read", "cat": "file_read",
    "Read": "file_read", "readFile": "file_read",
    # file_write
    "edit_file": "file_write", "create_file": "file_write",
    "write_file": "file_write", "Edit": "file_write",
    "editFile": "file_write", "create": "file_write",
    # file_search
    "grep": "file_search", "glob": "file_search", "find": "file_search",
    "ripgrep": "file_search", "search": "file_search",
    "codeSearch": "file_search", "listDir": "file_search",
    # shell
    "bash": "shell", "terminal": "shell", "exec": "shell",
    "runCommand": "shell",
    # git
    "git_diff": "git", "git_status": "git", "git_log": "git",
    # browser
    "fetch_url": "browser", "web_search": "browser",
    "WebFetch": "browser",
    # agent
    "task": "agent", "subagent": "agent", "Agent": "agent",
}

def classify_tool(tool_name: str) -> str:
    return TOOL_CATEGORIES.get(tool_name, "other")
```

### 9.5 New Dependencies

Both are local BPE tokenizers with zero network requirements. Install via `uv add`:

| Package | Purpose | Size | Runtime Cost |
|---------|---------|------|-------------|
| `tiktoken` | OpenAI/Copilot model tokenization | ~2 MB wheel | ~1M tokens/sec, synchronous |
| `anthropic-tokenizer` | Claude model tokenization | ~2.5 MB Rust wheel | ~1M tokens/sec, synchronous |

Neither package is currently in `pyproject.toml`. No existing token counting code
exists in the codebase — all token data currently comes from SDK-reported values.

### 9.6 Concrete Change Map

Summary of every file that must be modified or created, organized by phase:

**Phase 1 — Enrich Span Data:**

| Action | File | What Changes |
|--------|------|-------------|
| Create | `alembic/versions/0008_cost_analytics_spans.py` | Add columns to `job_telemetry_spans`, create `job_file_access_log` |
| Create | `backend/persistence/file_access_repo.py` | New repo for `job_file_access_log` CRUD |
| Modify | `backend/persistence/telemetry_spans_repo.py` | `insert()`: accept + store new columns |
| Modify | `backend/services/copilot_adapter.py` | Track turn_number, phase, tool enrichment, retry detection |
| Modify | `backend/services/claude_adapter.py` | Same as copilot adapter |
| Modify | `backend/services/runtime_service.py` | Emit `ExecutionPhaseChanged` for all phases, wire approval metrics |
| Create | `backend/services/retry_tracker.py` | `RetryTracker` class (§9.3) |
| Create | `backend/services/tool_classifier.py` | `TOOL_CATEGORIES` dict + `classify_tool()` (§9.4) |

**Phase 2 — Attribution Pipeline + Prompt Composition:**

| Action | File | What Changes |
|--------|------|-------------|
| Create | `alembic/versions/0009_cost_attribution.py` | Create `job_cost_attribution`, extend `job_telemetry_summary` |
| Create | `backend/services/cost_attribution.py` | Attribution pipeline (§7.1) |
| Create | `backend/services/conversation_ledger.py` | `ConversationLedger` + token counters (§9.2) |
| Modify | `backend/services/copilot_adapter.py` | Integrate ConversationLedger |
| Modify | `backend/services/claude_adapter.py` | Integrate ConversationLedger |
| Modify | `backend/services/runtime_service.py` | Trigger attribution on job completion, activate diff_snapshots |
| Modify | `backend/persistence/telemetry_summary_repo.py` | Support new summary columns |
| Create | `backend/persistence/cost_attribution_repo.py` | New repo for `job_cost_attribution` |
| Modify | `backend/api/analytics.py` | New endpoints: cost-drivers, waste, efficiency |
| Modify | `backend/api/jobs.py` | New endpoint: `/jobs/{id}/cost-attribution` |
| Modify | `backend/models/api_schemas.py` | New response models |
| Modify | `pyproject.toml` | Add `tiktoken`, `anthropic-tokenizer` dependencies |

**Phase 3 — Dashboard Views:**

| Action | File | What Changes |
|--------|------|-------------|
| Create | `frontend/src/components/CostDriversScreen.tsx` | New fleet-level cost view |
| Modify | `frontend/src/components/MetricsPanel.tsx` | Per-job cost anatomy |
| Modify | `frontend/src/components/AnalyticsScreen.tsx` | Tool deep-dive + fleet intelligence |
| Modify | `frontend/src/api/client.ts` | New fetch functions |
| Modify | `frontend/src/api/types.ts` | Friendly type aliases |
| Modify | `frontend/src/App.tsx` | New route for CostDriversScreen |

**Phase 4 — Statistical Analysis:**

| Action | File | What Changes |
|--------|------|-------------|
| Create | `alembic/versions/0010_observations.py` | Create `observations` table |
| Create | `backend/services/statistical_analysis.py` | SQL-based statistical computations |
| Create | `backend/persistence/observations_repo.py` | New repo for observations |
| Modify | `backend/api/analytics.py` | New endpoint: `/analytics/observations` |
| Modify | `frontend/src/components/AnalyticsScreen.tsx` | Observation cards |
| Modify | `frontend/src/components/MetricsPanel.tsx` | Per-job observations |

---

## Appendix A: Metric Catalog

### Existing Metrics (Unchanged)

| Metric | Type | Attributes |
|--------|------|------------|
| `cp.tokens.input` | Counter | job_id, sdk, model |
| `cp.tokens.output` | Counter | job_id, sdk, model |
| `cp.tokens.cache_read` | Counter | job_id, sdk, model |
| `cp.tokens.cache_write` | Counter | job_id, sdk, model |
| `cp.cost` | Counter | job_id, sdk, model |
| `cp.compactions` | Counter | job_id, sdk |
| `cp.tokens.compacted` | Counter | job_id, sdk |
| `cp.messages` | Counter | job_id, sdk, role |
| `cp.premium_requests` | Counter | job_id, sdk |
| `cp.approvals` | Counter | — |
| `cp.llm.duration` | Histogram | job_id, sdk, model, is_subagent |
| `cp.tool.duration` | Histogram | job_id, sdk, tool_name, success |
| `cp.approval.wait` | Histogram | — |
| `cp.context.tokens` | Gauge | job_id, sdk |
| `cp.context.window_size` | Gauge | job_id, sdk |
| `cp.quota.used` | Gauge | job_id, sdk, resource |
| `cp.quota.entitlement` | Gauge | job_id, sdk, resource |
| `cp.quota.remaining_pct` | Gauge | job_id, sdk, resource |

### New Metrics (Proposed)

| Metric | Type | Attributes | Section |
|--------|------|------------|---------|
| `cp.files.read` | Counter | job_id, file_path | §5.5 |
| `cp.files.read.repeat` | Counter | job_id, file_path | §5.5 |
| `cp.files.written` | Counter | job_id, file_path | §5.5 |
| `cp.context.compaction.reread_tokens` | Counter | job_id | §5.5 |
| `cp.tool.retry` | Counter | job_id, tool_name, tool_target | §5.6 |

### New Span Attributes (Proposed)

| Attribute | Span Type | Type | Section |
|-----------|----------|------|---------|
| `turn_number` | llm, tool | int | §5.1 |
| `execution_phase` | llm, tool | string | §5.4 |
| `tool_category` | tool | string | §5.3 |
| `tool_target` | tool | string | §5.3 |
| `tool_result_size` | tool | int | §5.3 |
| `tool_files_referenced` | tool | string[] | §5.3 |
| `is_retry` | tool | bool | §5.6 |
| `prior_failure_span_id` | tool | int | §5.6 |
| `prompt_system_tokens` | llm | int | §5.2 |
| `prompt_history_tokens` | llm | int | §5.2 |
| `prompt_tool_results_tokens` | llm | int | §5.2 |
| `prompt_file_contents_tokens` | llm | int | §5.2 |
| `prompt_overhead_tokens` | llm | int | §5.2 |

### New DB Tables (Proposed)

| Table | Purpose | Section |
|-------|---------|---------|
| `job_cost_attribution` | Materialized per-job cost breakdown | §6.2 |
| `job_file_access_log` | File access pattern tracking | §6.3 |
| `observations` | Statistical analysis results | §8.5 |

---

## Appendix B: API Endpoints (Proposed)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/analytics/cost-drivers` | Aggregated cost by tool category, phase |
| GET | `/analytics/cost-drivers/tools` | Tool-level cost attribution with drill-down |
| GET | `/analytics/cost-drivers/phases` | Phase-level cost distribution |
| GET | `/analytics/waste` | Retry/rework/re-read metrics across jobs |
| GET | `/analytics/efficiency` | Output efficiency, cache effectiveness |
| GET | `/analytics/observations` | Statistical observations with sample sizes |
| GET | `/analytics/fleet` | Cross-job correlation data |
| GET | `/jobs/{id}/cost-attribution` | Per-job cost anatomy |

---

## Appendix C: Open Questions

1. **Copilot SDK model name format:** When the Copilot SDK routes to a non-OpenAI
   model (e.g., Claude via Copilot), we need to know the exact model name string
   the SDK reports in `assistant.usage` events. The `make_counter()` routing logic
   uses `tiktoken.encoding_for_model()` (which raises `KeyError` for Claude names)
   and a `"claude" in model` check. We need to validate these model name strings
   against actual Copilot SDK events to ensure correct routing. **Verified facts:**
   tiktoken definitively does NOT support Claude model names — `encoding_for_model()`
   raises `KeyError` for all `claude-*` variants tested. The Copilot adapter extracts
   model name from `data.model` in `_handle_usage_event()` (line 337) — we need a
   sample of actual values for Copilot-routed Claude calls.

2. **Subagent attribution:** Subagent costs are currently rolled into the parent job.
   Should we track them as separate cost centers or keep the current model? Separate
   tracking enables answering "is delegation cost-effective?" The current span table
   already stores `is_subagent` in LLM span `attrs_json`, so per-span subagent
   identification is already possible — we just need the attribution pipeline to
   segment on it.

3. **Real-time vs. post-hoc:** The attribution pipeline (§7.1) runs post-job. Should
   we compute partial attribution during long-running jobs for live dashboard
   updates? This adds complexity but enables operators to intervene on wasteful jobs.
   **Recommendation:** Start post-hoc only (Phase 2). Add a "refresh attribution"
   button for running jobs in Phase 3 that triggers on-demand computation.

4. **Privacy of tool arguments:** Tool arguments may contain sensitive data (file
   paths, command contents). The `tool_target` field should be sanitized — e.g.,
   extract only the filename, not the full path, for cross-job aggregation. Need to
   define sanitization rules.

5. **Backfill:** Can we retroactively compute some enriched metrics for existing jobs?
   Tool categories and file access patterns can be derived from existing transcript
   events in the events table (which store `tool_args`). Retry detection can be run
   over existing span data. Worth a one-time migration script that replays events
   through the new trackers. **Verified:** TranscriptUpdated events store `tool_args`
   in their payload JSON — the event_repo `list_by_job()` method can retrieve them
   for replay.

6. **Local tokenizer accuracy vs. SDK-reported totals:** The `anthropic-tokenizer`
   package provides a local BPE tokenizer with 65K vocabulary. Our `overhead_tokens`
   field captures the delta between the local tokenizer's sum and the SDK-reported
   `input_tokens`. We should empirically measure this delta across a sample of real
   sessions to characterize its magnitude and stability. If the delta is
   consistently small (< 5% of total), the local tokenizer is highly accurate. If
   large or variable, it may indicate the tokenizer vocabulary is stale relative to
   the model version — in which case we should pin a specific `anthropic-tokenizer`
   version per Claude model generation. **Note:** For Claude, the SDK only provides
   session-total tokens (via `ResultMessage.usage`), so the local tokenizer is the
   **only** source of per-turn breakdown — making its accuracy critical.

7. **Diff snapshot activation:** The `diff_snapshots` table exists in the schema
   (migration `0001`) and has a `DiffSnapshotRow` model, but no code ever inserts
   rows. Phase 2 requires activating this: compute the unified diff via `GitService`
   at job completion and insert it. Currently, only `JobRepo.delete_diff_snapshots_for_jobs()`
   exists (line 361 in `job_repo.py`) — we need either a new `DiffSnapshotRepo` with
   an insert method, or an insert method added to `JobRepo`, plus a trigger in
   `RuntimeService`.

8. **ExecutionPhaseChanged coverage:** Currently only emitted for the verification
   phase. Phase 1 (§9.1) already plans to add emissions for all phase transitions
   in `RuntimeService`. This is a prerequisite — without it, most spans will have
   no `execution_phase` value. The `RuntimeService` orchestration flow has clear
   phase boundaries (workspace prep → agent start → verification → finalization)
   where emissions can be added.
