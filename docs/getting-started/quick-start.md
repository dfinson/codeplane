# Quick Start

Get your first coding job running in under 5 minutes.

## 1. Start the Server

```bash
cpl up
```

Or from a cloned repo with the frontend built:

```bash
make run
```

The server starts on `http://localhost:8080`. Open it in your browser.

!!! tip "Development Mode"
    For backend-only work, skip the frontend build:
    ```bash
    cpl up --dev
    ```

## 2. Register a Repository

Navigate to **Settings** (`Ctrl+,`) and add a repository:

1. Click **Add Repository**
2. Enter the path to a local Git repository
3. The repo appears in your repo list

<div class="screenshot-desktop" markdown>
![Settings Page](../images/screenshots/desktop/settings-page.png)
</div>

## 3. Create a Job

Press `Alt+N` or click **New Job** to open the job creation form.

<div class="screenshot-desktop" markdown>
![Job Creation](../images/screenshots/desktop/job-creation.png)
</div>

1. **Write a prompt** — Describe the coding task (e.g., "Add input validation to the user registration endpoint")
2. **Select a repository** — Choose the repo to work against
3. **Choose an SDK** — Select Copilot or Claude
4. **Pick a model** — Choose the AI model to use
5. Click **Create Job**

<div class="screenshot-desktop" markdown>
![Job Creation Filled](../images/screenshots/desktop/job-creation-filled.png)
</div>

## 4. Monitor Execution

The job detail view opens automatically. Watch the agent work in real time:

- **Transcript** — See the agent's reasoning and tool calls
- **Logs** — Structured log output with level filtering
- **Timeline** — Visual progress through execution phases
- **Plan** — The agent's planned steps and current progress

<div class="screenshot-desktop" markdown>
![Running Job Transcript](../images/screenshots/desktop/job-running-transcript.png)
</div>

## 5. Handle Approvals

If the agent tries to perform a risky action, you'll see an approval banner:

<div class="screenshot-desktop" markdown>
![Approval Banner](../images/screenshots/desktop/approval-banner.png)
</div>

Click **Approve** to let it proceed, **Reject** to block, or **Trust Session** to auto-approve all remaining requests for this job.

## 6. Review & Merge

When the job completes, review the changes in the diff viewer:

<div class="screenshot-desktop" markdown>
![Diff Viewer](../images/screenshots/desktop/job-diff-viewer.png)
</div>

Then choose a resolution:

- **Merge** — Merge the worktree branch into the base branch
- **Smart Merge** — Cherry-pick only the agent's commits
- **Create PR** — Open a pull request for team review
- **Discard** — Delete the worktree and discard changes

<div class="screenshot-desktop" markdown>
![Complete Job Dialog](../images/screenshots/desktop/complete-job-dialog.png)
</div>

## What's Next?

- [User Guide](../guide/index.md) — Deep dive into every feature
- [Configuration](configuration.md) — Customize CodePlane for your workflow
- [CLI Reference](../reference/cli.md) — All CLI commands and options
