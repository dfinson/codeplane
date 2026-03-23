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

<p align="center"><img src="docs/images/screenshots/desktop/hero-dashboard.png" alt="CodePlane — dashboard with active jobs" width="800" /></p>

## Features

- **Job orchestration** — Launch coding tasks against local repositories with prompt, model, and SDK selection
- **Live monitoring** — Watch agent reasoning, logs, timeline, metrics, and code changes as they happen
- **Approval gating** — Intercept and approve or reject risky actions before they execute
- **Operator intervention** — Send messages, cancel, pause, resume, or rerun jobs at any time
- **Workspace isolation** — Git worktrees for concurrent job execution
- **Code review** — Syntax-highlighted diff viewer and workspace file browser
- **Merge & PR** — Merge, smart merge, or create a pull request on job completion
- **Remote access** — Dev Tunnels exposes the UI over HTTPS for phone/remote control
- **Voice input** — Speak prompts and instructions into the browser (local Whisper transcription)
- **Terminal sessions** — Integrated terminal with multi-tab support
- **Command palette** — Quick navigation and job search with ⌘K / Ctrl+K
- **Telemetry & metrics** — Token usage, costs, and execution metrics per job
- **Job history** — Archive and browse completed jobs
- **Agent plan tracking** — Visualize the agent's planned steps and progress
- **Multi-SDK support** — Works with GitHub Copilot and Claude Code SDKs
- **MCP server** — Expose CodePlane as MCP tools for agent-to-agent orchestration

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                    Operator Browser                      │
│              React + TypeScript Frontend                 │
│          REST (commands/queries) + SSE (live)            │
└────────────────────────┬─────────────────────────────────┘
                         │ HTTP / SSE / WebSocket
┌────────────────────────▼─────────────────────────────────┐
│               FastAPI Backend (Python)                   │
│  REST API · SSE · Job orchestration · MCP server         │
│  Git service · Agent adapters · Approvals · Terminal     │
│  Voice transcription · Telemetry · Merge service         │
└────┬──────────┬──────────┬──────────┬──────────┬─────────┘
     │          │          │          │          │
┌────▼───┐ ┌───▼────┐ ┌───▼─────┐ ┌─▼──────┐ ┌─▼───────┐
│ SQLite │ │  Git   │ │Copilot  │ │ Claude │ │ Whisper │
│   DB   │ │  repos │ │  SDK    │ │  SDK   │ │ (local) │
└────────┘ └────────┘ └─────────┘ └────────┘ └─────────┘
```

## Quick Start

> Requires Python 3.11+, Node.js 20+, and [uv](https://docs.astral.sh/uv/).

```bash
make install                  # install backend + frontend dependencies
cp .env.sample .env           # optional: set CPL_DEVTUNNEL_PASSWORD
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

## CLI

```bash
uv run cpl up                           # start server on localhost:8080
uv run cpl up --remote                  # enable Dev Tunnels for remote access
uv run cpl up --dev                     # backend-only (skip frontend build)
uv run cpl up --port 9090               # custom port
uv run cpl up --remote --password SECRET # tunnel password
uv run cpl version                      # show version
uv run cpl setup                        # interactive first-time setup
uv run cpl doctor                       # diagnose environment issues
```

## Development

```bash
make lint        # ruff check + eslint
make format      # ruff format
make typecheck   # mypy + tsc
make test        # pytest + vitest with coverage (70% backend threshold)
make ci          # all of the above
```

The CI pipeline also runs Playwright E2E tests against the full stack.

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Alt+N` | New job |
| `⌘K` / `Ctrl+K` | Command palette |
| `⌘,` / `Ctrl+,` | Settings |
| ``Ctrl+` `` | Toggle terminal |
| `Ctrl+Enter` | Submit prompt |
| `/` | Filter jobs (on dashboard) |

See [CONTRIBUTING.md](CONTRIBUTING.md) for conventions and project structure.

See [SPEC.md](SPEC.md) for the full product specification.

## License

[MIT](LICENSE)
