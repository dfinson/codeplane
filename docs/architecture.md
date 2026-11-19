---
hide:
  - navigation
---

# How It Works

CodePlane is a **local server** that wraps the agent CLIs you already use and builds a normalized intelligence layer on top. It does not contain its own AI — it orchestrates, observes, and enriches agent sessions from Claude Code and GitHub Copilot.

## What CodePlane Is

- A **BYOCLI platform**. You install and authenticate agent CLIs (Claude Code, GitHub Copilot). CodePlane manages them — or simply observes them.
- A **headless, local-first server** that runs on your workstation — no editor required. Access it from any browser, or remotely from your phone via tunnels.
- An **intelligence layer** that enriches raw agent sessions with semantic trails, cost attribution, narrative review, and workspace memory.
- **Zero workflow change** if you want it. Mirror native CLI sessions you’re already running — no need to launch jobs through CodePlane to get the value.

## High-Level Architecture

<!-- PLACEHOLDER: Architecture Overview Diagram -->

**You → Browser → CodePlane → Agent CLI → Repository**

## Data Architecture

Every agent session — managed or mirrored — is normalized into a **local SQLite database** at `~/.codeplane/`. The schema captures:

- **Jobs** — prompt, repo, agent, model, timestamps, state transitions
- **Events** — every domain event (transcript updates, approval requests, state changes, log lines, telemetry)
- **Spans** — per-turn token counts and precomputed cost with model-aware pricing
- **Trails** — structured intent graph nodes with enrichment (rationale, intent, motivation)
- **Activities** — semantic groupings of related turns (implementation, verification, investigation) with cost rollups
- **Observations** — cross-job patterns: waste, rework, cost anomalies, behavioral signals that persist across sessions
- **Steps** — plan tracking with step-level Git changes

No data leaves your machine unless you explicitly configure OTEL export or push a branch to a remote.

The database is a standalone asset. Query it with `sqlite3`, point a notebook at it, or let another agent analyze your agent history — even if you never open the UI.

## Sidecar Intelligence

CodePlane runs **parallel LLM sessions** alongside the agent — not hooks that fire after the fact, but persistent reasoning processes that observe and intervene in real time.

### Built-in Sidecars

| Sidecar | What It Does |
|---------|-------------|
| **Enricher** | Annotates trail nodes with rationale, purpose, and edit motivations as the agent works |
| **Planner** | Infers the agent’s strategy and tracks progress against it |
| **Arbiter** | Detects stalled or looping agents and autonomously recovers them via message injection |
| **Activity Detector** | Groups turns into semantic activities and detects boundaries |

### Custom Sidecars

Define custom sidecars in plain English. A sidecar is a system prompt, a trigger condition, and an action — CodePlane manages the LLM session and the observation pipeline.

### Feedback Loop

Sidecars can feed intelligence back to the agent via `AgentMessageRoute`. This is unique — most tools fire one-shot hooks. CodePlane maintains a persistent reasoning session that can intervene mid-run.

## Key Concepts

### Jobs

A job is a single coding task. You provide a prompt, repository, agent, and model. CodePlane creates an isolated Git worktree for the job and starts an agent session. Each job has its own worktree, so multiple jobs can run concurrently without interfering.

### Worktrees

Every job runs in a Git worktree — a separate checkout of the repository on a temporary branch. Your main working directory is never modified. When the job completes, you choose to merge, create a PR, or discard.

### Events & SSE

All activity flows through domain events: job state changes, transcript updates, approval requests, log lines, diff updates, and telemetry. The browser receives these as a live SSE stream for real-time updates.

### Agent Adapters

Each agent CLI/SDK is wrapped behind a common adapter interface. CodePlane manages the SDK internals so you don’t have to — you just install and authenticate the CLI. Adding support for a new agent means writing one adapter file.

### Decision Trail

Every agent action is recorded as a **trail node** — a structured entry in an intent graph that captures what the agent did, why, and how:

- **Node kinds**: goal, turn, reasoning, decision, modify, write, verify, investigate, backtrack
- **Intent enrichment**: an async LLM pipeline annotates nodes with intent, rationale, and outcome
- **Activity grouping**: related turns are grouped into semantic activities with LLM-driven boundary detection
- **Policy context**: each node records its governance verdict — recommended action, risk band, and reason code — plus its checkpoint reference

The trail feeds the narrative review, cost attribution, and post-hoc debugging of agent behavior.

### Action Policy Engine

When an agent tries to perform an action, CodePlane's standalone policy combines bounded assessment, local constraints and exact execution ownership before AGT rule selection:

| Recommended action | Behavior |
|------|----------|
| **Allow** | Proceed — record only |
| **Warn** | Create a Git savepoint, then proceed |
| **Escalate** | Send for review before continuing |
| **Deny** | Block until operator approves |

The active preset selects fixed rule posture. Hard gates require explicit approval. Automatic Git mutation requires a current-byte or new-target-absence witness, rechecked before consumption. Historical TF policy and checkpoint recovery belong to the preserved transition release, not an optional final-runtime dependency.

### Authentication

CodePlane uses **your existing CLI authentication**. There is no separate auth system.

- For GitHub Copilot CLI: your existing GitHub authentication (`gh auth login`)
- For Claude Code CLI: your existing Anthropic credentials

If the agent CLIs work on your machine, CodePlane can use them. Run `cpl doctor` to verify.

## Infrastructure Details

### Sharing & Public Access

Jobs can be shared via **share tokens** — ephemeral, read-only authorization strings. A share token grants access to a single job’s metadata and SSE stream without requiring authentication. Tokens are stored in-memory with a 24-hour TTL and are lost on server restart.

Public share endpoints serve a minimal read-only view. No mutation operations (approvals, messages, cancellation) are exposed.

### Push Notifications

CodePlane uses the **Web Push** protocol (VAPID) to deliver browser notifications for approval requests, job completions, and failures. VAPID keys are generated at first startup and stored in `~/.codeplane/vapid.json`. Subscriptions are managed in-memory — the service worker re-subscribes automatically after a server restart.

### Port Preview Proxy

The backend includes a reverse proxy that forwards requests from `/api/preview/{port}/` to `127.0.0.1:{port}`. Combined with a tunnel, this lets you access development servers running on the workstation from a remote device. Only user ports (1024–65535) are allowed; the proxy never connects to external hosts (SSRF protection).

### MCP Server

CodePlane exposes itself as an **MCP server** — 7 tools for job orchestration, approval handling, workspace browsing, and repo management. External agents can delegate coding tasks to CodePlane and monitor them programmatically. See [MCP Server](mcp-server.md) for details.

## FAQ

??? question "What CodePlane Is Not"
    - Not an AI model or agent. It orchestrates and observes agents built by others.
    - Not a task decomposer — by design. One prompt, one agent, full transparency into every action it takes.
    - Not a cloud service. It runs locally with optional remote access via tunnels.
    - Not a replacement for your existing tools. It uses your Git, your CLI credentials, and your repos as-is.
