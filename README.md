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

CodePlane executes deterministic operations for AI coding agents. It owns indexing, file mutations, and Git operations—returning structured, auditable results so agents can act without probing or guesswork.

## Status

🚧 **Pre-alpha** — M0 + M1 complete, M2 (Index Engine) in progress.

Core infrastructure and Git operations are implemented. See the [roadmap](#roadmap) for progress.

## Architecture

CodePlane provides a **full stacked index**:

- **Tier 0 — Tantivy Lexical Index**: Fast, deterministic lexical retrieval for candidate discovery
- **Tier 1 — Tree-sitter/SQLite Structural Facts**: Definitions, references, scopes, imports, exports

The planner produces:
- Bounded candidate sets
- Patch previews with text edits
- Coverage + Risk manifests (explicit about what is PROVEN vs ANCHORED)

**Auto-apply rules:**
- Only PROVEN edits (same-file bindings, explicit imports) can auto-apply
- Everything else is proposal-only unless explicitly confirmed

**What CodePlane is NOT:**
- Not a semantic refactor engine (no SCIP/LSP authority)
- Not an IDE replacement
- Not an agent or orchestrator

## Roadmap

Track progress via [GitHub Milestones](https://github.com/dfinson/codeplane/milestones):

| Milestone | Description | Status |
|-----------|-------------|--------|
| M0: Foundation | Core types, errors, logging, configuration | ✅ Complete |
| M1: Git Operations | Status, staging, commits, branches, diffs | ✅ Complete |
| M2: Index Engine | Tantivy lexical + Tree-sitter/SQLite structural facts | 🚧 In Progress |
| M3: Refactor Planner | Bounded candidate sets with coverage/risk manifests | |
| M4: Mutation Engine | Atomic file changes with rollback | |
| M5: Ledger & Task Model | Operation history, convergence metrics | |
| M6: Daemon & CLI | HTTP daemon, `cpl` CLI commands | |
| M7: Core MCP Tools | File ops, search, git tools for agents | |
| M8: Test Runner | Framework detection, parallel execution | |
| M9: Polish & Hardening | Docs, benchmarks, security, packaging | |
| M10: Advanced Semantic Support | Research milestone: SCIP/LSP integration analysis | 🔬 Research |

**Note:** M10 is a research milestone where we analyze complexity vs benefit for full semantic refactor support (SCIP, LSP backends). Implementation decisions will be made based on documented analysis.

## Development

```bash
make dev         # Install with dev dependencies
make lint        # Run ruff linter
make typecheck   # Run mypy
make test        # Run pytest
```

## Design Authority

[SPEC.md](SPEC.md) is the single source of design truth. All design decisions are documented there, including:
- Section 7: Index Architecture (Tier 0 + Tier 1)
- Section 19: Semantic Support Exploration (design archaeology of approaches that failed)

## License

[MIT](LICENSE)
