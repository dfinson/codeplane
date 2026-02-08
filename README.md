<p align="center">
  <img src="docs/images/logo.png" alt="CodePlane Logo" width="200">
</p>

<h1 align="center">CodePlane</h1>

<p align="center">
  <strong>Local repository control plane for AI coding agents</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/status-alpha-yellow" alt="Status: Alpha">
  <img src="https://img.shields.io/badge/python-≥3.12-blue" alt="Python ≥3.12">
  <a href="https://codecov.io/gh/dfinson/codeplane"><img src="https://codecov.io/gh/dfinson/codeplane/branch/main/graph/badge.svg" alt="Coverage"></a>
  <img src="https://img.shields.io/github/license/dfinson/codeplane" alt="License">
</p>

---

## Quick Start

```bash
pip install git+https://github.com/dfinson/codeplane.git
```

```bash
cd /path/to/your-repo
cpl up              # Start daemon on default port 7654 (foreground, Ctrl+C to stop)
cpl up --port 7655  # Or specify a port
```

That's it. `cpl up` automatically:
- Creates `.vscode/mcp.json` with the MCP server config
- Injects agent instructions into `AGENTS.md` and `.github/copilot-instructions.md`
- Syncs the port if you change it later (`cpl up --port 8000`)

### CLI Reference

| Command | Description |
|---------|-------------|
| `cpl up` | Start server (foreground) |
| `cpl up --port N` | Start on specific port |
| `cpl up --reindex` | Rebuild index from scratch |
| `cpl init` | Initialize without starting |
| `cpl status` | Check daemon status |
| `cpl clear` | Clear index and cache |

---

## The Problem

AI coding agents lose a **lot** of time and waste a lot of token consumption on tasks that should be much closer to instant.

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

## IDE & Agent Integration

`cpl init` / `cpl up` automatically configures your IDE and agents:

| File | Purpose |
|------|---------||
| `.vscode/mcp.json` | VS Code MCP server config (uses `mcp-remote` for HTTP transport) |
| `AGENTS.md` | Tool reference for AI agents (tool names, parameters, patterns) |
| `.github/copilot-instructions.md` | Same instructions for GitHub Copilot |

The server name follows the pattern `codeplane-{repo_name}`, so tools appear as `mcp_codeplane_myrepo_read_files`, etc.

## MCP Tools

CodePlane exposes 30+ MCP tools organized by domain:

| Domain | Tools | Description |
|--------|-------|-------------|
| **Files** | `read_files`, `list_files` | Read content, list directories with filtering |
| **Git** | `git_status`, `git_diff`, `git_commit`, `git_log`, `git_push`, `git_pull`, `git_checkout`, `git_merge`, `git_reset`, `git_stage`, `git_branch`, `git_remote`, `git_stash`, `git_rebase`, `git_inspect`, `git_history`, `git_submodule`, `git_worktree` | Complete git operations with structured output |
| **Index** | `search`, `map_repo` | Lexical/symbol search, repository mental model |
| **Mutation** | `write_files` | Atomic file create/update/delete with content-addressed edits |
| **Refactor** | `refactor_rename`, `refactor_move`, `refactor_delete`, `refactor_apply`, `refactor_cancel`, `refactor_inspect` | Index-based refactoring with preview and certainty levels |
| **Testing** | `discover_test_targets`, `run_test_targets`, `get_test_run_status`, `cancel_test_run` | Multi-language test discovery and execution |
| **Lint** | `lint_check`, `lint_tools` | Auto-detected linters, formatters, type checkers |
| **Introspection** | `describe` | Self-documenting tool schemas |

## Roadmap

Track progress via [GitHub Milestones](https://github.com/dfinson/codeplane/milestones):

| Milestone | Description | Status |
|-----------|-------------|--------|
| **M0** | Foundation: Core types, errors, logging, configuration | ✅ |
| **M1** | Git Operations: Status, staging, commits, branches, diffs | ✅ |
| **M2** | Index Engine: Tantivy lexical + Tree-sitter/SQLite structural facts | 🚧 |
| **M3** | Refactor Planner: Bounded candidate sets with coverage/risk manifests | ✅ |
| **M4** | Mutation Engine: Atomic file changes with rollback | ✅ |
| **M5** | Ledger & Task Model: Operation history, convergence metrics | 🚧 |
| **M6** | Daemon & CLI: HTTP daemon, `cpl` CLI commands | ✅ |
| **M7** | Core MCP Tools: File ops, search, git tools for agents | ✅ |
| **M8** | Test Runner: Framework detection, parallel execution | ✅ |
| **M9** | Polish & Hardening: Docs, benchmarks, security, packaging | 🚧 |
| **M10** | Advanced Semantic Support (SCIP/LSP analysis) | 🔬 |

## Contributing

```bash
# Clone and install with dev dependencies
git clone https://github.com/dfinson/codeplane.git
cd codeplane
pip install -e ".[dev]"

# Start CodePlane on itself
cpl up
```

Then point your AI agent at the running MCP server.

## License

[MIT](LICENSE)
