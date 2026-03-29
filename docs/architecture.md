# How It Works

CodePlane is an **orchestration layer** for coding agents. It does not contain its own AI — it manages the underlying SDKs so you don't have to. You install and authenticate the agent CLIs; CodePlane handles everything else.

## What CodePlane Is

- A **control plane**, not an execution engine. It manages and supervises coding agents, but the agents themselves come from external CLIs (GitHub Copilot CLI, Claude Code CLI).
- A **local-first server** that runs on your workstation. Access it from a browser at `http://localhost:8080` — or remotely from your phone via Dev Tunnels or Cloudflare Tunnels.
- A **thin wrapper** around existing agent CLIs. CodePlane manages the SDKs internally and delegates to them for all AI reasoning and tool execution.

## What CodePlane Is Not

- Not an AI model or agent. It orchestrates agents built by others.
- Not a cloud service. It runs locally with optional remote access via tunnels.
- Not a replacement for your existing tools. It uses your Git installation, your CLI credentials, and your repositories as-is.

## Authentication

CodePlane uses **your existing CLI authentication**. There is no separate auth system to configure.

- For GitHub Copilot CLI: your existing GitHub authentication (`gh auth login`)
- For Claude Code CLI: your existing Anthropic credentials

If the agent CLIs work on your machine, CodePlane can use them. Run `cpl doctor` to verify.

## High-Level Architecture

```
┌─────────────────────────────────────────────┐
│              Your Browser (UI)              │
│         REST commands + live SSE stream      │
└──────────────────────┬──────────────────────┘
                       │
┌──────────────────────▼──────────────────────┐
│           CodePlane Server (Python)          │
│                                              │
│  Job orchestration · Approval gating         │
│  Git worktree management · Diff generation   │
│  SSE broadcasting · Telemetry · Merge/PR     │
└──┬───────────┬───────────┬──────────────────┘
   │           │           │
┌──▼──┐   ┌───▼────┐  ┌───▼─────────┐
│ Git │   │ SQLite │  │ Agent CLIs  │
│     │   │  (DB)  │  │ Copilot /   │
│     │   │        │  │ Claude Code │
└─────┘   └────────┘  └─────────────┘
```

**You → Browser → CodePlane → Agent CLI → Repository**

## Key Concepts

### Jobs

A job is a single coding task. You provide a prompt, repository, agent, and model. CodePlane creates an isolated Git worktree for the job and starts an agent session. Each job has its own worktree, so multiple jobs can run concurrently without interfering.

### Worktrees

Every job runs in a Git worktree — a separate checkout of the repository on a temporary branch. Your main working directory is never modified. When the job completes, you choose to merge, create a PR, or discard.

### Events

All activity flows through domain events: job state changes, transcript updates, approval requests, log lines, diff updates, and telemetry. The browser receives these as a live SSE stream for real-time updates.

### Agent Adapters

Each agent CLI/SDK is wrapped behind a common adapter interface. CodePlane manages the SDK internals so you don't have to — you just install and authenticate the CLI. Adding support for a new agent means writing one adapter file.

### Permission Callbacks

When an agent tries to perform a risky action (file write, shell command), the agent fires a permission callback. CodePlane intercepts this and either auto-approves or surfaces it to you for a decision. The job pauses until you respond.

## Data Storage

CodePlane stores job data, events, and metrics in a local SQLite database at `~/.codeplane/`. No data leaves your machine unless you explicitly configure OTEL export or create a PR on a remote.
