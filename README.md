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

## The Problem

AI coding agents lose a **lot** of time and waste a lot of token cosumption on tasks that should be much closer to instant.

- **Exploratory thrash** — repeated grep, file opens, and retries to build a mental model
- **Terminal mediation** — deterministic operations (git status, diff, run test) produce unstructured text and loops
- **Missing deterministic refactors** — renames that IDEs do in seconds take agents minutes via search-and-edit

The bottleneck is I/O and orchestration, not model capability.

## The Solution

CodePlane turns a repository into a **deterministic, queryable system**:

```
Agent plans and decides → CodePlane executes → Structured result → Next action
```

Every operation returns complete, structured context in a single call. No probing. No guesswork.

## Architecture

CodePlane provides a **full stacked index**:

- **Tier 0 — Tantivy Lexical Index**: Fast, deterministic lexical retrieval for candidate discovery
- **Tier 1 — Tree-sitter/SQLite Structural Facts**: Definitions, references, scopes, imports, exports

The planner produces:
- Bounded candidate sets
- Patch previews with text edits
- Coverage + Risk manifests (explicit about what is PROVEN vs ANCHORED)

## Status

🚧 **Pre-alpha** — M0 + M1 complete, M2 (Index Engine) in progress.

Core infrastructure and Git operations are implemented. See the [roadmap](#roadmap) for progress.

## Roadmap

Track progress via [GitHub Milestones](https://github.com/dfinson/codeplane/milestones):

| Milestone | Description | Status |
|-----------|-------------|--------|
| **M0** | Foundation: Core types, errors, logging, configuration | ✅ |
| **M1** | Git Operations: Status, staging, commits, branches, diffs | ✅ |
| **M2** | Index Engine: Tantivy lexical + Tree-sitter/SQLite structural facts | 🚧 |
| **M3** | Refactor Planner: Bounded candidate sets with coverage/risk manifests | |
| **M4** | Mutation Engine: Atomic file changes with rollback | |
| **M5** | Ledger & Task Model: Operation history, convergence metrics | |
| **M6** | Daemon & CLI: HTTP daemon, `cpl` CLI commands | |
| **M7** | Core MCP Tools: File ops, search, git tools for agents | |
| **M8** | Test Runner: Framework detection, parallel execution | |
| **M9** | Polish & Hardening: Docs, benchmarks, security, packaging | |
| **M10** | Advanced Semantic Support (SCIP/LSP analysis) | 🔬 |

## Quick Start

```bash
make dev         # Install with dev deps
make test        # Run tests
make lint        # Ruff
make typecheck   # Mypy
```

## Design Authority

[SPEC.md](SPEC.md) is the single source of truth. Key sections:

- §7: Index Architecture (Tier 0 + Tier 1)
- §19: Semantic Support Exploration (design archaeology)

## License

[MIT](LICENSE)
