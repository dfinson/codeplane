<p align="center">
  <img src="docs/images/logo.png" alt="CodePlane" width="200" />
</p>

<h1 align="center">CodePlane</h1>

<p align="center">
  <strong>Control plane for running and supervising coding agents</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/status-pre--alpha-orange" alt="Status: Pre-alpha">
  <img src="https://img.shields.io/badge/python-≥3.11-blue" alt="Python ≥3.11">
  <img src="https://img.shields.io/badge/node-≥20-green" alt="Node ≥20">
  <a href="https://github.com/dfinson/codeplane/actions/workflows/ci.yml"><img src="https://github.com/dfinson/codeplane/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://codecov.io/gh/dfinson/codeplane"><img src="https://codecov.io/gh/dfinson/codeplane/branch/main/graph/badge.svg" alt="Coverage"></a>
  <img src="https://img.shields.io/github/license/dfinson/codeplane" alt="License">
</p>

---

> **Pre-alpha** — Under active development. Not yet usable.

CodePlane is a control plane for running and supervising coding agents.

Launch automated coding tasks against real repositories, watch everything the agent does in real time, and intervene when needed. CodePlane gives you visibility into execution progress, code changes, logs, artifacts, and agent reasoning so work can be reviewed and controlled as it happens.

<!-- SCREENSHOT: Desktop hero — job in "running" state
     Capture: Full browser window (1280×800) showing the main dashboard with
     an active job. The transcript panel should show a mix of tool calls and
     assistant messages, a visible progress headline, and ideally an approval
     prompt waiting for operator input. Crop browser chrome.
     Save as: docs/images/screenshot-desktop-running.png
     Then uncomment the img tag below.
-->
<!-- <p align="center"><img src="docs/images/screenshot-desktop-running.png" alt="CodePlane — desktop UI with a running job" width="800" /></p> -->

<!-- SCREENSHOT: Mobile — job list
     Capture: Use Chrome DevTools device toolbar (iPhone 14 Pro, 393×852).
     Navigate to the job list with 2-3 jobs in different states (running,
     completed, failed). Take a full-page screenshot.
     Save as: docs/images/screenshot-mobile-joblist.png
     Then uncomment the img tag below.
-->

<!-- SCREENSHOT: Desktop — diff view
     Capture: Full browser window showing a completed job's diff tab in the
     Monaco editor. Pick a job with meaningful multi-file changes so the
     file list sidebar and diff content are both visible.
     Save as: docs/images/screenshot-desktop-diff.png
     Then uncomment the img tag below.
-->

<!-- When screenshots are ready, replace the commented img tags above with:
<p align="center">
  <img src="docs/images/screenshot-desktop-running.png" alt="Desktop — running job" width="800" />
</p>
<p align="center">
  <img src="docs/images/screenshot-mobile-joblist.png" alt="Mobile — job list" width="300" />
  <img src="docs/images/screenshot-desktop-diff.png" alt="Desktop — diff view" width="500" />
</p>
-->

## Features

- **Job orchestration** — Launch coding tasks against local repositories
- **Live monitoring** — Watch agent reasoning, logs, and code changes as they happen
- **Approval gating** — Intercept and approve or reject risky actions before they execute
- **Operator intervention** — Send messages, cancel, or rerun jobs at any time
- **Workspace isolation** — Git worktrees for concurrent job execution
- **Remote access** — Dev Tunnels exposes the UI over HTTPS for phone/remote control
- **Voice input** — Speak prompts and instructions into the browser
- **Merge & PR** — Auto-merge or create a pull request on job completion
- **MCP server** — Expose CodePlane as MCP tools for agent-to-agent orchestration

## Architecture

```
┌──────────────────────────────────────────────┐
│              Operator Browser                │
│        React + TypeScript Frontend           │
│     REST (commands/queries) + SSE (live)     │
└──────────────────┬───────────────────────────┘
                   │ HTTP / SSE
┌──────────────────▼───────────────────────────┐
│           FastAPI Backend (Python)           │
│  REST API · SSE · Job orchestration · MCP    │
│  Git worktrees · Agent adapter · Approvals   │
└──────┬──────────────┬──────────────┬─────────┘
       │              │              │
  ┌────▼────┐   ┌─────▼─────┐  ┌───▼────┐
  │ SQLite  │   │ Git repos │  │Copilot │
  │   DB    │   │/worktrees │  │  SDK   │
  └─────────┘   └───────────┘  └────────┘
```

## Quick Start

> Requires Python 3.11+, Node.js 20+, and [uv](https://docs.astral.sh/uv/).

```bash
make install                  # install backend + frontend dependencies
cp .env.sample .env           # optional: set CPL_TUNNEL_PASSWORD
make run                      # build frontend, start server with remote access
```

Or step by step:

```bash
uv sync                       # install backend dependencies
cd frontend && npm ci && cd ..
uv run cpl up                 # start server (localhost:8080)
uv run cpl up --remote        # start with remote access via Dev Tunnels
uv run cpl up --dev           # skip frontend build (backend-only work)
```

## Development

```bash
make lint        # ruff check + eslint
make format      # ruff format
make typecheck   # mypy + tsc
make test        # pytest + vitest with coverage
make ci          # all of the above
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for conventions and project structure.

See [SPEC.md](SPEC.md) for the full product specification.

## License

[MIT](LICENSE)
