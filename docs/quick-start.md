# Quick Start

Get CodePlane running and complete your first supervised agent job.

## Prerequisites

| Requirement | Details |
|-------------|---------|
| **Python** | 3.11 or later |
| **Git** | Any recent version |
| **Agent SDK** | At least one: [GitHub Copilot CLI](https://docs.github.com/en/copilot) or [Claude Code](https://docs.anthropic.com/en/docs/agents-and-tools/claude-code/overview) |

You also need a **local Git repository** to run jobs against.

## Install

```bash
pip install codeplane
```

Verify the installation:

```bash
cpl doctor
```

This checks that Python, Git, and your agent SDKs are available.

## Start the Server

```bash
cpl up
```

Open `http://localhost:8080` in your browser.

!!! tip "First-time setup"
    Run `cpl setup` for an interactive walkthrough that registers your first repository and sets SDK preferences.

## Register a Repository

Go to **Settings** (`Ctrl+,`) and add a local Git repository path. This tells CodePlane which codebases it can work against.

## Create Your First Job

1. Press `Alt+N` or click **New Job**
2. Write a prompt — e.g., *"Add input validation to the user registration endpoint"*
3. Select the repository, SDK, and model
4. Click **Create Job**

The agent starts working in an isolated Git worktree. Your working directory is never modified.

## Watch It Run

The job detail view shows live updates:

- **Transcript** — the agent's reasoning and tool calls
- **Plan** — the agent's planned steps and progress
- **Logs** — structured output with level filtering
- **Metrics** — token usage and estimated cost

<div class="screenshot-desktop" markdown>
![Running Job](images/screenshots/desktop/job-running-transcript.png)
</div>

## Handle Approvals

If the agent attempts a risky action (file writes, shell commands), you'll see an approval prompt. Choose:

- **Approve** — allow this action
- **Reject** — block it
- **Trust Session** — auto-approve all remaining actions for this job

## Review & Merge

When the agent finishes, review the diff:

<div class="screenshot-desktop" markdown>
![Diff Viewer](images/screenshots/desktop/job-diff-viewer.png)
</div>

Then resolve the job:

| Option | What it does |
|--------|-------------|
| **Merge** | Merge the worktree branch into your base branch |
| **Smart Merge** | Cherry-pick only the agent's commits (skips setup noise) |
| **Create PR** | Push the branch and open a pull request |
| **Discard** | Delete the worktree and discard all changes |

## What's Next

- [Usage Guide](guide.md) — the full workflow in detail
- [Configuration](configuration.md) — SDK defaults, permission modes, tunnels
- [CLI Reference](reference/cli.md) — all `cpl` commands
