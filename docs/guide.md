# Usage Guide

This page covers the core CodePlane workflow: create a job, supervise the run, handle approvals, review changes, and decide how they land.

## Creating Jobs

Jobs are the core unit of work. Each job runs an agent against a repository in an isolated Git worktree.

### Job Parameters

| Parameter | Description |
|-----------|-------------|
| **Prompt** | What you want the agent to do. Be specific — name files, describe the change, state constraints. |
| **Repository** | A registered local Git repo. Register repos in **Settings** (`Ctrl+,`). |
| **SDK** | GitHub Copilot or Claude Code. |
| **Model** | The AI model to use. Available models depend on your SDK and account. |

Press `Alt+N` or click **New Job** to open the form. Submit with **Create Job** or `Ctrl+Enter`.

### Voice Input

Click the microphone button to dictate your prompt. Audio is transcribed locally using [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — nothing leaves your machine.

---

## Monitoring Execution

Once a job starts, the detail view shows five tabs:

### Transcript

The primary monitoring view. Shows the agent's reasoning, tool calls (grouped with expandable details), operator messages you've sent, and AI-generated summaries of tool call groups.

Send messages to the agent at any time using the input box at the bottom. The agent receives your message as an operator instruction.

### Plan

The agent's planned steps with real-time status: ✅ done, 🔄 active, ⏳ pending, ⏭️ skipped.

### Logs

Structured log output with level filtering (debug, info, warning, error).

### Timeline

Visual timeline of execution phases — which are active, completed, or upcoming.

### Metrics

Token usage, estimated cost, LLM and tool call counts, cache hit rate, and context utilization.

### When to Intervene

- If the transcript shows repetitive actions, the agent may be stuck — send a message or cancel.
- If the plan isn't progressing, check logs for errors.
- If costs are climbing fast, check metrics to see if the agent is thrashing.

---

## Approvals

When an agent attempts a risky action, CodePlane can pause execution and ask for your approval.

### Permission Modes

| Mode | Behavior |
|------|----------|
| `auto` | SDK handles permissions automatically (default) |
| `read_only` | Agent can only read files; all writes require approval |
| `approval_required` | File writes, shell commands, and network access require your approval |

Set the mode in `~/.codeplane/config.yaml` or per-repo in `.codeplane.yml`:

```yaml
agent:
  permission_mode: approval_required
```

### Approval Actions

| Action | Effect |
|--------|--------|
| **Approve** | Allow this specific action |
| **Reject** | Block it — the agent may try an alternative |
| **Trust Session** | Auto-approve all remaining actions for this job |

### Hard-Gated Commands

Some commands **always** require approval regardless of mode: `git merge`, `git pull`, `git rebase`, `git cherry-pick`, and `git reset --hard`.

---

## Code Review

The **Diff** tab shows all files modified by the agent with syntax-highlighted, side-by-side diffs. Diffs update in real time as the agent works.

The **Workspace** view lets you browse the full file tree — not just changed files. This is useful for checking context or verifying overall structure.

---

## Merging & Resolution

When a job completes, it enters the **review** state. You decide how to land the changes:

| Option | Description |
|--------|-------------|
| **Merge** | Standard Git merge of the worktree branch into the base branch |
| **Smart Merge** | Cherry-pick only the agent's meaningful commits, skipping setup noise |
| **Create PR** | Push the branch and open a pull request for team review |
| **Discard** | Delete the worktree and throw away all changes |

If a merge encounters conflicts, CodePlane shows the conflicting files. You can resolve them in the built-in terminal, discard, or create a PR instead.

After resolution, the job moves to `completed`. Archive it to move it to history and keep the dashboard clean.

### Follow-Up Jobs

From a job in the `review` state, create a **follow-up job** that continues in the same worktree with a new prompt. This lets you iterate without starting over.

---

## Additional Features

### Terminal

Press `` Ctrl+` `` to open the integrated terminal. Supports multiple tabs — global terminals or job-specific terminals rooted in the worktree.

### Remote Access

Access CodePlane from your phone or another device:

```bash
cpl up --remote                              # Dev Tunnels (default)
cpl up --remote --provider cloudflare        # Cloudflare Tunnels
```

The UI is fully responsive on mobile — monitor jobs, approve actions, and send messages from anywhere.

### Command Palette

Press `⌘K` / `Ctrl+K` to search and navigate jobs by ID, title, repository, or branch.

### History

Archived jobs are browsable from the History page. Search, sort, and click into any past job to see its full transcript, diffs, and metrics.

### Analytics

Press `Alt+A` to open the analytics dashboard — aggregate costs, token usage, model breakdown, tool health, and per-repo spending across all jobs.

### MCP Server

CodePlane exposes an [MCP](https://modelcontextprotocol.io/) server at `http://localhost:8080/mcp`. External agents can create jobs, handle approvals, browse workspaces, and manage repositories programmatically.

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
