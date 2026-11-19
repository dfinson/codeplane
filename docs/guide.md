---
hide:
  - navigation
---

# Usage Guide

## Copilot-only operation

`runtime.copilot_only` defaults to `true`. CodePlane admits GitHub Copilot
execution, including models from other providers that Copilot serves. It keeps
other adapters and their historical jobs. Set the flag to `false` in the
CodePlane configuration to re-enable them. A stored non-Copilot default is
preserved while the effective default is Copilot.

Disabled integrations cannot create, resume or recover execution. History
remains readable. Startup records a specific failure for an in-flight managed
job whose integration is disabled. Normal operator terminals remain shells,
not provider sandboxes.

Scout remains a governed full-session context brief before primary execution.
It is distinct from text-only naming, narration and Chat completion. Discussion
Chats cannot use tools and do not create a workspace.

Native discovery stays within registered repositories and sessions created after observation starts. It follows the latest valid steering marker, including later enable/disable changes. Already imported sessions recover from their saved checkpoints.

Native history and checkpoints retain the local session ID. Ordinary steering uses the separate published task ID from that session's owned metadata. Missing, mismatched or disabled bindings deny control without guessing a target or changing the local identity.

Native observation honors process-scoped `COPILOT_HOME`. If native source
history or its mapping changed, observation stops with a recorded diagnostic
instead of silently reinterpreting accepted progress. A steering receipt means
admission, not proof the agent executed the instruction. An uncertain delivery
must not be retried automatically.

Cloud task existence, cached flags and HTTP errors prove neither a live CLI execution nor its termination. The current native integration has no authoritative live-identity signal, so strict coordinated dispatch withholds a token and fails closed. Ordinary operator-requested relay admission remains separate from that stricter guarantee.

Verification results distinguish completed, failed and interrupted agent
verification turns. A completed turn is not evidence that tests passed.
Review remains the operator's decision state.

## Runtime homes and generated artifacts

Set `CODEPLANE_HOME` before starting an isolated runtime. Generated artifacts
and subsequent updates use that home's `artifacts` directory, alongside its
database, rather than the operator's ordinary artifact store.

Existing records under the known default `~/.codeplane/artifacts` store remain
downloadable. An explicitly requested update writes the new content into the
active store and updates the same record's location and size. It leaves the
old file untouched. CodePlane does not bulk-migrate historical files or trust
arbitrary persisted paths.

Session-document collection honors an explicit collector directory first,
then `COPILOT_HOME`, then the ordinary `~/.copilot` default. For an isolated
Copilot journey, set both home variables to owned fixture directories.

## Restarting an Access-only development server

Run `uv run --no-sync python tools\dev_restart.py` from the intended checkout.
The existing Cloudflare operator login must already have cached an application
JWT for the recorded tunnel origin. CodePlane retrieves that cached token
privately; it does not launch a login flow. If the cache is missing or invalid,
complete operator login separately and retry. Never place the JWT in command
arguments, environment variables, configuration or request files.

Preparation finishes before the adoption timer starts. The helper authenticates
against the old server before reporting adoption, and waits for a separate
parent commit before pausing jobs. Parent death or a closed/invalid channel
before commit leaves the server untouched. Once committed, restart continues
in the detached helper even if the initiating process exits. The replacement
retains `--access-only`; local and password restart modes are unchanged.

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

When an agent attempts an action, CodePlane combines bounded semantic assessment with authoritative local facts and evaluates its standalone AGT policy. Unknown evidence cannot become automatic permission.

### Presets

Every job runs under a **preset** that controls how aggressively actions are gated:

| Preset | Behavior |
|--------|----------|
| **`autonomous`** | Known reads and fully evidenced Git-recoverable workspace mutations may proceed. Hard gates and incomplete evidence require explicit decisions. |
| **`supervised`** | Default. Fully evidenced Git-recoverable workspace mutations require a savepoint. External mutations and incomplete evidence are gated. |
| **`locked`** | Known shell reads require a savepoint. Mutations require approval. Trusted contained native reads remain read-only. |

Set the preset globally via **Settings → Policy**, per-job in the creation form, or via the API (`PUT /api/settings/policy/preset`).

### Recommended Actions

The standalone policy returns a **recommended action**, surfaced as a badge:

| Action | Symbol | Behavior |
|--------|--------|----------|
| **allow** | ○ | Proceeds immediately — no interruption |
| **warn** | ◐ | Creates a git savepoint, then proceeds (rollback available) |
| **escalate** | ● | Sent for review before continuing |
| **deny** | ● | Blocks execution until you approve or reject |

### Approval Actions

| Action | Effect |
|--------|--------|
| **Approve** | Allow this specific action |
| **Reject** | Block it — the agent may try an alternative |
| **Historical session trust** | Retained for transition recovery, not a waiver in the final runtime |

### Overrides

Beyond the preset, **Settings → Policy** shows current controls and thresholds:

- **Approval batch window** - group concurrent routine actions needing operator review
- **Verified-USD thresholds** - editable warning/escalation lines, never applied to native cost units or missing currency. Blank disables a line and zero takes effect immediately.
- **MCP server configs** — per-server launch configuration for MCP tools

Full approval of a routine batch grants its reasons for 24 hours within that Job and policy configuration. Partial approval releases only selected actions and does not create a grant. Mandatory security gates and incomplete evidence still require individual approval. See [transition recovery](transition-retirement.md) for old pinned executions and checkpoints.

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
