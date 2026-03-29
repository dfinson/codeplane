---
hide:
  - navigation
  - toc
---

<div class="hero" markdown>

<p align="center" markdown>
![CodePlane](images/logo.png){ width="180" }
</p>

# CodePlane

<span class="eyebrow">Control plane for coding agents</span>

**Launch coding tasks, supervise execution in real time, and control how changes land.**

CodePlane orchestrates coding agents against your repositories. You see every action the agent takes — transcript, diffs, costs — and decide what gets merged.

<div class="hero-actions" markdown>
[Quick Start](quick-start.md){ .md-button .md-button--primary }
[Usage Guide](guide.md){ .md-button }
[How It Works](architecture.md){ .md-button }
</div>

</div>

<div class="screenshot-desktop" markdown>
![CodePlane Dashboard](images/screenshots/desktop/hero-dashboard.png)
</div>

## The Core Loop

<div class="workflow-grid" markdown>

<div class="workflow-step" markdown>
<span class="step-index">1</span>
### Launch a task
Pick a repository, write a prompt, choose an SDK and model. The agent runs in an isolated Git worktree — your working directory is never touched.
</div>

<div class="workflow-step" markdown>
<span class="step-index">2</span>
### Supervise the run
Watch the transcript, logs, plan progress, and cost data while the agent works. Send messages to steer it if needed.
</div>

<div class="workflow-step" markdown>
<span class="step-index">3</span>
### Gate risky actions
File writes, shell commands, and destructive operations can require your approval before they execute.
</div>

<div class="workflow-step" markdown>
<span class="step-index">4</span>
### Land or discard
Review the diff, then merge, create a PR, or discard — based on what the agent actually produced.
</div>

</div>

## What You Get

<div class="feature-grid" markdown>

<div class="feature-card" markdown>
### :material-play-circle: Task Orchestration
Launch jobs with a prompt and model selection. Each job runs in its own Git worktree for safe, concurrent execution.
</div>

<div class="feature-card" markdown>
### :material-monitor-eye: Live Visibility
Transcript, logs, timeline, plan steps, and token costs — all streaming in real time as the agent works.
</div>

<div class="feature-card" markdown>
### :material-shield-check: Approval Gates
Risky operations pause for your review. Approve, reject, or trust the session to auto-approve the rest.
</div>

<div class="feature-card" markdown>
### :material-code-tags: Diff Review & Merge
Syntax-highlighted diffs, workspace browsing, and merge/PR/discard controls — all built in.
</div>

<div class="feature-card" markdown>
### :material-cellphone-link: Remote Access
Access the UI from your phone via Dev Tunnels or Cloudflare Tunnels. Approve actions and monitor jobs from anywhere.
</div>

<div class="feature-card" markdown>
### :material-chart-line: Cost Analytics
Track token usage, costs, model performance, and tool health across all jobs.
</div>

</div>

## Supported SDKs

CodePlane works with **GitHub Copilot** and **Claude Code** SDKs. Select your SDK and model per job — CodePlane handles the rest.

<div class="screenshot-desktop" markdown>
![Live Transcript](images/screenshots/desktop/job-running-transcript.png)
</div>
