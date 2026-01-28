<p align="center">
  <img src="docs/images/logo.png" alt="CodePlane Logo" width="200">
</p>

<h1 align="center">CodePlane</h1>

<p align="center">
  <strong>Local repository control plane for AI coding agents</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/status-pre--alpha-orange" alt="Status: Pre-alpha">
  <img src="https://img.shields.io/badge/python-≥3.12-blue" alt="Python ≥3.12">
  <a href="https://codecov.io/gh/dfinson/codeplane"><img src="https://codecov.io/gh/dfinson/codeplane/branch/main/graph/badge.svg" alt="Coverage"></a>
  <img src="https://img.shields.io/github/license/dfinson/codeplane" alt="License">
</p>

---

CodePlane executes safe, deterministic operations for AI coding agents. It owns indexing, refactoring, testing, and file mutations—applying changes atomically and returning structured, auditable results so agents can act without probing or guesswork.

## Status

🚧 **Pre-alpha** — M0 + M1 complete, M2 in progress.

Core infrastructure and Git operations are implemented. See the [roadmap](#roadmap) for progress.

## Vision

AI coding agents need reliable infrastructure:

- **Atomic mutations** — Changes succeed completely or roll back entirely
- **Fast code search** — Sub-second queries across the codebase  
- **Semantic refactoring** — Rename, extract, move with full reference updates
- **Structured test results** — Normalized output from any test framework
- **Operation history** — Full audit trail for debugging and convergence tracking

CodePlane provides this via MCP (Model Context Protocol) tools, exposing capabilities as a local HTTP daemon.

## Roadmap

Track progress via [GitHub Milestones](https://github.com/dfinson/codeplane/milestones):

| Milestone | Description | Status |
|-----------|-------------|--------|
| M0: Foundation | Core types, errors, logging, configuration | ✅ Complete |
| M1: Git Operations | Status, staging, commits, branches, diffs | ✅ Complete |
| M2: Index Engine | Full-text search, symbol lookup, relationships | 🚧 In Progress |
| M3: Mutation Engine | Atomic file changes with rollback | |
| M4: Ledger & Task Model | Operation history, convergence metrics | |
| M5: Daemon & CLI | HTTP daemon, `cpl` CLI commands | |
| M6: Core MCP Tools | File ops, search, git tools for agents | |
| M7: Test Runner | Framework detection, parallel execution | |
| M8: LSP & Refactor | Rename, extract, inline, move via LSP | |
| M9: Polish & Hardening | Docs, benchmarks, security, packaging | |

## Development

```bash
make dev         # Install with dev dependencies
make lint        # Run ruff linter
make typecheck   # Run mypy
make test        # Run pytest
```

## License

[MIT](LICENSE)
