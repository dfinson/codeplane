---
hide:
  - navigation
---

# Usage Guide

## Creating Jobs

Jobs are the core unit of work. Each job runs an agent against a repository in an isolated Git worktree.

### Job Parameters

| Parameter | Description |
|-----------|-------------|
| **Prompt** | What you want the agent to do. Be specific — name files, describe the change, state constraints. |
| **Repository** | A registered local Git repo. Register repos in **Settings** (`Ctrl+,`). |
| **Agent** | GitHub Copilot CLI or Claude Code CLI. CodePlane manages the underlying SDKs — you just need the CLIs installed and authenticated. |
| **Model** | The AI model to use. Available models depend on your agent CLI and account. |

Press `Alt+N` or click **New Job** to open the form. Submit with **Create Job** or `Ctrl+Enter`.

### Voice Input

Click the microphone button to dictate your prompt. Audio is transcribed locally using [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — nothing leaves your machine.

---

## Monitoring Execution

Once a job starts, the detail view shows real-time progress with a collapsible header card and tabbed content.

### Job Header

The header card shows the job title, state badge, SDK, and a live progress headline. On desktop it's a collapsible card with a colored accent border (keyed to job state); on mobile it's a compact rail with a swipe-up bottom sheet for details. The header auto-expands when a job completes, fails, or enters review.

### Activity Panel

The left sidebar (desktop) or slide-in overlay (mobile) shows a hierarchical activity timeline:

- **Activities** — semantic groups of related turns (e.g., "Implement validation", "Run tests")
- **Steps** — individual turns within each activity, showing title and governance badge (○ allow / ◐ warn / ● escalate or deny)

Activity boundaries are determined by an LLM that observes what the agent is doing and decides when it transitions to a new task. Click any step to scroll the transcript to that turn. Use `Cmd+F` / `Ctrl+F` to search transcript content with keyboard navigation through matches.

### Transcript

The primary monitoring view. Shows the agent's reasoning, tool calls (grouped with expandable details), operator messages you've sent, and AI-generated summaries of tool call groups.

Each turn shows its classified **intent** (implementation, investigation, verification, etc.) and the specific actions taken. Send messages to the agent at any time using the input box at the bottom.

### Plan

The agent's planned steps with real-time status indicators for done, active, pending, and skipped steps.

### Metrics

Token usage with input/output/cache breakdown, estimated cost with live cost badge, LLM and tool call counts, cache hit rate, and context utilization. Cost attribution is broken down by activity dimension (implementation, verification, investigation, etc.).

### When to Intervene

- If the transcript shows repetitive actions, the agent may be stuck — send a message or cancel.
- If the plan isn't progressing, check logs for errors.
- If costs are climbing fast, check the cost badge on the header or open the metrics panel to see which activity is consuming budget.

---

## Action Policy

When an agent attempts an action, CodePlane classifies it and delegates the decision to TraceForge governance, which recommends whether to allow it, proceed with a savepoint, or gate it for your approval.

### Presets

Every job runs under a **preset** that controls how aggressively actions are gated:

| Preset | Behavior |
|--------|----------|
| **`autonomous`** | Only non-contained actions (external calls, operations outside the worktree) require approval. Everything else proceeds immediately. |
| **`supervised`** | Default. Contained and reversible actions proceed; anything non-contained or irreversible requires approval. |
| **`locked`** | Even contained, reversible actions create a git checkpoint before proceeding. Non-contained or irreversible actions require approval. |

Set the preset globally via **Settings → Policy**, per-job in the creation form, or via the API (`PUT /api/settings/policy/preset`).

### Recommended Actions

TraceForge governance returns a **recommended action** for each classified action, surfaced as a badge:

| Action | Symbol | Behavior |
|--------|--------|----------|
| **allow** | ○ | Proceeds immediately — no interruption |
| **warn** | ◐ | Creates a git savepoint, then proceeds (rollback available) |
| **transform** | ◐ | Advisory change suggested; proceeds with a savepoint |
| **escalate** | ● | Sent for review (LLM monitor, if enabled) before continuing |
| **deny** | ● | Blocks execution until you approve or reject |

### Approval Actions

| Action | Effect |
|--------|--------|
| **Approve** | Allow this specific action |
| **Reject** | Block it — the agent may try an alternative |
| **Trust Session** | Auto-approve all remaining actions for this job |

### Overrides

Beyond the preset, **Settings → Policy** exposes the controls CodePlane layers on top of the governance profile:

- **Per-preset USD ceilings** — escalate oversight once a job's spend crosses a dollar threshold
- **Approval batch window** — how long consecutive gated actions are grouped into a single prompt
- **MCP server configs** — per-server launch configuration for MCP tools

The underlying decision rules — protected paths, effect budget, and reason-code trust — are owned by the `traceforge.governance` profile selected by your preset. Changes take effect at runtime: running jobs reload policy automatically.

### Hard-Gated Commands

Some commands **always** require approval regardless of preset: `git merge`, `git pull`, `git rebase`, `git cherry-pick`, and `git reset --hard`.

---

## Code Review

When a job finishes, the review experience has two complementary views: the narrative story and the actual Git diff.

### Review Story

The **Review** tab opens with a narrative that explains what the agent changed and why — not a summary of the diff, but a story traced back through the decision trail. The narrative includes:

- **What changed** — a structured walkthrough of the modifications with verified file references
- **Why it changed** — motivation provenance traced from the agent's reasoning through trail enrichment

This is designed so you can decide whether to merge *before* reading a single line of code.

### Diff & Workspace

The **Diff** tab shows all files modified by the agent with syntax-highlighted, side-by-side diffs. Diffs update in real time as the agent works.

The **Workspace** view lets you browse the full file tree — not just changed files. This is useful for checking context or verifying overall structure.

---

## Merging & Resolution

When a job completes, it enters the **review** state. You decide how to land the changes:

| Option | Description |
|--------|-------------|
| **Merge** | Cherry-pick only the agent's meaningful commits onto the base branch, skipping setup noise |
| **Create PR** | Push the branch and open a pull request for team review |
| **Discard** | Delete the worktree and throw away all changes |

If a merge encounters conflicts, CodePlane shows the conflicting files. You can ask the agent to resolve them, discard, or create a PR instead.

After resolution, the job moves to `completed`. Archive it to move it to history and keep the dashboard clean.

### Resume with Instructions

From a job in the `review` state, send follow-up instructions to **resume the same job** — it transitions back to `running` in the same worktree with your new prompt. This lets you iterate on the agent's work without starting over.

---

## Remote & Mobile Access

CodePlane is built for remote supervision. Run the agent on your workstation, control it from your phone, tablet, or any device:

```bash
cpl up --remote                              # Dev Tunnels (default)
cpl up --remote --provider cloudflare        # Cloudflare Tunnels
cpl info                                     # show URL + QR code
```

The UI is fully responsive — optimized layouts for mobile, tablet, and desktop. From your phone you can:

- **Monitor** running jobs and live transcripts
- **Approve or reject** permission requests with one tap
- **Send messages** to steer an agent mid-run
- **Review diffs** in a mobile-optimized single-column view
- **Create new jobs** and browse history

Scan the QR code from `cpl info` to open CodePlane on your phone instantly.

Use `--phone` as a shortcut for `--remote` when you're primarily accessing from a mobile device:

```bash
cpl up --phone                               # same as --remote
```

### Push Notifications

Enable browser push notifications in **Settings → Notifications** to receive alerts even when the browser tab is in the background or closed:

- **Approval needed** — a job is waiting for your decision
- **Job completed** — the agent finished successfully
- **Job failed** — something went wrong

Notifications work on desktop browsers and on mobile when CodePlane is installed as a PWA (see below).

### Install as PWA

CodePlane serves a Progressive Web App manifest. On supported browsers (Chrome, Safari, Edge), you'll see an "Install" or "Add to Home Screen" option. The installed app:

- Launches full-screen without browser chrome
- Receives push notifications like a native app
- Caches static assets for faster loading

---

## Sharing Jobs

Share a read-only view of any job with a colleague or team member — no CodePlane password required.

1. Open a job's detail page
2. Click the **Share** button in the toolbar
3. A link is copied to your clipboard

The share link provides a minimal, non-interactive view showing the job's status, progress headline, and live logs via SSE. Share tokens expire after 24 hours.

Share viewers **cannot** approve actions, send messages, cancel jobs, or access the full workspace.

!!! note "Access scope"
    Share links bypass CodePlane's password but **not** the tunnel provider's identity gate. The viewer must already be able to reach the server (same tunnel, LAN, or localhost). Share links are designed for team members with tunnel access who shouldn't need the CodePlane password to view a job.

---

## Port Preview

If a job (or you via the terminal) starts a development server on a local port, you can access it remotely through the tunnel:

```
https://{tunnel-url}/api/preview/{port}/
```

For example, a Vite dev server on port 5173 would be accessible at `/api/preview/5173/`. Ports must be in the range 1024–65535.

---

## Decision Trail

Every agent action is recorded as a node in a structured intent graph — the **decision trail**. This is richer than a transcript: each node captures what the agent did, why it did it, what kind of action it was (goal, turn, reasoning, modify, verify, investigate, backtrack), and its governance verdict (recommended action).

An async LLM pipeline enriches trail nodes with:

- **Rationale** — why the agent chose this action
- **Intent** — the semantic purpose (implementation, investigation, verification, etc.)
- **Edit motivation** — for file modifications, why this specific change was made

The trail feeds the narrative review, cost attribution by activity, and post-hoc debugging of agent behavior. Browse the trail in the Activity panel — click any node to jump to the corresponding transcript turn.

---

## Workspace Memory

CodePlane maintains **per-repo workspace memory** — knowledge distilled from completed jobs that carries forward to future sessions. After a job finishes, observations about the codebase (patterns, conventions, risk areas, cost characteristics) are persisted and made available to subsequent jobs in the same repository.

This means your 50th job in a repo starts with context your first job didn't have. The agent and sidecars can reference workspace memory to avoid repeating mistakes, respect discovered conventions, and build on prior analysis.

---

## Additional Features

### Terminal

Press `` Ctrl+` `` to open the integrated terminal. Two types are available:

- **Worktree terminal** — rooted in the job's worktree directory for manual inspection
- **Agent terminal** — read-only view of the agent's active terminal session

### Command Palette

Press `⌘K` / `Ctrl+K` to search and navigate jobs by ID, title, repository, or branch.

### History

Archived jobs are browsable from the History page. Search, sort, and click into any past job to see its full transcript, diffs, and metrics.

### Analytics

Press `Alt+A` to open the analytics dashboard — aggregate costs, token usage, model breakdown, tool health, and per-repo spending across all jobs.

### MCP Server

CodePlane exposes an [MCP](https://modelcontextprotocol.io/) server at `http://localhost:8080/mcp`, enabling agent-to-agent orchestration. External agents can create and monitor jobs, handle approvals, browse worktrees, and manage repos programmatically — 7 tools in total.

CodePlane also discovers MCP servers (from `.vscode/mcp.json` or global config) and makes them available to the agent during job execution. See [Configuration > MCP Server Discovery](configuration.md#mcp-server-discovery) for setup details.

Full tool reference: [MCP Server](reference/mcp-server.md).

---

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Alt+N` | New job |
| `Alt+J` | Dashboard |
| `Alt+A` | Analytics |
| `⌘K` / `Ctrl+K` | Command palette |
| `⌘,` / `Ctrl+,` | Settings |
| `` Ctrl+` `` | Toggle terminal |
| `Ctrl+Enter` | Submit prompt / message |
| `/` | Filter jobs (dashboard) |
