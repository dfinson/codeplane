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
10. [Appendix: Metric Catalog](#appendix-a-metric-catalog)

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
| **Tool call arguments** | Tool name only in spans | What file paths? What bash commands? What edit targets? |
| **Diff snapshots** | Final unified diff per job | How much code was written vs. tokens spent? |
| **Execution phases** | Phase change events emitted | Cost breakdown by phase (setup/reasoning/verification)? |
| **Agent plans** | Plan steps with status | How much rework vs. linear progress? |
| **Progress headlines** | Headlines with replaces_count | How many pivots/restarts occurred? |
| **Compaction events** | Count + tokens reclaimed | What was lost? Did it cause re-reads? |
| **Approval wait time** | Metric defined but not recorded | Cost of idle time during approvals? |
| **Model downgrades** | Event emitted | Cost saved vs. quality impact? |

### 2.3 Architectural Strengths

- **Dual persistence** (OTel in-memory + SQLite) means we can query without external
  infra.
- **Event bus** already captures rich domain events — we just need to connect them to
  cost.
- **Span-level detail** per LLM/tool call is the right granularity for attribution.
- **Transcript already captures tool arguments** (`tool_args` field in
  TranscriptUpdated payload) — this is rich data we're ignoring for analytics.

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

### 3.4 Task & Repo Characteristics

> *"What kinds of tasks are expensive?"*

- Does prompt length/complexity predict total cost?
- Do certain repos consistently cost more? Why? (Size? Language? Complexity?)
- Do jobs that touch more files cost proportionally more, or is there a non-linear
  explosion?
- Does the number of operator interventions (approvals, follow-ups) correlate with
  cost?
- Do verification/self-review passes provide value proportional to their cost?

### 3.5 Model & SDK Efficiency

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
| **Turn-level cost attribution** | 3.1, 3.2, 3.3 | Medium | Link LLM spans to transcript turn sequence numbers |
| **Prompt composition breakdown** | 3.1 | Hard | Instrument adapter to capture prompt segment sizes |
| **Tool call arguments** | 3.2 | Easy | Already in transcript; copy to span attrs |
| **File access tracking** | 3.1, 3.2 | Medium | Parse tool args for file paths; deduplicate |
| **Phase-tagged cost** | 3.3 | Medium | Tag each span with current execution phase |
| **Retry/rework detection** | 3.3 | Hard | Heuristic: same tool+target within N turns |
| **Post-compaction re-read cost** | 3.1, 3.3 | Hard | Track pre-compaction file set, detect re-reads |
| **Task complexity features** | 3.4 | Medium | Extract features from prompt + repo at job start |
| **Output efficiency** | 3.5 | Easy | Compute from existing: diff LOC / cost |
| **Cross-job aggregation** | All | Medium | New query layer over existing tables |

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
  │    record_tool_call(turn_number, tool_name, tool_args_summary, ...)
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

**Concept:** For each LLM call, capture the approximate size of each segment of the
input prompt. This doesn't require capturing the prompt content — just byte/token
counts per segment.

**New span attributes for LLM calls:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `prompt_system_tokens` | int | Tokens in system prompt (estimated) |
| `prompt_history_tokens` | int | Tokens in conversation history |
| `prompt_tool_results_tokens` | int | Tokens in tool result content |
| `prompt_file_contents_tokens` | int | Tokens in inline file contents |
| `prompt_user_message_tokens` | int | Tokens in the latest user/operator message |

**Implementation approach:** The agent SDKs don't expose prompt segmentation
directly. Two strategies:

- **Strategy A (SDK instrumentation):** If the SDK emits prompt details in its events
  or logs, parse them. The Copilot SDK's `CompletionRequested` event may contain
  message array structure. The Claude SDK exposes `input_tokens` at the message level.
  
- **Strategy B (Heuristic estimation):** Track system prompt size at session init
  (roughly constant). Track cumulative tool result sizes from transcript events. The
  remainder is conversation history. This is approximate but useful for trend analysis
  across hundreds of jobs.

- **Strategy C (Proxy/middleware):** If using OTLP export to a collector, add a
  processor that inspects prompt payloads. This is the most accurate but requires
  external infra.

**Recommendation:** Start with Strategy B (heuristic). It's implementable entirely
within the adapter layer, requires no SDK changes, and is accurate enough for the
cross-job trend analysis we're targeting. Upgrade to Strategy A when SDK support
improves.

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

The `ExecutionPhaseChanged` event is already emitted. We need to:

1. Track `current_phase` in the adapter as state.
2. Stamp each LLM and tool span with the current phase.

**New span attribute:**

| Attribute | Type | Values |
|-----------|------|--------|
| `execution_phase` | string | `environment_setup`, `agent_reasoning`, `verification`, `finalization`, `post_completion` |

> **Note:** `post_completion` occurs after the agent session ends (operator review,
> merge decisions). It typically has zero LLM/tool cost but is included for
> completeness — any operator-triggered follow-up actions are tagged here.

### 5.5 Context Efficiency Tracking

**Concept:** Track how efficiently the context window is being used — are we
re-reading files? Is compaction causing re-work?

**New metrics:**

| Instrument | Type | Unit | Description |
|------------|------|------|-------------|
| `cp.files.read` | Counter | — | File read operations (with `file_path` attr) |
| `cp.files.read.repeat` | Counter | — | File re-reads (same file, same job) |
| `cp.files.written` | Counter | — | File write operations |
| `cp.context.compaction.reread_cost` | Counter | tokens | Estimated tokens spent re-reading post-compaction |

**Implementation:** Maintain a per-job `Set[file_path]` of files already read. When
a `file_read` tool call occurs for a path already in the set, increment
`cp.files.read.repeat`. After a compaction event, clear the set (or a subset) to
model the context loss.

### 5.6 Retry & Rework Detection

**Concept:** Detect when the agent is spinning — retrying the same operation or
re-reading the same information.

**Heuristics:**

1. **Tool retry:** Same `tool_name` + `tool_target` within a sliding window of 3
   turns → mark as retry.
2. **Edit retry:** `edit_file` on the same path within 5 turns → possible rework.
3. **Reasoning loop:** 3+ consecutive LLM calls with no tool calls between them →
   possible reasoning loop.
4. **Plan churn:** `AgentPlanUpdated` events where step labels change significantly →
   pivot detection.

**New span attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `is_retry` | bool | Heuristic: repeated tool+target |
| `retry_depth` | int | How many times this same operation has been attempted |

**New summary metrics:**

| Metric | Description |
|--------|-------------|
| `retry_count` | Total tool retries in the job |
| `rework_cost_usd` | Estimated cost of retry/rework turns |
| `reasoning_loop_count` | Number of detected reasoning loops |

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
    
    -- Prompt composition breakdown (tokens, averaged across turns)
    avg_system_prompt_tokens    INTEGER DEFAULT 0,
    avg_history_tokens          INTEGER DEFAULT 0,
    avg_tool_result_tokens      INTEGER DEFAULT 0,
    avg_file_content_tokens     INTEGER DEFAULT 0,
    
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
    rework_cost_usd         REAL DEFAULT 0,
    reasoning_loop_count    INTEGER DEFAULT 0,
    compaction_reread_cost  REAL DEFAULT 0,
    
    -- Output efficiency
    diff_lines_added        INTEGER DEFAULT 0,
    diff_lines_removed      INTEGER DEFAULT 0,
    cost_per_diff_line      REAL DEFAULT 0,
    
    -- Task characteristics (extracted at job start)
    prompt_word_count       INTEGER DEFAULT 0,
    prompt_complexity_score REAL DEFAULT 0,   -- heuristic
    files_touched           INTEGER DEFAULT 0,
    
    -- Subagent cost
    subagent_cost_usd       REAL DEFAULT 0,
    subagent_fraction       REAL DEFAULT 0,
    
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
ALTER TABLE job_telemetry_summary ADD COLUMN rework_cost_usd REAL DEFAULT 0;
ALTER TABLE job_telemetry_summary ADD COLUMN reasoning_loop_count INTEGER DEFAULT 0;
ALTER TABLE job_telemetry_summary ADD COLUMN unique_files_read INTEGER DEFAULT 0;
ALTER TABLE job_telemetry_summary ADD COLUMN repeated_file_reads INTEGER DEFAULT 0;
ALTER TABLE job_telemetry_summary ADD COLUMN subagent_cost_usd REAL DEFAULT 0;
ALTER TABLE job_telemetry_summary ADD COLUMN diff_lines_added INTEGER DEFAULT 0;
ALTER TABLE job_telemetry_summary ADD COLUMN diff_lines_removed INTEGER DEFAULT 0;
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
│     → retry count, rework cost      │
│  6. Extract diff stats from         │
│     diff_snapshots table            │
│     → lines added/removed           │
│  7. Compute prompt composition      │
│     estimates from span attrs       │
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
-- Jobs with highest retry/rework waste
SELECT
    s.job_id,
    s.total_cost_usd,
    a.retry_count,
    a.rework_cost_usd,
    ROUND(a.rework_cost_usd / NULLIF(s.total_cost_usd, 0) * 100, 1)
        as waste_pct,
    a.reasoning_loop_count,
    a.compaction_reread_cost
FROM job_telemetry_summary s
JOIN job_cost_attribution a ON s.job_id = a.job_id
WHERE s.completed_at > datetime('now', '-30 days')
ORDER BY waste_pct DESC
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
│  │  │ History: 40%      │   │  │  Avg rework cost:   $0.32/job   │ │
│  │  │ Tool results: 30% │   │  │  Re-read rate:      2.3x/file   │ │
│  │  │ File contents: 12%│   │  │  Compaction loss:   $0.08/job   │ │
│  │  │ User message: 3%  │   │  │  Reasoning loops:   0.4/job     │ │
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
│  │  WASTE ANALYSIS                                             │   │
│  │  Retries: 4 tool retries ($0.18 rework cost = 14% of total)│   │
│  │  Re-reads: 3 files read 2+ times ($0.09 redundant reads)   │   │
│  │  Compactions: 1 (re-read 2 files after, est. $0.04)        │   │
│  │  Reasoning loops: 0                                         │   │
│  │                                                             │   │
│  │  Efficiency: 45 lines of diff at $0.028/line               │   │
│  └─────────────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────────┘
```

### 8.4 Fleet Intelligence (Cross-Job Patterns)

**Purpose:** Surface patterns that only become visible across dozens/hundreds of jobs.

```
┌────────────────────────────────────────────────────────────────────┐
│  FLEET INTELLIGENCE  (last 90 days, 156 jobs)                      │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│  INSIGHTS                                                          │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ ⚠ 23% of total spend is rework (retried tools + re-reads)   │  │
│  │ ⚠ bash outputs > 5000 chars correlate with 2.3x higher      │  │
│  │   next-turn cost — consider truncation                       │  │
│  │ ✓ Cache hit rate improved 15% → 38% over last 30 days       │  │
│  │ ⚠ Jobs on repo "backend-api" cost 2.1x more than average    │  │
│  │ ✓ Claude subagent tasks cost 40% less than main-agent equiv  │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                    │
│  COST PREDICTORS (correlation with total job cost)                 │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ Prompt word count         ●──────────────── r=0.42           │  │
│  │ Files touched             ●─────────────────── r=0.58        │  │
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

### 8.5 Insights Engine (Automated Observations)

**Concept:** Compute a set of heuristic "insights" after each job and across the
fleet, surfacing them as cards in the dashboard.

**Job-level insights:**
- "This job spent 35% of its cost on file re-reads after a context compaction"
- "4 edit_file retries on `src/auth.ts` cost $0.18 — consider breaking into subtasks"
- "Verification phase cost more than reasoning — is the test suite slow?"

**Fleet-level insights:**
- "Jobs with prompts > 100 words cost 2.3x more on average"
- "bash tool calls with output > 5000 chars are followed by LLM calls that cost
  2.1x the average"
- "Repo X has a 40% retry rate on edit_file — may have complex merge conflicts"
- "Cache hit rate has declined from 45% to 28% — check if system prompts changed"

**Implementation:** Each insight is a SQL query + threshold + template. Run after
each job completion and on a schedule for fleet-level insights. Store as:

```sql
CREATE TABLE insights (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    scope       TEXT NOT NULL,      -- 'job' or 'fleet'
    job_id      TEXT,               -- NULL for fleet insights
    category    TEXT NOT NULL,      -- 'waste', 'efficiency', 'cost_driver', 'trend'
    severity    TEXT NOT NULL,      -- 'info', 'warning', 'critical'
    message     TEXT NOT NULL,
    metric_key  TEXT,               -- e.g. 'retry_rate', 'cache_hit_rate'
    metric_value REAL,
    computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## 9. Implementation Approach

### 9.1 Phased Rollout

#### Phase 1: Enrich Span Data (Low Risk, High Value)

**Scope:** Add `turn_number`, `execution_phase`, `tool_category`, `tool_target`,
`result_size`, and `is_retry` to span collection. Extend the DB schema. No frontend
changes yet.

**Changes:**
1. Alembic migration: add columns to `job_telemetry_spans`, create
   `job_file_access_log` table.
2. `telemetry.py`: Accept new attributes in `record_llm_call()` and
   `record_tool_call()`.
3. `copilot_adapter.py` / `claude_adapter.py`: Track `turn_number` and
   `current_phase` as adapter state. Extract `tool_category` and `tool_target` from
   tool events. Detect retries.
4. `telemetry_spans_repo.py`: Store new columns.

**Validation:** Run jobs, verify spans have enriched attributes. Compare old vs. new
query patterns.

#### Phase 2: Post-Job Attribution Pipeline

**Scope:** Build the `job_cost_attribution` computation and store results. New
analytics API endpoints.

**Changes:**
1. New service: `backend/services/cost_attribution.py` — reads spans + file access
   log, computes attribution, writes to `job_cost_attribution`.
2. Hook into `RuntimeService._finalize_job()` to trigger attribution after job
   completion.
3. New Alembic migration: create `job_cost_attribution` table, extend
   `job_telemetry_summary`.
4. New API endpoints:
   - `GET /analytics/cost-drivers` — aggregated cost by tool category, phase, etc.
   - `GET /analytics/waste` — retry/rework metrics across jobs.
   - `GET /analytics/efficiency` — output efficiency, cache effectiveness.
   - `GET /jobs/{id}/cost-attribution` — per-job breakdown.
5. Update `api_schemas.py` with response models.

**Validation:** Run 10+ jobs, verify attribution sums to ≈100% of total cost. Compare
with manual analysis of transcripts.

#### Phase 3: Dashboard Views

**Scope:** Build the four new dashboard views described in Section 8.

**Changes:**
1. New frontend API client functions for cost-driver endpoints.
2. `CostDriversScreen.tsx` — fleet-level cost drivers view (Section 8.1).
3. Enhanced `MetricsPanel.tsx` — per-job cost anatomy (Section 8.3).
4. Enhanced `AnalyticsScreen.tsx` — tool deep-dive (Section 8.2) + fleet
   intelligence (Section 8.4).
5. Route additions in `App.tsx`.

#### Phase 4: Insights Engine

**Scope:** Automated insight generation and display.

**Changes:**
1. New service: `backend/services/insights.py` — SQL-based insight rules.
2. New table: `insights`.
3. New API endpoint: `GET /analytics/insights`.
4. Frontend: Insight cards in dashboard and per-job views.

### 9.2 Prompt Composition Estimation (Strategy B Detail)

Since we're starting with the heuristic approach, here's the concrete logic:

```python
class PromptCompositionEstimator:
    """Estimates prompt segment sizes without access to raw prompts."""

    def __init__(self):
        self.system_prompt_tokens: int = 0      # Set once at session start
        self.cumulative_tool_result_tokens: int = 0
        self.cumulative_file_content_tokens: int = 0

    def on_session_start(self, model: str):
        # System prompts are roughly constant per model/SDK
        self.system_prompt_tokens = SYSTEM_PROMPT_ESTIMATES.get(model, 2000)

    def on_tool_result(self, tool_name: str, result: str, result_tokens: int):
        if tool_name in FILE_READ_TOOLS:
            self.cumulative_file_content_tokens += result_tokens
        else:
            self.cumulative_tool_result_tokens += result_tokens

    def estimate_composition(self, total_input_tokens: int) -> dict:
        system = self.system_prompt_tokens
        tools = min(self.cumulative_tool_result_tokens, total_input_tokens - system)
        files = min(self.cumulative_file_content_tokens, total_input_tokens - system)
        # History is everything else
        history = max(0, total_input_tokens - system - tools - files)
        return {
            "prompt_system_tokens": system,
            "prompt_history_tokens": history,
            "prompt_tool_results_tokens": tools,
            "prompt_file_contents_tokens": files,
        }
```

This won't be exact (tool results may be truncated or summarized in context), but
across hundreds of jobs the trends will be meaningful.

### 9.3 Retry Detection Algorithm

```python
class RetryDetector:
    """Detects tool call retries using a sliding window."""

    def __init__(self, window_size: int = 3):
        self.window_size = window_size
        self.recent_calls: deque[tuple[str, str]] = deque(maxlen=50)

    def check(self, tool_name: str, tool_target: str) -> tuple[bool, int]:
        """Returns (is_retry, retry_depth)."""
        key = (tool_name, tool_target)
        # Look back through recent calls within window
        depth = 0
        for past_name, past_target in reversed(self.recent_calls):
            if (past_name, past_target) == key:
                depth += 1
        self.recent_calls.append(key)
        return (depth > 0, depth)
```

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
| `cp.context.compaction.reread_cost` | Counter | job_id | §5.5 |
| `cp.tool.retry` | Counter | job_id, tool_name, tool_target | §5.6 |
| `cp.reasoning.loop` | Counter | job_id | §5.6 |

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
| `retry_depth` | tool | int | §5.6 |
| `prompt_system_tokens` | llm | int | §5.2 |
| `prompt_history_tokens` | llm | int | §5.2 |
| `prompt_tool_results_tokens` | llm | int | §5.2 |
| `prompt_file_contents_tokens` | llm | int | §5.2 |

### New DB Tables (Proposed)

| Table | Purpose | Section |
|-------|---------|---------|
| `job_cost_attribution` | Materialized per-job cost breakdown | §6.2 |
| `job_file_access_log` | File access pattern tracking | §6.3 |
| `insights` | Automated insight storage | §8.5 |

---

## Appendix B: API Endpoints (Proposed)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/analytics/cost-drivers` | Aggregated cost by tool category, phase |
| GET | `/analytics/cost-drivers/tools` | Tool-level cost attribution with drill-down |
| GET | `/analytics/cost-drivers/phases` | Phase-level cost distribution |
| GET | `/analytics/waste` | Retry/rework/re-read metrics across jobs |
| GET | `/analytics/efficiency` | Output efficiency, cache effectiveness |
| GET | `/analytics/insights` | Auto-generated insight cards |
| GET | `/analytics/fleet` | Cross-job correlation data |
| GET | `/jobs/{id}/cost-attribution` | Per-job cost anatomy |

---

## Appendix C: Open Questions

1. **SDK prompt visibility:** Can we get actual prompt segment sizes from the Copilot
   SDK's `CompletionRequested` event or Claude's message-level token counts? If so,
   Strategy A (§5.2) becomes viable and much more accurate.

2. **Subagent attribution:** Subagent costs are currently rolled into the parent job.
   Should we track them as separate cost centers or keep the current model? Separate
   tracking enables answering "is delegation cost-effective?"

3. **Real-time vs. post-hoc:** The attribution pipeline (§7.1) runs post-job. Should
   we compute partial attribution during long-running jobs for live dashboard
   updates? This adds complexity but enables operators to intervene on wasteful jobs.

4. **Privacy of tool arguments:** Tool arguments may contain sensitive data (file
   paths, command contents). The `tool_target` field should be sanitized — e.g.,
   extract only the filename, not the full path, for cross-job aggregation. Need to
   define sanitization rules.

5. **Insight quality:** The insights engine (§8.5) uses fixed SQL queries + thresholds.
   Should we explore lightweight ML models for anomaly detection once we have
   sufficient data (>500 jobs)? This could surface unexpected patterns like "jobs
   started after 6pm cost 30% more" (developer fatigue → vague prompts).

6. **Backfill:** Can we retroactively compute some enriched metrics for existing jobs?
   Tool categories and some waste metrics could be derived from existing span
   attrs_json. Worth a one-time migration script.
