<p align="center">
  <img src="docs/images/logo.png" alt="CodePlane" width="200" />
</p>

<h1 align="center">CodePlane</h1>

<p align="center">
  <strong>The control plane for AI coding agents</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/status-pre--alpha-orange" alt="Status: Pre-alpha">
  <img src="https://img.shields.io/badge/python-≥3.11-blue" alt="Python ≥3.11">
  <a href="https://github.com/dfinson/codeplane/actions/workflows/ci.yml"><img src="https://github.com/dfinson/codeplane/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://codecov.io/gh/dfinson/codeplane"><img src="https://codecov.io/gh/dfinson/codeplane/branch/main/graph/badge.svg" alt="Coverage"></a>
  <img src="https://img.shields.io/github/license/dfinson/codeplane" alt="License">
</p>

---

> **Pre-alpha** — Under active development.

CodePlane wraps Claude Code and GitHub Copilot with a fleet dashboard and its own AI that watches while your agents work, catching stalls, tracking progress, and scoring the risk of every code change. When an agent finishes, you get a narrative explaining what changed and why, not just a diff. Run multiple agents from a kanban board, chat mid-run, approve from your phone. Or keep using your agents the way you already do and CodePlane picks them up automatically.

<!-- PLACEHOLDER: Hero Dashboard Screenshot -->

## Two Ways to Use It

### 1. Launch jobs through CodePlane (headless orchestration)

Write a prompt, pick a repo and model, hit go. The agent runs in an isolated worktree. When it finishes, you get a structured review — merge or discard.

### 2. Mirror your native CLI sessions (zero workflow change)

Keep using `claude` or `copilot` in your terminal exactly as you do now. CodePlane auto-discovers running sessions via file-tailing, ingests them into the same pipeline, and gives you the full dashboard — cost tracking, trail, narrative review — without you changing anything.

| Agent | Managed (headless) | Mirrored (native CLI) |
|-------|-------------------|-----------------------|
| Claude Code | Yes | Yes |
| GitHub Copilot | Yes | Yes |

## What You Get

### Supervise

- Start a task, walk away. Approve risky actions from your phone
- Remote access via Dev Tunnels or Cloudflare — supervise from anywhere
- Live transcripts, plan progress, and running cost as the agent works
- Standalone action policy with bound per-action approvals, verified-USD thresholds, recovery witnesses and protected-path gates

### Review as a Story

- **Narrative review** — answers "why was this change made?" with verified references traced back through the decision trail
- **Decision trail** — a structured intent graph of what the agent did, why, and where it backtracked — enriched by parallel LLM analysis

### Analyze

- **Cost attribution** — every token and dollar attributed by activity, model, file, and phase
- **Waste detection** — rework, rereads, retry storms, cost escalation surfaced across jobs
- **Fleet analytics** — cross-job observations that persist so your 50th job is more valuable than your first
- **Workspace memory** — per-repo knowledge that carries forward so each job starts smarter

## What’s Different

Other tools give you a dashboard for one agent session. CodePlane builds a **normalized intelligence layer** underneath:

- **Provenance data layer** — every session (managed or mirrored) normalized into a local SQLite database with precomputed cost per span, per file, per phase. Query it with `sqlite3` or point a notebook at it
- **Sidecar intelligence** — parallel LLM sessions that observe and intervene in real time: stall recovery, plan inference, enrichment, custom sidecars in plain English
- **Action policy engine** - bounded evidence, protected-path gates, verified-USD thresholds and exact per-action decisions above the SDK's binary allow/deny
- **MCP server** — CodePlane *exposes itself as* an MCP server. External agents can delegate coding tasks and monitor them programmatically

## Quick Start

> Requires Python 3.11+ and Git. Install and authenticate at least one agent CLI: [Claude Code](https://docs.anthropic.com/en/docs/claude-code) or [GitHub Copilot CLI](https://docs.github.com/en/copilot/managing-copilot/configure-personal-settings/using-github-copilot-in-the-cli).

```bash
pip install codeplane
cpl up                        # start server on localhost:8080
```

Open `http://localhost:8080`. Any native CLI sessions running in registered repos are picked up automatically. Or create a new job from the UI.

## CLI

```bash
cpl up                                       # start server
cpl up --remote                              # enable Dev Tunnels for remote access
cpl up --phone                               # shortcut for --remote
cpl up --remote --provider cloudflare        # use Cloudflare Tunnel
cpl up --port 9090                           # custom port
cpl down                                     # stop server
cpl restart                                  # stop and restart
cpl setup                                    # interactive first-time setup
cpl doctor                                   # diagnose environment issues
cpl info                                     # show connection details and QR code
cpl version                                  # show version
```

## Who This Is For

- **Solo devs using Claude Code or Copilot CLI** who want cost visibility and review tools without changing their workflow
- **Teams running many agent sessions** who need cost forensics and behavioral pattern analysis, not just a billing page
- **Reviewers of agent output** who need a navigable diff and an evidence-based narrative explanation, not just a raw diff
- **Regulated environments** where AI-generated code requires decision provenance and an audit trail

## Documentation

Full docs: [dfinson.github.io/codeplane](https://dfinson.github.io/codeplane)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, testing, and conventions.

## License

[MIT](LICENSE)
