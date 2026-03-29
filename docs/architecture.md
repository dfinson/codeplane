# How It Works

CodePlane is an **orchestration layer** for coding agents. It does not contain its own AI — it wraps existing agent SDKs and provides the control surface around them.

## What CodePlane Is

- A **control plane**, not an execution engine. It manages and supervises coding agents, but the agents themselves come from external SDKs (GitHub Copilot, Claude Code).
- A **local server** that runs on your machine. You interact with it through a web browser at `http://localhost:8080`.
- A **thin wrapper** around existing CLIs and SDKs. It delegates to the SDK for all AI reasoning and tool execution.

## What CodePlane Is Not

- Not an AI model or agent. It orchestrates agents built by others.
- Not a cloud service. It runs locally (cloud deployment is a future direction).
- Not a replacement for your existing tools. It uses your Git installation, your SDK credentials, and your repositories as-is.

## Authentication

CodePlane uses **your existing SDK authentication**. There is no separate auth system to configure.

- For GitHub Copilot: your existing GitHub authentication
- For Claude Code: your existing Anthropic credentials

If the SDK CLIs work on your machine, CodePlane can use them.

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
│ Git │   │ SQLite │  │ Agent SDKs  │
│     │   │  (DB)  │  │ Copilot /   │
│     │   │        │  │ Claude Code │
└─────┘   └────────┘  └─────────────┘
```

**You → Browser → CodePlane → Agent SDK → Repository**

## Key Concepts

### Jobs

A job is a single coding task. You provide a prompt, repository, SDK, and model. CodePlane creates an isolated Git worktree for the job and starts an agent session. Each job has its own worktree, so multiple jobs can run concurrently without interfering.

### Worktrees

Every job runs in a Git worktree — a separate checkout of the repository on a temporary branch. Your main working directory is never modified. When the job completes, you choose to merge, create a PR, or discard.

### Events

All activity flows through domain events: job state changes, transcript updates, approval requests, log lines, diff updates, and telemetry. The browser receives these as a live SSE stream for real-time updates.

### Agent Adapters

Each SDK is wrapped behind a common adapter interface. CodePlane doesn't know or care about SDK internals — it talks to the adapter, which translates to and from the specific SDK. Adding a new SDK means writing one new adapter file.

### Permission Callbacks

When an agent tries to perform a risky action (file write, shell command), the SDK fires a permission callback. CodePlane intercepts this and either auto-approves or surfaces it to you for a decision. The job pauses until you respond.

## Data Storage

CodePlane stores job data, events, and metrics in a local SQLite database at `~/.codeplane/`. No data leaves your machine unless you explicitly configure OTEL export or create a PR on a remote.
