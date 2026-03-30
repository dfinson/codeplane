---
hide:
  - navigation
---

# Analytics & Cost Tracking

CodePlane tracks every token, tool call, and dollar across all jobs — giving you fleet-wide visibility into what your coding agents cost and how they perform. Open the analytics dashboard with **Alt+A**.

!!! tip "Understanding costs"
    For subscription plans (like Claude Max or Copilot Business), CodePlane shows what the same usage **would cost at API rates**. This gives you a consistent cost metric for comparing models and optimizing agent behavior, even when you're on a flat-rate plan.

![Analytics Dashboard](images/screenshots/desktop/analytics-dashboard.png)

---

## Scorecard

The scorecard is the top-level summary. It shows per-SDK budget totals, job activity breakdown, and daily cost trends over a configurable period (7–365 days).

![Scorecard](images/screenshots/desktop/analytics-scorecard.png)

- **Budget by SDK** — Total spend for each SDK (Copilot, Claude, etc.) with cost trends
- **Activity breakdown** — Jobs by resolution: running, merged, PR created, discarded, failed, cancelled
- **Copilot quota** — If you use Copilot, the scorecard tracks premium request consumption and alerts when quota exceeds 80%
- **Daily cost trend** — Area chart showing spend over time

---

## Model Comparison

Compare models head-to-head on cost, speed, and outcomes.

![Model Comparison](images/screenshots/desktop/analytics-model-comparison.png)

| Metric | Description |
|--------|-------------|
| **Avg Cost** | Average USD per job for each model |
| **Avg Duration** | Average job runtime |
| **Cost/min** | Spend efficiency — lower is better |
| **Cost/turn** | How much each agent turn costs on average |
| **Resolution rates** | Per-model breakdown of merged / PR'd / discarded / failed |

Filter by repository to compare model performance on specific codebases.

---

## Repository Breakdown

See which repos drive the most spend and activity.

![Repository Breakdown](images/screenshots/desktop/analytics-repo-breakdown.png)

- Cost, job count, and token totals per repository
- Tool calls and average job duration
- Premium request consumption (Copilot)

---

## Tool Health

Monitor the reliability and latency of every tool your agents use.

![Tool Health](images/screenshots/desktop/analytics-tool-health.png)

- **Call counts** — How often each tool is invoked
- **Failure rate** — Percentage of calls that errored (flagged when ≥20%, critical at ≥50%)
- **Latency** — Average, p50, p95, and p99 durations
- **Tool categories** — file_write, file_read, file_search, git, shell, browser, agent, system

---

## Per-Job Cost Drill-Down

Click any job in the analytics table to see where its budget went.

### Cost Attribution by Activity

Each job's cost is broken down by what the agent was doing:

| Activity | What It Covers |
|----------|---------------|
| **Reasoning** | LLM inference not tied to a specific tool |
| **Code reading** | File reads, grep, semantic search |
| **Code changes** | File writes, git operations |
| **Search & discovery** | Browser, file search, broad exploration |
| **Command execution** | Shell commands |
| **Delegation** | Sub-agent calls |

![Cost Drivers](images/screenshots/desktop/analytics-cost-drivers.png)

### Turn Economics

The turn-by-turn cost curve shows how spend evolved during the job:

![Turn Economics](images/screenshots/desktop/analytics-turn-economics.png)

- **Peak turn cost** — The most expensive single turn
- **Avg turn cost** — Mean cost across all turns
- **First half vs. second half** — Detects cost escalation (second half costing >2× the first half is flagged)

### File I/O Analysis

- Unique files read and total reread count
- Most-accessed files ranked by access count with read/write breakdown

---

## Observations

CodePlane runs statistical analysis across your job history and surfaces anomalies automatically.

![Observations](images/screenshots/desktop/analytics-observations.png)

| Category | Trigger | Severity |
|----------|---------|----------|
| **File reread hotspot** | Same file read ≥10 times across ≥3 jobs | Warning (Critical at ≥50) |
| **Tool failure pattern** | Tool failure rate ≥20% over 30 days | Warning (Critical at ≥50%) |
| **Turn cost escalation** | Second-half cost >2× first-half (≥6 turns) | Warning |
| **Retry waste** | Retries costing more than original attempts | Warning |
| **Phase imbalance** | Verification phase consuming more than reasoning | Info |

Each observation shows the affected job count and estimated waste in USD — helping you decide where to optimize prompts, tooling, or model selection.

---

## Job Telemetry Table

The paginated jobs table lists every job with full telemetry, sortable and filterable:

![Jobs Table](images/screenshots/desktop/analytics-jobs-table.png)

- Filter by SDK, model, status, or repository
- Sort by cost, tokens, duration, or any column
- 50 jobs per page with cursor pagination

---

## Token & Cache Metrics

Every job tracks token usage in detail:

- **Input tokens** and **output tokens** (separately)
- **Cache read tokens** and **cache write tokens** (prompt caching)
- **Cache hit rate** — percentage of input tokens served from cache
- Per-model and per-repo token aggregations
