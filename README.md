<h1 align="center">Tower</h1>

<p align="center">
  <strong>Control tower for running and supervising coding agents</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/status-pre--alpha-orange" alt="Status: Pre-alpha">
  <img src="https://img.shields.io/badge/python-≥3.11-blue" alt="Python ≥3.11">
  <img src="https://img.shields.io/badge/node-≥20-green" alt="Node ≥20">
  <a href="https://github.com/dfinson/agent-tower/actions/workflows/ci.yml"><img src="https://github.com/dfinson/agent-tower/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://codecov.io/gh/dfinson/agent-tower"><img src="https://codecov.io/gh/dfinson/agent-tower/branch/main/graph/badge.svg" alt="Coverage"></a>
  <img src="https://img.shields.io/github/license/dfinson/agent-tower" alt="License">
</p>

---

> **Pre-alpha** — Under active development. Not yet usable.

Tower is a control tower for running and supervising coding agents.

Launch automated coding tasks against real repositories, watch everything the agent does in real time, and intervene when needed. Tower gives you visibility into execution progress, code changes, logs, artifacts, and agent reasoning so work can be reviewed and controlled as it happens.

## Features (Planned)

- **Job orchestration** — Launch coding tasks against local repositories
- **Live monitoring** — Watch agent reasoning, logs, and code changes as they happen
- **Approval gating** — Intercept and approve or reject risky actions before they execute
- **Operator intervention** — Send messages, cancel, or rerun jobs at any time
- **Workspace isolation** — Git worktrees for concurrent job execution
- **Remote access** — Dev Tunnel exposes the UI over HTTPS for phone/remote control
- **Voice input** — Speak prompts and instructions into the browser
- **PR creation** — Automatically opens a pull request on success (via `gh` CLI or GitHub MCP)

## Architecture

```
React + TypeScript (Vite)  ──REST/SSE──▶  FastAPI (Python)
                                            ├── SQLite
                                            ├── Git worktrees
                                            └── Copilot SDK
```

## Quick Start

> Requires Python 3.11+ and Node.js 20+

```bash
# Backend
uv sync
uv run tower up --dev

# Frontend (separate terminal)
cd frontend
npm install
npm run dev
```

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and guidelines.

See [ROADMAP.md](ROADMAP.md) for the implementation plan.

See [SPEC.md](SPEC.md) for the full product specification.

## License

See [LICENSE](LICENSE).
