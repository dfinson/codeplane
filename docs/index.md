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

<span class="eyebrow">The control plane for AI coding agents.</span>

**Run any agent from anywhere. Own the data. Review with intelligence.**

CodePlane wraps Claude Code and GitHub Copilot with a fleet dashboard and its own AI that watches while your agents work, catching stalls, tracking progress, and scoring the risk of every code change. When an agent finishes, you get a narrative explaining what changed and why, not just a diff. Run multiple agents from a kanban board, chat mid-run, approve from your phone. Or keep using your agents the way you already do and CodePlane picks them up automatically.

<div class="hero-actions" markdown>
[Quick Start](quick-start.md){ .md-button .md-button--primary }
[Usage Guide](guide.md){ .md-button }
[Architecture](architecture.md){ .md-button }
</div>

<p class="works-with">Works with <strong>Claude Code CLI</strong> and <strong>GitHub Copilot CLI</strong> &nbsp;·&nbsp; Open source, MIT license</p>

</div>

<!-- PLACEHOLDER: CodePlane Dashboard -->

<!-- PLACEHOLDER: CodePlane Dashboard — Mobile -->

## Two Modes, One Pipeline

<div class="workflow-grid" markdown>

<div class="workflow-step" markdown>
<span class="step-index">A</span>
### Launch through CodePlane
Write a prompt, pick a repo and model. The agent runs headless in an isolated worktree. When it finishes, you get a structured review — approve from your phone or desktop.
</div>

<div class="workflow-step" markdown>
<span class="step-index">B</span>
### Mirror your native sessions
Run `claude` or `copilot` in your terminal as usual. CodePlane auto-discovers the session via file-tailing and ingests it — full dashboard, cost tracking, and trail enrichment with zero workflow change.
</div>

</div>

Either way, you get the same intelligence pipeline: trail enrichment, cost attribution, narrative story, and workspace memory.

| Agent | Managed (headless) | Mirrored (native CLI) |
|-------|-------------------|-----------------------|
| Claude Code | Yes | Yes |
| GitHub Copilot | Yes | Yes |

External agents can also orchestrate CodePlane programmatically through its built-in [MCP server](mcp-server.md).

## The Experience

<div class="feature-grid" markdown>

<div class="feature-card" markdown>
### :material-play-circle: Supervise

Launch headless jobs from a dashboard or discover native CLI sessions automatically. Watch live transcripts, plan progress, and cost as the agent works. Gate risky actions for approval — from your phone, tablet, or desktop. Push notifications when the agent needs you.

<!-- PLACEHOLDER: Live supervision -->

<!-- PLACEHOLDER: Live supervision — Mobile -->

</div>

<div class="feature-card" markdown>
### :material-book-open-variant: Review as a Story

When the agent finishes, you don't get a raw diff. You get a narrative that explains what changed and why — with verified file references traced back through the decision trail. Inspect the actual diff alongside the recorded decisions and motivations before choosing the next action.

<!-- PLACEHOLDER: Narrative review -->

<!-- PLACEHOLDER: Narrative review — Mobile -->

</div>

<div class="feature-card" markdown>
### :material-chart-line: Analyze

Every token, tool call, and dollar — attributed by activity, model, file, and phase. Waste patterns surface across jobs: rework, rereads, retry storms, cost escalation. The analysis compounds — your 50th job is more valuable than your first because the system has learned what your codebase costs.

<!-- PLACEHOLDER: Analytics dashboard -->

<!-- PLACEHOLDER: Analytics dashboard — Mobile -->

</div>

</div>

## What's Underneath

Behind the dashboard, CodePlane builds a normalized intelligence layer that no agent CLI provides on its own.

<div class="feature-grid" markdown>

<div class="feature-card" markdown>
### :material-database: Provenance Data Layer
Every agent session — managed or mirrored — is normalized into a local SQLite database with precomputed cost per span, per file, per phase. Token counts, retry tracking, approval audit trails, intent graphs, and cross-job observations, all in relational tables. Query it with `sqlite3`, point a notebook at it, or let another agent analyze your agent history. The database is a standalone asset even if you never open the UI.
</div>

<div class="feature-card" markdown>
### :material-brain: Sidecar Intelligence
CodePlane runs parallel LLM sessions alongside the agent — not hooks that fire after the fact, but persistent reasoning that observes and intervenes in real time. A stall detector that autonomously recovers stuck agents. A planner that infers the agent's strategy. An enricher that annotates what's happening as it happens. Custom sidecars you define in plain English.
</div>

<div class="feature-card" markdown>
### :material-graph: Decision Trail
Not a transcript — a structured intent graph. Each action is recorded as a trail node capturing what the agent did, why, and where it changed course. Nodes are enriched with rationale, purpose, and edit motivations by parallel LLM analysis. The trail feeds the narrative review and powers post-hoc debugging of agent behavior.
</div>

<div class="feature-card" markdown>
### :material-lock-check: Action Policy Engine
The agent SDKs give binary allow/deny. CodePlane adds bound decisions, protected-path gates, recovery witnesses, configured soft verified-USD controls and immutable approval batches. Routine grants are scoped to the Job, policy and reason for 24 hours. Historical TF grants remain preserved data, not silently converted waivers.
</div>

<div class="feature-card" markdown>
### :material-swap-horizontal: MCP Server
Agents use MCP tools. CodePlane *exposes itself as* an MCP server — 7 tools for job orchestration, approval handling, workspace browsing, and repo management. External agents can delegate coding tasks to CodePlane and monitor them programmatically. Agent-to-agent orchestration through a control plane.
</div>

</div>

## Who This Is For

- **Solo devs using Claude Code or Copilot CLI** who want cost visibility and review tools without changing their workflow
- **Teams running many agent sessions** who need cost forensics and behavioral pattern analysis across jobs, not just a billing page
- **Reviewers of agent output** who need a navigable diff and an evidence-based narrative explanation, not just a raw diff
- **Regulated environments** where AI-generated code changes require decision provenance and an audit trail
